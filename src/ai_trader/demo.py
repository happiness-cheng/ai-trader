"""不访问模型、网络或交易软件的安全离线演示。"""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field

from ai_trader.domain.trading import (
    AccountSnapshot,
    OrderSide,
    Quote,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.evals.loader import load_dataset
from ai_trader.evals.runner import ExpectedTraceExecutor, run_dataset
from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from ai_trader.risk.gate import RiskContext, RiskGate
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.tools.runtime import (
    ToolCall,
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolRisk,
    ToolRuntime,
)


DEMO_TIME = datetime(2026, 7, 11, 2, 0, tzinfo=UTC)


def build_demo_context() -> RiskContext:
    proposal = TradeProposal(
        proposal_id="proposal-demo-001",
        symbol="600519",
        side=OrderSide.BUY,
        quantity=100,
        limit_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("115"),
        evidence_refs=("fixture-quote-001", "fixture-signal-001"),
        created_at=DEMO_TIME - timedelta(seconds=5),
        expires_at=DEMO_TIME + timedelta(minutes=2),
    )
    return RiskContext(
        proposal=proposal,
        quote=Quote(symbol="600519", price=Decimal("100"), as_of=DEMO_TIME),
        account=AccountSnapshot(
            total_equity=Decimal("100000"),
            available_cash=Decimal("50000"),
            daily_pnl=Decimal("0"),
        ),
        positions=(),
        open_order_symbols=frozenset(),
        trading_enabled=True,
        now=DEMO_TIME,
    )


def run_trade_demo() -> int:
    context = build_demo_context()
    pending = RiskGate().evaluate(context)
    print(f"proposal={context.proposal.proposal_id}")
    print(f"risk_outcome={pending.outcome.value}")

    approved = RiskDecision(
        proposal_id=context.proposal.proposal_id,
        outcome=RiskOutcome.APPROVE,
        codes=("HUMAN_APPROVED",),
        reasons=("离线演示中的显式人工审批",),
    )
    broker = PaperBrokerAdapter(
        id_factory=lambda: "paper-demo-001",
        clock=lambda: DEMO_TIME,
    )
    order = broker.submit(context.proposal, approved)
    print(f"order={order.order_id}")
    print(f"status={order.status.value}")
    print("live_execution=false")
    return 0


class _QuoteInput(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")


class _QuoteOutput(BaseModel):
    symbol: str
    price: Decimal


def run_structured_agent_demo() -> int:
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            name="get_quote",
            description="获取离线 fixture 行情",
            input_model=_QuoteInput,
            output_model=_QuoteOutput,
            handler=lambda data: _QuoteOutput(
                symbol=data.symbol, price=Decimal("100")
            ),
            permission="market:read",
            risk=ToolRisk.LOW,
            effect=ToolEffect.READ,
        )
    )
    provider = FakeModelProvider(
        responses=[
            ModelResponse(
                provider="fake",
                model="fake-agent",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-quote-1",
                        name="get_quote",
                        arguments={"symbol": "600519"},
                    ),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
                attempts=1,
                elapsed_ms=0,
            ),
            ModelResponse(
                provider="fake",
                model="fake-agent",
                text="600519 的离线价格为 100，未执行真实交易。",
                usage=ModelUsage(input_tokens=20, output_tokens=10),
                attempts=1,
                elapsed_ms=0,
            ),
        ]
    )
    gateway = ModelGateway([provider])
    tool_context = ToolExecutionContext(permissions=frozenset({"market:read"}))
    request = ModelRequest(
        model="fake-agent",
        messages=({"role": "user", "content": "查询 600519 行情"},),
        tools=runtime.schemas(tool_context),
    )
    first = gateway.complete(request)
    model_call = first.tool_calls[0]
    result = runtime.execute(
        ToolCall(
            call_id=model_call.call_id,
            name=model_call.name,
            arguments=model_call.arguments,
        ),
        tool_context,
    )
    final = gateway.complete(
        request.model_copy(
            update={
                "messages": request.messages
                + ({"role": "tool", "content": str(result.output)},)
            }
        )
    )
    print(f"model_tool_call={model_call.name}")
    print(f"tool_result={result.output}")
    print(f"final={final.text}")
    print("live_execution=false")
    return 0


def run_recovery_demo() -> int:
    ticks = count()
    with TemporaryDirectory(prefix="ai-trader-demo-") as temp_dir:
        database_path = Path(temp_dir) / "runs.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        first_repository = SqlRunRepository(database_url)
        first = PersistentOrchestrator(
            first_repository,
            clock=lambda: DEMO_TIME + timedelta(seconds=next(ticks)),
        )
        run = first.create_run("offline recovery fixture", run_id="run-demo-001")
        first.start(run.run_id)
        first_repository.close()

        second_repository = SqlRunRepository(database_url)
        recovered = second_repository.get(run.run_id)
        events = second_repository.events(run.run_id)
        print(f"recovered_run={recovered.run_id}")
        print(f"state={recovered.state.value}")
        print(f"version={recovered.version}")
        print(f"events={len(events)}")
        print("replayed_side_effects=0")
        second_repository.close()
    return 0


def run_evals_demo() -> int:
    report = run_dataset(
        load_dataset("evals/scenarios/core.json"), ExpectedTraceExecutor()
    )
    print(f"scenarios={report.scenario_count}")
    print(f"pass_rate={report.pass_rate:.1%}")
    print(f"policy_compliance={report.metrics['policy_compliance']:.1%}")
    print(f"groundedness={report.metrics['groundedness']:.1%}")
    print("mode=deterministic_contract_baseline")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Trader 安全离线演示")
    parser.add_argument(
        "--agent", action="store_true", help="运行结构化工具调用演示"
    )
    parser.add_argument(
        "--recovery", action="store_true", help="运行 SQLite 任务恢复演示"
    )
    parser.add_argument("--evals", action="store_true", help="运行确定性 Eval 契约基线")
    args = parser.parse_args(argv)
    if args.evals:
        return run_evals_demo()
    if args.recovery:
        return run_recovery_demo()
    return run_structured_agent_demo() if args.agent else run_trade_demo()


if __name__ == "__main__":
    raise SystemExit(main())
