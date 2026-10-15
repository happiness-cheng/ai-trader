# Paper生产模式保护与离线状态恢复

本轮完成本地工程验收，暂不上线、不实盘。最新215 passed；mypy strict覆盖51个源模块；覆盖率87.65%（门槛85%）。测试状态都是合成临时目录，没有操作原workflow数据库或凭据。

## 认证与就绪

`create_paper_app(production=True, operator_token=...)`必须显式提供至少32字符、不含空白的操作者token；工厂检查失败时不创建状态。除health/ready外所有路由要求Bearer认证，以摘要恒时比较；X-Reviewer-Id仅是认证操作者提交的审计标签，不是身份认证。仍只接收可信loopback来源，单writer和串行写请求约束保留，不能当作公网多租户服务。

已有CLI可加`--production --operator-token-file <已有文件>`；只读取文件，不生成、修改或输出凭据。当前默认provider/行情是offline fixture；外部provider/model必须由调用方明确注入，认证不代表市场行情已接通。

`/health`存活契约不变；`/ready`检查SQLite六张业务表及磁盘ledger与活动Paper订单一致，未启动、schema缺失、ledger损坏/丢失/不一致时503。不会借健康检查调用模型/实盘。只读SQLite连接显式close，已复现并修复上下文退出不关闭连接的问题。

## 备份与恢复

必须停服务；拿与服务相同的状态锁，运行中拒绝备份。backup要求全新目标目录，restore允许空目标。SQLite backup API读取已提交WAL数据，原库只读；仅workflow.db及可选paper-ledger.json进入备份，manifest保存SHA256。恢复校验白名单、目录/符号链接、哈希、SQLite integrity和ledger结构后再创建目标，复制后再校验哈希/完整性，避免输入在复制期间变化却误报成功。目标排他创建/写入，已验证预检查后其他进程创建目标不会覆盖其数据。

```text
python -m ai_trader.state_backup backup --source <停止后的绝对状态目录> --destination <全新备份目录>
python -m ai_trader.state_backup restore --source <完整备份目录> --destination <空恢复目录>
```

最终wheel独立导入后，实际跑通认证→分析→人工审批→Paper成交→CLI备份→空目录恢复→reconcile，始终一笔订单。源码测试另覆盖响应丢失后execution_unknown状态的备份恢复：必须先对账，不能直接再审批执行。

哈希提供完整性，不证明备份来源可信；仅恢复可信离线备份，不做在线备份、跨机器时间回溯保证或自动删除失败残留。复制/磁盘失败后可能留下不完整新目标；操作方须选新的空目录，不能直接启动失败目标。记忆成交写入失败仍是审计+人工reconcile重放，没有伪称自动可靠outbox。

## 取舍

保留现有审批、风险重检、proposal_id幂等和未知执行对账。参考[LangGraph Checkpointers §Durability modes/Fault-tolerance](https://docs.langchain.com/oss/python/langgraph/checkpointers)与[Functional API §Idempotency](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/functional-api.mdx)：checkpoint不保证外部执行exactly-once。为迁框架重写已验收状态机风险大，本轮加强既有边界。

## 制品与验证

runtime wheel包含51个当前Python源码文件，逐个字节与工作区核对；没有根目录旧版交易应用、个人数据或凭据。最终制品SHA256：`08a2cc8716c0f1a905f8848c9c72335c2073bff38e7d12053e1d1f73fda157b4`。不能把wheel范围描述为旧版主应用整体可独立安装。

从源码仓库运行：

```text
python -m ai_trader.offline_tests --verify-isolation
python -X utf8 -m ai_trader.offline_tests -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85 -q --tb=short
python -m mypy
python -m compileall -q src tests config.py
python -m ruff check src tests --extend-ignore I001,RUF100
python -m ruff check src/ai_trader/paper_service.py src/ai_trader/state_health.py src/ai_trader/state_backup.py
python scripts/check_wheel.py <wheel绝对路径>
```

全仓库lint保留既有导入顺序/noqa样式，新运行保护模块额外严格检查；没有用自动格式化覆盖其他用户修改。coverage提示入口包在测量前导入，总覆盖率按实际输出记录，未虚称100%。CI定义配置这些门禁及optional MCP/memory依赖；未提交/推送，远端CI未运行。外网模型请求预算本轮为0，模型效果仍看此前的实测记录。
