"""交易日志系统
每一步操作都有结构化记录，Claude可随时读取分析
"""
import json
import logging
import os
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _time_str():
    return datetime.now().strftime('%H%M')


def _day_dir(category):
    """某类日志的今日目录"""
    d = os.path.join(config.LOG_DIR, category, _today())
    os.makedirs(d, exist_ok=True)
    return d


def _append_jsonl(filepath, record):
    """追加一行JSON到文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


# ========== 各类日志 ==========

def log_market(overview):
    """记录大盘数据"""
    filepath = os.path.join(_day_dir('market'), f'{_time_str()}.json')
    record = {'time': _now(), 'type': 'market', 'data': overview}
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"[日志] 大盘数据 → {filepath}")


def log_indicators(stock_code, stock_name, indicators):
    """记录技术指标"""
    filepath = os.path.join(_day_dir('indicators'), f'{_time_str()}.jsonl')
    record = {
        'time': _now(), 'type': 'indicators',
        'code': stock_code, 'name': stock_name,
        'data': indicators,
    }
    _append_jsonl(filepath, record)


def log_signal(stock_code, stock_name, signal_type, details):
    """记录规则信号（金叉/超卖/放量等）
    Args:
        signal_type: 'buy' / 'sell' / 'watch'
        details: 信号详情
    """
    filepath = os.path.join(_day_dir('signals'), f'{_time_str()}.jsonl')
    record = {
        'time': _now(), 'type': 'signal',
        'code': stock_code, 'name': stock_name,
        'signal': signal_type, 'details': details,
    }
    _append_jsonl(filepath, record)
    logger.info(f"[日志] 信号: {stock_name}({stock_code}) → {signal_type}")


def log_ai_decision(stock_code, stock_name, recommendation, confidence,
                    reasoning, risk_level, stop_loss, take_profit):
    """记录AI分析决策"""
    filepath = os.path.join(config.LOG_DIR, 'ai_decisions', f'{_today()}.jsonl')
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    record = {
        'time': _now(), 'type': 'ai_decision',
        'code': stock_code, 'name': stock_name,
        'recommendation': recommendation,
        'confidence': confidence,
        'risk_level': risk_level,
        'reasoning': reasoning[:500],
        'stop_loss': stop_loss,
        'take_profit': take_profit,
    }
    _append_jsonl(filepath, record)
    logger.info(f"[日志] AI决策: {stock_name}({stock_code}) → {recommendation} (置信度{confidence})")


def log_trade(action, stock_code, stock_name, price, quantity, reason, success):
    """记录交易执行"""
    filepath = os.path.join(config.LOG_DIR, 'trades', f'{_today()}.jsonl')
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    record = {
        'time': _now(), 'type': 'trade',
        'action': action,  # 'buy' / 'sell'
        'code': stock_code, 'name': stock_name,
        'price': price, 'quantity': quantity,
        'reason': reason, 'success': success,
    }
    _append_jsonl(filepath, record)
    logger.info(f"[日志] 交易: {action} {stock_name}({stock_code}) {quantity}股@{price} → {'成功' if success else '失败'}")


# ========== 读取日志 ==========

def read_today_logs(category=None):
    """读取今日所有日志（或某类日志）
    Args:
        category: 'market'/'indicators'/'signals'/'ai_decisions'/'trades'，None=全部
    Returns:
        list: 日志记录列表
    """
    logs = []
    base = config.LOG_DIR
    today = _today()

    categories = [category] if category else [
        'market', 'indicators', 'signals', 'ai_decisions', 'trades'
    ]

    for cat in categories:
        day_path = os.path.join(base, cat, today)
        if not os.path.exists(day_path):
            continue

        for fname in sorted(os.listdir(day_path)):
            fpath = os.path.join(day_path, fname)
            try:
                if fname.endswith('.jsonl'):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                logs.append(json.loads(line))
                elif fname.endswith('.json'):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        logs.append(json.load(f))
            except Exception as e:
                logger.error(f"读取日志失败 {fpath}: {e}")

    return sorted(logs, key=lambda x: x.get('time', ''))


def read_recent_ai_decisions(n=20):
    """读取最近N条AI决策"""
    filepath = os.path.join(config.LOG_DIR, 'ai_decisions', f'{_today()}.jsonl')
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    records = []
    for line in lines[-n:]:
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def read_recent_trades(n=20):
    """读取最近N笔交易"""
    filepath = os.path.join(config.LOG_DIR, 'trades', f'{_today()}.jsonl')
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    records = []
    for line in lines[-n:]:
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    log_market({'上证': 4169, '深证': 15569})
    log_signal('600519', '贵州茅台', 'watch', 'RSI超卖28.6')
    log_ai_decision('600519', '贵州茅台', '观望', 0.65, '空头排列', '中', 1300, 1380)
    log_trade('buy', '600519', '贵州茅台', 1324.3, 100, '测试', True)

    print('\n=== 今日日志 ===')
    for log in read_today_logs():
        print(f"  {log.get('time','')} [{log.get('type','')}] {log.get('name','')} {log.get('recommendation','') or log.get('signal','') or log.get('action','')}")
