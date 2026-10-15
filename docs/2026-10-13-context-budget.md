# 2026-10-13：调用前上下文预算与候选对照

`ProductionAgentRunner` 现在通过 `ContextBuilder` 构造每次发给 gateway 的完整 `ModelRequest`，再检查预算。它解决的是“大工具结果逐轮追加，累计 usage 限制还没触发，下一次输入已经过大”的工程问题。默认上限为 **24,000 个序列化字符**，不称为 24,000 tokens。

## 1. 实际接入位置与失败行为

调用路径为 `Paper HTTP analyze → TradingWorkflow → ProductionAgentRunner → ContextBuilder → ModelGateway`。当前目标、业务说明与服务器约束作为三个独立参数保存；工具历史保存为完整的 `ToolExchange`，每轮同时包含 `tool_use` 和对应的 `tool_result`。

预算计算采用 `len(request.model_dump_json())`，包含 model 名称、messages、工具 schema 和 max_tokens。原有累计 usage、总轮数和合作式运行时限继续存在。检查不通过时，不调用 provider，run 进入 `failed`，失败分类为 `context_budget_exceeded`；异常原因区分字符超限、token 超限、计数器不可用、无效计数、非法配对或不可序列化约束。

`RunnerResult.context_reports` 记录每轮原始/实际字符数、可选 token 计数、压缩结果 ID 和省略调用 ID；provider 收到的请求与该轮报告对应。失败观测仍保留实际成功过的工具调用与原结果 hash，不把未发给模型的内容说成模型已经看过。

Paper 入口实际通过 `runner.instructions` 传入已有 `DECISION_INSTRUCTION`。新增可选的 `TradingWorkflow.constraints_source(run_id)`，在分析开始前从该 run 和 Paper ledger 收集账户、持仓、Paper/fixture 模式、风险策略与时间戳；不会读取其他 run 的目标或历史。新分析尚未产生提案，因此 `current_proposal=None`、`risk_status=not_evaluated`；`execution_unknown` 只描述该 run 的快照状态。来源不可用时，在模型调用前失败。批准阶段继续重新计算账户并执行原有确定性风控，不以模型看到的旧快照替代复查。

2026-10-14补齐实际角色边界：`ModelRequest.system`承载可信业务说明和服务器约束，`AnthropicProvider`映射为SDK顶层`system`；用户目标、模型工具调用与观察仍在`messages`。真实SDK内存传输核对了system/messages/cache_control的请求正文，没有网络调用。其他provider必须实现同一字段映射才能获得此结构保证；默认fixture只验证请求传递。角色分区仍不等于prompt injection语义安全，确定性风控与审批继续在模型之外执行。

## 2. 三种可运行策略

| policy | 行为 | 代价与边界 |
|---|---|---|
| `disabled` | 关闭缩减，完整上下文仍须通过调用前预算 | 大输入直接失败，保留完整信息 |
| `mechanical` | 从最旧轮次开始删除完整配对 | 最新观察、显式服务器约束和含受保护观察字段的旧轮次不可删除；超限时失败 |
| `structured`，默认 | 大结果替换为结构化预览，再按相同规则裁剪旧轮次 | 保留前 256 字符、原长、SHA-256、不完整警示和原始观察字段；没有 LLM 总结调用 |

结构化预览的 `retained_observation_fields` 复制可解析 JSON 中 proposal、risk、status、account、order、price 等受保护字段，记录其 JSON Pointer。它们明确标为 `unverified_tool_observation`，只是工具观察原值，不升级为可信事实、不并入服务器约束。不能从任意自由文本中可靠识别的关键状态，应由调用者显式提供在 `constraints` 中。受保护字段或约束本身过大时，失败关闭。

请求会标明哪些结果是预览、哪些整轮已省略。tool_use 的名称、ID、参数和与结果的配对不被改写；不生成原结果中没有的业务事实。SHA-256 用于关联原观察，不是可供模型自行取回全文的地址。未展示部分不能当作完整证据。这些规则没有证明压缩后真实模型的业务判断一定正确。

## 3. 同一输入的实际字符对照

使用 Python 3.12 独立环境、相同 2,500 字符预算、相同目标/约束；每个合成轮次只有一个工具调用。样本 A 为“大旧历史 + 小近期风险观察”，样本 B 为“大近期观察 + 风险字段”。成功后只调用固定响应的 `FakeModelProvider`，没有在线模型、token 成本或任务质量收益测量。

| 样本 | policy | 原始字符 | 实际发送字符 | 省略轮数 | 压缩结果数 | 风险原字段 | 结果 |
|---|---|---:|---:|---:|---:|---|---|
| A | disabled | 13,765 | 未发送 | — | — | 未发送 | input_chars，provider 0 次 |
| A | mechanical | 13,765 | 710 | 1 | 0 | 保留 | 固定 fixture completion |
| A | structured | 13,765 | 1,450 | 0 | 1 | 保留 | 固定 fixture completion |
| B | disabled | 17,567 | 未发送 | — | — | 未发送 | input_chars，provider 0 次 |
| B | mechanical | 17,567 | 未发送 | — | — | 未发送 | input_chars，provider 0 次 |
| B | structured | 17,567 | 1,267 | 0 | 1 | 保留 | 固定 fixture completion |

