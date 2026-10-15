"""本地 stdio 只读 MCP：固定服务器权限，复用 ToolRuntime 的执行边界。"""

import argparse
import asyncio
import json
from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server

from ai_trader.tools.runtime import (
    ToolCall,
    ToolErrorCode,
    ToolExecutionContext,
    ToolResult,
    ToolRuntime,
)


class ReadOnlyMCPAdapter:
    def __init__(
        self, runtime: ToolRuntime, context: ToolExecutionContext, *, allowlist: frozenset[str],
    ) -> None:
        self._runtime = runtime
        self._context = context
        self._schemas: dict[str, dict[str, object]] = {}
        for schema in runtime.schemas(context):
            name = cast(str, schema['name'])
            if (name in allowlist and schema['effect'] in {'read', 'compute'}
                    and schema['risk'] == 'low' and not schema['requires_approval']):
                copied = deepcopy(schema)
                # 适配器只接受公开参数，不能把额外权限或审批字段静默忽略。
                input_schema = cast(dict[str, object], copied['input_schema'])
                input_schema['additionalProperties'] = False
                self._schemas[name] = copied

    def schemas(self) -> tuple[dict[str, object], ...]:
        return tuple(deepcopy(schema) for schema in self._schemas.values())

    def call(self, name: str, arguments: dict[str, object]) -> ToolResult:
        call_id = uuid4().hex

        def error(code: ToolErrorCode, message: str) -> ToolResult:
            return ToolResult(
                call_id=call_id, tool_name=name, ok=False, elapsed_ms=0,
                error_code=code, error_message=message,
            )

        if name not in self._schemas:
            return error(ToolErrorCode.UNKNOWN_TOOL, 'tool is not exposed by this read-only server')
        if len(json.dumps(arguments, ensure_ascii=False)) > 16000:
            return error(ToolErrorCode.VALIDATION_ERROR, 'arguments exceed 16000 characters')
        schema = cast(dict[str, Any], self._schemas[name]['input_schema'])
        if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
            return error(ToolErrorCode.VALIDATION_ERROR, 'arguments do not match the published schema')
        return self._runtime.execute(
            ToolCall(call_id=call_id, name=name, arguments=arguments), self._context,
        )


def create_server(adapter: ReadOnlyMCPAdapter, *, fixture: bool = False) -> Server[dict[str, Any], Any]:
    # 可选依赖仅由 MCP 入口使用；应用的普通 Paper 入口不依赖此协议。
    import anyio
    from mcp.types import (
        CallToolResult,
        TextContent,
        Tool,
        ToolAnnotations,
    )

    server: Server[dict[str, Any], Any] = Server('ai_trader_mcp')

    async def list_tools() -> list[Tool]:
        return [Tool(
            name=cast(str, schema['name']), description=cast(str, schema['description']),
            inputSchema=cast(dict[str, Any], schema['input_schema']),
            outputSchema=ToolResult.model_json_schema(),
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                openWorldHint=not fixture,
            ),
        ) for schema in adapter.schemas()]

    async def call_tool(name: str, arguments: dict[str, object]) -> CallToolResult:
        # 校验由 adapter + 原 Runtime 执行，保留统一的结构化错误码。
        result = await anyio.to_thread.run_sync(adapter.call, name, arguments)
        structured = result.model_dump(mode='json')
        return CallToolResult(
            content=[TextContent(type='text', text=json.dumps(structured, ensure_ascii=False))],
            structuredContent=structured, isError=not result.ok,
        )

    # 官方 1.30 注册方法没有返回类型，仅放宽这个 SDK 注册调用。
    server.list_tools()(list_tools)  # type: ignore[no-untyped-call]
    server.call_tool(validate_input=False)(call_tool)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', action='store_true', required=True,
                        help='仅本地合成行情，不读取凭据或启动真实行情接口')
    parser.parse_args()

    from types import SimpleNamespace

    from ai_trader.tools.legacy_market import create_legacy_market_runtime

    def module_loader(name: str) -> object:
        if name != 'market_data':
            raise ValueError('fixture has no access to this module')
        return SimpleNamespace(get_realtime_quote=lambda symbol: {
            'symbol': symbol, 'price': 100., 'fixture': True,
        })

    runtime = create_legacy_market_runtime(module_loader=module_loader)
    adapter = ReadOnlyMCPAdapter(
        runtime, ToolExecutionContext(permissions=frozenset({'market:read'})),
        allowlist=frozenset({'get_quote'}),
    )
    server = create_server(adapter, fixture=True)

    async def serve() -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(serve())


if __name__ == '__main__':
    main()
