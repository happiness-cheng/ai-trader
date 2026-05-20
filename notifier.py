"""飞书推送通知模块
带频率限制，防止被飞书限流
"""
import os
import logging
import time
import requests

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

# 频率限制：两次推送最少间隔5秒
_last_send_time = 0
MIN_INTERVAL = 5  # 秒


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


def send_buy_signal(stock_code, stock_name, price, quantity, reason):
    """推送买入信号"""
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
    send(
        title=f"卖出: {stock_name}({stock_code})",
        content=(
            f"**股票**: {stock_name} ({stock_code})\n"
            f"**卖出价**: {price}\n"
            f"**原因**: {reason[:200]}"
        ),
    )


def send_stop_loss_alert(stock_name, stock_code, buy_price, current_price, change_pct):
    """推送止损预警"""
    send(
        title=f"止损: {stock_name}",
        content=(
            f"**止损触发**\n"
            f"买入价: {buy_price} → 当前价: {current_price}\n"
            f"亏损: {change_pct}%"
        ),
    )


def send_take_profit_alert(stock_name, stock_code, buy_price, current_price, change_pct):
    """推送止盈提醒"""
    send(
        title=f"止盈: {stock_name}",
        content=(
            f"**止盈触发**\n"
            f"买入价: {buy_price} → 当前价: {current_price}\n"
            f"盈利: {change_pct}%"
        ),
    )


def send_daily_report(report_text):
    """推送每日复盘"""
    send(title="每日复盘", content=report_text[:2000])


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = send("AI Trader 测试", "飞书推送正常")
    print(f"结果: {'成功' if ok else '失败'}")
