"""预测追踪模块
记录每次AI分析的结果和后续实际表现，用于修正后续分析
"""
import json
import logging
import os
from datetime import datetime, timedelta

import config
import market_data as md

logger = logging.getLogger(__name__)

PREDICTIONS_FILE = os.path.join(config.DATA_DIR, 'predictions.json')


def _load():
    if os.path.exists(PREDICTIONS_FILE):
        with open(PREDICTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def _save(data):
    with open(PREDICTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def log_prediction(stock_code, stock_name, recommendation, confidence,
                   price, stop_loss, take_profit, reasoning, key_signals):
    """记录一次预测"""
    preds = _load()
    preds.append({
        'id': len(preds) + 1,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'code': stock_code,
        'name': stock_name,
        'recommendation': recommendation,
        'confidence': confidence,
        'price_at_prediction': price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'reasoning': reasoning[:300],
        'key_signals': key_signals,
        'outcome': None,  # 待填入：correct/wrong/partial
        'actual_change_pct': None,  # 实际涨跌幅
        'resolved_date': None,
    })
    _save(preds)
    logger.info(f"记录预测: {stock_name}({stock_code}) → {recommendation}")


def resolve_prediction(pred_id, outcome, actual_change_pct):
    """标记预测结果
    Args:
        pred_id: 预测ID
        outcome: 'correct' / 'wrong' / 'partial'
        actual_change_pct: 实际涨跌幅
    """
    preds = _load()
    for p in preds:
        if p['id'] == pred_id:
            p['outcome'] = outcome
            p['actual_change_pct'] = actual_change_pct
            p['resolved_date'] = datetime.now().strftime('%Y-%m-%d')
            break
    _save(preds)


def get_accuracy_stats(recent_n=50):
    """获取预测准确率统计
    Returns:
        dict: 各维度的准确率
    """
    preds = _load()
    resolved = [p for p in preds if p['outcome'] is not None]

    if not resolved:
        return {'total': 0, 'message': '暂无已验证的预测记录'}

    recent = resolved[-recent_n:]

    # 总体准确率
    correct = sum(1 for p in recent if p['outcome'] == 'correct')
    wrong = sum(1 for p in recent if p['outcome'] == 'wrong')
    partial = sum(1 for p in recent if p['outcome'] == 'partial')

    # 按建议类型统计
    by_rec = {}
    for p in recent:
        rec = p['recommendation']
        if rec not in by_rec:
            by_rec[rec] = {'correct': 0, 'wrong': 0, 'partial': 0, 'total': 0}
        by_rec[rec][p['outcome']] += 1
        by_rec[rec]['total'] += 1

    stats = {
        'total': len(recent),
        'correct': correct,
        'wrong': wrong,
        'partial': partial,
        'accuracy': round(correct / len(recent) * 100, 1) if recent else 0,
        'by_recommendation': {},
    }

    for rec, data in by_rec.items():
        stats['by_recommendation'][rec] = {
            **data,
            'accuracy': round(data['correct'] / data['total'] * 100, 1) if data['total'] > 0 else 0,
        }

    return stats


def get_prompt_context():
    """生成一段提示词，让AI知道自己过去的准确率"""
    stats = get_accuracy_stats()

    if stats['total'] == 0:
        return ""

    lines = [
        f"\n## 你的历史表现（最近{stats['total']}次已验证的预测）",
        f"- 总体准确率: {stats['accuracy']}% ({stats['correct']}对/{stats['wrong']}错/{stats['partial']}部分)",
    ]

    for rec, data in stats.get('by_recommendation', {}).items():
        lines.append(f"- {rec}准确率: {data['accuracy']}% ({data['total']}次)")

    if stats['accuracy'] < 40:
        lines.append("\n**注意：你最近的准确率较低，请更加谨慎，提高置信度门槛。**")
    elif stats['accuracy'] > 70:
        lines.append("\n你最近表现不错，继续保持分析质量。")

    return '\n'.join(lines)


def auto_resolve_stale_predictions(max_age_days=5):
    """自动结算过期预测（用当前价格判断对错）

    买入预测：现价>买入价→correct，现价<止损→wrong，其余→partial
    卖出预测：现价<卖出价→correct，现价>止盈→wrong，其余→partial
    观望预测：价格在止损到止盈之间→correct

    Returns: 新结算数量
    """
    preds = _load()
    unresolved = [p for p in preds if p.get('outcome') is None]

    if not unresolved:
        return 0

    cutoff = datetime.now() - timedelta(days=max_age_days)
    resolved_count = 0

    for p in unresolved:
        # 检查年龄
        try:
            pred_time = datetime.strptime(p['date'], '%Y-%m-%d %H:%M')
        except (ValueError, KeyError):
            continue

        if pred_time > cutoff:
            continue  # 还没过期

        code = p.get('code', '')
        if not code:
            continue

        # 获取当前价格
        quote = md.get_realtime_quote(code)
        if not quote or not quote.get('price'):
            logger.warning(f"无法获取 {code} 价格，跳过结算")
            continue

        current_price = quote['price']
        buy_price = p.get('price_at_prediction', 0)
        stop_loss = p.get('stop_loss')
        take_profit = p.get('take_profit')

        if not buy_price:
            continue

        change_pct = round((current_price - buy_price) / buy_price * 100, 2)
        rec = p.get('recommendation', '')

        # 根据推荐类型判断结果
        if rec == '买入':
            if current_price > buy_price:
                outcome = 'correct'
            elif stop_loss and current_price <= stop_loss:
                outcome = 'wrong'
            else:
                outcome = 'partial'
        elif rec == '卖出':
            if current_price < buy_price:
                outcome = 'correct'
            elif take_profit and current_price >= take_profit:
                outcome = 'wrong'
            else:
                outcome = 'partial'
        else:  # 观望
            low_bound = stop_loss if stop_loss else buy_price * 0.95
            high_bound = take_profit if take_profit else buy_price * 1.15
            if low_bound <= current_price <= high_bound:
                outcome = 'correct'
            else:
                outcome = 'partial'

        resolve_prediction(p['id'], outcome, change_pct)
        resolved_count += 1
        logger.info(f"自动结算: {p.get('name','')}({code}) {rec} → {outcome} (实际{change_pct:+.1f}%)")

    return resolved_count


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 记录测试预测
    log_prediction('600519', '贵州茅台', '观望', 0.65, 1324.3, 1300, 1380,
                   '均线空头排列，RSI超卖但趋势未反转', ['RSI超卖', '价格低于MA20'])

    stats = get_accuracy_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print()
    print(get_prompt_context())
