"""标准化模型响应、重试和提供方 fallback 的网关。"""

from enum import Enum
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class GatewayModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelToolCall(GatewayModel):
    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object]


class ModelUsage(GatewayModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelRequest(GatewayModel):
    model: str = Field(min_length=1)
    messages: tuple[dict[str, object], ...] = Field(min_length=1)
    tools: tuple[dict[str, object], ...] = ()
    max_tokens: int = Field(default=2000, gt=0, le=100_000)


class ModelResponse(GatewayModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage
    attempts: int = Field(ge=1)
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def require_content(self) -> "ModelResponse":
        if not self.text.strip() and not self.tool_calls:
            raise ValueError("模型响应必须包含文本或结构化工具调用")
        return self


class ProviderErrorCode(str, Enum):
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        code: ProviderErrorCode = ProviderErrorCode.UNKNOWN,
    ) -> None:
        super().__init__(message[:450])
        self.retryable = retryable
        self.code = code


class ModelGatewayError(RuntimeError):
    """所有模型提供方都无法返回有效结果。"""


class ModelProvider(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> object: ...


class ModelGateway:
    def __init__(
        self,
        providers: list[ModelProvider],
        *,
        max_attempts_per_provider: int = 2,
    ) -> None:
        if not providers:
            raise ValueError("at least one model provider is required")
        if max_attempts_per_provider < 1:
            raise ValueError("max attempts must be positive")
        self._providers = tuple(providers)
        self._max_attempts = max_attempts_per_provider

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        total_attempts = 0
        errors: list[str] = []

        for provider in self._providers:
            for _ in range(self._max_attempts):
                total_attempts += 1
                try:
                    raw_response = provider.complete(request)
                except ProviderError as exc:
                    errors.append(f"{provider.name}:{exc.code.value}:{exc}")
                    if exc.retryable:
                        continue
                    break
                except Exception as exc:
                    errors.append(f"{provider.name}:unknown:{str(exc)[:200]}")
                    break

                try:
                    response = ModelResponse.model_validate(raw_response)
                except ValidationError as exc:
                    errors.append(
                        f"{provider.name}:invalid_response:{str(exc)[:200]}"
                    )
                    break

                return response.model_copy(
                    update={
                        "provider": provider.name,
                        "attempts": total_attempts,
                        "elapsed_ms": max(0.0, (perf_counter() - started) * 1000),
                    }
                )

        message = "; ".join(errors) or "no provider returned a response"
        raise ModelGatewayError(message[:500])
