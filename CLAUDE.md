# AI Trader - Claude 投资助手

## 工作模式
- **模拟盘（当前）**：全自动。有买入信号时直接用 ths_trader.py 下单，不用等用户确认
- **实盘**：用户说"进入实盘"后，切换为只给建议，用户自己下单
- 用户说"分析"→ 执行分析
- 用户说"开始监控"→ 后台持续监控，自动买卖
- 飞书推送：有操作时推飞书通知用户

## 完整分析管线（main.py 每5分钟一轮）
1. 拉行情数据 → logs/market/
2. 算技术指标 → logs/indicators/
3. 规则引擎初筛 → logs/signals/
4. 有信号 → 调MiMo API AI确认 → logs/ai_decisions/
5. AI确认买入 → 自动下单(ths_trader) → logs/trades/
6. 推飞书

## 读取日志（用户问"今天发生了什么"时）
```python
import trade_logger as tlog
logs = tlog.read_today_logs()  # 今日全部日志
trades = tlog.read_recent_trades()  # 最近交易
decisions = tlog.read_recent_ai_decisions()  # 最近AI决策
```
日志目录: C:\Users\陈独秀\ai-trader\logs\

## 用户信息
- 陈独秀，软件工程大二，炒股零经验
- 模拟盘 16万+ 虚拟资金
- 想在过程中学习（每笔分析附教学解释）

## 数据获取（用 curl，不用 requests，代理TLS问题）
- 历史K线：新浪财经（market_data.py 的 get_stock_history）
- 实时行情/大盘：东方财富（market_data.py 的 get_realtime_quote）
- 技术指标：ta 库自动计算（get_technical_indicators）

## 交易执行
用 ths_trader.py 的 THSTrader 类：
```python
from ths_trader import THSTrader
t = THSTrader()
t.connect()
t.buy('600519', 100)  # 买入100股
t.sell('600519', 100) # 卖出100股
t.get_balance()       # 查资金
t.get_position()      # 查持仓
```

## Python 路径
D:/Users/陈独秀/AppData/Local/Programs/Python/Python314/python.exe

## 风控规则
- 单只止损: 5%，止盈: 15%
- 最大持仓: 8只，单只仓位: 12%
- 交易时间: 9:30-11:30, 13:00-15:00

## 关注列表
600519(贵州茅台), 300750(宁德时代), 601318(中国平安), 000858(五粮液), 000001(平安银行)

## 飞书推送
- webhook: 从环境变量 `FEISHU_WEBHOOK` 读取，参见 `env.example`
- 代码: notifier.py 的 send(title, content)

## 同花顺 UI 映射（v9.50.90）
- 交易窗口: title='网上股票交易系统5.0'
- 买入: Edit(id=1032=代码, 1034=数量), Button(id=1006=买入)
- 卖出: 点击 TreeItem '卖出[F2]'
- 资金: 先点"查询[F4]"→"资金股票"再读取