机械裁剪在 A 中输入更小，但丢弃了旧观察；结构化压缩保留旧观察的预览，字符更多。B 中最新观察不可整轮丢弃，机械策略只能失败；结构化策略可在保留原风险字段和不完整标记的条件下发送。此比较支撑“工程预算与字段保留有效”，不支撑“真实模型效果改善”。

复现全部候选测量并打印 JSON：

```powershell
python -m ai_trader.offline_tests -k candidate_experiment -s -q
```

## 4. 按任务说明与工具 schema 的候选

`TaskInstructionSet` 接受调用者明确提供的 `shared`、`analyse`、`review` 可信说明。`select(task, enabled=True)` 保留共享说明和当前任务说明；`enabled=False` 保留全量说明。不读取 Skills 文件，不根据任意用户文本自动决定任务，不截断业务说明或服务器约束。

| 任务 | 全量说明字符 / 请求字符 | 按需说明字符 / 请求字符 | 共享、当前任务及服务器约束 |
|---|---:|---:|---|
| analyse | 517 / 888 | 399 / 768 | 均保留 |
| review | 517 / 888 | 234 / 557 | 均保留 |

这是可调用、可关闭的候选；单元测试把选择后的说明实际传给 builder。**当前 Paper 默认入口仍直接使用 DECISION_INSTRUCTION，没有自动挂接 TaskInstructionSet 的任务选择；review 仍是人工审批与确定性执行，没有新增“模型审核批准”路径。** 可以通过 `runner.run(instructions=...)` 显式试用选出的说明。

另以 3 个合成只读工具 `get_quote/get_account/get_order_status` 对比全 schema 与手工任务映射：

| 任务 | 全量 3 schema 请求字符 | 手工选 2 schema 请求字符 |
|---|---:|---:|
| analyse | 1,352 | 1,149 |
| review | 1,141 | 952 |

当前 Paper 实际只有 `get_quote` 一个工具，仍暴露全部已授权 schema。上述只是合成候选测量，没有改默认工具集，不称为智能发现或 MCP 工具发现收益。减少 schema 的主要风险是当前任务误选导致必要工具不可见；没有真实任务集证据时不自动采用。

## 5. token 预算的条件与未完成项

默认没有 tokenizer，`input_tokens=None`。可同时注入 `token_counter: Callable[[ModelRequest], int]` 与 `max_input_tokens`；只给其中一个会拒绝配置，计数器失败或返回负数、bool、非整数时失败关闭。最终请求重新计数并满足字符与 token 两个上限后才交给 provider。

**条件保证**：只有计数器确实匹配实际模型、消息编码、工具 schema 和 provider 适配产生的额外内容时，计数上限才对应真实 token 上限。提供方的 strict/cache 参数、fallback 编码和输出预留都需另行纳入匹配验证。本轮测试使用合成计数函数检查执行路径，没有使用真实 tokenizer 或远程 count API，不能标记 A2 的 token 与真实效果部分完全验收。

下一步验收应使用匹配计数器和固定真实任务集，比较完整输入、机械裁剪、结构化压缩对判断正确性与必要证据的影响，并验证实际兼容提供方遵循system角色。当前可以讲清预算触发前后的实际请求、为何保留配对、为何无法压缩时失败，以及候选的取舍。

## 6. 验证证据

首个 RED 在现有真实 runner 上复现：下一次请求为 **40,515 字符**，超过预期 24,000。随后新 builder 缺失时为 9 failed；实现后发现“旧风险结果压缩后被当作普通旧历史删除”，对应回归先失败、修复后通过。Paper 输入注入测试先因缺少 `server_constraints` 失败，再通过。

实际执行过：

```powershell
D:\python.exe -m pytest tests/unit/test_context_builder.py -q
# 18 passed
D:\python.exe -m ruff check src/ai_trader/agents/context.py src/ai_trader/agents/runner.py src/ai_trader/execution/workflow.py src/ai_trader/paper_service.py tests/unit/test_context_builder.py tests/integration/test_paper_service.py
# All checks passed!
D:\python.exe -m mypy src/ai_trader/agents/context.py src/ai_trader/agents/runner.py src/ai_trader/paper_service.py --follow-imports=silent
# Success: no issues found in 3 source files
```

另在独立 Python 3.12 环境联跑 context、production runner、workflow、Paper service：增补最终两个测试前为 **53 passed，1 个第三方 TestClient 弃用 warning**。最终结果由主任务统一复验。本轮没有在线模型调用、真实 tokenizer、数据库 schema 迁移或真实记忆读写。

2026-10-14补充：system角色/SDK映射新增测试RED 2 failed→3 passed（包含真实SDK内存传输），合并context/Paper/provider 36 passed；新的字符测量通过仓库内隔离入口实际重跑，表格已更新。SDK新增Omit类型导致NotGiven可选参数静态检查失败，改为TypedDict条件传入system后mypy 3文件通过，保留旧版未传system的请求兼容。

官方依据：[Using the Messages API / Multiple conversational turns → System role in messages](https://platform.claude.com/docs/en/build-with-claude/working-with-messages#multiple-conversational-turns)与[Prompt caching / Automatic caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#automatic-caching)。API大页面本轮工具读取超出大小限制，改读两个具体官方指南及实际安装SDK。当前项目使用兼容网关，不能据Anthropic原生文档宣称该网关实际命中缓存。
