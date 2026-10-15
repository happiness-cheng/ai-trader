# A3 受控记忆闭环

记录日期：2026-10-14；接续 2026-10-13 核心能力矩阵。此切片实现跨任务记忆的授权、业务确认、来源、TTL 与冲突规则，不以“接上向量库”代替这些验收。

## 方案与取舍

短期任务历史仍由 `ContextBuilder` 组装。长期记忆继续使用现有 `RagStore` 的 Chroma embedding/持久化能力，写入现有 `lessons` 集合，以 `controlled_` ID 前缀与 `controlled` 元数据区分。没有新建记忆集合、迁移旧集合或修改 SQL schema；旧数据仍保留，但缺少受控元数据时不进入新读取路径。

| 方案 | 收益 | 代价和边界 |
|---|---|---|
| 本次采用：业务确认事件 + 现有 lessons + 检索前 metadata 过滤 | 复用存储；用户隔离、确认状态和 TTL 不依赖模型自觉；重复确认可串行重放；保留来源 | 需要业务入口提供可信事件；语义查询后还要查询同组全部有效来源解决冲突；确认写入与原业务提交没有跨存储事务 |
| 直接保存模型对话/建议后任意检索 | 接入成本低、内容丰富 | CoT 和建议会混入事实；缺少授权、有效期和确认来源，不能作为已确认业务依据 |
| 独立业务事件库 + 单独记忆索引 | 更容易做条件写入、事件审计和可靠投递 | 增加持久化组件、schema 和恢复成本；本轮未为该切片新增基础设施 |

## 接口与可信入口

```python
ConfirmedMemoryEvent(
    event_id, user_id, source_run, confirmed_at, outcome,
    content, conflict_key, confirmed,
    kind="confirmed_outcome", origin="business_confirmation",
)
ControlledMemory(
    store, *, user_id, permissions,
    clock=UTC_now, ttl_seconds=2592000,
)
memory.record_confirmed(event)  # written / duplicate + record_id
memory.search(query, top_k=3)  # current_confirmed / historical / conflict
create_legacy_market_runtime(memory_reader=memory.search)
```

`user_id`、权限、时钟和 TTL 由服务器绑定。`confirmed` 必须显式提供，没有默认 True。写入要求 `memory:write`、事件 owner 与绑定 owner 一致、确认时间带时区且不在未来、来源非空、正文有界、origin 为业务确认。

允许的 kind/outcome 如下：

| kind | 允许 outcome | 读取含义 |
|---|---|---|
| confirmed_outcome | filled / paper_filled / reconciled_filled / cancelled / rejected | 当前有效的业务确认记录 |
| historical_advice | reviewed | 已审阅的历史建议，仍是 historical |
| preference | user_confirmed / reviewed | 已确认偏好，仍按 historical context 提供 |
| lesson | reviewed | 已审阅经验，不获得交易权限 |

AI 的 BUY/SELL、pending、model_suggestion 或 model_reasoning 不能通过该确认写入规则。模型工具只暴露 reader，没有任意写入或自证确认工具。Paper 业务 sink 由业务层在确认成交/对账完成后构造事件，并从订单生成内容，不读取模型推理文本。

**Python 事件对象本身不是认证证明。** 可信业务层负责验证订单、人工确认和身份；任意可信代码若谎报事件也能绕过调用约定，不能将类型/布尔值声称为密码学认证。当前生产入口的显式 `memory_owner` 是可信本机单用户 namespace，不能替代远程多用户身份认证。未指定 owner 的生产入口不授予记忆读取；不存在硬编码全局 owner。

## 记录、读取与冲突

记录保留 owner、event_id、source_run、写入时间、确认时间、确认 outcome、TTL、expires_at、kind、conflict_key、origin 和内容 fingerprint。ID 根据 owner 与 event_id 确定：相同事件/内容重复确认返回 duplicate，不刷新 TTL；相同事件 ID 改内容或 outcome 返回冲突错误，不覆盖旧记录。

读取前将 `controlled=True / user_id / verified=True / expires_at>now / confirmed_at<=now / origin` 直接传入 Chroma metadata 查询。同类结果按 `user + kind + conflict_key` 分组：

