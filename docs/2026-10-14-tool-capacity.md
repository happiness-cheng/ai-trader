# 超时后的后台容量与离线请求边界

2026-10-14第四批，源码最终228项通过、覆盖率87.43%，strict mypy51模块通过；本地Paper与最终wheel验收通过。未实盘或部署。

## 工具资源缺口

旧ToolRuntime每次调用创建独立单线程pool，future超时后shutdown(wait=False)，请求能及时返回，但工作线程继续运行。连续8次20ms超时实测8个handler都在途，共246.717ms；持续请求会累计线程。不是wait=False强制取消了工具。

最小修复是在既有调用路径上增加全进程4槽门禁，权限/调用级审批/参数校验仍先执行。future完成或未开始即取消时归还槽；返回timeout不归还。第5次立即返回capacity_unavailable，不排队；不同ToolRuntime实例不能通过重建绕过。pool提交失败归还槽并返回稳定错误。

同一8次探测修复后最多4个在途任务，前4个timeout、后4个capacity_unavailable，共120.372ms；handler结束后正常调用恢复。时间包含4次即时拒绝，不可称同等成功负载时延提升。新增公共接口测试先观察第5次错误timeout，再修复通过；另验证8次线程池启动失败不泄漏槽/不回显异常。

取舍：沿用既有每调用短生命周期pool，避免再加后台生命周期；全进程任务数有上限。代价是慢工作占满后快速失败，可用性降低；线程仍不能被强杀，进程退出仍可能等待底层handler。4只是当前保护默认，未基于真实负载调优，也不是跨进程全局上限。WRITE超时不代表副作用未发生，成交恢复仍依既有reservation/unknown/reconcile，不靠线程取消保证。

## 离线守卫

socket连接只限制远端地址，允许本机proxy会漏掉实际外部目标。新离线runner清除HTTP(S)/ALL代理，在HTTPTransport及AsyncHTTPTransport每一次实际传输检查目标，覆盖重定向；MockTransport没有真实网络，不受拦截，API契约正常运行。4个本机假代理用例在旧代码失败、修复后通过，代理仅回固定响应，从不转发。

## 验证与制品

```text
python -X utf8 -m ai_trader.offline_tests -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85 -q --tb=short
# 228 passed / 37.18s / coverage87.43%（门槛85%）
python -m mypy
# strict 51源文件通过
python -m ruff check src tests --extend-ignore I001,RUF100
python -m compileall -q src tests config.py
python scripts/check_wheel.py <第四批最终wheel>
# 51源码逐字节一致；不包含业务状态
```

新变更模块与新测试另按完整ruff规则通过；旧I001/RUF100风格保留。预导入coverage提示仍在，未宣称100%覆盖。

最终wheel SHA256：`69007b924572c19639a3c5793aba10dfc0b1538f27d4f398f033e4c3c30dfa87`。独立导入实际验证4超时+4容量拒绝、Bearer认证、Paper成交、CLI备份恢复/reconcile单笔、恢复后丢账ready503/写503。根目录旧应用仍不在包范围；真实行情/模型/交易所与长期SLA未验。
