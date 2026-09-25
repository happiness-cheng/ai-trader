# AI Trader — 生产级 Agent 架构（真实代码版）

> 生成日期：2026-09-23
> 来源：逐文件读取 `src/ai_trader/` 源码，每一条断言都带 `文件:行号`，可直接核对。
> **本文与同目录 `ARCHITECTURE.md` 描述的不是同一个系统**——那份是旧世代（`main.py` 管线 + Rule Engine + pywinauto 自动下单），本文是新世代（`src/ai_trader/`）。

---

## 一、两代对照（面试必须先分清你在讲哪一代）

| | 旧世代 | 新世代 |
|---|---|---|
| 位置 | 仓库根 `main.py` / `agent_planner.py` / `agent_tools.py` | `src/ai_trader/` |
| 决策 | Rule Engine 初筛 + LLM 单次确认 | 手动 ReAct 循环，最多 8 轮（`agents/runner.py:45,69`） |
| 模型 | MiMo API（Claude 兼容） | `ModelGateway` + `AnthropicProvider`（`models/gateway.py:86`、`models/anthropic_provider.py:34`） |
| 下单 | pywinauto GUI 自动点击，**全自动** | `ExecutionCoordinator` + 人工审批，**Agent 无写权限** |
| 文档 | `ARCHITECTURE.md`（已过期） | 本文 + `docs/superpowers/specs/2026-07-11-production-agent-runtime-design.md` |
| 关系 | 被新世代**包裹成数据源**：`tools/legacy_market.py:33` 懒加载旧的 `market_data` / `strategy` / `rag_store` 模块 | |

> 关键：新世代 docstring 自称"旧版默认保持不变"（`runtime.py:1`），并且默认不启用——必须 `AI_TRADER_RUNTIME_MODE=production` 才走新链路（`runtime.py:112`、`:145-147`）。

---

## 二、入口层

```
runtime.py:129  main()
  ├─ :136  --dry-run        → run_dry(:68)          用 FakeModelProvider(:13)，无网络
  ├─ :139  --smoke-online   → run_online_smoke(:50)  三重门禁 validate_online_smoke(:41)
  └─ :148  build_production_runner(:111)  ← 生产装配的唯一入口
             :116 SqlRunRepository(runtime_database_url)
             :118 ModelGateway([AnthropicProvider.from_settings(settings)])
             :119 create_legacy_market_runtime()      ← 工具集在这里定死
             :120 PersistentOrchestrator(repository)
             :121-123 ToolExecutionContext(permissions={market:read, portfolio:read, memory:read})

api/app.py:70  create_app()   ← HTTP 控制面
  :74  GET  /health              :78  GET  /latest_evals
  :84  POST /runs                :88  GET  /runs/{id}      :95 GET /runs/{id}/events
  :102 POST /runs/{id}/cancel
  :112 POST /proposals           :119 POST /proposals/{id}/approve   :134 .../reject
```

---

## 三、Flow A —— Agent 分析循环（**只读**）

`agents/runner.py:56 run(goal)`

| 步 | 行号 | 做什么 |
|---|---|---|
| 1 | `:57` | `orchestrator.create_run(goal)` → `RunState.CREATED` |
| 2 | `:58` | `orchestrator.start(run_id)` → `RunState.PLANNING` |
| 3 | `:69` | `for turn in range(1, max_turns + 1)`，`max_turns=8`（`:45`） |
| 4 | `:71-78` | `gateway.complete(ModelRequest(messages, tools.schemas(ctx), max_tokens=4000))` |
| 5 | `:79-83` | 网关抛 `ModelGatewayError` → `transition(FAILED)` + `AgentRunFailed` |
| 6 | `:85-87` | 累加 `input_tokens` / `output_tokens` / `latency_ms` |
| 7 | `:88-104` | **无 tool_calls → `transition(:89 SUCCEEDED)` + 返回 `RunnerResult`**（正常出口） |
| 8 | `:107-117` | 死循环检测：`_detect_loop(:22, window=3)`，连续 3 轮**完全相同**的 `frozenset` 签名 → `transition(FAILED)` + raise |
| 9 | `:129-133` | 每个 tool_call → `transition(TOOL_RUNNING)` |
| 10 | `:134-141` | `tools.execute(ToolCall, context)` |
| 11 | `:142-147` | `error_code == APPROVAL_REQUIRED` → `request_approval` + raise（**见第六节：生产装配下为死代码**） |
| 12 | `:148-154` | 其他失败 → `transition(FAILED)` + raise |
| 13 | `:155` | `transition(PLANNING)` |
| 14 | `:174-177` | `messages += (assistant_blocks, tool_results)` ← **这就是"输出变下一轮输入"** |
| 15 | `:179-182` | 跑满 8 轮 → `transition(FAILED, max_turns_exceeded)` + raise |

