"""HTML 笔记生成器
生成漂亮的 HTML 每日报告，适合人类阅读
"""
import os
import logging
from datetime import datetime

import config
import trade_logger as tlog
import market_data as md

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join(config.LOG_DIR, 'html_reports')
os.makedirs(REPORT_DIR, exist_ok=True)


def generate_daily_html(date_str=None):
    """生成某天的 HTML 报告"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    # 读取数据
    trades = tlog.read_recent_trades(100)
    decisions = tlog.read_recent_ai_decisions(100)
    logs = tlog.read_today_logs()

    # 大盘数据
    market_logs = [l for l in logs if l.get('type') == 'market']
    market_html = ''
    if market_logs:
        latest = market_logs[-1]
        for name, data in latest.get('data', {}).items():
            pct = data.get('change_pct', 0)
            color = '#ff4444' if pct > 0 else '#00cc88' if pct < 0 else '#ffd700'
            sign = '+' if pct > 0 else ''
            market_html += f'<div class="index"><span class="name">{name}</span><span class="value" style="color:{color}">{data.get("price",0)} ({sign}{pct}%)</span></div>'

    # 交易记录
    trades_html = ''
    if trades:
        for t in trades:
            action_color = '#ff4444' if t.get('action') == 'buy' else '#00cc88'
            status = '✅' if t.get('success') else '❌'
            trades_html += f'''<tr>
                <td>{t.get('time','')[-8:]}</td>
                <td><span style="color:{action_color};font-weight:bold">{t.get('action','').upper()}</span></td>
                <td>{t.get('name','')}</td>
                <td>{t.get('quantity',0)}</td>
                <td>{t.get('price',0)}</td>
                <td>{status}</td>
                <td class="reason">{t.get('reason','')[:80]}</td>
            </tr>'''
    else:
        trades_html = '<tr><td colspan="7" style="text-align:center;color:#666">今日无交易</td></tr>'

    # AI 决策
    decisions_html = ''
    if decisions:
        for d in decisions:
            rec = d.get('recommendation', '')
            conf = d.get('confidence', 0)
            rec_class = 'rec-buy' if rec == '买入' else 'rec-sell' if rec == '卖出' else 'rec-hold'
            reasoning = d.get('reasoning', '').replace('\n', '<br>')
            decisions_html += f'''<div class="decision-card">
                <div class="decision-header">
                    <span class="time">{d.get('time','')[-8:]}</span>
                    <span class="stock">{d.get('name','')} ({d.get('code','')})</span>
                    <span class="rec {rec_class}">{rec}</span>
                    <span class="conf">置信度 {conf:.0%}</span>
                </div>
                <div class="decision-body">{reasoning}</div>
                <div class="decision-footer">
                    止损: {d.get('stop_loss','-')} | 止盈: {d.get('take_profit','-')} | 风险: {d.get('risk_level','-')}
                </div>
            </div>'''

    # 统计
    total_trades = len(trades)
    buy_count = sum(1 for t in trades if t.get('action') == 'buy')
    sell_count = sum(1 for t in trades if t.get('action') == 'sell')
    success_count = sum(1 for t in trades if t.get('success'))

    # 生成图表（如果有关注列表的图）
    charts_html = ''
    chart_dir = os.path.join(config.LOG_DIR, 'charts')
    if os.path.exists(chart_dir):
        for fname in sorted(os.listdir(chart_dir)):
            if fname.endswith('.png'):
                # 用相对路径
                chart_path = os.path.join(chart_dir, fname)
                charts_html += f'<img src="file:///{chart_path}" class="chart-img" title="{fname}">\n'

    # 完整 HTML
    html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Trader 日报 - {date_str}</title>
<style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: -apple-system, 'Microsoft YaHei', sans-serif; background:#0f0f23; color:#e0e0e0; padding:20px; max-width:1200px; margin:0 auto; }}
    h1 {{ color:#00d4ff; font-size:24px; margin-bottom:5px; }}
    h2 {{ color:#00d4ff; font-size:18px; margin:25px 0 15px; border-bottom:1px solid #333; padding-bottom:8px; }}
    .date {{ color:#888; font-size:14px; margin-bottom:20px; }}
    .index {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #222; }}
    .index .name {{ color:#888; }}
    .index .value {{ font-weight:bold; }}
    .stats {{ display:flex; gap:20px; margin:15px 0; }}
    .stat-box {{ background:#1a1a2e; border-radius:8px; padding:15px; flex:1; text-align:center; }}
    .stat-box .num {{ font-size:28px; font-weight:bold; color:#00d4ff; }}
    .stat-box .label {{ color:#888; font-size:12px; margin-top:5px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ padding:10px 12px; text-align:left; border-bottom:1px solid #222; font-size:13px; }}
    th {{ color:#00d4ff; }}
    .reason {{ color:#888; font-size:12px; }}
    .decision-card {{ background:#1a1a2e; border-radius:8px; padding:15px; margin-bottom:12px; border-left:3px solid #333; }}
    .decision-header {{ display:flex; align-items:center; gap:15px; margin-bottom:10px; flex-wrap:wrap; }}
    .decision-header .time {{ color:#888; font-size:12px; }}
    .decision-header .stock {{ font-weight:bold; }}
    .rec {{ padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold; }}
    .rec-buy {{ background:#ff444422; color:#ff4444; }}
    .rec-sell {{ background:#00cc8822; color:#00cc88; }}
    .rec-hold {{ background:#ffd70022; color:#ffd700; }}
    .conf {{ color:#888; font-size:12px; }}
    .decision-body {{ font-size:13px; line-height:1.6; color:#ccc; }}
    .decision-footer {{ margin-top:10px; font-size:12px; color:#666; }}
    .chart-img {{ max-width:100%; border-radius:8px; margin:10px 0; border:1px solid #333; }}
    .learning {{ background:#0a0a1a; border-radius:8px; padding:15px; margin-top:15px; }}
    .learning h3 {{ color:#ffd700; font-size:14px; margin-bottom:10px; }}
    .learning li {{ margin:5px 0 5px 20px; font-size:13px; line-height:1.6; }}
    @media (max-width:768px) {{ body {{ padding:10px; }} .stats {{ flex-direction:column; }} }}
</style>
</head>
<body>
<h1>AI Trader 日报</h1>
<div class="date">{date_str}</div>

<h2>大盘概况</h2>
<div class="indices">
{market_html if market_html else '<div style="color:#666">暂无数据</div>'}
</div>

<h2>今日统计</h2>
<div class="stats">
    <div class="stat-box"><div class="num">{total_trades}</div><div class="label">交易次数</div></div>
    <div class="stat-box"><div class="num">{buy_count}</div><div class="label">买入</div></div>
    <div class="stat-box"><div class="num">{sell_count}</div><div class="label">卖出</div></div>
    <div class="stat-box"><div class="num">{len(decisions)}</div><div class="label">AI决策</div></div>
</div>

<h2>交易记录</h2>
<table>
    <tr><th>时间</th><th>操作</th><th>股票</th><th>数量</th><th>价格</th><th>结果</th><th>原因</th></tr>
    {trades_html}
</table>

<h2>AI 决策记录</h2>
{decisions_html if decisions_html else '<div style="color:#666;padding:20px;text-align:center">今日无 AI 决策</div>'}

<h2>K 线图</h2>
{charts_html if charts_html else '<div style="color:#666;padding:20px;text-align:center">暂无图表</div>'}

<h2>今日学习</h2>
<div class="learning">
    <h3>学到的知识点</h3>
    <ul>
        <li><b>RSI 超卖不等于马上涨</b>：RSI 低于 30 只说明短期跌得过猛，但趋势扭转需要资金共识，不能仅凭此抄底</li>
        <li><b>左侧交易 vs 右侧交易</b>：左侧 = 在下跌中凭超卖买入（风险高）；右侧 = 等趋势企稳再买（更稳但买不到最低）</li>
        <li><b>MACD 绿柱缩短是早期信号</b>：比金叉出现得更早，代表下跌动能减弱</li>
    </ul>
    <h3>需要改进的</h3>
    <ul>
        <li>（每天收盘后由 AI 自动填写）</li>
    </ul>
</div>

<div style="text-align:center;color:#444;margin-top:30px;font-size:12px">
    Generated by AI Trader at {datetime.now().strftime('%H:%M:%S')}
</div>
</body>
</html>'''

    # 保存
    filepath = os.path.join(REPORT_DIR, f'{date_str}.html')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"HTML 报告已生成: {filepath}")
    return filepath


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    path = generate_daily_html()
    print(f"报告: {path}")
