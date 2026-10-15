"""从真实 Agent loop 到 Paper 执行与 run 终态的业务验收。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.agents.state import RunState
from ai_trader.domain.trading import AccountSnapshot, Quote
from ai_trader.execution.coordinator import ExecutionCoordinator
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.execution.workflow import TradingWorkflow, WorkflowConflict
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelGateway, ModelResponse, ModelToolCall, ModelUsage
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.persistence.trading import SqlTradingRepository
from ai_trader.risk.gate import RiskContext
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime

NOW = datetime(2026, 10, 12, 2, tzinfo=UTC)


class Empty(BaseModel):
    pass


class QuoteResult(BaseModel):
    symbol: str = '600519'
    price: Decimal = Decimal(100)


@pytest.fixture
def system(tmp_path):
    url = f"sqlite:///{tmp_path / 'workflow.db'}"
    runs, trades = SqlRunRepository(url), SqlTradingRepository(url)
    clock = [NOW]
    orchestration = PersistentOrchestrator(runs, clock=lambda: clock[0])
    tools = ToolRuntime()
    tools.register(ToolDefinition(name='get_quote', description='fixture quote',
        input_model=Empty, output_model=QuoteResult, handler=lambda _: QuoteResult(), permission='market:read'))
    proposal_text = json.dumps({'action': 'propose', 'trade': {
        'symbol': '600519', 'side': 'buy', 'quantity': 100, 'limit_price': '100',
        'stop_loss': '95', 'take_profit': '115', 'evidence_refs': ['get_quote']}})
    responses = [ModelResponse(provider='fake', model='fixture', tool_calls=(
        ModelToolCall(call_id='quote-1', name='get_quote', arguments={}),),
        usage=ModelUsage(input_tokens=10, output_tokens=5), attempts=1, elapsed_ms=0),
        ModelResponse(provider='fake', model='fixture', text=proposal_text,
        usage=ModelUsage(input_tokens=20, output_tokens=10), attempts=1, elapsed_ms=0)]
    runner = ProductionAgentRunner(ModelGateway([FakeModelProvider(responses)]), tools, orchestration,
        ToolExecutionContext(permissions=frozenset({'market:read'})), model='fixture')
    broker = PaperBrokerAdapter(clock=lambda: clock[0])
    coordinator = ExecutionCoordinator(trades, broker, clock=lambda: clock[0])
    cash = [Decimal(50000)]
    def context(proposal):
        return RiskContext(proposal=proposal, quote=Quote(symbol=proposal.symbol, price=Decimal(100), as_of=clock[0]),
            account=AccountSnapshot(total_equity=Decimal(100000), available_cash=cash[0]),
            positions=(), open_order_symbols=frozenset(), trading_enabled=True, now=clock[0])
    workflow = TradingWorkflow(runner, orchestration, runs, trades, coordinator, context,
                               clock=lambda: clock[0])
    yield SimpleNamespace(workflow=workflow, runs=runs, trades=trades, broker=broker,
                          coordinator=coordinator, runner=runner, orchestration=orchestration,
                          context=context, clock=clock, cash=cash, url=url, tools=tools)
    runs.close()
    trades.close()


def test_analysis_waits_for_approval_then_executes_and_finishes(system):
    result = system.workflow.analyze('分析离线行情')
    assert result.run.state is RunState.WAITING_APPROVAL
    assert len(system.broker.list_orders()) == 0
    order = system.workflow.approve(result.run.run_id, 'reviewer')
    assert order.quantity == 100
    assert system.runs.get(result.run.run_id).state is RunState.SUCCEEDED
    assert system.workflow.approve(result.run.run_id, 'reviewer') == order
    assert len(system.broker.list_orders()) == 1


def test_workflow_preserves_maximum_length_user_goal(system):
    goal = '分' * 4000
    result = system.workflow.analyze(goal)
    assert result.run.goal == goal
    assert result.run.state is RunState.WAITING_APPROVAL


def test_server_risk_context_failure_is_a_terminal_analysis_error(system):
    def unavailable(_):
        raise TimeoutError('account source unavailable')
    workflow = TradingWorkflow(system.runner, system.orchestration, system.runs, system.trades,
        system.coordinator, unavailable, clock=lambda: NOW)
    run = system.orchestration.create_run('上下文不可用')
    with pytest.raises(WorkflowConflict, match='context'):
        workflow.analyze(run.goal, run_id=run.run_id)
    assert system.runs.get(run.run_id).state is RunState.FAILED
    assert system.broker.list_orders() == ()


def test_rejection_is_terminal_and_cannot_execute(system):
    result = system.workflow.analyze('分析行情')
    system.workflow.reject(result.run.run_id, 'reviewer', '不执行')
    assert system.runs.get(result.run.run_id).state is RunState.CANCELLED
    with pytest.raises(WorkflowConflict):
        system.workflow.approve(result.run.run_id, 'reviewer')
    assert system.broker.list_orders() == ()


def test_risk_is_read_again_from_server_before_execution(system):
    result = system.workflow.analyze('分析行情')
    system.cash[0] = Decimal(0)
    with pytest.raises(WorkflowConflict, match='risk'):
        system.workflow.approve(result.run.run_id, 'reviewer')
    assert system.runs.get(result.run.run_id).state is RunState.FAILED
    assert system.broker.list_orders() == ()


def test_expiration_fails_before_execution(system):
    result = system.workflow.analyze('分析行情')
    system.clock[0] += timedelta(minutes=10)
    with pytest.raises(WorkflowConflict, match='expired'):
        system.workflow.approve(result.run.run_id, 'reviewer')
    assert system.runs.get(result.run.run_id).state is RunState.FAILED


def test_waiting_task_recovers_with_new_workflow_and_repositories(system):
    result = system.workflow.analyze('分析行情')
    system.runs.close()
    system.trades.close()
    runs, trades = SqlRunRepository(system.url), SqlTradingRepository(system.url)
    try:
        workflow = TradingWorkflow(None, PersistentOrchestrator(runs, clock=lambda: system.clock[0]),
            runs, trades, ExecutionCoordinator(trades, system.broker, clock=lambda: system.clock[0]),
            system.context, clock=lambda: system.clock[0])
        order = workflow.approve(result.run.run_id, 'reviewer')
        assert order.proposal_id == result.proposal.proposal_id
        assert runs.get(result.run.run_id).state is RunState.SUCCEEDED
    finally:
        runs.close()
        trades.close()


def test_repeated_parallel_approval_only_submits_once(system):
    from concurrent.futures import ThreadPoolExecutor
    result = system.workflow.analyze('分析行情')
    def attempt(_):
        try:
            return system.workflow.approve(result.run.run_id, 'reviewer').order_id
        except WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(4)))
    assert any(value != 'conflict' for value in outcomes)
    assert len(system.broker.list_orders()) == 1
    assert system.runs.get(result.run.run_id).state is RunState.SUCCEEDED


def test_approve_reject_race_has_single_terminal_winner(system):
    from concurrent.futures import ThreadPoolExecutor
    result = system.workflow.analyze('分析行情')
    def attempt(action):
        try:
            if action == 'approve':
                system.workflow.approve(result.run.run_id, 'reviewer')
            else:
                system.workflow.reject(result.run.run_id, 'reviewer', '拒绝')
            return action
        except WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ['approve', 'reject']))
    state = system.runs.get(result.run.run_id).state
    assert outcomes.count('conflict') == 1
    assert state in {RunState.SUCCEEDED, RunState.CANCELLED}
    assert len(system.broker.list_orders()) == int(state is RunState.SUCCEEDED)


def test_no_trade_is_completed_without_approval_or_order(system):
    response = ModelResponse(provider='fake', model='fixture',
        text=json.dumps({'action': 'no_trade', 'reason': '证据不足'}),
        usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0)
    runner = ProductionAgentRunner(ModelGateway([FakeModelProvider([response])]), system.tools,
        system.orchestration, ToolExecutionContext(permissions=frozenset({'market:read'})), model='fixture')
    workflow = TradingWorkflow(runner, system.orchestration, system.runs, system.trades,
        system.coordinator, system.context, clock=lambda: NOW)
    result = workflow.analyze('没有机会时不要交易')
    assert result.run.state is RunState.SUCCEEDED
    assert result.proposal is None
    assert system.broker.list_orders() == ()


def test_rejection_invalidates_prior_approval_at_same_timestamp(system):
    result = system.workflow.analyze('分析行情')
    system.coordinator.approve(result.proposal.proposal_id, 'prior-reviewer')
    system.workflow.reject(result.run.run_id, 'reviewer', '撤销')
    assert system.trades.get_valid_approval(result.proposal.proposal_id, NOW) is None


def test_reserved_unknown_execution_remains_recoverable(system):
    result = system.workflow.analyze('分析行情')
    system.trades.reserve_order(result.proposal.proposal_id, 'prior-reservation', NOW)
    with pytest.raises(WorkflowConflict, match='unknown'):
        system.workflow.approve(result.run.run_id, 'reviewer')
    assert system.runs.get(result.run.run_id).state is RunState.EXECUTING
    assert system.workflow.recover(result.run.run_id) is None
    assert system.broker.list_orders() == ()


def test_paper_order_survives_broker_restart_and_response_loss(system, tmp_path):
    class LostResponseBroker(PaperBrokerAdapter):
        def submit(self, proposal, decision):
            super().submit(proposal, decision)
            raise TimeoutError('response lost after Broker persisted order')
    ledger = tmp_path / 'paper-ledger.json'
    broker = LostResponseBroker(clock=lambda: NOW, ledger_path=ledger)
    coordinator = ExecutionCoordinator(system.trades, broker, clock=lambda: NOW)
    workflow = TradingWorkflow(system.runner, system.orchestration, system.runs, system.trades,
        coordinator, system.context, clock=lambda: NOW)
    result = workflow.analyze('分析行情')
    with pytest.raises(WorkflowConflict, match='unknown'):
        workflow.approve(result.run.run_id, 'reviewer')
    assert system.runs.get(result.run.run_id).state is RunState.EXECUTING
    restarted_broker = PaperBrokerAdapter(clock=lambda: NOW, ledger_path=ledger)
    resumed = TradingWorkflow(None, system.orchestration, system.runs, system.trades,
        ExecutionCoordinator(system.trades, restarted_broker, clock=lambda: NOW),
        system.context, clock=lambda: NOW)
    order = resumed.recover(result.run.run_id)
    assert order is not None
    assert system.runs.get(result.run.run_id).state is RunState.SUCCEEDED
    assert len(restarted_broker.list_orders()) == 1
    assert resumed.recover(result.run.run_id) == order


def test_http_control_plane_drives_the_same_workflow(system):
    from fastapi.testclient import TestClient
    from ai_trader.api.app import ApiDependencies, create_app
    client = TestClient(create_app(ApiDependencies(
        system.orchestration, system.runs, system.coordinator, workflow=system.workflow)))
    created = client.post('/runs', json={'goal': '离线分析'}).json()
    run_id = created['run_id']
    analyzed = client.post(f'/runs/{run_id}/analyze')
    assert analyzed.status_code == 200
    assert analyzed.json()['run']['state'] == 'waiting_approval'
    assert client.post(f'/runs/{run_id}/approve').status_code == 401
    headers = {'X-Reviewer-Id': 'local-reviewer'}
    approved = client.post(f'/runs/{run_id}/approve', headers=headers)
    assert approved.status_code == 200
    assert approved.json()['run']['state'] == 'succeeded'
    assert client.post(f'/runs/{run_id}/approve', headers=headers).json()['order'] == approved.json()['order']
    assert client.post('/proposals/any/approve', headers=headers).status_code == 409


def test_http_cancel_cannot_interrupt_an_execution_with_unknown_result(system):
    from fastapi.testclient import TestClient
    from ai_trader.api.app import ApiDependencies, create_app
    result = system.workflow.analyze('分析行情')
    system.trades.reserve_order(result.proposal.proposal_id, 'reserved', NOW)
    with pytest.raises(WorkflowConflict):
        system.workflow.approve(result.run.run_id, 'reviewer')
    client = TestClient(create_app(ApiDependencies(
        system.orchestration, system.runs, system.coordinator, workflow=system.workflow)))
    response = client.post(f'/runs/{result.run.run_id}/cancel', json={'reason': '不要丢失对账状态'})
    assert response.status_code == 409
    assert system.runs.get(result.run.run_id).state is RunState.EXECUTING


def test_unobserved_evidence_fails_without_a_proposal(system):
    text = json.dumps({'action': 'propose', 'trade': {'symbol': '600519', 'side': 'buy',
        'quantity': 100, 'limit_price': '100', 'stop_loss': '95', 'take_profit': '115',
        'evidence_refs': ['fabricated']}})
    response = ModelResponse(provider='fake', model='fixture', text=text,
        usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0)
    runner = ProductionAgentRunner(ModelGateway([FakeModelProvider([response])]), system.tools,
        system.orchestration, ToolExecutionContext(permissions=frozenset({'market:read'})), model='fixture')
    workflow = TradingWorkflow(runner, system.orchestration, system.runs, system.trades,
        system.coordinator, system.context, clock=lambda: NOW)
    run = system.orchestration.create_run('证据校验')
    with pytest.raises(WorkflowConflict, match='invalid decision'):
        workflow.analyze('证据校验', run_id=run.run_id)
    assert system.runs.get(run.run_id).state is RunState.FAILED
    assert system.broker.list_orders() == ()
