"""将旧版行情、持仓和 RAG 能力懒加载到新 Tool Runtime。"""

from collections.abc import Callable
from importlib import import_module
from typing import Any, cast

from pydantic import BaseModel, Field

from ai_trader.tools.runtime import ToolDefinition, ToolEffect, ToolRisk, ToolRuntime


class _NoInput(BaseModel):
    pass


class _StockInput(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")


class _RagInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10)


class _ObjectOutput(BaseModel):
    data: dict[str, object]


class _ListOutput(BaseModel):
    data: list[dict[str, object]]


def create_legacy_market_runtime(
    *, module_loader: Callable[[str], object] = import_module
) -> ToolRuntime:
    runtime = ToolRuntime()

    def quote(data: _StockInput) -> _ObjectOutput:
        module = cast(Any, module_loader("market_data"))
        return _ObjectOutput(data=dict(module.get_realtime_quote(data.symbol) or {}))

    def overview(_data: _NoInput) -> _ObjectOutput:
        module = cast(Any, module_loader("market_data"))
        return _ObjectOutput(data=dict(module.get_market_overview() or {}))

    def indicators(data: _StockInput) -> _ObjectOutput:
        module = cast(Any, module_loader("market_data"))
        history = module.get_stock_history(data.symbol, days=120)
        return _ObjectOutput(data=dict(module.get_technical_indicators(history) or {}))

    def positions(_data: _NoInput) -> _ListOutput:
        module = cast(Any, module_loader("strategy"))
        return _ListOutput(data=[dict(item) for item in module.get_local_positions()])

    def rag_search(data: _RagInput) -> _ListOutput:
        module = cast(Any, module_loader("rag_store"))
        results = module.get_store().search(data.query, top_k=data.top_k)
        return _ListOutput(data=[dict(item) for item in results])

    definitions: tuple[Any, ...] = (
        ToolDefinition(
            name="get_quote", description="当需要获取 A 股实时行情的时候使用",
            input_model=_StockInput, output_model=_ObjectOutput, handler=quote,
            permission="market:read", risk=ToolRisk.LOW, effect=ToolEffect.READ,
        ),
        ToolDefinition(
            name="get_market_overview", description="当需要获取大盘概况的时候使用",
            input_model=_NoInput, output_model=_ObjectOutput, handler=overview,
            permission="market:read", risk=ToolRisk.LOW, effect=ToolEffect.READ,
        ),
        ToolDefinition(
            name="get_technical_indicators", description="当需要计算股票技术指标的时候使用",
            input_model=_StockInput, output_model=_ObjectOutput, handler=indicators,
            permission="market:read", risk=ToolRisk.LOW, effect=ToolEffect.COMPUTE,
        ),
        ToolDefinition(
            name="get_positions", description="当需要读取当前持仓的时候使用",
            input_model=_NoInput, output_model=_ListOutput, handler=positions,
            permission="portfolio:read", risk=ToolRisk.LOW, effect=ToolEffect.READ,
        ),
        ToolDefinition(
            name="rag_search", description="当需要检索已验证的历史经验的时候使用",
            input_model=_RagInput, output_model=_ListOutput, handler=rag_search,
            permission="memory:read", risk=ToolRisk.LOW, effect=ToolEffect.READ,
        ),
    )
    for definition in definitions:
        runtime.register(definition)
    return runtime
