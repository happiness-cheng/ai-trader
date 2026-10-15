# Paper / Agent / MCP复现范围

本次使用独立Windows / CPython 3.12.15环境。先复现Paper与只读fixture MCP；本轮没有验证实盘收益、实盘执行或公开多租户服务。完整源码回归203 passed，mypy对49个src模块通过。

## 源码方式

在项目根目录创建隔离环境：

```powershell
uv venv --python 3.12 .venv-verified
uv pip install --python .venv-verified/Scripts/python.exe -e '.[test,mcp,memory]' -c constraints.verified-win-py312.txt
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.offline_tests --verify-isolation
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.offline_tests -q --tb=short
.venv-verified/Scripts/python.exe -m mypy src/ai_trader
```

离线入口已经定位整个tests目录，命令无需再附加相对`tests`参数，因为实际cwd被移到临时目录。原.env只读为空、提供方凭据清空、使用临时业务状态，进程内阻断外网。子进程不自动继承socket monkeypatch；MCP与演示子进程必须显式fixture/dry-run。

版本约束固定112个已安装依赖，不包含本仓库的editable绝对路径；不是跨平台、带文件hash的依赖lock。真实Chroma记忆测试使用临时目录和测试embedding函数。默认ONNX embedding首次加载可能下载，实际复现记忆时应预先准备模型；本轮没有触发该下载。

## CLI

```powershell
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.paper_demo --output C:/temp/trader-paper-demo
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.paper_demo --response-loss --output C:/temp/trader-paper-loss
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.paper_service --state-directory C:/temp/trader-paper-state --port 8000
.venv-verified/Scripts/python.exe -X utf8 -m ai_trader.tools.readonly_mcp --fixture
```

输出/状态目录应选自己的新目录，避免混用真实数据。Paper服务CLI只允许字面loopback地址、单worker、禁用proxy headers；同状态目录单进程锁。HTTP流程、审批header及重启对账见[Paper服务](2026-10-13-paper-service.md)。X-Reviewer是审计身份标签，不是身份认证；服务是可信本机演示边界。

MCP通过官方SDK1.30 stdio完成initialize/list/call验证，只暴露get_quote。fixture报价是合成数据。见[只读MCP](2026-10-14-readonly-mcp.md)。

## 已构建wheel

最终wheel在本轮实施目录`verification/wheels-current/ai_trader_runtime-0.1.0-py3-none-any.whl`，SHA256为`d402883f576cdf039ee4b39ff29090972a1d36a865fe68dad7b84c0e3d8b40c7`。独立安装目录以`python -I`从site-packages运行，pip check、Paper正常成交、响应丢失恢复、runtime dry-run、真实MCP stdio均退出0。两条Paper演示都是orders=1与duplicate_same_order=true；丢响应演示recovered=true。

包含54成员，不含.env、数据库、日志、模型权重或原业务数据。旧`rag_store.py / market_data.py / strategy.py`位于项目根，尚不在wheel中；依赖它们的旧行情/记忆集成需源码editable运行。不能把Paper/MCP冷安装结果说成整个旧项目可任意部署。

旧wheel因runtime.py后续身份长度校验修正而落后一处，最终已重新构建、安装并验收；旧产物与旧证据保留，使用wheels-current和wheel-current-*记录。

## 当前取舍

上下文保护在provider前，系统规则与服务端风险快照放system；默认字符预算，未接匹配提供方tokenizer时不声称硬token预算。结构预览保留审计hash与原风险字段，不冒充模型生成摘要。[上下文预算](2026-10-13-context-budget.md)

记忆只接收确认业务事件、强制user/来源/确认时间/有效期；历史建议不能覆盖当前风控。Paper记忆sink显式注入，默认关闭，记忆失败审计并允许reconcile重放，不改已确认成交状态。无事务outbox/CAS保证；本机memory_owner不是远程认证。[受控记忆](2026-10-13-controlled-memory.md)
