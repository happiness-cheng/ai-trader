# 项目证据：Agent、执行与恢复

主线是`src/ai_trader` runtime，不混入根目录旧交易应用、预测/微调实验。这里用具体触发、处理方式、对照和剩余边界说明实现；fixture质量与真实模型质量分开。

## 1. 从模型循环到可执行工作流

`paper_service → TradingWorkflow → ProductionAgentRunner → get_quote → BusinessDecision/TradeProposal → RiskGate → waiting_approval → approve → ExecutionCoordinator → PaperBroker`。

模型/行情是固定fixture，但运行的循环、类型验证、SQL、审批和Paper副作用是真代码。缺证据、工具失败、上下文超限有明确失败原因，不自动成交；拒绝cancelled后不能再批准，重复批准返回同订单。[工作流测试](../tests/integration/test_trade_workflow.py)、[HTTP入口测试](../tests/integration/test_paper_service.py)。

这是比“模型输出BUY文本”更完整的业务边界，代价是持久提案、状态与审批复杂度；默认Paper只有get_quote，不声称五工具已装进该服务或自动多Agent协作。

## 2. 上下文：整轮裁剪与结构化预览的适用条件

[实际ContextBuilder/Fake provider对照](evidence/context-candidates.json)，预算2500**序列化字符**：

| 样本 | disabled | mechanical整轮删除 | structured结构化预览 |
|---|---|---|---|
| 旧历史很大，当前观察小；原13765 | 拒发，provider0 | 710，删1轮 | 1450，保留压缩预览 |
| 最新观察很大且含风险；原17567 | 拒发，provider0 | 仍拒发，provider0 | 1267，风险原字段保留 |

不截断原JSON，不拆tool_use/tool_result配对；风险字段仍是unverified观察，审批读取服务器真值。保留不下时拒绝比静默截断更保守，代价是有些任务无法继续。预览有长度/hash，不提供全文回读地址；没有测真实LLM决策收益或token节省。[实现](../src/ai_trader/agents/context.py)、[完整对照](../tests/unit/test_context_builder.py)。

TaskInstructionSet按需说明使analyse请求888→768、review888→557字符；合成3 schema手工选2为1352→1149、1141→952。都是显式候选，不等于自动工具发现；实际Paper默认未采用这些候选。

## 3. 成交响应丢失：先对账，不盲重试

Paper ledger写成后注入异常，SQL尚未确认。reservation保留执行身份；重启后查broker，找到同proposal订单才登记成功，没找到继续未知。相比一律重试牺牲可用性，避免不确定窗口直接产生第二次副作用。

[正常演示](evidence/paper-normal.json)和[丢响应演示](evidence/paper-response-loss.json)均为1单、重复审批返回同单；后者确实重建仓库/Broker并恢复。原始事件和状态进入测试日志，不用虚构外部券商保证。

[20run/8客户端并发](evidence/paper-concurrency.json)得到1次200、19次风控409、1单。它是不同提案的仓位保护，不是20次成功吞吐，也不等于同提案并发幂等。[执行占位/对账](../src/ai_trader/execution/coordinator.py)、[执行前风险重读](../tests/integration/test_trade_workflow.py)。

## 4. 缺账恢复：hash正确仍可能业务不一致

旧readiness把ledger[]重建为空持仓，与磁盘相等，SQL订单却仍在，因此错误ready200并可继续买入。现在SQL确认订单必须与ledger完整一致；ledger额外单只允许与合法reserved/unknown提案及要素匹配。重复proposal/order身份、孤儿单、数量/价格不符、合法hash但混杂状态都拒绝。

同一业务规则用于readiness、写请求和备份恢复：缺账503并拒写，合法未知成交仍可对账。不从SQL自动补造broker事实；hash保护完整性，不证明备份来源可信。代价是全量核验、停服务备份和恢复介入；不是在线灾备RTO或大账本吞吐保证。[故障测试](../tests/integration/test_paper_state_consistency.py)、[状态规则](../src/ai_trader/state_health.py)、[备份入口](../src/ai_trader/state_backup.py)。

## 5. timeout不等于工作停止

原先每调用建pool，wait=False仅让请求返回；8次超时留下8个活跃handler。[修复前](evidence/tool-capacity-before.json)与[修复后](evidence/tool-capacity-after.json)同探测：8次调用变为4 timeout+4 capacity_unavailable，最多4在途；handler实际结束/未开始即取消才归还槽，启动失败也归还。

246.717→120.372ms包含快速拒绝，不能叫成功吞吐提升。槽限单进程handler，不能强杀线程/底层I/O，也不跨进程。写工具超时仍按未知副作用处理。[实现](../src/ai_trader/tools/runtime.py)、[公共接口测试](../tests/unit/test_tool_capacity.py)。

## 6. 记忆与MCP不是另一条授权通道

确认记忆需要业务/人工确认、owner、event/source、确认时间和TTL。语义只命中旧项时，读取有效冲突组选择最新；同时间内容冲突明确返回conflict。TTL只做读取过滤，没有物理删除。确认事件需由可信入口创建，无outbox/并发CAS保证；写失败记录为待处理，不重做已成交交易。[记忆生命周期](../src/ai_trader/memory/lifecycle.py)、[成交/对账入口测试](../tests/integration/test_paper_memory_event.py)。

MCP官方SDK1.30 stdio跑通initialize/list/call；允许fixture quote，保留`000001`前导零，拒绝非法symbol、多余权限字段和直接写工具调用。annotations不是授权；allowlist/effect/risk/approval及原Runtime权限共同校验。[stdio测试](../tests/integration/test_readonly_mcp.py)。没有公网OAuth或真实行情质量结论。

## 验证与复现

2026-10-15 Windows / CPython3.12.15：228 passed、coverage87.43%（门槛85%）、mypy51模块、ruff通过，构建wheel并逐字节核对51源码模块；实际运行正常/丢响应Paper和runtime dry-run。命令/范围记录于[verification.json](evidence/verification.json)。

```text
python -X utf8 -m ai_trader.offline_tests --verify-isolation
python -X utf8 -m ai_trader.offline_tests -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85 -q
python -m mypy
python -m ruff check src tests --extend-ignore I001,RUF100
python -X utf8 -m ai_trader.paper_demo
python -X utf8 -m ai_trader.paper_demo --response-loss
python -m ai_trader.runtime --dry-run
```

安装见[README](../README.md)。wheel仅覆盖src runtime；单目录单进程串行写，未实现部分成交、结算、跨账户资金预占、远程多租户或线上SLA。真实LLM交易质量/收益仍未验证；工程回归与fixtureusage不能冒充这些结果。
