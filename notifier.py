"""飞书推送通知模块
带频率限制 + 通知去重（同类风险每天最多1次）
"""
import json
import os
import logging
import time
from datetime import datetime
import requests

import config

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/7a16bfb4-6a0c-4bd7-b277-0f68f4c71c0d")

# 频率限制：两次推送最少间隔5秒
_last_send_time = 0
MIN_INTERVAL = 5  # 秒

# 通知去重：同股票同类型每天最多1次
NOTIFICATION_LOG_FILE = os.path.join(config.DATA_DIR, 'notification_log.json')


def send(title, content):
    """发送飞书消息（带频率限制）
    Args:
        title: 消息标题
        content: 消息正文
    Returns:
        bool: 是否发送成功
    """
    global _last_send_time

    # 频率限制
    now = time.time()
    elapsed = now - _last_send_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)

    try:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content,
                    }
                ],
            },
        }
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        data = resp.json()
        _last_send_time = time.time()

        if data.get("code") == 0 or data.get("StatusCode") == 0:
            logger.info(f"飞书推送成功: {title}")
            return True
        else:
            logger.error(f"飞书推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")
        _last_send_time = time.time()
        return False


def _can_send(stock_code, risk_type):
    """检查是否已发送过（同类风险每天最多1次）"""
    today = datetime.now().strftime('%Y-%m-%d')
    key = f"{stock_code}:{risk_type}"

    # 加载日志
    log = {}
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                log = json.load(f)
        except Exception:
            log = {}

    # 清理过期条目
    log = {k: v for k, v in log.items() if v == today}

    if key in log:
        return False  # 今天已经发过

    # 记录
    log[key] = today
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"写入通知日志失败: {e}")

    return True


def _get_action_suggestion(sig_type, severity, gain_pct):
    """根据信号类型+盈亏比例生成操作建议"""
    if sig_type == 'break_ma20_volume':
        if gain_pct > 10:
            return "建议减仓50%，保留底仓等反弹。跌破今日低点则清仓。"
        elif gain_pct > 3:
            return "建议减仓30%，跌破止损价全部卖出。"
        else:
            return "已接近止损线，准备执行止损。"
    elif sig_type == 'macd_death_cross':
        if gain_pct > 15:
            return "盈利丰厚，建议减仓30%锁定部分利润，剩余持仓提高止损价。"
        elif gain_pct > 5:
            return "还有利润，建议减仓20%，止损价上移到成本价附近。"
        else:
            return "暂持观望，如果明天继续下跌则止损。"
    elif sig_type == 'below_both_ma':
        if gain_pct > 5:
            return "建议减仓30%，剩余持仓设好止损。"
        else:
            return "趋势偏弱，密切关注止损线。"
    return "请关注后续走势。"


def send_buy_signal(stock_code, stock_name, price, quantity, reason):
    """推送买入信号"""
    if not _can_send(stock_code, 'buy_signal'):
        return
    send(
        title=f"买入: {stock_name}({stock_code})",
        content=(
            f"**股票**: {stock_name} ({stock_code})\n"
            f"**买入价**: {price}\n"
            f"**数量**: {quantity}股\n"
            f"**原因**: {reason[:200]}"
        ),
    )


def send_sell_signal(stock_code, stock_name, price, reason):
    """推送卖出信号"""
    if not _can_send(stock_code, 'sell_signal'):
        return
    send(
        title=f"卖出: {stock_name}({stock_code})",
        content=(
            f"**股票**: {stock_name} ({stock_code})\n"
            f"**卖出价**: {price}\n"
            f"**原因**: {reason[:200]}"
        ),
    )


def send_stop_loss_alert(stock_name, stock_code, buy_price, current_price, change_pct):
    """推送止损预警（含操作指引）"""
    if not _can_send(stock_code, 'stop_loss'):
        return

    if change_pct > 0:
        action = f"盈利{change_pct:+.1f}%触发追踪止损，已锁定利润。建议执行卖出。"
    else:
        action = f"亏损{change_pct:.1f}%触发止损。建议立即执行卖出，控制损失。"

    send(
        title=f"止损: {stock_name}",
        content=(
            f"**止损触发**\n"
            f"买入价: {buy_price} → 当前价: {current_price}\n"
            f"盈亏: {change_pct:+.1f}%\n\n"
            f"**建议操作**: {action}\n"
            f"卖了之后不要急着买回来，等新的信号。"
        ),
    )


def send_take_profit_alert(stock_name, stock_code, buy_price, current_price, change_pct):
    """推送止盈提醒"""
    if not _can_send(stock_code, 'take_profit'):
        return

    send(
        title=f"止盈: {stock_name}",
        content=(
            f"**止盈提醒**\n"
            f"买入价: {buy_price} → 当前价: {current_price}\n"
            f"盈利: {change_pct:+.1f}%\n\n"
            f"**建议操作**: 盈利可观，建议减仓30-50%锁定利润。\n"
            f"剩余持仓跟踪止损价已上移，继续持有直到触发止损。"
        ),
    )


def send_position_alert(position, sig_type, severity, message, current_price, gain_pct):
    """推送持仓技术面恶化预警"""
    if not _can_send(position['code'], sig_type):
        return

    action = _get_action_suggestion(sig_type, severity, gain_pct)
    icon = '⚠️' if severity == '高' else '📊'

    send(
        title=f"{icon} {position['name']} - {severity}风险",
        content=(
            f"**{position['name']}({position['code']})**\n"
            f"买入价: {position['buy_price']} → 现价: {current_price}\n"
            f"盈亏: {gain_pct:+.1f}%\n\n"
            f"**信号**: {message}\n\n"
            f"**建议操作**: {action}"
        ),
    )


def send_market_alert(crash_level, overview, positions):
    """推送大盘暴跌预警"""
    if not _can_send('market', f'crash_{crash_level}'):
        return

    sh_pct = overview.get('上证指数', {}).get('change_pct', 0)

    actions = {
        'caution': f"大盘跌{abs(sh_pct):.1f}%，注意风险。建议暂停新开仓，检查持仓止损设置。",
        'warning': f"大盘跌{abs(sh_pct):.1f}%，风险较高。建议减仓盈利较多的持仓，收紧止损价。",
        'crash': (
            f"大盘暴跌{abs(sh_pct):.1f}%！建议执行以下操作：\n"
            f"1. 盈利持仓减仓50%以上\n"
            f"2. 亏损持仓检查是否触及止损\n"
            f"3. 暂停所有买入操作"
        ),
    }

    icon = '🔴' if crash_level == 'crash' else '🟡'
    label = '暴跌' if crash_level == 'crash' else '预警'

    send(
        title=f"{icon} 大盘{label}",
        content=actions.get(crash_level, ''),
    )


def send_daily_report(report_text):
    """推送每日复盘"""
    send(title="每日复盘", content=report_text[:2000])


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = send("AI Trader 测试", "飞书推送正常")
    print(f"结果: {'成功' if ok else '失败'}")
