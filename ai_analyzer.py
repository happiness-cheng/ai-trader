"""AI分析引擎
直接调用 Claude API（绕过 SDK 兼容问题）
"""
import json
import logging
import requests

import config

logger = logging.getLogger(__name__)


def _call_claude(prompt, max_tokens=2000):
    """直接调用 Claude Messages API"""
    url = f"{config.API_BASE_URL}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config.API_KEY,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    # 提取文本内容
    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"]
    return ""


ANALYSIS_PROMPT = """你是一位专业的A股技术分析师和投资顾问。用户是零经验的大学生，正在学习炒股。
你需要分析行情数据并给出明确的买卖建议，同时解释你的分析逻辑以便用户学习。

## 分析要求
1. 基于技术指标判断当前趋势（上升/下降/震荡）
2. 识别关键信号（金叉/死叉/超买超卖/放量缩量）
3. 给出明确建议：买入 / 卖出 / 持有 / 观望
4. 解释你的分析思路（教学性质，让用户理解为什么）
5. 评估风险等级：低/中/高

## 股票信息
{stock_info}

## 技术指标
{indicators}

## 大盘概况
{market_overview}

## 热门板块
{hot_sectors}

请以 JSON 格式返回分析结果，格式如下：
{{
    "stock_code": "股票代码",
    "stock_name": "股票名称",
    "current_price": 当前价格,
    "trend": "上升/下降/震荡",
    "recommendation": "买入/卖出/持有/观望",
    "confidence": 0.0-1.0,
    "risk_level": "低/中/高",
    "reasoning": "分析思路（中文，通俗易懂，适合初学者）",
    "key_signals": ["信号1", "信号2"],
    "suggested_action": "具体操作建议",
    "stop_loss_price": 止损价,
    "take_profit_price": 止盈价,
    "learning_points": ["知识点1", "知识点2"]
}}

只返回 JSON，不要其他内容。"""


SCREENING_PROMPT = """你是一位A股选股专家。请从以下股票列表中，基于技术指标筛选出最有潜力的股票。

## 候选股票技术指标
{candidates}

## 大盘概况
{market_overview}

## 选股要求
1. 选出最多5只股票
2. 优先选择：MACD金叉/即将金叉、RSI在30-60区间、放量突破
3. 回避：RSI超买(>70)、MACD死叉、缩量下跌
4. 考虑大盘环境

请以 JSON 数组格式返回，每个元素格式：
[
    {{
        "stock_code": "代码",
        "stock_name": "名称",
        "score": 0-100,
        "reason": "选择理由（中文）",
        "risk_level": "低/中/高",
        "entry_price": 建议入场价,
        "stop_loss": 止损价,
        "take_profit": 止盈价
    }}
]

只返回 JSON 数组，不要其他内容。"""


def _parse_json(text):
    """解析可能被 markdown 包裹的 JSON"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text.strip())


def analyze_stock(stock_info, indicators, market_overview=None, hot_sectors=None):
    """分析单只股票，返回买卖建议"""
    try:
        # 加入历史预测表现（让AI知道自己过去准不准）
        from prediction_tracker import get_prompt_context, log_prediction
        history = get_prompt_context()

        # 加入历史经验教训（从知识库读取）
        try:
            from kb_integration import get_lessons_prompt_context
            lessons = get_lessons_prompt_context()
        except Exception:
            lessons = ''

        prompt = ANALYSIS_PROMPT.format(
            stock_info=json.dumps(stock_info, ensure_ascii=False, indent=2),
            indicators=json.dumps(indicators, ensure_ascii=False, indent=2),
            market_overview=json.dumps(market_overview or {}, ensure_ascii=False, indent=2),
            hot_sectors=json.dumps(hot_sectors or [], ensure_ascii=False, indent=2),
        )

        if history:
            prompt += history
        if lessons:
            prompt += lessons

        text = _call_claude(prompt, max_tokens=4000)
        result = _parse_json(text)
        logger.info(f"AI分析完成: {result.get('stock_code')} -> {result.get('recommendation')} (置信度{result.get('confidence')})")

        # 记录预测
        log_prediction(
            result.get('stock_code', ''),
            result.get('stock_name', ''),
            result.get('recommendation', ''),
            result.get('confidence', 0),
            result.get('current_price', 0),
            result.get('stop_loss_price'),
            result.get('take_profit_price'),
            result.get('reasoning', ''),
            result.get('key_signals', []),
        )

        return result

    except json.JSONDecodeError as e:
        logger.error(f"AI返回的JSON解析失败: {e}\n原始文本: {text[:500]}")
        return {"error": "JSON解析失败", "raw": text[:500]}
    except Exception as e:
        logger.error(f"AI分析失败: {e}")
        return {"error": str(e)}


def screen_stocks(candidates, market_overview=None):
    """从候选股票中筛选"""
    try:
        prompt = SCREENING_PROMPT.format(
            candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
            market_overview=json.dumps(market_overview or {}, ensure_ascii=False, indent=2),
        )

        text = _call_claude(prompt, max_tokens=4000)
        results = _parse_json(text)
        logger.info(f"AI选股完成: 选出 {len(results)} 只")
        return results

    except Exception as e:
        logger.error(f"AI选股失败: {e}")
        return []


def generate_daily_report(balance, positions, trades, analyses):
    """生成每日复盘报告"""
    try:
        prompt = f"""请为以下模拟盘交易情况生成一份每日复盘报告。
用户是零经验的大学生，报告要通俗易懂，带有教学性质。

## 资金状况
{json.dumps(balance, ensure_ascii=False, indent=2)}

## 当前持仓
{json.dumps(positions, ensure_ascii=False, indent=2)}

## 今日交易记录
{json.dumps(trades, ensure_ascii=False, indent=2)}

## 今日分析记录
{json.dumps(analyses, ensure_ascii=False, indent=2)}

请生成中文报告，包含：
1. 今日总结（盈亏情况、大盘环境）
2. 持仓分析（每只股票的当前状态和建议）
3. 今日操作回顾（做得好的/需要改进的）
4. 明日展望和操作建议
5. 今日学习要点（关联股票知识）"""

        return _call_claude(prompt, max_tokens=3000)

    except Exception as e:
        logger.error(f"生成日报失败: {e}")
        return f"日报生成失败: {e}"


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    result = analyze_stock(
        stock_info={'code': '600519', 'name': '贵州茅台', 'price': 1324.3},
        indicators={
            'macd_line': -5.92, 'macd_signal': -3.2, 'macd_hist': -5.92,
            'macd_hist_prev': -6.43, 'rsi': 26.32, 'ma5': 1333.3, 'ma20': 1380.64,
            'ma5_prev': 1340, 'ma20_prev': 1385, 'close': 1324.3,
            'macd_golden_cross': False, 'macd_death_cross': False,
            'rsi_oversold': True, 'rsi_overbought': False,
            'volume_surge': False, 'pct_change': -1.2,
            'price_above_ma5': False, 'price_above_ma20': False,
        },
        market_overview={'上证指数': {'price': 3200, 'change_pct': -0.5}},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
