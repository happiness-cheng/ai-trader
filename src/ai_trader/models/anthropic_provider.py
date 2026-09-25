"""使用官方 Anthropic Python SDK 的 Messages Provider。"""

from typing import cast

import anthropic
from anthropic.types import MessageParam, ToolParam

from ai_trader.models.gateway import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ProviderError,
    ProviderErrorCode,
)
from ai_trader.settings import Settings


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, client: anthropic.Anthropic) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnthropicProvider":
        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            max_retries=0,
        )
        return cls(client=client)

    def complete(self, request: ModelRequest) -> ModelResponse:
        tools = [
            {
                "name": schema["name"],
                "description": schema["description"],
                "input_schema": schema["input_schema"],
                "strict": True,
            }
            for schema in sorted(request.tools, key=lambda item: str(item["name"]))
        ]
        try:
            response = self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                messages=cast(list[MessageParam], list(request.messages)),
                tools=cast(list[ToolParam], tools),
                cache_control={"type": "ephemeral"},
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "authentication failed",
                retryable=False,
                code=ProviderErrorCode.AUTHORIZATION,
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError(
                "permission denied",
                retryable=False,
                code=ProviderErrorCode.AUTHORIZATION,
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                "rate limited", retryable=True, code=ProviderErrorCode.RATE_LIMIT
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError(
                "provider timeout", retryable=True, code=ProviderErrorCode.TIMEOUT
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                "provider connection failed",
                retryable=True,
                code=ProviderErrorCode.SERVER,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"provider status {exc.status_code}",
                retryable=exc.status_code >= 500,
                code=(
                    ProviderErrorCode.SERVER
                    if exc.status_code >= 500
                    else ProviderErrorCode.INVALID_RESPONSE
                ),
            ) from exc

        text_parts: list[str] = []
        tool_calls: list[ModelToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ModelToolCall(
                        call_id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )
        usage = response.usage
        return ModelResponse(
            provider=self.name,
            model=response.model,
            text="\n".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=ModelUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=getattr(
                    usage, "cache_creation_input_tokens", 0
                )
                or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0)
                or 0,
            ),
            attempts=1,
            elapsed_ms=0,
        )
