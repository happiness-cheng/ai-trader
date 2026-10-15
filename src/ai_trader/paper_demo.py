"""可复现的 Paper 闭环；固定模型 / 行情，不读取凭据，不访问网络。"""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp

from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.domain.trading import AccountSnapshot, Quote, TradeProposal
from ai_trader.execution.coordinator import ExecutionCoordinator
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.execution.workflow import TradingWorkflow, WorkflowConflict
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelGateway, ModelResponse, ModelToolCall, ModelUsage
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.persistence.trading import SqlTradingRepository
from ai_trader.risk.gate import RiskContext
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime


class QuoteInput(BaseModel):
    symbol: str


class QuoteOutput(BaseModel):
    price: Decimal = Decimal(100)


def fixture_response(*, text: str = '', calls: tuple[ModelToolCall, ...] = ()) -> ModelResponse:
    return ModelResponse(provider='fake', model='fixture', text=text, tool_calls=calls,
        usage=ModelUsage(input_tokens=15, output_tokens=5), attempts=1, elapsed_ms=0)


def run_demo(directory: Path, *, response_loss: bool = False) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC)
    url = f"sqlite:///{(directory / 'workflow.db').as_posix()}"
    runs, trades = SqlRunRepository(url), SqlTradingRepository(url)
    orchestration = PersistentOrchestrator(runs, clock=lambda: now)
    tools = ToolRuntime()
    tools.register(ToolDefinition(name='get_quote', description='fixed quote', input_model=QuoteInput,
        output_model=QuoteOutput, handler=lambda _: QuoteOutput(), permission='market:read'))
    decision = json.dumps({'action': 'propose', 'trade': {'symbol': '600519', 'side': 'buy',
        'quantity': 100, 'limit_price': '100', 'stop_loss': '95', 'take_profit': '115',
        'evidence_refs': ['get_quote']}})
    runner = ProductionAgentRunner(ModelGateway([FakeModelProvider([
        fixture_response(calls=(ModelToolCall(call_id='quote', name='get_quote', arguments={'symbol': '600519'}),)),
        fixture_response(text=decision),
    ])]), tools, orchestration, ToolExecutionContext(permissions=frozenset({'market:read'})), model='fixture')

    class LostResponseBroker(PaperBrokerAdapter):
        def submit(self, proposal, decision):  # type: ignore[no-untyped-def]
            super().submit(proposal, decision)
            raise TimeoutError('simulated response loss after persistence')

    ledger = directory / 'paper-ledger.json'
    broker_type = LostResponseBroker if response_loss else PaperBrokerAdapter
    broker = broker_type(clock=lambda: now, ledger_path=ledger)

    def context(proposal: TradeProposal) -> RiskContext:
        return RiskContext(proposal=proposal,
            quote=Quote(symbol=proposal.symbol, price=Decimal(100), as_of=now),
            account=AccountSnapshot(total_equity=Decimal(100000), available_cash=Decimal(50000)),
            positions=(), open_order_symbols=frozenset(), trading_enabled=True, now=now)

    workflow = TradingWorkflow(runner, orchestration, runs, trades,
        ExecutionCoordinator(trades, broker, clock=lambda: now), context, clock=lambda: now)
    try:
        result = workflow.analyze('根据固定行情提出 Paper 建议')
        run_id = result.run.run_id
        if response_loss:
            try:
                workflow.approve(run_id, 'local-demo-reviewer')
            except WorkflowConflict:
                pass
            runs.close()
            trades.close()
            runs, trades = SqlRunRepository(url), SqlTradingRepository(url)
            broker = PaperBrokerAdapter(clock=lambda: now, ledger_path=ledger)
            workflow = TradingWorkflow(None, PersistentOrchestrator(runs, clock=lambda: now), runs, trades,
                ExecutionCoordinator(trades, broker, clock=lambda: now), context, clock=lambda: now)
            order = workflow.recover(run_id)
        else:
            order = workflow.approve(run_id, 'local-demo-reviewer')
        if order is None:
            raise RuntimeError('fixture recovery did not find the order')
        return {'mode': 'offline_fixture_paper', 'network': False, 'live_execution': False,
            'run_id': run_id, 'state': runs.get(run_id).state.value,
            'orders': len(broker.list_orders()), 'recovered': response_loss,
            'duplicate_same_order': workflow.approve(run_id, 'local-demo-reviewer') == order,
            'events': [event.model_dump(mode='json') for event in runs.events(run_id)],
            'artifact_directory': str(directory)}
    finally:
        runs.close()
        trades.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--response-loss', action='store_true')
    parser.add_argument('--output', type=Path, help='必须是尚不存在的目录，避免覆盖历史演示')
    args = parser.parse_args(argv)
    directory = args.output or Path(mkdtemp(prefix='ai-trader-paper-')) / 'artifacts'
    result = run_demo(directory, response_loss=args.response_loss)
    (directory / 'trace.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'events'}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
