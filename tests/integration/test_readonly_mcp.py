"""真实 stdio MCP 与 ToolRuntime 之间的只读权限/参数边界。"""

import asyncio
import sys

import pytest
from pydantic import BaseModel, ConfigDict, Field

pytest.importorskip('mcp')

from ai_trader.tools.runtime import (
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolRuntime,
)


def _runtime(effect=ToolEffect.READ):
    class Input(BaseModel):
        model_config = ConfigDict(extra='forbid')
        symbol: str = Field(pattern=r'^\d{6}$')

    class Output(BaseModel):
        symbol: str

    calls = []
    runtime = ToolRuntime()

    def handler(data):
        calls.append(data.symbol)
        return Output(symbol=data.symbol)

    runtime.register(ToolDefinition(
        name='get_quote', description='只读测试行情', input_model=Input,
        output_model=Output, handler=handler, permission='market:read', effect=effect,
    ))
    return runtime, calls


def test_adapter_preserves_parameters_and_runtime_validation():
    from ai_trader.tools.readonly_mcp import ReadOnlyMCPAdapter

    runtime, calls = _runtime()
    adapter = ReadOnlyMCPAdapter(
        runtime, ToolExecutionContext(permissions=frozenset({'market:read'})),
        allowlist=frozenset({'get_quote'}),
    )
    result = adapter.call('get_quote', {'symbol': '000001'})
    assert result.ok and result.output == {'symbol': '000001'}
    assert calls == ['000001']
    bad = adapter.call('get_quote', {'symbol': '000001', 'approved_call_ids': ['x']})
    assert not bad.ok and bad.error_code.value == 'validation_error'
    assert calls == ['000001']


def test_permission_is_server_owned_and_forbidden_tools_never_run():
    from ai_trader.tools.readonly_mcp import ReadOnlyMCPAdapter

    runtime, calls = _runtime()
    adapter = ReadOnlyMCPAdapter(
        runtime, ToolExecutionContext(permissions=frozenset()),
        allowlist=frozenset({'get_quote'}),
    )
    assert adapter.schemas() == ()
    assert not adapter.call('get_quote', {'symbol': '000001'}).ok
    assert calls == []
    runtime, calls = _runtime(ToolEffect.WRITE)
    adapter = ReadOnlyMCPAdapter(
        runtime, ToolExecutionContext(permissions=frozenset({'market:read'})),
        allowlist=frozenset({'get_quote'}),
    )
    assert adapter.schemas() == ()
    assert not adapter.call('get_quote', {'symbol': '000001'}).ok
    assert calls == []


def test_real_stdio_initialize_list_call_and_reject_execution():
    pytest.importorskip('mcp')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=['-m', 'ai_trader.tools.readonly_mcp', '--fixture'],
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ['get_quote']
            assert listed.tools[0].annotations.readOnlyHint is True
            good = await session.call_tool('get_quote', {'symbol': '000001'})
            assert not good.isError
            assert good.structuredContent['output']['data']['symbol'] == '000001'
            assert good.structuredContent['output']['data']['fixture'] is True
            bad = await session.call_tool('get_quote', {'symbol': '1'})
            assert bad.isError
            forbidden = await session.call_tool('execute_trade', {'symbol': '000001'})
            assert forbidden.isError
            assert forbidden.structuredContent['error_code'] == 'unknown_tool'

    asyncio.run(scenario())
