import sys
from decimal import Decimal

from pydantic import BaseModel, Field

from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from ai_trader.tools.runtime import (
    ToolCall,
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolRisk,
    ToolRuntime,
)


class QuoteInput(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")


class QuoteOutput(BaseModel):
    symbol: str
    price: Decimal


def test_structured_model_call_is_validated_and_grounded():
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            name="get_quote",
            description="Get a fixture quote",
            input_model=QuoteInput,
            output_model=QuoteOutput,
            handler=lambda data: QuoteOutput(symbol=data.symbol, price=Decimal(100)),
            permission="market:read",
            risk=ToolRisk.LOW,
            effect=ToolEffect.READ,
        )
    )
    provider = FakeModelProvider(
        responses=[
            ModelResponse(
                provider="fake",
                model="fake-agent",
                text="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-quote-1",
                        name="get_quote",
                        arguments={"symbol": "600519"},
                    ),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
                attempts=1,
                elapsed_ms=0,
            ),
            ModelResponse(
                provider="fake",
                model="fake-agent",
                text="600519 fixture price is 100; no live trade was executed.",
                usage=ModelUsage(input_tokens=20, output_tokens=10),
                attempts=1,
                elapsed_ms=0,
            ),
        ]
    )
    gateway = ModelGateway([provider])
    request = ModelRequest(
        model="fake-agent",
        messages=({"role": "user", "content": "quote 600519"},),
        tools=runtime.schemas(
            ToolExecutionContext(permissions=frozenset({"market:read"}))
        ),
    )

    first = gateway.complete(request)
    call = first.tool_calls[0]
    result = runtime.execute(
        ToolCall(call_id=call.call_id, name=call.name, arguments=call.arguments),
        ToolExecutionContext(permissions=frozenset({"market:read"})),
    )
    final = gateway.complete(
        request.model_copy(
            update={
                "messages": request.messages
                + ({"role": "tool", "content": str(result.output)},)
            }
        )
    )

    assert result.ok is True
    assert result.output == {"symbol": "600519", "price": "100"}
    assert "price is 100" in final.text
    for forbidden in ("agent_tools", "model_client", "ths_trader", "requests"):
        assert forbidden not in sys.modules
