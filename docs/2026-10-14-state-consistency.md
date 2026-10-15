# SQL与Paper ledger一致性故障闭环

本页为第三批历史验收；第四批新增工具容量/离线边界后，最新测试数和wheel见[第四批说明](2026-10-14-tool-capacity.md)，下文hash不代表当前源码构建。

2026-10-14：最终源码222项通过，覆盖率87.63%，strict mypy51模块通过。所有状态均为本轮合成临时目录；不实盘，不操作原库。上一批215项结果保留历史。

## 真正复现的问题

完成一次Paper成交后停服务，把本轮ledger改写为[]，保留SQL订单。重启会从空ledger重建空内存状态，因此原健康检查“两者相等”返回ready200；服务端持仓被重置，第二个run又能approve200。另发现两个proposal共享同一order_id仍能被载入，订单查询身份出现歧义。

## 修复与取舍

read_ledger和Broker载入共用身份校验，proposal_id和order_id都必须唯一。实际readiness及每个写请求核验磁盘/活动ledger一致，并核验SQL所有已确认订单的身份和完整payload存在于ledger。

SQL未确认的ledger订单不能直接当作垃圾删掉：响应丢失/崩溃时它可能是真实已执行副作用。它必须对应SQL已有reserved/execution_unknown保留及交易要素，才能保留reconcile通道。孤儿、确认事实缺失或数量等不匹配均拒绝业务写入，保持health存活和认证只读诊断；不从SQL自动补造broker成交事实。已确认事实丢失需要可信备份恢复。

备份、恢复输入及复制后验证复用同一规则。即使manifest哈希合法，跨存储状态不一致也拒绝。结构/hash/integrity单独成功不再被当作整个业务状态健康。

## 验证结果

- 缺ledger重启、SQL订单篡改、重复身份、孤儿未确认单：503/拒绝，合法unknown仍能对账成单。
- 20个待审批run，8个并发TestClient客户端：1次200、19次风控409，始终1单，完成后ready200；总628.426ms、P50 187.540ms、P95 317.710ms。仅本机进程内Paper/固定行情和模型fixture，不是网络/真实模型/生产SLA。
- 最终wheel独立导入，完成Bearer→分析→审批→CLI备份恢复→reconcile一单；清空恢复目录的测试ledger并重启，ready503且新写503。

最终wheel SHA256：`388369ce0b3e3583a1c8758d91a66801c784c43520b3f7a74ea027efba4d2628`，51源码文件逐字节匹配。仍不包含根目录旧版主应用。每次写入增加只读一致性检查，有性能开销；JSON ledger及全量订单核验适合当前本地Paper范围，没有声明长期大账本吞吐或跨存储原子提交。

```text
python -X utf8 -m ai_trader.offline_tests -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85 -q --tb=short
python -m mypy
python -m ruff check src tests --extend-ignore I001,RUF100
python -m compileall -q src tests config.py
python scripts/check_wheel.py <本批wheel绝对路径>
```

既有导入/noqa风格保留，新保护模块另按完整规则检查。覆盖率入口预导入提示已记录，未虚称全覆盖。源码、制品和本机并发通过；远端CI、云部署、真实行情/模型和实盘未验收。
