# AI Trader

> **Rule Engine + LLM Agent 双层决策 — A 股自动分析+下单全闭环系统**

Rule Engine 每 5 分钟扫描全市场 → 筛出候选 → MiMo API 深度分析 → pywinauto 自动下单同花顺模拟盘 → 飞书推送通知到手机。端到端无人值守。

## 架构

```mermaid
graph LR
    A[新浪财经/东方财富 API] --> B[数据采集层]
    B --> C[Rule Engine 技术筛选]
    C -->|候选 5-10 只| D[LLM Agent 深度分析]
    D -->|买/卖 + 置信度| E[pywinauto 下单]
    E --> F[飞书推送通知]
    D --> G[Web 仪表盘]
    B --> G

    style C fill:#4CAF50,color:#fff
    style D fill:#2196F3,color:#fff
    style E fill:#FF9800,color:#fff
```

**双层决策设计**：全市场 5000+ 股票，规则引擎 < 1s/只快速筛出信号，LLM 只对候选做 ~10s 深度分析。API 调用量减少 99%，决策质量不降。

## 技术栈

| 模块 | 技术 | 说明 |
|------|------|------|
| 数据采集 | curl + subprocess | 绕过 Windows 代理 TLS 兼容问题 |
| 技术指标 | ta (Python) | MACD/RSI/布林带等 40+ 指标 |
| AI 决策 | MiMo API (Claude 兼容) | 流式输出，带历史准确率自修正 |
| GUI 自动化 | pywinauto + UIA | 操作同花顺原生窗口，无需券商 API |
| Web 仪表盘 | FastAPI + 原生 HTML/JS | 轻量异步，实时监控 |
| 推送通知 | 飞书自定义机器人 Webhook | 免费、富文本卡片、手机即时通知 |
| 图表 | matplotlib (Agg 后端) | 无 GUI 服务器端生成 K 线图 |

## 核心特性

- **双层决策架构**：Rule Engine（技术指标筛选）+ LLM Agent（AI 深度判断），成本低、速度快
- **自我学习机制**：记录每次 AI 预测 → 自动对比实际结果 → 注入下次分析 prompt，准确率持续优化
- **全链路日志**：JSONL 格式，按日期/类别归档，方便复盘和调试
- **Web 仪表盘**：实时查看持仓、分析记录、K 线图
- **飞书即时通知**：买入/卖出/止损止盈/日报，手机实时收到
- **开机自启**：Windows Task Scheduler 调度，无需人工干预

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/happiness-cheng/ai-trader.git
cd ai-trader

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp env.example .env
# 编辑 .env，填入 MiMo API Key 和飞书 Webhook

# 4. 启动（确保同花顺已打开交易面板）
start.bat
```

## 目录结构

```
ai-trader/
├── main.py              # 调度引擎（定时触发各阶段）
├── market_data.py       # 数据采集（新浪财经/东方财富）
├── strategy.py          # Rule Engine 技术指标筛选
├── ai_analyzer.py       # LLM Agent 深度分析
├── ths_trader.py        # pywinauto 同花顺 GUI 下单
├── notifier.py          # 飞书推送通知
├── dashboard.py         # Web 仪表盘
├── stock_pool.py        # 自选股管理
├── prediction_tracker.py # 预测准确率追踪
├── chart_gen.py         # K 线图生成
├── config.py            # 配置管理
├── requirements.txt
├── env.example          # 环境变量模板
├── ARCHITECTURE.md      # 详细技术架构文档
└── start.bat            # 一键启动脚本
```

## License

MIT
