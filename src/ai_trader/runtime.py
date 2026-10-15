"""新生产 Runtime 的显式启用入口；旧版默认保持不变。"""

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.models.anthropic_provider import AnthropicProvider
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.settings import Settings
from ai_trader.tools.legacy_market import create_legacy_market_runtime
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime


class _NoInput(BaseModel):
    pass


class _QuoteOutput(BaseModel):
    price: Decimal


class OnlineSmokeDenied(RuntimeError):
    pass


def select_runtime_mode(value: str) -> str:
    if value not in {"pipeline", "agent", "production"}:
        raise ValueError(f"unsupported runtime mode: {value}")
    return value


def validate_online_smoke(settings: Settings, *, allow_network: bool) -> None:
    if not allow_network:
        raise OnlineSmokeDenied("--allow-network is required")
    if settings.runtime_mode != "production":
        raise OnlineSmokeDenied("production runtime mode is required")
    if not settings.anthropic_api_key:
        raise OnlineSmokeDenied("Anthropic API credential is required")


def run_online_smoke(settings: Settings, *, allow_network: bool) -> int:
    validate_online_smoke(settings, allow_network=allow_network)
    response = ModelGateway([AnthropicProvider.from_settings(settings)]).complete(
        ModelRequest(
            model=settings.anthropic_model,
            messages=({"role": "user", "content": "Reply with exactly: runtime-smoke-ok"},),
            max_tokens=64,
        )
    )
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"text={response.text}")
    print(f"input_tokens={response.usage.input_tokens}")
    print(f"output_tokens={response.usage.output_tokens}")
    print(f"cache_read_tokens={response.usage.cache_read_input_tokens}")
    return 0


def run_dry() -> int:
    tools = ToolRuntime()
    tools.register(
        ToolDefinition(
            name="get_quote",
            description="fixture quote",
            input_model=_NoInput,
            output_model=_QuoteOutput,
            handler=lambda _data: _QuoteOutput(price=Decimal(100)),
            permission="market:read",
        )
    )
    provider = FakeModelProvider(
        [
            ModelResponse(
                provider="fake", model="fake", text="",
                tool_calls=(ModelToolCall(call_id="call-1", name="get_quote", arguments={}),),
                usage=ModelUsage(input_tokens=10, output_tokens=5), attempts=1, elapsed_ms=0,
            ),
            ModelResponse(
                provider="fake", model="fake", text="fixture answer: price 100",
                usage=ModelUsage(input_tokens=15, output_tokens=5), attempts=1, elapsed_ms=0,
            ),
        ]
    )
    with TemporaryDirectory(prefix="ai-trader-runtime-") as temp_dir:
        path = Path(temp_dir) / "runtime.db"
        repository = SqlRunRepository(f"sqlite:///{path.as_posix()}")
        runner = ProductionAgentRunner(
            ModelGateway([provider]), tools,
            PersistentOrchestrator(repository, clock=lambda: datetime.now(UTC)),
            ToolExecutionContext(permissions=frozenset({"market:read"})),
            model="fake",
        )
        result = runner.run("get fixture quote")
        print(f"run_id={result.run_id}")
        print(f"final={result.final_text}")
        print("network=false")
        print("live_execution=false")
        repository.close()
    return 0


def _scoped_memory_reader(owner: str) -> Callable[..., list[dict[str, object]]]:
    # 延迟打开现有store；身份由可信本机入口绑定，模型不能选择其他owner。
    from importlib import import_module

    from ai_trader.memory.lifecycle import ControlledMemory

    memory: ControlledMemory | None = None

    def read(query: str, top_k: int = 3) -> list[dict[str, object]]:
        nonlocal memory
        if memory is None:
            legacy = cast(Any, import_module('rag_store'))
            memory = ControlledMemory(
                legacy.get_store(), user_id=owner, permissions=frozenset({'memory:read'}),
                clock=lambda: datetime.now(UTC),
            )
        return memory.search(query, top_k=top_k)

    return read


def build_production_runner(
    settings: Settings, *, memory_owner: str | None = None,
    memory_reader: Callable[..., list[dict[str, object]]] | None = None,
) -> tuple[ProductionAgentRunner, SqlRunRepository]:
    if settings.runtime_mode != "production":
        raise RuntimeError("AI_TRADER_RUNTIME_MODE=production is required")
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API credential is required")
    if memory_reader is not None and memory_owner is None:
        raise ValueError('memory reader requires an explicitly bound owner')
    if memory_owner is not None and (not memory_owner.strip() or len(memory_owner) > 128):
        raise ValueError('memory owner must be nonempty')
    if memory_owner is not None and memory_reader is None:
        memory_reader = _scoped_memory_reader(memory_owner)
    repository = SqlRunRepository(settings.runtime_database_url)
    runner = ProductionAgentRunner(
        ModelGateway([AnthropicProvider.from_settings(settings)]),
        create_legacy_market_runtime(memory_reader=memory_reader),
        PersistentOrchestrator(repository),
        ToolExecutionContext(
            permissions=frozenset({"market:read", "portfolio:read"})
            | (frozenset({'memory:read'}) if memory_owner is not None else frozenset()),
        ),
        model=settings.anthropic_model,
    )
    return runner, repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Trader production runtime")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke-online", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--goal", default="分析当前市场环境")
    parser.add_argument('--memory-owner', help='由可信本机调用方指定的记忆命名空间，不代表多用户鉴权')
    args = parser.parse_args(argv)
    if args.dry_run:
        return run_dry()
    settings = Settings()
    if args.smoke_online:
        try:
            return run_online_smoke(settings, allow_network=args.allow_network)
        except OnlineSmokeDenied as exc:
            print(f"online_smoke=DENIED reason={exc}")
            return 2
    if settings.runtime_mode != "production":
        print("runtime_mode=legacy (set AI_TRADER_RUNTIME_MODE=production to opt in)")
        return 0
    runner, repository = build_production_runner(settings, memory_owner=args.memory_owner)
    try:
        result = runner.run(args.goal)
        print(result.final_text)
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
