# 2026-10-13：可启动的完整 Paper 服务

新增 `ai_trader.paper_service`，将已有 `ProductionAgentRunner → TradingWorkflow → ExecutionCoordinator → PaperBrokerAdapter` 接入同一 HTTP 服务。第一次分析先等待人工审批，批准后得到一个 Paper 订单和 run 终态；拒绝、重复批准与重启对账走已有业务实现。

这是一条可复现的本地业务链路。默认模型、行情和账户初始值都是离线 fixture，不代表真实模型质量、真实成交或投资收益。

## 1. 启动与完整调用

前提：使用 Python 3.12 或 3.13，安装 `pyproject.toml` 中的依赖，包括 Uvicorn。项目安装方式见仓库 README。`--state-directory` 必填且必须为绝对路径，首次演示使用新目录，恢复时才使用同一目录。

在项目目录启动：

```powershell
python -m ai_trader.paper_service --state-directory "$env:TEMP\ai-trader-paper-service-demo-20261013-1" --port 8000
```

默认只监听 `127.0.0.1`。`--host` 只接受 loopback IP，`--port` 范围为 1–65535；不提供线上模型、实盘、reload 或多 worker 参数。可打开 `http://127.0.0.1:8000/docs` 查看已有 API。

在另一终端调用：

```powershell
$paperBaseUrl = 'http://127.0.0.1:8000'
Invoke-RestMethod "$paperBaseUrl/health"
$paperCreated = Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs" -ContentType 'application/json' -Body (@{goal='根据离线行情提出 Paper 建议'} | ConvertTo-Json)
$paperRunId = $paperCreated.run_id
Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs/$paperRunId/analyze"
$paperReviewer = @{'X-Reviewer-Id'='local-demo-reviewer'}
Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs/$paperRunId/approve" -Headers $paperReviewer
Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs/$paperRunId/approve" -Headers $paperReviewer
Invoke-RestMethod "$paperBaseUrl/runs/$paperRunId/events"
```

预期：创建返回 `created`；分析返回 `waiting_approval`，提案引用真实执行过的 `get_quote`；批准返回 `succeeded`、订单 `filled`；第二次批准返回相同订单。没有 `X-Reviewer-Id` 的批准返回 401。`analyze`、`approve`、`reconcile` 不接收请求正文，发送账户、行情或风控条件返回 422。

拒绝路径需创建另一个独立状态目录完成同样的创建和分析，再调用：

```powershell
Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs/$paperRunId/reject" -Headers $paperReviewer -ContentType 'application/json' -Body (@{reason='人工决定不执行'} | ConvertTo-Json)
```

拒绝后状态为 `cancelled`，批准返回 409，不产生订单。上面的 `$paperRunId` 必须来自尚待审批的新 run。

## 2. 服务端数据与运行边界

模型只可调用 `get_quote`。默认脚本固定建议买入 `600519`、100 股、限价 100；止损 95、止盈 115。行情只支持该标的，价格固定为 100，时间为服务端 fixture 时钟。服务端接受的 Paper 提案也限于该标的和限价。初始可用现金 50,000，总权益 100,000；其中另外 50,000 为固定的其他资产。

每次分析和执行前，服务从 Paper ledger 的已成交订单计算现金和持仓，再交给原有确定性风控。调用者不能通过 HTTP 请求提供这些数据。已有 100 股后再买入 100 股会超过 15% 单标的仓位上限，返回 `POSITION_LIMIT_EXCEEDED`。未持有股票时卖出会被拒绝。这里没有完整的资金预占、结算日、手续费、部分成交或多账户模型。

单个状态目录只允许一个遵循本入口锁协议的 Paper 服务进程；锁在 ASGI lifespan 中取得和释放，工厂函数本身不创建状态文件。同一服务的写请求串行执行，使不同 run 的执行前风控和 Paper 成交不会交错。不要同时用旧 demo 或其他程序写该目录：它们不遵循本入口的锁协议。

本入口属于**可信单用户本地接口**：同时限制监听地址和连接来源为 loopback，关闭 Uvicorn 的 forwarded-header 信任。`X-Reviewer-Id` 只是演示审批身份，本地调用者可以自行填写，不能称为真实鉴权。不要通过反向代理、端口转发或公网服务器暴露这个服务。此切片没有交付多用户身份、RBAC 或生产交易安全体系。