**三道门，全在 Flow A 里：**

**门 1 — 模型网关** `models/gateway.py:86 ModelGateway.complete`
- `:54 ProviderErrorCode` / `:63 ProviderError` / `:76 ModelGatewayError`：错误分级
- `:80 ModelProvider` Protocol → 实现两个：`anthropic_provider.py:34`（真）/ `fake.py:13`（测试用）
- fallback 换供应商 + 内层重试（外层 providers 列表 `runtime.py:118`）

**门 2 — 工具运行时** `tools/runtime.py:106 ToolRuntime.execute`（统一的失败面）
| 行号 | 失败码 |
|---|---|
| `:108-110` | `UNKNOWN_TOOL` |
| `:111-114` | `PERMISSION_DENIED`（`definition.permission not in context.permissions`） |
| `:115-121` | `APPROVAL_REQUIRED` |
| `:124-128` | `VALIDATION_ERROR`（入参 Pydantic 校验） |
| `:130-140` | `TIMEOUT`（`ThreadPoolExecutor` + `timeout_seconds`）/ `HANDLER_ERROR` |
| `:143-147` | `OUTPUT_VALIDATION_ERROR`（出参再校验一次） |

**工具集：`tools/legacy_market.py:33 create_legacy_market_runtime()` —— 5 个，全部只读**

| 工具 | 行号 | permission | risk | effect |
|---|---|---|---|---|
| `get_quote` | `:61-65` | `market:read` | LOW | **READ** |
| `get_market_overview` | `:66-70` | `market:read` | LOW | **READ** |
| `get_technical_indicators` | `:71-75` | `market:read` | LOW | **COMPUTE** |
| `get_positions` | `:76-80` | `portfolio:read` | LOW | **READ** |
| `rag_search` | `:81-85` | `memory:read` | LOW | **READ** |

数据来源是旧世代模块，懒加载（`:34` `module_loader`）：`market_data`（`:39,:43,:47`）、`strategy.get_local_positions`（`:52`）、`rag_store`（`:56`）。

---

## 四、Flow B —— 交易执行链（**人工审批驱动，不经 Agent**）

| 步 | 位置 | 做什么 |
|---|---|---|
| 1 | `api/app.py:112-117` | `POST /proposals` → `coordinator.submit_proposal(context)` |
| 2 | `execution/coordinator.py:38-40` | `:39 repository.save_proposal(proposal)` → `:40 risk_gate.evaluate(context)` |
| 3 | `api/app.py:119-132` | `POST /proposals/{id}/approve` → `coordinator.approve(id, reviewer)` |
| 4 | `coordinator.py:42-56` | `:45 is_expired → ExecutionUnknown`；`:53 expires_at = min(proposal.expires_at, now + 2min)`；`:55 record_approval` |
| 5 | `coordinator.py:72-109` | `execute_approved(context)` —— **五道校验串行** |
| 5a | `:74-76` | **幂等第 1 层**：`get_order_by_proposal` 已有订单 → 直接返回 |
| 5b | `:78-79` | `is_expired` → `ExecutionUnknown` |
| 5c | `:80-82` | `get_valid_approval` 为 None → `ExecutionUnknown("valid human approval is required")` |
| 5d | `:83-85` | **风控门第二次评估**（`replace(context, now=now)`），`REJECT` → `ExecutionUnknown` |
| 5e | `:88-95` | **幂等第 2 层**：`reserve_order(proposal_id, reservation_id, now)`；`DuplicateReservation` → 再查订单，有则返回，无则 `ExecutionUnknown` |
| 6 | `:104-106` | `broker.submit(proposal, decision)` → `repository.save_order(order)` |
| 7 | `:107-109` | **幂等第 3 层**：任何异常 → `mark_execution_unknown(proposal_id)` 后抛出 |
| 8 | `:118-132` | `reconcile(proposal_id)`：本地无订单 → `broker.list_orders()` 里按 `proposal_id` 找 → 找到则落库 |

