"""条件预警系统
AI分析时设定价格目标，每轮监控检查是否触发，触发推飞书
"""
import json
import logging
import os
from datetime import datetime

import config

logger = logging.getLogger(__name__)

ALERTS_FILE = os.path.join(config.DATA_DIR, 'price_alerts.json')


def _load():
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def _save(alerts):
    with open(ALERTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)


def add_alert(stock_code, stock_name, target_price, direction, reason):
    """添加一条价格预警
    Args:
        direction: 'above'（突破上穿）或 'below'（跌破下穿）
        reason: 预警原因（AI分析结果）
    """
    alerts = _load()
    # 先移除同方向的旧预警
    alerts = [a for a in alerts if not (a['code'] == stock_code and a['direction'] == direction and a['active'])]
    alerts.append({
        'id': len(alerts) + 1,
        'code': stock_code,
        'name': stock_name,
        'target': target_price,
        'direction': direction,
        'reason': reason,
        'created_at': str(datetime.now()),
        'active': True,
        'triggered_at': None,
    })
    _save(alerts)
    logger.info(f"添加预警: {stock_name}({stock_code}) {'上穿' if direction == 'above' else '下破'} {target_price}")


def check_alerts(current_prices):
    """检查所有活跃预警是否触发
    Args:
        current_prices: {code: price} 字典
    Returns:
        list: 触发的预警列表
    """
    alerts = _load()
    triggered = []

    for alert in alerts:
        if not alert['active']:
            continue
        code = alert['code']
        if code not in current_prices:
            continue

        price = current_prices[code]
        target = alert['target']
        direction = alert['direction']

        hit = False
        if direction == 'above' and price >= target:
            hit = True
        elif direction == 'below' and price <= target:
            hit = True

        if hit:
            alert['active'] = False
            alert['triggered_at'] = str(datetime.now())
            alert['triggered_price'] = price
            triggered.append(alert)
            logger.info(f"预警触发: {alert['name']}({code}) 现价{price} {'上穿' if direction == 'above' else '下破'} {target}")

    _save(alerts)
    return triggered


def get_active_alerts():
    """获取所有活跃预警"""
    return [a for a in _load() if a['active']]


def cancel_alert(alert_id):
    """取消预警"""
    alerts = _load()
    for a in alerts:
        if a['id'] == alert_id:
            a['active'] = False
    _save(alerts)


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    add_alert('600519', '贵州茅台', 1333, 'above', 'AI建议突破MA5入场')
    add_alert('600519', '贵州茅台', 1290, 'below', '止损线')
    print(f"活跃预警: {len(get_active_alerts())} 条")
    for a in get_active_alerts():
        print(f"  {a['name']} {'上穿' if a['direction']=='above' else '下破'} {a['target']}: {a['reason']}")
