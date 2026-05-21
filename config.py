"""全局配置"""
import os

# === 同花顺配置 ===
THS_DIR = r'D:\同花顺\同花顺'
THS_EXE = os.path.join(THS_DIR, 'hexin.exe')
THS_XIADAN = os.path.join(THS_DIR, 'xiadan.exe')
TRADE_WINDOW_TITLE = '网上股票交易系统5.0'

# === 资金配置 ===
TOTAL_CAPITAL = 164516.53  # 模拟盘初始资金（会动态读取）
MAX_POSITIONS = 8  # 最大持仓数
POSITION_RATIO = 0.12  # 单只仓位占总资金比例（12%）
STOP_LOSS = 0.05  # 初始止损 5%
TAKE_PROFIT = 0.15  # 止盈 15%
TRAILING_GAIN_STEP = 0.05  # 跟踪止损步长：每涨5%

# === 交易时间 ===
TRADE_START = '09:30'
TRADE_END = '15:00'
MORNING_END = '11:30'
AFTERNOON_START = '13:00'

# === AI Trader API 配置 ===
# 不在这里缓存，每次调用时实时读取环境变量
# 这样你用 cc-switch 切模型后，AI Trader 自动跟着切
def get_api_config():
    """实时读取当前 API 配置（跟随 cc-switch 切换）"""
    return {
        'base_url': os.environ.get('ANTHROPIC_BASE_URL', 'https://token-plan-cn.xiaomimimo.com/anthropic'),
        'api_key': os.environ.get('ANTHROPIC_AUTH_TOKEN', os.environ.get('ANTHROPIC_API_KEY', '')),
        'model': os.environ.get('ANTHROPIC_MODEL', 'mimo-v2-pro').split('[')[0],
    }

# === Web仪表盘 ===
DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 8501

# === 数据目录 ===
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# === 技术指标参数 ===
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
MA_SHORT = 5
MA_LONG = 20