默认不读取 `Settings`、`.env` 或全局秘密配置，不访问行情、RAG 或模型网络。Python 工厂允许调用者同时提供 `provider` 和非空 `model`；若只提供一个则拒绝启动。调用者自行构造提供方并决定其联网、凭据与期限，服务仍只暴露 fixture 行情工具并使用 Paper Broker。本轮仅用离线 `FakeModelProvider` 验证该注入接口，没有运行真实模型。fixture 的 usage 数值只供流程测试，不能写成实际 token 成本或质量评估结果。

## 3. 重启与结果未知

指定状态目录包含 `workflow.db`、`paper-ledger.json` 和 `.paper-service.lock`。ledger 在 Paper Broker 返回订单前独立持久化，因此可验证“Broker 已落盘、调用者未收到响应”的恢复路径。

- `waiting_approval`：用同一目录重启后仍可批准；提案已经过期则失败，不执行。
- `executing` / `verifying`：结果未知时先对账。再次批准或取消不会直接重试委托。
- 对账找到 ledger 中的订单：保存订单并推进到 `succeeded`，随后批准或再次对账仍返回同一订单。
- 对账没有找到订单：返回 `order: null`，保留原执行状态，不能宣称“未成交”或自动重发。

同一目录重启后调用：

```powershell
Invoke-RestMethod -Method Post -Uri "$paperBaseUrl/runs/$paperRunId/reconcile" -Headers $paperReviewer
```

测试通过在真实 Paper submit 已写 ledger 后注入响应丢失，关闭服务、重建仓库与 Broker，再经 HTTP 对账。没有在正式 CLI 中增加故障注入开关。

## 4. 取舍与面试可讲范围

已有只读 runtime 提供模型与工具 loop，但没有装配完整审批交易服务；新增工厂复用原有业务实现，使服务启动、HTTP 路由、服务器账户源和资源关闭属于同一入口。相较继续在测试中手动拼装对象，它减少“测试闭环存在但交付入口缺失”的落差。

选择 loopback 单写者和串行写请求，换取当前 SQLite + JSON ledger 的清晰一致性边界；代价是长分析会阻塞其他写请求，不适合高并发或多实例。独立 ledger 能复现响应丢失后对账，但不等同于真实券商状态或数据库事务覆盖外部副作用。下一步若扩展并发或真实行情，需要先设计账户资金预占、异步任务、模型隔离和可信身份，不能仅修改监听地址。

可以描述为“完成离线 Agent 到类型化提案、服务器风控、人工审批和 Paper 订单的服务闭环，并验证重复批准和重启对账”。尚不能描述为“线上交易系统已生产可用”或“真实模型已达到某项效果”。

## 5. 本轮验证证据

实际执行环境：`D:\python.exe --version` 返回 Python 3.13.5，符合当前项目的 `>=3.12,<3.14`。本节只记录该环境的验证，独立环境安装由主任务另行核验。

```powershell
D:\python.exe -m pytest tests/integration/test_paper_service.py -q
# 13 passed
D:\python.exe -m ruff check src/ai_trader/paper_service.py tests/integration/test_paper_service.py
# All checks passed!
D:\python.exe -m mypy src/ai_trader/paper_service.py --follow-imports=silent
# Success: no issues found in 1 source file
D:\python.exe -m py_compile src/ai_trader/paper_service.py tests/integration/test_paper_service.py
# exit 0
```

TDD 观察到的失败：入口不存在导致导入失败；缺风险正文拒绝、账本持仓、loopback 限制、目录单写者、配置校验时为 `5 failed, 2 passed`；缺 CLI 时为 `2 failed, 9 passed`。实现对应行为后达到上述结果。

另用隐藏子进程实际启动 CLI，经真实 loopback HTTP 调用：health 200、create 201、analyze 200 / `waiting_approval`、approve 200 / `succeeded`、重复 approve 200 / 同订单，ledger 只有 1 个订单。测试状态使用临时新目录，未调用在线提供方或实盘工具。