- 不能只在语义 top-k 中选择最新。即使语义查询只命中旧版本，也查询该组所有有效确认记录，再选择确认时间最新者。
- 时间较新的确认值替代旧值供当前读取，原记录与来源保留，不进行物理删除。
- 最新时间并列且内容/outcome 不同，输出显式 `conflict`，保留各来源正文，要求进一步处理；不把全部互斥值默认为可共同使用的事实。
- 历史建议和偏好不与 confirmed_outcome 混组；所有输出标记 `authority=historical_context_only`、`can_override_risk=False`。

记忆工具无权改变权限、审批或确定性资金限制。实际 Runner 测试确认：包含“跳过审批、扩大资金上限”的记忆正文留在 tool result，服务器资金约束保留在提供方 system；只读 reader 无法执行确认写入。此结果证明数据/规则角色隔离，不证明模型永远不受提示注入影响；最终交易仍须经过服务器风控与审批。

## 故障边界

| 情形 | 行为 |
|---|---|
| 无 read/write 权限、跨用户写入 | 存储访问之前拒绝 |
| 未确认、模型来源、非法 outcome、缺 provenance、无时区或未来确认 | 不写入 |
| 过期/旧无 scope/未 verified/其他用户记录 | 查询前过滤，返回前再次检查 |
| 写入操作之前报错 | MemoryStoreError，重放可正常写入 |
| 已写入但调用报错 | MemoryStoreError，重放按同 ID/fingerprint 返回 duplicate |
| 检索或同组读取失败 | MemoryStoreError，不能冒充空记忆 |
| rag_search 未注入服务器绑定 reader | fail closed，不退回旧全局 RagStore |
| 模型在 rag_search 参数里提供 user_id | schema 校验拒绝，不覆盖绑定身份 |

“verified”表示业务来源已确认，不表示每句自然语言经过事实裁判。内容生成必须来自可信业务事件或人工确认；本切片不自动总结 CoT，不把模型自行保存的建议升级为已确认事实。

## 实际 RED / GREEN

测试通过假 config 模块、临时真实 Chroma 数据目录及固定 embedding 隔离原配置/数据和外网，不下载默认 ONNX 模型。Chroma 为 1.5.9。实际使用干净 Python 3.12 trader 环境，所有记忆测试均执行，未因缺 Chroma 跳过。

实际命令：

```powershell
python -X utf8 -m ai_trader.offline_tests -k memory_lifecycle -q --tb=short
python -X utf8 -m ai_trader.offline_tests -k "memory_lifecycle or legacy_market_tools" -q --tb=short
```

隔离入口在临时工作目录运行完整测试收集，使用 `-k` 筛选；不能向它传临时工作目录中不存在的相对测试文件路径。

观察到的过程：

- 最小写后读/无权限测试：`1 failed, 1 error in 3.13s`，真实 RagStore 缺少 client 注入、记忆接口未实现。
- 完成最小闭环：`2 passed, 3 warnings in 1.25s`；固定 embedding 补充现有协议方法后，后续测试没有该弃用提醒。
- 冲突来源、工具绑定和 preference 的缺失行为：`6 failed, 28 passed, 168 deselected in 10.49s`。
- 实现对应规则：`34 passed, 168 deselected in 8.04s`。
- 联合旧行情工具测试：`36 passed, 167 deselected in 8.80s`。

新增 lifecycle、工具增量及测试的 Ruff 检查通过；lifecycle 与工具文件的 Mypy 检查为 `Success: no issues found in 2 source files`。旧 `rag_store.py` 整文件仍有 5 个已有 lint 问题；对 HEAD 同文件实际检查有 6 个，没有新增问题，未顺手修改未涉及的旧代码。

## 仍需明确的限制

- 同 ID/fingerprint 幂等针对正常同输入和串行重放。查询后 upsert 不是条件事务；并发相同 ID 的不同内容仍可能竞争，后续需要可信事件唯一性/跨 worker 协调或有条件的事件存储。
- 业务确认与 Chroma 写入没有事务性 outbox。失败需要业务审计、对账及重放；本切片不声称已有自动可靠投递。
- TTL 仅控制读取，不物理清除旧记录。大量来源会增加同组查询和上下文成本；后续需按实际负载评估分页/保留策略。
- 默认 Chroma embedding 首次使用可能下载模型；本次测试始终注入固定 embedding。真实生产启用前需要确认模型缓存、版本和离线策略。
- 真正多用户部署仍需把已认证 principal 绑定到 owner 和业务事件，不能让公开客户端自行选择 namespace。
- 固定 embedding 和 fake 模型只用于工程行为验收，不能证明语义检索质量、交易收益或跨任务记忆提高回答质量。
