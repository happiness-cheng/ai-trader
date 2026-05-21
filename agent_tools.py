"""Agent 工具注册表
定义 AI Agent 可调用的所有工具，支持两种模式：
1. native: Claude 原生 tools API（如果代理支持）
2. prompt: 在 prompt 中描述工具，AI 返回 TOOL_CALL 格式（兜底方案）
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import config
import market_data as md
import strategy
import notifier
import price_alert

logger = logging.getLogger(__name__)


# ========== 数据结构 ==========

@dataclass
class ToolParam:
    """工具参数定义"""
    name: str
    type: str  # "string", "number", "boolean", "array"
    description: str
    required: bool = True
    enum: list = None


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: list
    handler: Callable

    def to_claude_schema(self) -> dict:
        """转 Claude API tools 格式"""
        props = {}
        required = []
        for p in self.parameters:
            prop = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }

    def to_prompt_desc(self) -> str:
        """转 prompt 文本格式"""
        params = []
        for p in self.parameters:
            marker = "*" if p.required else ""
            params.append(f"{p.name}({p.type}{marker}: {p.description})")
        param_str = ", ".join(params) if params else "无参数"
        return f"- {self.name}: {self.description}\n  参数: {param_str}"


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef):
        self._tools[tool.name] = tool

    def get(self, name: str):
        return self._tools.get(name)

    def execute(self, name: str, params: dict) -> dict:
        """执行工具，返回 {"tool_name": name, "result": ...}"""
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"未知工具: {name}"}
        try:
            # 过滤掉不在参数定义中的字段
            valid_params = {}
            for p in tool.parameters:
                if p.name in params:
                    valid_params[p.name] = params[p.name]
            result = tool.handler(**valid_params)
            return {"tool_name": name, "result": result}
        except Exception as e:
            logger.error(f"工具执行失败 [{name}]: {e}")
            return {"tool_name": name, "error": str(e)}

    def to_claude_tools(self) -> list:
        """所有工具的 Claude API 格式"""
        return [t.to_claude_schema() for t in self._tools.values()]

    def to_prompt_text(self) -> str:
        """所有工具的 prompt 文本"""
        return "\n".join(t.to_prompt_desc() for t in self._tools.values())


# ========== 工具处理函数（包装现有模块） ==========

def _tool_get_stock_history(code: str, days: int = 120):
    """获取历史K线数据"""
    df = md.get_stock_history(code, days=days)
    if df.empty:
        return {"error": f"{code} 数据不足"}
    # 只返回最近5天的摘要
    recent = df.tail(5)
    return {
        "code": code,
        "total_days": len(df),
        "recent": [
            {"date": str(row['date'].date()), "close": round(row['close'], 2),
             "pct_change": round(row.get('pct_change', 0), 2)}
            for _, row in recent.iterrows()
        ],
    }


def _tool_get_realtime_quote(code: str):
    """获取实时行情"""
    result = md.get_realtime_quote(code)
    if not result:
        return {"error": f"获取 {code} 行情失败"}
    return result


def _tool_get_technical_indicators(code: str):
    """获取技术指标（自动获取历史数据并计算）"""
    df = md.get_stock_history(code, days=120)
    if len(df) < 30:
        return {"error": f"{code} 数据不足"}
    indicators = md.get_technical_indicators(df)
    # 返回关键指标
    return {
        "code": code,
        "close": indicators.get('close'),
        "ma5": indicators.get('ma5'),
        "ma20": indicators.get('ma20'),
        "rsi": indicators.get('rsi'),
        "macd_hist": indicators.get('macd_hist'),
        "macd_golden_cross": indicators.get('macd_golden_cross'),
        "macd_death_cross": indicators.get('macd_death_cross'),
        "vol_ratio": indicators.get('vol_ratio'),
        "pct_change": indicators.get('pct_change'),
        "bb_upper": indicators.get('bb_upper'),
        "bb_lower": indicators.get('bb_lower'),
        "price_above_ma5": indicators.get('price_above_ma5'),
        "price_above_ma20": indicators.get('price_above_ma20'),
    }


def _tool_get_market_overview():
    """获取大盘概况"""
    return md.get_market_overview()


def _tool_get_hot_sectors():
    """获取热门板块"""
    return md.get_hot_sectors()


def _tool_get_fundamentals(code: str):
    """获取基本面数据"""
    return md.get_fundamentals(code)


def _tool_rule_signal(code: str):
    """获取规则信号（自动获取数据并计算）"""
    df = md.get_stock_history(code, days=120)
    if len(df) < 30:
        return {"error": f"{code} 数据不足"}
    indicators = md.get_technical_indicators(df)
    signal = strategy.rule_signal(indicators)
    signals = []
    if indicators.get('macd_golden_cross'): signals.append('MACD金叉')
    if indicators.get('rsi_oversold'): signals.append(f'RSI超卖({indicators["rsi"]:.0f})')
    if indicators.get('ma_golden_cross'): signals.append('均线金叉')
    if indicators.get('volume_surge'): signals.append('放量')
    if indicators.get('macd_death_cross'): signals.append('MACD死叉')
    return {"code": code, "signal": signal, "signals": signals}


def _tool_check_stop_loss():
    """检查所有持仓的止损状态"""
    positions = strategy.get_local_positions()
    if not positions:
        return {"message": "无持仓"}

    current_prices = {}
    for pos in positions:
        quote = md.get_realtime_quote(pos['code'])
        if quote:
            current_prices[pos['code']] = quote['price']

    results = strategy.check_stop_loss(positions, current_prices)
    return [
        {"name": r['name'], "code": r['code'],
         "change_pct": r['change_pct'], "reason": r['reason']}
        for r in results
    ] if results else {"message": "未触发止损"}


def _tool_check_market_crash():
    """检查大盘是否暴跌"""
    overview = md.get_market_overview()
    level = strategy.check_market_crash(overview)
    return {"crash_level": level, "overview": overview}


def _tool_rag_search(query: str, top_k: int = 3):
    """RAG语义检索历史经验"""
    try:
        from rag_store import get_store
        store = get_store()
        results = store.search(query, top_k=top_k)
        return [
            {"collection": r['collection'], "document": r['document'][:200],
             "date": r['metadata'].get('date', '')}
            for r in results
        ] if results else {"message": "未找到相关经验"}
    except ImportError:
        return {"error": "RAG模块未安装"}


def _tool_send_notification(title: str, content: str):
    """发送飞书通知"""
    notifier.send(title, content)
    return {"message": f"已发送: {title}"}


def _tool_add_price_alert(code: str, name: str, price: float,
                          direction: str, reason: str):
    """设置价格预警"""
    price_alert.add_alert(code, name, price, direction, reason)
    return {"message": f"已设置预警: {name} {direction} {price}"}


def _tool_get_watchlist():
    """获取关注列表"""
    return strategy.get_watchlist()


def _tool_get_positions():
    """获取当前持仓"""
    return strategy.get_local_positions()


# ========== 注册默认工具 ==========

def create_default_registry() -> ToolRegistry:
    """创建并注册所有默认工具"""
    registry = ToolRegistry()

    registry.register(ToolDef(
        name="get_stock_history",
        description="获取股票历史K线数据（最近N天的收盘价和涨跌幅）",
        parameters=[
            ToolParam("code", "string", "股票代码，如 600519"),
            ToolParam("days", "number", "获取天数，默认120", required=False),
        ],
        handler=_tool_get_stock_history,
    ))

    registry.register(ToolDef(
        name="get_realtime_quote",
        description="获取股票实时行情（当前价格、涨跌幅、成交量等）",
        parameters=[ToolParam("code", "string", "股票代码")],
        handler=_tool_get_realtime_quote,
    ))

    registry.register(ToolDef(
        name="get_technical_indicators",
        description="获取股票技术指标（MA5/MA20、RSI、MACD、布林带等）",
        parameters=[ToolParam("code", "string", "股票代码")],
        handler=_tool_get_technical_indicators,
    ))

    registry.register(ToolDef(
        name="get_market_overview",
        description="获取大盘概况（上证指数、深证成指、创业板指的涨跌幅）",
        parameters=[],
        handler=_tool_get_market_overview,
    ))

    registry.register(ToolDef(
        name="get_hot_sectors",
        description="获取当前热门板块排名（按涨幅排序的行业板块）",
        parameters=[],
        handler=_tool_get_hot_sectors,
    ))

    registry.register(ToolDef(
        name="get_fundamentals",
        description="获取股票基本面数据（市盈率PE、总市值、换手率）",
        parameters=[ToolParam("code", "string", "股票代码")],
        handler=_tool_get_fundamentals,
    ))

    registry.register(ToolDef(
        name="rule_signal",
        description="获取股票的规则信号（基于MACD/RSI/均线的买卖信号判断）",
        parameters=[ToolParam("code", "string", "股票代码")],
        handler=_tool_rule_signal,
    ))

    registry.register(ToolDef(
        name="check_stop_loss",
        description="检查所有持仓是否触发止损（跟踪止损机制）",
        parameters=[],
        handler=_tool_check_stop_loss,
    ))

    registry.register(ToolDef(
        name="check_market_crash",
        description="检查大盘是否出现暴跌风险（返回normal/caution/warning/crash）",
        parameters=[],
        handler=_tool_check_market_crash,
    ))

    registry.register(ToolDef(
        name="rag_search",
        description="从历史交易经验中语义检索相关内容（RAG检索）",
        parameters=[
            ToolParam("query", "string", "搜索关键词，如 RSI超卖买入"),
            ToolParam("top_k", "number", "返回结果数，默认3", required=False),
        ],
        handler=_tool_rag_search,
    ))

    registry.register(ToolDef(
        name="send_notification",
        description="发送飞书通知消息给用户",
        parameters=[
            ToolParam("title", "string", "消息标题"),
            ToolParam("content", "string", "消息内容（支持markdown）"),
        ],
        handler=_tool_send_notification,
    ))

    registry.register(ToolDef(
        name="add_price_alert",
        description="设置价格预警（当价格达到目标时触发通知）",
        parameters=[
            ToolParam("code", "string", "股票代码"),
            ToolParam("name", "string", "股票名称"),
            ToolParam("price", "number", "目标价格"),
            ToolParam("direction", "string", "方向: above(上穿) 或 below(下破)", enum=["above", "below"]),
            ToolParam("reason", "string", "预警原因"),
        ],
        handler=_tool_add_price_alert,
    ))

    registry.register(ToolDef(
        name="get_watchlist",
        description="获取用户的关注股票列表",
        parameters=[],
        handler=_tool_get_watchlist,
    ))

    registry.register(ToolDef(
        name="get_positions",
        description="获取用户的当前持仓列表",
        parameters=[],
        handler=_tool_get_positions,
    ))

    return registry


# ========== Prompt 模式的工具调用解析 ==========

def parse_tool_calls(text: str) -> list:
    """从AI回复中解析 TOOL_CALL 格式的工具调用

    支持格式:
    TOOL_CALL: tool_name(param1="value1", param2=123)
    TOOL_CALL: tool_name()
    """
    calls = []
    for line in text.split('\n'):
        line = line.strip()
        if not line.startswith('TOOL_CALL:'):
            continue

        call_str = line[len('TOOL_CALL:'):].strip()
        # 提取工具名和参数
        match = re.match(r'(\w+)\((.*)\)$', call_str, re.DOTALL)
        if not match:
            logger.warning(f"无法解析工具调用: {call_str}")
            continue

        tool_name = match.group(1)
        params_str = match.group(2).strip()

        params = {}
        if params_str:
            # 解析 key="value" 或 key=123 格式的参数
            # 先尝试 ast.literal_eval 包装成 dict
            try:
                # 把 key=value 格式转成 "key": value 的 JSON 格式
                json_str = '{' + re.sub(r'(\w+)=', r'"\1":', params_str) + '}'
                params = json.loads(json_str)
            except (json.JSONDecodeError, ValueError):
                # fallback: 逐个提取
                for kv in re.findall(r'(\w+)=(?:"([^"]*)"|(\d+\.?\d*)|(\w+))', params_str):
                    key = kv[0]
                    val = kv[1] or kv[2] or kv[3]
                    # 尝试转数字
                    try:
                        val = int(val)
                    except ValueError:
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                    params[key] = val

        calls.append({"name": tool_name, "params": params})

    return calls


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    registry = create_default_registry()
    print(f"已注册 {len(registry._tools)} 个工具:\n")
    print(registry.to_prompt_text())

    print("\n=== 测试执行 ===")
    result = registry.execute("get_market_overview", {})
    print(f"get_market_overview: {json.dumps(result, ensure_ascii=False, indent=2)[:300]}")

    print("\n=== 测试 TOOL_CALL 解析 ===")
    test_text = '我来查看一下大盘。\nTOOL_CALL: get_market_overview()\n然后再看看茅台的技术指标。\nTOOL_CALL: get_technical_indicators(code="600519")'
    calls = parse_tool_calls(test_text)
    for c in calls:
        print(f"  解析到: {c['name']}({c['params']})")
