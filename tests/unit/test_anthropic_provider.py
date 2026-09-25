from types import SimpleNamespace

from ai_trader.models.anthropic_provider import AnthropicProvider
from ai_trader.models.gateway import ModelRequest


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_translates_cached_structured_request_and_response():
    response = SimpleNamespace(
        model="fixture-model",
        content=[
            SimpleNamespace(type="text", text="checking"),
            SimpleNamespace(
                type="tool_use", id="call-1", name="get_quote",
                input={"symbol": "600519"},
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=80, cache_read_input_tokens=0,
        ),
    )
    messages = FakeMessages(response)
    client = SimpleNamespace(messages=messages)
    provider = AnthropicProvider(client=client)
    request = ModelRequest(
        model="fixture-model",
        messages=({"role": "user", "content": "quote"},),
        tools=(
            {"name": "get_quote", "description": "quote", "input_schema": {"type": "object"}, "risk": "low"},
        ),
    )
    result = provider.complete(request)
    assert messages.kwargs["cache_control"] == {"type": "ephemeral"}
    assert messages.kwargs["tools"][0]["strict"] is True
    assert "risk" not in messages.kwargs["tools"][0]
    assert result.text == "checking"
    assert result.tool_calls[0].arguments == {"symbol": "600519"}
    assert result.usage.cache_creation_input_tokens == 80
