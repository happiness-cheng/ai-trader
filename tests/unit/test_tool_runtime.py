from time import sleep

from pydantic import BaseModel, Field

from ai_trader.tools.runtime import (
    ToolCall,
    ToolDefinition,
    ToolEffect,
    ToolErrorCode,
    ToolExecutionContext,
    ToolRisk,
    ToolRuntime,
)


class EchoInput(BaseModel):
    value: int = Field(gt=0)


class EchoOutput(BaseModel):
    doubled: int


def make_runtime(**overrides):
    values = {
        "name": "double",
        "description": "Double a positive integer",
        "input_model": EchoInput,
        "output_model": EchoOutput,
        "handler": lambda data: EchoOutput(doubled=data.value * 2),
        "permission": "market:read",
        "risk": ToolRisk.LOW,
        "effect": ToolEffect.READ,
        "timeout_seconds": 0.1,
        "requires_approval": False,
    }
    values.update(overrides)
    runtime = ToolRuntime()
    runtime.register(ToolDefinition(**values))
    return runtime


def context(*permissions, approvals=()):
    return ToolExecutionContext(
        permissions=frozenset(permissions),
        approved_call_ids=frozenset(approvals),
    )


def test_valid_input_executes_and_serializes_output():
    result = make_runtime().execute(
        ToolCall(call_id="call-1", name="double", arguments={"value": 4}),
        context("market:read"),
    )
    assert result.ok is True
    assert result.output == {"doubled": 8}
    assert result.elapsed_ms >= 0


def test_unknown_tool_returns_stable_error():
    result = ToolRuntime().execute(
        ToolCall(call_id="call-1", name="missing", arguments={}), context()
    )
    assert result.error_code is ToolErrorCode.UNKNOWN_TOOL


def test_invalid_arguments_do_not_reach_handler():
    result = make_runtime().execute(
        ToolCall(call_id="call-1", name="double", arguments={"value": 0}),
        context("market:read"),
    )
    assert result.error_code is ToolErrorCode.VALIDATION_ERROR


def test_missing_permission_is_rejected():
    result = make_runtime().execute(
        ToolCall(call_id="call-1", name="double", arguments={"value": 2}), context()
    )
    assert result.error_code is ToolErrorCode.PERMISSION_DENIED


def test_write_tool_requires_call_specific_approval():
    runtime = make_runtime(
        effect=ToolEffect.WRITE,
        risk=ToolRisk.HIGH,
        requires_approval=True,
    )
    call = ToolCall(call_id="call-1", name="double", arguments={"value": 2})
    denied = runtime.execute(call, context("market:read"))
    allowed = runtime.execute(call, context("market:read", approvals=("call-1",)))
    assert denied.error_code is ToolErrorCode.APPROVAL_REQUIRED
    assert allowed.ok is True


def test_handler_exception_is_bounded():
    def explode(_data):
        raise RuntimeError("x" * 1000)

    result = make_runtime(handler=explode).execute(
        ToolCall(call_id="call-1", name="double", arguments={"value": 2}),
        context("market:read"),
    )
    assert result.error_code is ToolErrorCode.HANDLER_ERROR
    assert len(result.error_message) <= 500


def test_timeout_returns_structured_error():
    def slow(data):
        sleep(0.05)
        return EchoOutput(doubled=data.value * 2)

    result = make_runtime(handler=slow, timeout_seconds=0.001).execute(
        ToolCall(call_id="call-1", name="double", arguments={"value": 2}),
        context("market:read"),
    )
    assert result.error_code is ToolErrorCode.TIMEOUT


def test_schemas_only_expose_permitted_tools():
    runtime = make_runtime()
    assert runtime.schemas(context()) == ()
    schemas = runtime.schemas(context("market:read"))
    assert schemas[0]["name"] == "double"
    assert schemas[0]["input_schema"]["properties"]["value"]["exclusiveMinimum"] == 0
