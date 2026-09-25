# AI Trader 生产级 Agent Runtime 设计

## 1. 目标

将当前“固定交易流水线 + 可选 ReAct 分析器”重构为面向高风险金融场景的受约束 Agent Runtime。系统必须可观测、可评测、可恢复、可审计，并且任何模型输出都不能绕过确定性风控与审批流程直接下单。

本次不建设通用多租户 Agent 平台，不引入计费、组织管理、可视化工作流编排等与面试主线无关的能力。

## 2. 验收标准

1. Agent 只能生成 `TradeProposal`，不能直接调用买入、卖出或撤单。
2. 所有高风险操作必须经过确定性 `RiskGate`；需人工确认的提案必须持久化为等待审批状态。
3. 重复执行同一 `proposal_id` 不得生成第二笔委托。
4. Agent 任务在进程重启后可从最后已持久化状态恢复，不重复运行已成功的副作用。
5. 每次模型和工具调用均记录 `run_id`、输入摘要、输出摘要、耗时、状态、错误码和成本信息。
6. 完整离线 Demo 不依赖真实 API、实时行情或同花顺，且默认禁止任何真实交易。
7. 至少建立 30 个 Agent Eval 场景，覆盖工具选择、参数、风控、异常恢复、Prompt Injection、重复委托和审批超时。
8. 敏感信息不得存在于源码、Trace 或测试固件中。

## 3. 架构边界

### 3.1 控制面

FastAPI 提供创建任务、查询 Trace、查询提案、审批、拒绝、取消和紧急停止接口。控制面不直接运行交易 GUI 操作。

### 3.2 Agent Orchestrator

Orchestrator 以显式状态机运行：

`CREATED -> PLANNING -> TOOL_RUNNING -> WAITING_APPROVAL -> EXECUTING -> VERIFYING -> SUCCEEDED`

任一节点可进入 `FAILED` 或 `CANCELLED`。状态变更在执行下一个副作用前落库，用于恢复和防重。

### 3.3 Tool Runtime

工具使用 Pydantic 定义输入输出，并声明：

- 权限和风险等级；
- 是否具有副作用；
- 是否要求人工审批；
- 超时、重试和幂等策略；
- 输出大小上限与敏感字段。

工具分为 Read、Compute、Write、High Risk 四类。真实交易执行器不注册为 LLM 可见工具。

### 3.4 Model Gateway

业务代码不再直接调用 `_call_claude`。Model Gateway 统一处理模型选择、结构化工具调用、Schema 校验、超时、重试、fallback、token/成本统计和 Prompt 版本。

### 3.5 RiskGate 和审批

`TradeProposal` 包含交易标的、方向、数量、价格上限、止损、止盈、证据引用、生成时间和过期时间。

RiskGate 以纯函数规则检查交易时间、报价时效、单笔与总仓位、集中度、日内损失、重复委托和 Kill Switch。输出为 `APPROVE`、`REJECT` 或 `REQUIRE_HUMAN`，并附结构化原因码。

### 3.6 Execution

交易通过 `BrokerAdapter` 抽象。默认实现为 `PaperBrokerAdapter`；同花顺 UIA 为可选 Adapter，仅在显式开启交易开关后可用。

委托状态为 `SUBMITTED`、`ACKNOWLEDGED`、`PARTIALLY_FILLED`、`FILLED`、`REJECTED` 或 `CANCELLED`。点击 GUI 提交按钮不等于委托成功，必须从委托列表确认状态。

### 3.7 Memory

分离工作记忆、交易事件记忆、经验语义记忆、用户风险配置和不可变审计记录。未验证的模型结论不直接进入长期经验库。

### 3.8 Observability 与 Evals

Trace 记录任务、模型、工具、风控、审批和执行事件。评测框架基于固定 fixture 离线执行，输出 Task Success、Tool Selection、Argument Accuracy、Policy Compliance、Recovery Rate、Step Efficiency、Groundedness、Cost 和 Latency。

## 4. 数据持久化

第一版使用 SQLite + SQLAlchemy，数据表包括：

- `agent_runs`：任务和当前状态；
- `run_events`：追加式事件 Trace；
- `tool_calls`：工具调用和结果；
- `trade_proposals`：交易提案；
- `approvals`：审批记录；
- `orders`：委托和幂等键；
- `prompt_versions`：Prompt 版本元数据。

通过 repository 接口隔离持久化实现，后续可切换 PostgreSQL，但首版不引入分布式数据库。

## 5. 错误与恢复

- 网络、限流和短暂 5xx 允许有界重试；
- 参数错误、权限错误、风控拒绝不重试；
- 具有副作用的工具默认不自动重试；
- 每次副作用前写入幂等键和意图记录；
- 任务取消和超时在安全点生效；
- 恢复时先对账外部委托状态，不盲目重放。

## 6. 安全约束

- `TRADING_ENABLED=false` 和 `PAPER_TRADING_ONLY=true` 为安全默认值；
- Secret 只从环境变量或密钥服务读取；
- Trace 写入前对 token、webhook、账号与个人信息脱敏；
- 工具输出视为不可信数据，不能覆盖系统策略；
- 提供 Kill Switch，其判定不依赖 LLM；
- 严格限制交易执行 Worker 的网络与文件权限。

## 7. 测试策略

### 单元测试

覆盖 Schema、状态转移、RiskGate、幂等、脱敏、工具权限和 Prompt 解析。

### 集成测试

使用 Fake Model、Fixture Tools、SQLite 临时库和 Paper Broker 验证完整任务、暂停审批、恢复、失败与取消。

### 契约测试

验证 Model Provider、Market Data Provider 和 Broker Adapter 的输入输出兼容性。

### Agent Evals

至少 30 个离线场景，默认使用 Fake Model 实现稳定回归；可选使用真实模型运行非确定性对比，但不作为 CI 必经步骤。

## 8. 实施分期

### Phase 1：安全基线与领域模型

修复 Secret，建立配置校验、交易开关、`TradeProposal`、`RiskDecision`、Paper Broker 和基础测试环境。

### Phase 2：Tool Runtime 与 Model Gateway

实现 Pydantic Tool Schema、权限、超时、结构化错误、统一 Model Gateway 与 structured tool calling。

### Phase 3：持久化 Orchestrator

实现状态机、SQLite repository、事件 Trace、任务恢复和取消。

### Phase 4：RiskGate、审批与执行

实现确定性风控、审批 API、幂等委托、Paper Broker 闭环，再将同花顺封装为默认关闭的 Adapter。

### Phase 5：Evals、可观测性与演示

建立场景数据集、自动评分、报告、Trace 查询页和一键离线 Demo，更新 README 与架构文档。

## 9. 非目标

- 不承诺模型能够稳定获得投资收益；
- 不提供真实资金自动交易默认配置；
- 不在首版引入微服务、Kubernetes、多租户和计费；
- 不删除现有模型权重、训练数据或实验结果；
- 不在没有独立安全验收前开放真实 Broker Adapter。
