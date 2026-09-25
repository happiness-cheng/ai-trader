from types import SimpleNamespace

from ai_trader.tools.legacy_market import create_legacy_market_runtime
from ai_trader.tools.runtime import ToolCall, ToolExecutionContext, ToolErrorCode


def test_legacy_modules_are_lazy_and_only_read_tools_are_exposed():
    loaded = []

    def loader(name):
        loaded.append(name)
        if name == "market_data":
            return SimpleNamespace(get_realtime_quote=lambda code: {"code": code, "price": 100})
        raise AssertionError(name)

    runtime = create_legacy_market_runtime(module_loader=loader)
    assert loaded == []
    context = ToolExecutionContext(permissions=frozenset({"market:read"}))
    names = {schema["name"] for schema in runtime.schemas(context)}
    assert {"get_quote", "get_market_overview", "get_technical_indicators"} <= names
    assert not {"buy", "sell", "send_notification"} & names
    result = runtime.execute(
        ToolCall(call_id="call-1", name="get_quote", arguments={"symbol": "600519"}),
        context,
    )
    assert result.output["data"]["price"] == 100
    assert loaded == ["market_data"]


def test_invalid_symbol_is_rejected_before_legacy_import():
    loaded = []
    runtime = create_legacy_market_runtime(module_loader=lambda name: loaded.append(name))
    result = runtime.execute(
        ToolCall(call_id="call-1", name="get_quote", arguments={"symbol": "ABC"}),
        ToolExecutionContext(permissions=frozenset({"market:read"})),
    )
    assert result.error_code is ToolErrorCode.VALIDATION_ERROR
    assert loaded == []
