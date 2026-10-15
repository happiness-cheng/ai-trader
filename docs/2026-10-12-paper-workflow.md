# Paper 工作流与运行时评测

本次交付补齐实际只读 Agent → 类型化提案 → 服务端风控 → 等待审批 → Paper 执行 → 验证终态。固定模型和行情只用于复现工程机制；没有真实模型准确率、真实成交或生产部署结论。

## 最短演示

在仓库根目录、已安装 `.[test]` 的 Python 3.13 环境中：

```powershell
py -3.13 -m ai_trader.paper_demo
py -3.13 -m ai_trader.paper_demo --response-loss
py -3.13 -m ai_trader.evals.runtime_benchmark
```

普通演示与响应丢失演示都应输出 `state=succeeded`、`orders=1`、`duplicate_same_order=true`。输出目录含 SQLite、Paper ledger、trace.json，默认保留在独立临时目录；`--output` 必须选择尚不存在的目录。恢复演示在 Broker 已持久化订单后模拟超时，重新创建 Broker 和两个 repository，只查询对账，不再次提交。

旧 `ai_trader.evals.cli` / `demo --evals` 的 32 场景仍为 ExpectedTraceExecutor 契约基线。新 `runtime_benchmark` 的 10 场景通过实际 ProductionAgentRunner、ToolRuntime、持久化状态机，报告含真实观察轨迹、失败原因、数据 hash。固定 provider 的 tokens 是人工设置的，不能作为真实 LLM 性能或商业任务成功率。新增 provider_error 与 wall_clock_budget 记录实际 gateway / runner 的失败；wall_clock_budget 使用确定性虚拟时钟，因此其延迟为合成值，整体延迟指标不可作为性能成绩。测试会故意修改 golden，证明评分能变红。

## 接入控制面

`TradingWorkflow` 依赖 runner、orchestrator、两个 repository、Paper coordinator 和服务端 `context_source`。把实例作为 `ApiDependencies(..., workflow=workflow)` 传入 `create_app`。这项装配是显式可选的，既有 runtime 启动入口没有自动切换为交易工作流。

接口顺序：`POST /runs` 创建 → `POST /runs/{id}/analyze` 分析 → `GET /runs/{id}/proposal` 查看 → `POST /runs/{id}/approve` 或 `/reject`。执行结果不明时调用 `/reconcile`。审批 / 拒绝 / 对账需要 `X-Reviewer-Id`；它只是在本地受信环境标记操作者，不是生产身份认证。没有对公网开放本控制面。

启用 workflow 后旧 `/proposals` 写入口返回 409，避免绕过 run 状态与服务端风险数据。等待审批任务使用 CAS 占有 EXECUTING 或 CANCELLED；重复批准终态任务返回既有订单；并发冲突返回 409。提交前重新读取服务端行情、资金、提案期限；过期或风控变化不下单。拒绝后的提案不能恢复旧审批，需生成新提案。

## 恢复与预算边界

- 支持等待审批时重建服务；支持 EXECUTING / VERIFYING 时查询订单对账。订单查不到则保留未决，禁止自动补单。
- 不支持重启后继续模型 messages 循环；PLANNING 的全步骤检查点、提案落库与 run 事件的跨表原子提交未实现。
- Paper JSON ledger 是模拟 Broker 的单写者持久化文件；不保证多个进程同时写同一路径。应用 SQLite 订单表和 ledger 分离，是为验证响应丢失后 Broker 侧状态仍存在。
- runner 默认最多 8 轮、32,000 已观察 input+output tokens（包含缓存 input）、120 秒合作式期限。输出上限随剩余预算收紧；输入用量只能在响应后知道，允许单次请求超出后停止，不能承诺费用硬上限。
- 时间预算在 provider / tool 调用前后检查，不能强杀正在进行的同步调用。ToolRuntime 原有线程超时也不终止已经运行的 handler，写工具不应靠这一机制获得 exactly-once 保证。
- 工具轨迹记录参数、状态、耗时和输出摘要 hash，已有持久化边界负责脱敏；不保存私有思维链。证据检查只保证引用了实际成功调用的工具名，不保证语义结论被证据充分支持。

## 2026-10-12 本地验收

`pytest tests -q -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85`：123 passed，覆盖 87.97%。`mypy`：42 源文件通过。`ruff check src tests`：通过。上述两条 Paper CLI 与 8 场景运行时评测已实际运行。没有在本轮验证远程 CI、真实模型或同花顺执行。

同日扩展后的最新全量验收：**125 passed / 覆盖 88.11%**，mypy 42 源文件、全仓 src/tests Ruff、diff 检查通过。10 场景 CLI 为 10/10；普通与响应丢失 Paper 演示均 succeeded、orders=1、duplicate_same_order=true。原 8 场景和 123 项是前一阶段记录。

面试可讲：为什么把模型决策与执行权限分开；如何阻止审批重复和并发冲突；订单超时为什么不能直接重试；契约评测与运行时评测为何不同。效果指标必须另做真实、独立样本评测后再填写。

## 授权后的真实模型补充

2026-10-12 用 DeepSeek 官方 `deepseek-flash` 在公开人工工具 / 风险数据环境中完成两题集成检查：实际报价工具调用后生成类型化提案进入 WAITING_APPROVAL；证据不足时返回 no_trade 并进入 SUCCEEDED。分别 2 次 / 1 次 API 请求，订单均为 0。本次没有人工批准或真实市场验证，不是策略效果评测。在线 harness 复用知识库隔离 Python 3.14.8，从源目录加载 trader，超出其声明的 `<3.14` 安装支持范围；前述完整离线验收使用 Python 3.13。
