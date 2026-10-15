"""把只读 Agent、类型化提案与人工审批连接为限定 Paper 业务流程。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner, RunnerResult
from ai_trader.agents.state import AgentRun, EventType, InvalidTransition, RunState
from ai_trader.domain.trading import (
    Order,
    OrderSide,
    OrderStatus,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.execution.coordinator import ExecutionCoordinator, ExecutionUnknown
from ai_trader.persistence.runs import SqlRunRepository, VersionConflict
from ai_trader.persistence.trading import SqlTradingRepository
from ai_trader.risk.gate import RiskContext

DECISION_INSTRUCTION = '''\n最终只输出 JSON：
{"action":"propose","trade":{"symbol":"六位代码","side":"buy 或 sell",
"quantity":100,"limit_price":"价格","stop_loss":"止损","take_profit":"止盈",
"evidence_refs":["实际成功调用过的工具名"]}}
或 {"action":"no_trade","reason":"证据不足或无需操作的具体原因"}。
不生成提案 ID 或时间，不调用任何执行工具；风控与人工审批由服务端处理。'''


class WorkflowConflict(RuntimeError):
    pass


class TradeTerms(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    symbol: str = Field(pattern=r'^\d{6}$')
    side: OrderSide
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class BusinessDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    action: Literal['propose', 'no_trade']
    trade: TradeTerms | None = None
    reason: str = Field(default='', max_length=1000)


@dataclass(frozen=True)
class WorkflowResult:
    run: AgentRun
    proposal: TradeProposal | None = None
    risk: RiskDecision | None = None
    analysis: RunnerResult | None = None


class TradingWorkflow:
    def __init__(
        self, runner: ProductionAgentRunner | None, orchestrator: PersistentOrchestrator,
        runs: SqlRunRepository, trades: SqlTradingRepository, coordinator: ExecutionCoordinator,
        context_source: Callable[[TradeProposal], RiskContext], *,
        clock: Callable[[], datetime] | None = None,
        constraints_source: Callable[[str | None], dict[str, object]] | None = None,
        confirmed_event_sink: Callable[[str, Order], None] | None = None,
    ) -> None:
        if not coordinator.paper_only:
            raise ValueError('workflow only supports Paper Broker')
        self._runner = runner
        self._orchestrator = orchestrator
        self._runs, self._trades = runs, trades
        self._coordinator, self._context_source = coordinator, context_source
        self._clock = clock or (lambda: datetime.now(UTC))
        self._constraints_source = constraints_source
        self._confirmed_event_sink = confirmed_event_sink

    def analyze(self, goal: str, *, run_id: str | None = None) -> WorkflowResult:
        if self._runner is None:
            raise WorkflowConflict('analysis runner not configured')
        constraints = None
        if self._constraints_source is not None:
            if run_id is None:
                run_id = self._orchestrator.create_run(goal).run_id
            try:
                constraints = self._constraints_source(run_id)
            except Exception as exc:
                self._orchestrator.transition(run_id, RunState.FAILED,
                    {'error': 'analysis_context_unavailable', 'error_type': type(exc).__name__})
                raise WorkflowConflict('analysis context unavailable; no model call attempted') from exc
        result = self._runner.run(goal, instructions=DECISION_INSTRUCTION, constraints=constraints,
                                  complete_run=False, run_id=run_id)
        try:
            decision = BusinessDecision.model_validate_json(result.final_text)
            if decision.action == 'no_trade':
                if not decision.reason.strip() or decision.trade is not None:
                    raise WorkflowConflict('no_trade requires reason and no proposal')
                run = self._orchestrator.transition(result.run_id, RunState.SUCCEEDED,
                                                     {'action': 'no_trade', 'reason': decision.reason})
                return WorkflowResult(run=run, analysis=result)
            if decision.trade is None:
                raise WorkflowConflict('proposal is missing')
            if not set(decision.trade.evidence_refs) <= set(result.observation.evidence_refs):
                raise WorkflowConflict('proposal cites evidence not observed by runner')
            now = self._clock()
            proposal = TradeProposal(
                **decision.trade.model_dump(), proposal_id=f'proposal:{result.run_id}',
                created_at=now, expires_at=now + timedelta(minutes=5),
            )
        except (ValidationError, WorkflowConflict) as exc:
            self._orchestrator.transition(result.run_id, RunState.FAILED, {'error': 'invalid_decision'})
            raise WorkflowConflict(f'invalid decision: {type(exc).__name__}') from exc
        try:
            context = self._context_source(proposal)
            risk = self._coordinator.submit_proposal(context)
        except Exception as exc:
            self._orchestrator.transition(result.run_id, RunState.FAILED,
                {'error': 'risk_context_unavailable', 'error_type': type(exc).__name__})
            raise WorkflowConflict('risk context unavailable; no execution attempted') from exc
        if risk.outcome is RiskOutcome.REJECT:
            run = self._orchestrator.transition(result.run_id, RunState.FAILED,
                                                 {'proposal_id': proposal.proposal_id, 'risk': list(risk.codes)})
        else:
            run = self._orchestrator.request_approval(result.run_id, {'proposal_id': proposal.proposal_id})
        return WorkflowResult(run=run, proposal=proposal, risk=risk, analysis=result)

    def proposal_for(self, run_id: str) -> TradeProposal:
        self._runs.get(run_id)
        for event in reversed(self._runs.events(run_id)):
            proposal_id = event.payload.get('proposal_id')
            if isinstance(proposal_id, str):
                return self._trades.get_proposal(proposal_id)
        raise WorkflowConflict('run has no linked proposal')

    def _claim(self, run: AgentRun, target: RunState, payload: dict[str, object]) -> AgentRun:
        try:
            return self._runs.append_transition(
                run_id=run.run_id, expected_version=run.version, target=target,
                event_type=EventType.INTENT_RECORDED if target is RunState.EXECUTING else EventType.CANCEL_REQUESTED,
                payload=payload, now=self._clock(), event_id=f'event-{uuid4()}',
            )
        except (VersionConflict, InvalidTransition) as exc:
            raise WorkflowConflict('run state conflict') from exc

    def approve(self, run_id: str, reviewer_id: str) -> Order:
        if not reviewer_id.strip():
            raise WorkflowConflict('reviewer required')
        current = self._runs.get(run_id)
        proposal = self.proposal_for(run_id)
        if current.state is RunState.SUCCEEDED:
            order = self._trades.get_order_by_proposal(proposal.proposal_id)
            if order is not None:
                self._notify_confirmed(run_id, order)
                return order
        if current.state is not RunState.WAITING_APPROVAL:
            raise WorkflowConflict('run is not waiting for approval; reconcile unknown execution first')
        self._claim(current, RunState.EXECUTING,
                    {'proposal_id': proposal.proposal_id, 'reviewer_id': reviewer_id})
        try:
            self._coordinator.approve(proposal.proposal_id, reviewer_id)
            context = self._context_source(proposal)
            order = self._coordinator.execute_approved(context)
        except ExecutionUnknown as exc:
            if not exc.uncertain:
                self._orchestrator.transition(run_id, RunState.FAILED, {'error': str(exc)})
            raise WorkflowConflict(str(exc)) from exc
        except Exception as exc:
            # 委托是否已执行未知：保留 EXECUTING，只允许对账，不自动再次提交。
            self._runs.append_event(
                run_id=run_id, expected_version=self._runs.get(run_id).version,
                event_type=EventType.ERROR_RECORDED,
                payload={'error': 'execution_unknown', 'error_type': type(exc).__name__},
                now=self._clock(), event_id=f'event-{uuid4()}',
            )
            raise WorkflowConflict('execution unknown; reconciliation required') from exc
        return self._finish(run_id, order)

    def _finish(self, run_id: str, order: Order) -> Order:
        if self._runs.get(run_id).state is RunState.EXECUTING:
            self._orchestrator.transition(run_id, RunState.VERIFYING, {'order_id': order.order_id})
        if order.status is not OrderStatus.FILLED:
            raise WorkflowConflict('Paper order is not filled; verification pending')
        if self._runs.get(run_id).state is RunState.VERIFYING:
            self._orchestrator.transition(run_id, RunState.SUCCEEDED, {'order_id': order.order_id})
        self._notify_confirmed(run_id, order)
        return order

    def _notify_confirmed(self, run_id: str, order: Order) -> None:
        if self._confirmed_event_sink is None or order.status is not OrderStatus.FILLED:
            return
        try:
            self._confirmed_event_sink(run_id, order)
        except Exception as exc:  # noqa: BLE001  记忆不可用不能改变已经确认的成交事实。
            try:
                self._runs.append_event(
                    run_id=run_id, expected_version=self._runs.get(run_id).version,
                    event_type=EventType.ERROR_RECORDED,
                    payload={'error': 'memory_write_failed', 'error_type': type(exc).__name__,
                             'order_id': order.order_id},
                    now=self._clock(), event_id=f'event-{uuid4()}',
                )
            except Exception:  # noqa: BLE001  审计失败也不能将已成交操作变成可重新执行。
                logging.getLogger(__name__).error('confirmed trade memory failure could not be audited')

    def reject(self, run_id: str, reviewer_id: str, reason: str) -> AgentRun:
        if not reviewer_id.strip() or not reason.strip():
            raise WorkflowConflict('reviewer and rejection reason required')
        current = self._runs.get(run_id)
        proposal = self.proposal_for(run_id)
        if current.state is not RunState.WAITING_APPROVAL:
            raise WorkflowConflict('only a waiting run can be rejected')
        run = self._claim(current, RunState.CANCELLED, {'reason': reason, 'reviewer_id': reviewer_id})
        self._coordinator.reject(proposal.proposal_id, reviewer_id, reason)
        return run

    def recover(self, run_id: str) -> Order | None:
        run = self._runs.get(run_id)
        if run.state not in {RunState.EXECUTING, RunState.VERIFYING, RunState.SUCCEEDED}:
            raise WorkflowConflict('run is not in an execution recovery state')
        proposal = self.proposal_for(run_id)
        order = self._coordinator.reconcile(proposal.proposal_id)
        return None if order is None else self._finish(run_id, order)
