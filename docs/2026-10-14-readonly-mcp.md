# 本地只读MCP工具边界

已新增`ai_trader.tools.readonly_mcp`，使用官方Python SDK **1.30.0**的stdio传输。它适配现有ToolRuntime，复用原工具模型、参数、权限、超时与结构化错误；CLI默认只允许显式`--fixture`，使用注入的合成行情模块，不导入实际行情或交易模块。

```powershell
python -m pip install -e ".[test,mcp]"
python -m ai_trader.tools.readonly_mcp --fixture
# stdio服务器等候MCP客户端，stdout仅用于协议；交互验证：
python -m ai_trader.offline_tests -k readonly_mcp -q
```

服务器权限由构造者绑定，模型参数不能传入user身份、permission或approval。公开工具还须在显式allowlist中、effect为read/compute、risk为low且不需审批。注册为write/high_risk或未授权的工具既不出现在tools/list，也不能通过直接tools/call调用。适配器拒绝额外公开参数，合法`000001`完整保留，SDK协议外观不会扩大原Runtime权限。

`readOnlyHint`等annotations只是说明；真正限制由服务端筛选、输入校验及ToolRuntime执行。返回TextContent与同一份structuredContent，成功/失败均沿用ToolResult。SDK1.30的call_tool包装会把包括McpError的handler异常变成isError工具结果，因此未知/禁止工具沿用`isError=true`和`error_code=unknown_tool`；没有宣称实现了独立JSON-RPC未知工具异常分支。

TDD：模块缺失时3 failed，实现后3 passed；随后尝试不同异常形式导致完整回归1 failed/160 passed，核对安装源码后修正。最终真实stdio客户端实际执行initialize→list_tools→有效行情→非法symbol→禁止execute_trade；前导零、额外审批字段、权限不足与write工具拒绝另有测试。没有远程模型调用；这是传输与权限边界验证，不是LLM工具选择效果测量。

取舍：采用本地stdio和固定可信调用者，省去远程服务与身份协议；本样例没有实现公网多用户认证、远程OAuth、模型自动发现收益或生产配额。现有SDK主分支已为v2 API，样例固定v1.30而非照搬滚动main。生产动态接入必须明确绑定已认证用户、可信工具集合和实际数据来源；不能把任意MCP服务器的annotations当授权。

官方依据：[Python SDK v1.30 README](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/README.md)，[MCP Transports / stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio)，[MCP Tools / Tool、Structured Content、Error Handling、Security Considerations](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。
