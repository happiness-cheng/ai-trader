"""具备 Schema、权限、审批、超时和结构化错误的工具运行时。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from enum import Enum
from threading import BoundedSemaphore
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolEffect(str, Enum):
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    HIGH_RISK = "high_risk"


class ToolErrorCode(str, Enum):
    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"
    TIMEOUT = "timeout"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    HANDLER_ERROR = "handler_error"
    OUTPUT_VALIDATION_ERROR = "output_validation_error"


class RuntimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolExecutionContext(RuntimeModel):
    permissions: frozenset[str]
    approved_call_ids: frozenset[str] = frozenset()


class ToolCall(RuntimeModel):
    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object]


class ToolResult(RuntimeModel):
    call_id: str
    tool_name: str
    ok: bool
    elapsed_ms: float = Field(ge=0)
    output: dict[str, object] | None = None
    error_code: ToolErrorCode | None = None
    error_message: str = ""


@dataclass(frozen=True)
class ToolDefinition[InputT: BaseModel, OutputT: BaseModel]:
    name: str
    description: str
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: Callable[[InputT], OutputT]
    permission: str
    risk: ToolRisk = ToolRisk.LOW
    effect: ToolEffect = ToolEffect.READ
    timeout_seconds: float = 10.0
    requires_approval: bool = False


# 返回超时不表示handler结束；不同runtime实例共用进程容量，避免重建绕过。
MAX_INFLIGHT_TOOLS = 4
_slots = BoundedSemaphore(MAX_INFLIGHT_TOOLS)


class ToolRuntime:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition[BaseModel, BaseModel]] = {}

    def register[InputT: BaseModel, OutputT: BaseModel](
        self, definition: ToolDefinition[InputT, OutputT]
    ) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"duplicate tool: {definition.name}")
        if definition.timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        self._definitions[definition.name] = definition  # type: ignore[assignment]

    def schemas(
        self, context: ToolExecutionContext
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_model.model_json_schema(),
                "risk": definition.risk.value,
                "effect": definition.effect.value,
                "requires_approval": definition.requires_approval,
            }
            for definition in self._definitions.values()
            if definition.permission in context.permissions
        )

    def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        started = perf_counter()
        definition = self._definitions.get(call.name)
        if definition is None:
            return self._error(call, started, ToolErrorCode.UNKNOWN_TOOL, "unknown tool")
        if definition.permission not in context.permissions:
            return self._error(
                call, started, ToolErrorCode.PERMISSION_DENIED, "permission denied"
            )
        if definition.requires_approval and call.call_id not in context.approved_call_ids:
            return self._error(
                call,
                started,
                ToolErrorCode.APPROVAL_REQUIRED,
                "call-specific approval is required",
            )

        try:
            parsed_input = definition.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._error(
                call, started, ToolErrorCode.VALIDATION_ERROR, str(exc)
            )

        if not _slots.acquire(blocking=False):
            return self._error(
                call, started, ToolErrorCode.CAPACITY_UNAVAILABLE, "tool capacity unavailable"
            )
        executor = None
        try:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-tool")
            future = executor.submit(definition.handler, parsed_input)
        except Exception:  # noqa: BLE001 - 提交失败必须归还槽，不能遗留不可恢复容量。
            _slots.release()
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            return self._error(call, started, ToolErrorCode.HANDLER_ERROR, "tool execution unavailable")
        # 只有真正完成/未开始即取消才归还；请求返回TIMEOUT时仍占容量。
        future.add_done_callback(lambda _future: _slots.release())
        try:
            raw_output = future.result(timeout=definition.timeout_seconds)
        except TimeoutError:
            future.cancel()
            return self._error(call, started, ToolErrorCode.TIMEOUT, "tool timed out")
        except Exception as exc:  # noqa: BLE001 - handler 可抛任意异常，必须转成结构化结果
            return self._error(call, started, ToolErrorCode.HANDLER_ERROR, str(exc))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        try:
            output = definition.output_model.model_validate(raw_output)
        except ValidationError as exc:
            return self._error(
                call, started, ToolErrorCode.OUTPUT_VALIDATION_ERROR, str(exc)
            )

        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            ok=True,
            elapsed_ms=self._elapsed(started),
            output=output.model_dump(mode="json"),
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return max(0.0, (perf_counter() - started) * 1000)

    def _error(
        self,
        call: ToolCall,
        started: float,
        code: ToolErrorCode,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            ok=False,
            elapsed_ms=self._elapsed(started),
            error_code=code,
            error_message=message[:500],
        )
