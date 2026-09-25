from collections import deque

import pytest

from ai_trader.models.gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ProviderError,
)


class QueueProvider:
    def __init__(self, name, items):
        self.name = name
        self.items = deque(items)
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        item = self.items.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def request():
    return ModelRequest(
        model="test-model",
        messages=({"role": "user", "content": "quote 600519"},),
        max_tokens=200,
    )


def response(**overrides):
    values = {
        "provider": "raw",
        "model": "test-model",
        "text": "done",
        "tool_calls": (),
        "usage": ModelUsage(input_tokens=10, output_tokens=5),
        "attempts": 1,
        "elapsed_ms": 0,
    }
    values.update(overrides)
    return ModelResponse(**values)


def test_normalizes_text_usage_and_provider():
    provider = QueueProvider("primary", [response()])
    result = ModelGateway([provider]).complete(request())
    assert result.text == "done"
    assert result.provider == "primary"
    assert result.usage.total_tokens == 15
    assert result.attempts == 1


def test_preserves_structured_tool_calls():
    tool_call = ModelToolCall(
        call_id="call-1", name="get_quote", arguments={"symbol": "600519"}
    )
    provider = QueueProvider("primary", [response(text="", tool_calls=(tool_call,))])
    result = ModelGateway([provider]).complete(request())
    assert result.tool_calls == (tool_call,)


def test_retryable_primary_failure_is_retried():
    provider = QueueProvider(
        "primary",
        [ProviderError("temporary", retryable=True), response()],
    )
    result = ModelGateway([provider], max_attempts_per_provider=2).complete(request())
    assert result.attempts == 2
    assert provider.calls == 2


def test_fallback_provider_is_used_after_primary_exhausted():
    primary = QueueProvider("primary", [ProviderError("down", retryable=True)])
    fallback = QueueProvider("fallback", [response()])
    result = ModelGateway([primary, fallback], max_attempts_per_provider=1).complete(
        request()
    )
    assert result.provider == "fallback"
    assert result.attempts == 2


def test_nonretryable_error_is_not_retried_on_same_provider():
    primary = QueueProvider("primary", [ProviderError("unauthorized", retryable=False)])
    fallback = QueueProvider("fallback", [response()])
    result = ModelGateway([primary, fallback], max_attempts_per_provider=3).complete(
        request()
    )
    assert primary.calls == 1
    assert result.provider == "fallback"


def test_invalid_provider_response_fails_without_retry():
    provider = QueueProvider("primary", [{"model": "missing-required-fields"}])
    with pytest.raises(ModelGatewayError, match="invalid_response"):
        ModelGateway([provider], max_attempts_per_provider=3).complete(request())
    assert provider.calls == 1


def test_final_error_message_is_bounded():
    provider = QueueProvider("primary", [ProviderError("x" * 1000, retryable=False)])
    with pytest.raises(ModelGatewayError) as exc_info:
        ModelGateway([provider]).complete(request())
    assert len(str(exc_info.value)) <= 500