**门 3 — 风控门** `risk/gate.py:51 RiskGate.evaluate(:57)`，**完全独立于 LLM 的确定性规则**。
`RiskPolicy`（`:19-24`）：`max_quote_age_seconds=30` / `max_position_ratio=0.15` / `max_total_positions=8` / `max_daily_loss_ratio=0.03` / `require_human_approval=True`
9 条拒绝码（`:38-48`，判定在 `:61-99`）：`TRADING_DISABLED` / `PROPOSAL_EXPIRED` / `QUOTE_SYMBOL_MISMATCH` / `STALE_QUOTE` / `DUPLICATE_ORDER` / `DAILY_LOSS_LIMIT_EXCEEDED` / `INSUFFICIENT_CASH` / `POSITION_LIMIT_EXCEEDED` / `MAX_POSITIONS_EXCEEDED`
三态出口：有码 → `REJECT`（`:102-107`）→ 无码且 `require_human_approval` → `REQUIRE_HUMAN`（`:109-115`）→ 否则 `APPROVE`（`:117-122`）

**门 4 — 人工审批**：`get_valid_approval`（`coordinator.py:80` → `persistence/trading.py:121`），审批有效期 `min(提案过期, now+2min)`（`:53`）。

**门 5 — Broker 抽象** `execution/base.py:12 BrokerAdapter` Protocol（`submit` / `get_order` / `list_orders`），三个实现：
- `execution/paper.py:18 PaperBrokerAdapter`（模拟盘）
- `execution/ths.py:36 ThsBrokerAdapter`（同花顺，`LiveExecutionDisabled(:21)` 默认关）
- 测试替身

---

## 五、横切：状态机 / 事件溯源 / 评测 / 脱敏

**状态机** `agents/state.py`
- 9 个状态 `:10-19`：`CREATED / PLANNING / TOOL_RUNNING / WAITING_APPROVAL / EXECUTING / VERIFYING / SUCCEEDED / FAILED / CANCELLED`
- 合法转移表 `_ALLOWED` `:78-104`（`SUCCEEDED/FAILED/CANCELLED` 为终态，空集）
- `transition(:107-115)`：**拒绝时间倒流**（`:109-110`）+ **拒绝非法转移**（`:111-112`，`InvalidTransition(:32)`）+ `version+1`

**事件溯源 + 乐观并发** `persistence/runs.py:78 SqlRunRepository`
- `append_transition(:121)` / `append_event(:172)` / `events(:220)` / `list_recoverable(:229)`
- 全部带 `expected_version` → 并发冲突抛 `VersionConflict(:21)`
- 编排器 `agents/orchestrator.py:11 PersistentOrchestrator`：`:39-64 transition` 在进 `TOOL_RUNNING`/`EXECUTING` 前先写 `INTENT_RECORDED` 事件（`:47-55`，**意图先落盘再执行**）
- 事件类型 7 种 `state.py:22-30`；`payload` 上限 64 KiB（`state.py:70-75`）

**离线评测** `evals/`：`contracts.py` / `loader.py` / `grading.py` / `reporting.py` / `runner.py` / `trace_executor.py` / `cli.py`；`RunnerResult.observation`（`runner.py:93-103`）直接产出评测观测（tokens / latency / steps / tool_calls / evidence_refs）

**脱敏** `observability/redaction.py:38 redact_payload`

---

## 六、⚠️ 三个必须知道的"断裂点"（实测，非推测）

### 断裂 1：Flow A 和 Flow B 在代码层面不相连

`ExecutionCoordinator` 的引用只有三处：`execution/coordinator.py:22`（定义）、`api/app.py:13,23`（HTTP 装配）、`tests/`。
**`agents/runner.py`、`agents/orchestrator.py`、`runtime.py` 都不引用它。**

后果：**Agent 循环无法触发交易**。交易只能由人通过 HTTP 端点发起。

### 断裂 2：生产工具集里没有任何写操作，`runner.py` 的审批分支是死代码

- 5 个生产工具**全部** `requires_approval=False`（默认）、`risk=LOW`、`effect` 只出现 READ/COMPUTE（`legacy_market.py:64,69,74,79,84`）
- `ToolEffect.WRITE` / `ToolEffect.HIGH_RISK`（`tools/runtime.py:22-23`）**在生产代码中零使用**，只在 `tests/unit/test_tool_runtime.py:84-86` 出现
- `ToolRisk.MEDIUM` / `HIGH`（`:14-15`）同样零使用
- ⇒ `tools/runtime.py:115` 的 `APPROVAL_REQUIRED` 分支、以及 `runner.py:142-147` 的处理逻辑，**在当前生产装配下永远不会触发**

### 断裂 3：`EXECUTING` / `VERIFYING` 是不可达状态

