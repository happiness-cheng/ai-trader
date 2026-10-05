# 设计任务书：Agent 自主交易安全闭环

> 立项：2026-09-26（会话主体 09-25 晚）
> 靶子选定：用户拍板 A（ai-trader 的 Agent 自主交易安全闭环）
> 工作协议：**设计与实现由用户完成；reviewer 只做三件事——挑没考虑的备选、指出论证不成立处、以面试官视角追问。**

---

## 0. 为什么是这个靶子（决策记录，供答辩引用）

不新建第三个系统。理由：现有两个项目已是可部署系统，且 `src/ai_trader/` 是生产级设计（风控门/状态机/事件溯源/乐观并发/幂等三层/模型网关/工具运行时/离线评测/FastAPI 控制面/脱敏/CI）。**2026-09-26 实测：用户无法画出该系统的架构图（答"不知道"）。** 新建第三个只会得到第三个不了解的系统。

正确形态：**不新建系统，把已有组件组装出一个新能力。** 组件的价值在组装，而设计的本质就是组装决策。

---

## 1. 现状（已核实事实，可直接引用；建议每条自己复核一遍）

| # | 事实 | 位置 |
|---|---|---|
| F1 | 生产 Agent 的工具集 5 个**全是只读**：`get_quote` / `get_market_overview` / `get_technical_indicators` / `get_positions` / `rag_search`，全部 `risk=LOW`、`effect` 仅 READ/COMPUTE、`requires_approval=False` | `tools/legacy_market.py:61-85` |
| F2 | 生产装配点把上述工具集注入 runner | `runtime.py:119` |
| F3 | `ToolEffect.WRITE` / `HIGH_RISK`、`ToolRisk.MEDIUM` / `HIGH`、`requires_approval` **生产代码零使用**（只在 `tests/unit/test_tool_runtime.py:84-86` 出现） | `tools/runtime.py:13-24, :76, :115` |
| F4 | 因此 `APPROVAL_REQUIRED` 分支与 runner 的处理逻辑**永不触发（死代码）** | `tools/runtime.py:115-121`、`agents/runner.py:142-147` |
| F5 | `ExecutionCoordinator` **不被** `agents/runner.py` / `agents/orchestrator.py` / `runtime.py` 引用；仅被 HTTP 层装配 | `api/app.py:13,23`、`execution/coordinator.py:22` |
| F6 | 唯一能推进到 `EXECUTING` 的是 `resume_approved`，其调用者**只有测试**，生产零调用 | `agents/orchestrator.py:80-96`、`tests/integration/test_persistent_orchestrator.py:45` |
| F7 | 无任何 `src/` 代码 transition 到 `VERIFYING`；该状态仅在枚举与允许表中定义 | `agents/state.py:16,96,98` |
| F8 | 原设计的目标状态链与实现不一致：设计写 `... WAITING_APPROVAL → EXECUTING → VERIFYING → SUCCEEDED`，实现断在 `WAITING_APPROVAL` | `docs/superpowers/specs/2026-07-11-production-agent-runtime-design.md:30` |
| F9 | 设计文档写明 `ExecutionCoordinator` 应 compose `PersistentOrchestrator`，实现里 `__init__` 未接（import 段也不含 `agents`） | `docs/superpowers/plans/2026-07-11-production-agent-runtime-phase-4.md:7` vs `execution/coordinator.py:1-15, :23-36` |
| F10 | 现有幂等三层：先查后做 / `reserve_order` 幂等键 / 失败标 `mark_execution_unknown` + `reconcile` 对账 | `execution/coordinator.py:74-76, :88-95, :107-109, :118-132` |
| F11 | 审批有效期被收缩为 `min(提案过期, now + 2min)`；执行前二次过风控门 | `execution/coordinator.py:53, :83-85` |
| F12 | 风控门 9 条硬规则，完全独立于 LLM，三态出口 `REJECT` / `REQUIRE_HUMAN` / `APPROVE` | `risk/gate.py:38-48, :61-99, :102-122` |
| F13 | 状态机拒绝非法转移与时间倒流；9 状态 + 合法转移表 | `agents/state.py:78-104, :107-115` |
| F14 | 意图先落盘：进 `TOOL_RUNNING` / `EXECUTING` 前先写 `INTENT_RECORDED` 事件 | `agents/orchestrator.py:47-55` |
| F15 | Agent 循环 `max_turns=8`；死循环检测 = 连续 3 轮完全相同工具调用签名 | `agents/runner.py:45, :22-26, :107-117` |
| F16 | 模拟盘/实盘开关：`live_execution_allowed`、`LiveExecutionDisabled`；新链路默认不启用 | `settings.py:35`、`execution/ths.py:21`、`runtime.py:145-147` |
| F17 | Broker 抽象为 Protocol，三个实现（paper / ths / 测试替身） | `execution/base.py:12-17`、`execution/paper.py:18`、`execution/ths.py:36` |

---

## 2. 你必须做出的 5 个设计决策

> 每个决策必须给出：**选定方案** + **至少 2 个备选** + **为什么不选备选** + **这个决策的失效条件（什么情况下它会变成错的）**。

### D1. Agent 的写权限边界
Agent 能否直接持有"提请交易"的工具？若可以，风控门放在 Agent 循环**内**（工具执行前）还是**外**（`ExecutionCoordinator` 里）？两层都放是否重复？
关联：F1 F2 F3 F4 F12

