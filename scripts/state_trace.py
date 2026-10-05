"""X 光机：把一次离线 Agent run 的完整状态序列打出来。

对应问题（Q2）：一个 run 从生到死经历哪些状态？谁驱动每一次转移？
完全离线：用 FakeModelProvider，不联网、不下单、不花钱。

跑法（在仓库根目录）：
    $env:PYTHONPATH='src'
    python scripts/state_trace.py
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import (
    ModelGateway,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.tools.runtime import (
    ToolDefinition,
    ToolExecutionContext,
    ToolRuntime,
)


class _NoInput(BaseModel):
    pass


class _QuoteOutput(BaseModel):
    price: Decimal


def build_runner(repository: SqlRunRepository) -> ProductionAgentRunner:
    """和 runtime.run_dry() 同一套装配，只是把 fake 回复固定成"调一次工具再回答"。"""
    tools = ToolRuntime()
    tools.register(
        ToolDefinition(
            name="get_quote",
            description="离线 fixture 行情",
            input_model=_NoInput,
            output_model=_QuoteOutput,
            handler=lambda _data: _QuoteOutput(price=Decimal("100")),
            permission="market:read",
        )
    )
    provider = FakeModelProvider(
        [
            # 第 1 轮回复：模型要求调用 get_quote
            ModelResponse(
                provider="fake",
                model="fake",
                text="",
                tool_calls=(
                    ModelToolCall(call_id="call-1", name="get_quote", arguments={}),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
                attempts=1,
                elapsed_ms=0,
            ),
            # 第 2 轮回复：模型不再调工具，直接给最终答案
            ModelResponse(
                provider="fake",
                model="fake",
                text="离线答案：价格 100",
                usage=ModelUsage(input_tokens=15, output_tokens=5),
                attempts=1,
                elapsed_ms=0,
            ),
        ]
    )
    return ProductionAgentRunner(
        ModelGateway([provider]),
        tools,
        PersistentOrchestrator(repository, clock=lambda: datetime.now(UTC)),
        ToolExecutionContext(permissions=frozenset({"market:read"})),
        model="fake",
    )


def main() -> int:
    with TemporaryDirectory(prefix="ai-trader-trace-") as temp_dir:
        db = Path(temp_dir) / "runs.db"
        repository = SqlRunRepository(f"sqlite:///{db.as_posix()}")
        runner = build_runner(repository)

        result = runner.run("查一下 600519 的行情")

        print(f"run_id       = {result.run_id}")
        print(f"final_text   = {result.final_text}")
        print(f"steps(轮数)  = {result.observation.steps}")
        print()
        print("事件流水（events 表，append-only）：")
        print(f"{'seq':<5}{'event_type':<20}{'from':<18}{'to'}")
        print("-" * 62)
        for event in repository.events(result.run_id):
            frm = event.state_from.value if event.state_from else "-"
            print(
                f"{event.sequence:<5}{event.event_type.value:<20}"
                f"{frm:<18}{event.state_to.value}"
            )

        run = repository.get(result.run_id)
        print()
        print("runs 表（当前状态）：")
        print(f"  state   = {run.state.value}")
        print(f"  version = {run.version}")

        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