- 唯一能推进到 `EXECUTING` 的是 `orchestrator.py:80-96 resume_approved`
- `resume_approved` 的调用者只有 `tests/integration/test_persistent_orchestrator.py:45` —— **生产代码零调用**
- `api/app.py:119 approve_proposal` 调的是 `execution_coordinator.approve`，**不是** `orchestrator.resume_approved`
- 没有任何 `src/` 代码 transition 到 `VERIFYING`（只出现在 `state.py:16,96,98` 的定义与允许表、以及测试 `tests/unit/test_agent_state.py:35-36`、设计文档）
- ⇒ 状态机"定义了"这些转移合法，但**没有代码执行它们**

### 断裂 4（设计 vs 实现的差值）

`docs/superpowers/plans/2026-07-11-production-agent-runtime-phase-4.md:7` 写明设计意图：
> "`ExecutionCoordinator` composes RiskGate, approval records, **PersistentOrchestrator** and BrokerAdapter"

但实现里 `coordinator.py:23-36 __init__` 只接 `repository` + `broker` + `risk_gate`，**没有接 `PersistentOrchestrator`**（import 段 `:1-15` 也不含 `agents`）。

⇒ 这正好解释了断裂 3：执行链不知道 `RunState` 的存在，所以交易执行不会推进 Agent 状态。设计里的目标链（`docs/.../specs/...design.md:30`：`CREATED → PLANNING → TOOL_RUNNING → WAITING_APPROVAL → EXECUTING → VERIFYING → SUCCEEDED`）在实现里断在 `WAITING_APPROVAL`。

---

## 七、面试时能说什么、不能说什么

### ✅ 可以自信说的（每条都有代码）
- **确定性风控独立于 LLM**：`risk/gate.py` 是纯函数式规则门，9 条硬规则 + 三态出口，LLM 无权绕过
- **显式状态机 + 事件溯源 + 乐观并发**：非法转移被拒（`state.py:111`）、时间倒流被拒（`:109`）、并发冲突抛 `VersionConflict`（`persistence/runs.py:21`）
- **意图先落盘**：进 `TOOL_RUNNING`/`EXECUTING` 前先写 `INTENT_RECORDED`（`orchestrator.py:47-55`）→ 崩溃后能靠 `recoverable_runs(:110)` 恢复
- **幂等三层**：先查后做（`coordinator.py:74`）+ 幂等键 `reserve_order`（`:88`）+ 失败标 `unknown` 留对账（`:108`）、`reconcile(:118)`
- **工具运行时统一失败面**：6 类错误码，入参出参双向 Pydantic 校验，超时用线程池隔离（`tools/runtime.py:108-147`）
- **审批有效期收缩**：`min(提案过期, now+2min)`（`coordinator.py:53`）
- **带评测框架**：`evals/` 产出 token/latency/steps/evidence 的观测（`runner.py:93-103`）
- **安全默认关闭**：`live_execution_allowed`（`settings.py:35`）、`LiveExecutionDisabled`（`execution/ths.py:21`）、新链路默认不启用（`runtime.py:145-147`）

### ❌ 绝对不能说
- **"我的 Agent 能自动下单"** —— 生产装配的 5 个工具全只读，Agent 拿不到写工具，`ExecutionCoordinator` 不在它的调用链上。**面试官打开代码就崩。**
- "Agent 会根据风控反馈自我修正" —— 风控门不在 Agent 循环里（它在 Flow B）

### ⚠️ 主动交底（这三条是 L4 加分，不是扣分）
> "我这套东西的设计和实现有落差，我清楚在哪：状态机定义了 `EXECUTING`/`VERIFYING`，但 `resume_approved` 在生产里没有调用者，所以这两个状态目前不可达；`ExecutionCoordinator` 按设计文档应该 compose `PersistentOrchestrator`，实现里没 compose，导致 Agent 状态和交易执行状态是两个互不知道的世界。我选的是先让写路径绝对安全、Agent 只读，代价是"自动交易"这条线还没接通。"

---

## 八、待你亲自验证的清单

本文所有行号来自 2026-09-23 的源码直接读取。请你自己跑一遍核对（开卷不算作弊）：

- [ ] `cd ai-trader` → 用 `D:/Users/陈独秀/AppData/Local/Programs/Python/Python314/python.exe -m pytest tests/ -q` 确认测试现状
- [ ] 打开 `tools/legacy_market.py:60-86`，**亲眼确认 5 个工具没有一个是 `effect=ToolEffect.WRITE`**
- [ ] 打开 `agents/coordinator.py` 对应位置（实为 `execution/coordinator.py:23-36`），确认 `__init__` 不接 orchestrator
- [ ] grep `resume_approved`，确认除测试外无调用者
- [ ] 确认 `AI_TRADER_RUNTIME_MODE` 当前值（未设 = 走旧链路）
- [ ] 核对完后：把这张图从空白纸重画一遍，不看文档