### D2. 状态机的收敛路径
`EXECUTING` 与 `VERIFYING` 的**语义定义**是什么？谁负责驱动进入与离开？`WAITING_APPROVAL` 挂起后 Agent 循环如何退场（现在是直接 raise）？恢复时如何续接（`recoverable_runs` 已存在）？是否需要新增/合并状态？
关联：F6 F7 F8 F13 F14

### D3. 幂等边界的划分
Agent 重试 / 人工重试 / 崩溃恢复，三条路径是否共用同一套幂等键？**Agent 遇到执行超时该如何决策**：继续等 / 查对账 / 放弃 / 挂起等人？`ExecutionUnknown` 应该向 Agent 暴露到什么程度？
关联：F10 F11

### D4. 审批的等待模型
挂起-恢复（状态机持久化 + 外部触发）vs 长连接等待 vs 轮询，三者的代价与适用条件。审批过期后 run 应该进入什么终态？用户拒绝后呢？
关联：F6 F11 F13

### D5. 可观测与评测的接入
一次完整自主闭环的 trace 记什么、存在哪？如何把这条新路径加入现有离线评测（`evals/` 与 `eval/`）？用什么指标判定"自主闭环"是安全的？
关联：`evals/contracts.py`、`agents/runner.py:93-103`、F4

---

## 3. 硬约束（违反即设计不通过）

1. **LLM 不得绕过风控门**——任何写操作必须过 `RiskGate`，且风控判定不得由模型决定。
2. **不得破坏现有保证**：幂等三层、事件溯源可审计（每次转移/事件都可回放）、乐观并发（`expected_version` → `VersionConflict`）、非法转移与时间倒流拒绝。
3. **默认安全**：模拟盘默认可用，实盘默认关闭；新路径默认不启用（与 `runtime.py:145-147` 的既有风格一致）。
4. **必须能离线跑通**：`--dry-run`（`runtime.py:68`，`FakeModelProvider`）路径下可完整复现一次自主闭环，不依赖网络与真实 API。
5. **代码风格跟随既有约定**：Pydantic v2（`ConfigDict(frozen=True, extra="forbid")`）、`Decimal` 表示金额、**tz-aware datetime**（naive 必须报错）、显式错误类型而非裸 `Exception`、面向 Protocol 而非具体实现。
6. **不得引入只有你能跑的环境依赖**（不得假设有 Docker/NTP 之外的额外基础设施）。

---

## 4. 交付物

### 4.1 设计文档（第一件，也是唯一先交的东西）
路径：`ai-trader/docs/design/01-autonomy-loop-design.md`

必须包含：
- **问题陈述**：现状 + 缺口，逐条带 `文件:行号`（F1~F17 可引用，但鼓励自己复核并修正）
- **5 个决策**（见第 2 节）完整展开
- **状态机图**：新旧对比
- **时序图**：一次完整自主闭环——从 `runner.run(goal)` 到 `Order` 落库，含审批挂起与恢复的那一段
- **失败模式清单**：至少 6 条（LLM 不可用 / 工具超时 / 审批过期 / 执行结果 unknown / 进程崩溃后恢复 / 重复提案），每条写清"检测手段 + 恢复动作 + 用户可见结果"
- **评测方案**：怎么离线测这条新路径，指标是什么
- **非目标（明确的"不做什么"）**：至少 3 条
- **引用**：你用到了所参考资料的哪几条原则，逐条映射到具体决策（**不许只写"参考了某书"**）

### 4.2 实现与测试（文档通过后才开始）
- 真代码 + 测试（unit + integration），`pytest` 全绿
- dry-run 下可复现的端到端演示

### 4.3 答辩包
- **一张你自己画的架构图**（这张图 = M11-13 白板题素材）
- **90 秒答辩稿**：每个论点必须挂一个数字或一个 `文件:行号`（本 tracker 硬规则）

---

## 5. 会被打回的情况（先说清）

- 只写"我要加一个 submit_trade 工具"而不讨论风控门放在哪一层 → 打回
- 状态图里出现实现不可能达到的转移（如现在的 `EXECUTING`）却不解释谁驱动 → 打回
- 只给方案不给备选，或备选是稻草人 → 打回
- 失败模式少于 6 条，或只写"加 try/except" → 打回
- 任何断言没有 `文件:行号` 或数字 → 打回（**注意：行号必须按功能定位，不得背显眼行号——2026-09-26 已用血换过这一课**）
- 说"参考了某资料"但无法逐条映射到决策 → 打回

---

## 6. 时间盒

| 阶段 | 期限 | 说明 |
|---|---|---|
| 设计文档第一稿 | **2026-09-29 之前** | 不写代码，只写文档 |
| reviewer 一轮（挑备选 + 挑论证 + 面试官追问） | 收到后 24h 内 | |
| 实现 + 测试 | 设计通过后 | |
| 答辩包 | 实现完成后 | |

**为什么先只写文档**：设计的价值在决策与取舍，不在代码行数。代码写完但说不清为什么 = 又一个你不了解的系统。而且文档本身就是 M11 答辩稿的原料。

---

## 7. 今晚（09-26 凌晨）的最小动作

读完上面第 1 节的 17 条对应位置（**建议亲自打开文件看，不要只信这张表**），然后回答**一句话**：

> **D1~D5 里，哪一个最难？为什么？**

只要一句。不要写文档，不要写代码。
