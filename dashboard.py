"""Web仪表盘
FastAPI 后端，显示持仓、盈亏、交易记录、分析日志
"""
import json
import logging
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

import config
import market_data as md
import strategy
import ths_trader

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Trader Dashboard")


def _load_json(filepath, default=None):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default if default is not None else []


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """主仪表盘页面"""
    html = """
    <!DOCTYPE html>
    <html lang="zh">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI Trader</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: -apple-system, 'Microsoft YaHei', sans-serif; background: #0f0f23; color: #e0e0e0; padding: 20px; }
            .header { text-align: center; margin-bottom: 30px; }
            .header h1 { color: #00d4ff; font-size: 28px; }
            .header .subtitle { color: #888; margin-top: 5px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }
            .card { background: #1a1a2e; border-radius: 12px; padding: 20px; border: 1px solid #333; }
            .card h2 { color: #00d4ff; font-size: 16px; margin-bottom: 15px; border-bottom: 1px solid #333; padding-bottom: 8px; }
            .stat { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #222; }
            .stat .label { color: #888; }
            .stat .value { font-weight: bold; }
            .profit { color: #ff4444; }
            .loss { color: #00cc88; }
            .neutral { color: #ffd700; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #222; }
            th { color: #00d4ff; font-size: 13px; }
            .btn { background: #00d4ff; color: #000; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; }
            .btn:hover { background: #00a8cc; }
            .refresh-info { text-align: center; color: #666; margin-top: 20px; font-size: 13px; }
            .loading { text-align: center; padding: 40px; color: #666; }
            .chart-block { margin-bottom: 20px; background: #1a1a2e; border-radius: 12px; padding: 15px; border: 1px solid #333; }
            .chart-block img { width: 100%; border-radius: 8px; }
            .chart-analysis { margin-top: 10px; padding: 10px; background: #0f0f23; border-radius: 8px; font-size: 13px; line-height: 1.6; }
            .chart-analysis .rec { font-size: 18px; font-weight: bold; }
            .rec-buy { color: #ff4444; }
            .rec-sell { color: #00cc88; }
            .rec-hold { color: #ffd700; }
            .tip { color: #888; font-size: 12px; border-bottom: 1px dashed #555; cursor: help; }
            .glossary { background: #0a0a1a; border-radius: 8px; padding: 12px; margin-top: 10px; font-size: 12px; line-height: 1.8; color: #aaa; }
            .glossary b { color: #00d4ff; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>AI Trader</h1>
            <div class="subtitle">同花顺模拟盘 | 自动交易系统</div>
        </div>
        <div class="grid">
            <div class="card">
                <h2>账户概况</h2>
                <div id="balance"><div class="loading">加载中...</div></div>
            </div>
            <div class="card">
                <h2>今日大盘</h2>
                <div id="market"><div class="loading">加载中...</div></div>
            </div>
            <div class="card">
                <h2>当前持仓</h2>
                <div id="positions"><div class="loading">加载中...</div></div>
            </div>
            <div class="card">
                <h2>关注列表</h2>
                <div id="watchlist"><div class="loading">加载中...</div></div>
            </div>
            <div class="card" style="grid-column: 1 / -1;">
                <h2>今日复盘</h2>
                <div id="review"><div class="loading">AI复盘生成中...</div></div>
            </div>
            <div class="card" style="grid-column: 1 / -1;">
                <h2>AI推荐信号</h2>
                <div id="signals"><p style="color:#666;padding:20px;text-align:center">有买入/卖出信号时，自动显示图表+分析</p></div>
            </div>
            <div class="card" style="grid-column: 1 / -1;">
                <h2>最近交易</h2>
                <div id="trades"><div class="loading">加载中...</div></div>
            </div>
        </div>
        <div class="refresh-info">
            <button class="btn" onclick="loadAll()">刷新数据</button>
            <span style="margin-left:10px" id="update-time"></span>
        </div>
        <script>
            async function fetchJSON(url) {
                try { const r = await fetch(url); return await r.json(); }
                catch(e) { return {error: e.message}; }
            }
            async function loadAll() {
                const [balance, market, positions, watchlist, trades] = await Promise.all([
                    fetchJSON('/api/balance'),
                    fetchJSON('/api/market'),
                    fetchJSON('/api/positions'),
                    fetchJSON('/api/watchlist'),
                    fetchJSON('/api/trades'),
                ]);
                renderBalance(balance);
                renderMarket(market);
                renderPositions(positions);
                renderWatchlist(watchlist);
                renderTrades(trades);
                document.getElementById('update-time').textContent = '更新: ' + new Date().toLocaleTimeString();
            }
            function renderBalance(d) {
                if (d.error) { document.getElementById('balance').textContent = d.error; return; }
                let html = '';
                for (const [k,v] of Object.entries(d)) {
                    const cls = k.includes('盈亏') ? (parseFloat(v)>=0?'profit':'loss') : '';
                    html += `<div class="stat"><span class="label">${k}</span><span class="value ${cls}">${v}</span></div>`;
                }
                document.getElementById('balance').innerHTML = html;
            }
            function renderMarket(d) {
                if (d.error) { document.getElementById('market').textContent = d.error; return; }
                let html = '';
                for (const [name, info] of Object.entries(d)) {
                    const pct = info.change_pct || 0;
                    const cls = pct > 0 ? 'profit' : pct < 0 ? 'loss' : 'neutral';
                    html += `<div class="stat"><span class="label">${name}</span><span class="value ${cls}">${info.price} (${pct>0?'+':''}${pct}%)</span></div>`;
                }
                document.getElementById('market').innerHTML = html;
            }
            function renderPositions(d) {
                if (!d.length) { document.getElementById('positions').innerHTML = '<p style="color:#666">暂无持仓</p>'; return; }
                let html = '<table><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th></tr>';
                d.forEach(p => { html += `<tr><td>${p.code}</td><td>${p.name}</td><td>${p.quantity}</td><td>${p.buy_price}</td></tr>`; });
                html += '</table>';
                document.getElementById('positions').innerHTML = html;
            }
            function renderWatchlist(d) {
                if (!d.length) { document.getElementById('watchlist').innerHTML = '<p style="color:#666">关注列表为空</p>'; return; }
                let html = '<table><tr><th>代码</th><th>名称</th><th>添加时间</th></tr>';
                d.forEach(s => { html += `<tr><td>${s.code}</td><td>${s.name||'-'}</td><td>${s.added_at||'-'}</td></tr>`; });
                html += '</table>';
                document.getElementById('watchlist').innerHTML = html;
            }
            function renderTrades(d) {
                if (!d.length) { document.getElementById('trades').innerHTML = '<p style="color:#666">暂无交易记录</p>'; return; }
                let html = '<table><tr><th>代码</th><th>名称</th><th>买入价</th><th>卖出价</th><th>盈亏</th><th>日期</th></tr>';
                d.slice(-10).reverse().forEach(t => {
                    const cls = (t.profit||0)>=0?'profit':'loss';
                    html += `<tr><td>${t.code}</td><td>${t.name}</td><td>${t.buy_price}</td><td>${t.sell_price||'-'}</td><td class="${cls}">${t.profit||'-'}</td><td>${(t.sell_date||t.buy_date||'').slice(0,10)}</td></tr>`;
                });
                html += '</table>';
                document.getElementById('trades').innerHTML = html;
            }
            async function generateCharts() {
                document.getElementById('charts').innerHTML = '<div class="loading">生成中...</div>';
                const r = await fetch('/api/generate_charts');
                const d = await r.json();
                loadCharts();
            }
            async function loadCharts() {
                const r = await fetch('/api/charts');
                const files = await r.json();
                if (!files.length) { document.getElementById('charts').innerHTML = '<p style="color:#666">暂无图表，点击"生成图表"</p>'; return; }
                let html = '';
                for (const f of files) {
                    const code = f.split('_')[0];
                    const name = f.split('_')[1]?.replace('.png','') || code;
                    // 获取该股票的分析
                    let analysis = '';
                    try {
                        const ar = await fetch(`/api/chart_analysis/${code}`);
                        const ad = await ar.json();
                        if (!ad.error) {
                            const recClass = ad.recommendation === '买入' ? 'rec-buy' : ad.recommendation === '卖出' ? 'rec-sell' : 'rec-hold';
                            const glossary = ad.glossary ? `<div class="glossary">${ad.glossary}</div>` : '';
                            analysis = `<div class="chart-analysis">
                                <span class="rec ${recClass}">${ad.recommendation}</span> (置信度 ${(ad.confidence*100).toFixed(0)}%) | 趋势: ${ad.trend} | 风险: ${ad.risk_level}
                                <br>${ad.reasoning || ''}
                                <br><span style="color:#888">止损: ${ad.stop_loss || '-'} | 止盈: ${ad.take_profit || '-'}</span>
                                ${glossary}
                            </div>`;
                        }
                    } catch(e) {}
                    html += `<div class="chart-block"><img src="/api/charts/${f}" title="${name}">${analysis}</div>`;
                }
                document.getElementById('charts').innerHTML = html;
            }
            async function loadReview() {
                const r = await fetch('/api/review');
                const d = await r.json();
                if (d.error) { document.getElementById('review').innerHTML = '<p style="color:#666">复盘生成失败</p>'; return; }
                let html = '';

                // 大盘
                html += `<div style="color:#888;margin-bottom:10px">${d.date} | ${d.market_analysis}</div>`;

                // 持仓详细分析
                if (d.holdings && d.holdings.length) {
                    d.holdings.forEach(h => {
                        const cls = h.pnl_pct >= 0 ? 'profit' : 'loss';
                        const sign = h.pnl_pct >= 0 ? '+' : '';
                        html += `<div style="background:#0f0f23;border-radius:8px;padding:12px;margin-bottom:10px">
                            <div style="display:flex;justify-content:space-between;align-items:center">
                                <b>${h.name} (${h.code})</b>
                                <span class="${cls}" style="font-size:18px;font-weight:bold">${sign}${h.pnl_pct}% (${sign}${h.pnl_yuan}元)</span>
                            </div>
                            <div style="color:#888;font-size:12px;margin-top:5px">${h.quantity}股 | 成本${h.buy_price} | 现价${h.current_price}</div>
                            <div style="margin-top:8px;font-size:13px;line-height:1.6">
                                <b>趋势</b>: ${h.trend} — ${h.trend_advice}<br>
                                <b>RSI</b>: ${h.rsi_note}<br>
                                <b>MACD</b>: ${h.macd_note}<br>
                                <b>MA</b>: MA5=${h.ma5?.toFixed(2)} vs MA20=${h.ma20?.toFixed(2)}<br>
                                <b>止损</b>: ${h.stop_loss} (距${h.distance_to_stop}%) | <b>止盈</b>: ${h.take_profit} (距${h.distance_to_profit}%)
                            </div>
                        </div>`;
                    });
                    html += `<div style="color:#888;margin-bottom:15px">总盈亏: <span class="${d.total_pnl>=0?'profit':'loss'}">${d.total_pnl>=0?'+':''}${d.total_pnl}元</span></div>`;
                }

                // 明日关注
                if (d.tomorrow_watch && d.tomorrow_watch.length) {
                    html += '<div style="background:#1a1a2e;border-radius:8px;padding:12px;margin-bottom:10px"><b style="color:#ffd700">明日关注</b>';
                    d.tomorrow_watch.forEach(w => {
                        html += `<div style="font-size:13px;margin-top:5px">- ${w}</div>`;
                    });
                    html += '</div>';
                }

                // 今日学到的
                if (d.learning && d.learning.length) {
                    html += '<div style="background:#0a0a1a;border-radius:8px;padding:12px"><b style="color:#00d4ff">今日知识点</b>';
                    d.learning.forEach(l => {
                        html += `<div style="font-size:12px;margin-top:5px;color:#aaa">- ${l}</div>`;
                    });
                    html += '</div>';
                }

                // 今日信号
                if (d.signals && d.signals.length) {
                    html += '<div style="margin-top:10px"><b>今日信号</b>';
                    d.signals.forEach(s => {
                        html += `<div style="font-size:12px;color:#888">${s.time?.slice(-8)||''} ${s.name}: ${s.details}</div>`;
                    });
                    html += '</div>';
                }

                document.getElementById('review').innerHTML = html;
            }

            async function loadSignals() {
                const r = await fetch('/api/signals');
                const signals = await r.json();
                if (!signals.length) {
                    document.getElementById('signals').innerHTML = '<p style="color:#666;padding:20px;text-align:center">今日暂无买入/卖出信号</p>';
                    return;
                }
                let html = '';
                signals.reverse().forEach(s => {
                    const cls = s.action === 'buy' ? 'rec-buy' : 'rec-sell';
                    const label = s.action === 'buy' ? '买入' : '卖出';
                    const conf = (s.confidence * 100).toFixed(0);
                    html += `<div style="background:#0f0f23;border-radius:8px;padding:15px;margin-bottom:12px;border-left:3px solid ${s.action==='buy'?'#ff4444':'#00cc88'}">
                        <div style="display:flex;justify-content:space-between;align-items:center">
                            <span class="rec ${cls}" style="font-size:16px">${label}: ${s.name} (${s.code})</span>
                            <span style="color:#888">${s.time}</span>
                        </div>
                        <div style="margin-top:8px;font-size:13px">
                            价格: ${s.price} | 数量: ${s.quantity}股 | 止损: ${s.stop_loss} | 止盈: ${s.take_profit}
                        </div>
                        <div style="margin-top:8px;font-size:13px;line-height:1.6;color:#ccc">
                            ${s.reasoning}
                        </div>
                        ${s.chart ? `<img src="/api/charts/${s.chart}" style="width:100%;border-radius:8px;margin-top:10px;border:1px solid #333">` : ''}
                    </div>`;
                });
                document.getElementById('signals').innerHTML = html;
            }

            loadAll();
            loadReview();
            loadSignals();
            setInterval(loadAll, 60000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


_balance_cache = {}
_balance_cache_time = 0


@app.get("/api/balance")
async def api_balance():
    """实时计算资金（基于持仓+API价格，不连同花顺）"""
    try:
        positions = strategy.get_local_positions()
        total_mv = 0  # 股票市值
        total_pnl = 0  # 总盈亏

        for pos in positions:
            quote = md.get_realtime_quote(pos['code'])
            if quote and quote.get('price'):
                current = quote['price']
                mv = current * pos['quantity']
                pnl = (current - pos['buy_price']) * pos['quantity']
                total_mv += mv
                total_pnl += pnl

        total_assets = 164516.53 + total_pnl  # 初始资金 + 盈亏
        available = 164516.53 - total_mv  # 总资金 - 已用

        return {
            "资金余额": f"{164516.53:.2f}",
            "可用金额": f"{max(available, 0):.2f}",
            "总资产": f"{total_assets:.2f}",
            "股票市值": f"{total_mv:.2f}",
            "持仓盈亏": f"{total_pnl:.2f}",
        }
    except Exception as e:
        return {"资金余额": "164516.53", "可用金额": "31921.12", "总资产": "164532.12", "股票市值": "132611.00"}


@app.get("/api/balance/refresh")
async def api_balance_refresh():
    """手动刷新资金（点按钮时才连同花顺）"""
    import time
    global _balance_cache, _balance_cache_time
    try:
        trader = ths_trader.THSTrader()
        if trader.connect():
            _balance_cache = trader.get_balance()
            _balance_cache_time = time.time()
            return _balance_cache
        return {"error": "同花顺未连接"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/market")
async def api_market():
    """获取大盘概况"""
    return md.get_market_overview()


@app.get("/api/positions")
async def api_positions():
    """获取持仓"""
    return strategy.get_local_positions()


@app.get("/api/watchlist")
async def api_watchlist():
    """获取关注列表"""
    return strategy.get_watchlist()


@app.get("/api/trades")
async def api_trades():
    """获取交易历史"""
    return strategy._load_json(os.path.join(config.DATA_DIR, 'trades.json'), [])


@app.get("/api/analyze/{stock_code}")
async def api_analyze(stock_code: str):
    """分析单只股票"""
    result = strategy.full_analysis(stock_code, use_ai=True)
    return result


@app.get("/api/charts")
async def api_charts():
    """获取图表列表"""
    import glob
    chart_dir = os.path.join(config.LOG_DIR, 'charts')
    if not os.path.exists(chart_dir):
        return []
    files = sorted(glob.glob(os.path.join(chart_dir, '*.png')))
    return [os.path.basename(f) for f in files]


@app.get("/api/charts/{filename}")
async def api_chart_image(filename: str):
    """获取单张图表（防路径遍历）"""
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    # 禁止路径遍历：只允许纯文件名
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    chart_path = os.path.abspath(os.path.join(config.LOG_DIR, 'charts', filename))
    charts_dir = os.path.abspath(os.path.join(config.LOG_DIR, 'charts'))
    if not chart_path.startswith(charts_dir):
        raise HTTPException(status_code=400, detail="非法路径")
    if os.path.exists(chart_path):
        return FileResponse(chart_path, media_type='image/png')
    return {"error": "图表不存在"}


@app.get("/api/generate_charts")
async def api_generate_charts():
    """生成关注列表图表"""
    import chart_gen
    paths = chart_gen.generate_watchlist_charts()
    return {"generated": len(paths), "files": [os.path.basename(p) for p in paths]}


@app.post("/api/positions/add")
async def api_add_position(req: dict):
    """添加持仓（手动录入）"""
    strategy.save_position(
        req.get('code', ''), req.get('name', ''),
        req.get('quantity', 0), req.get('price', 0))
    return {"ok": True}


@app.post("/api/positions/remove")
async def api_remove_position(req: dict):
    """移除持仓（卖出后）"""
    strategy.remove_position(req.get('code', ''), req.get('price', 0))
    return {"ok": True}


@app.get("/api/review")
async def api_review():
    """生成今日详细复盘"""
    import trade_logger as tlog
    import chart_gen
    try:
        trades = tlog.read_recent_trades(50)
        decisions = tlog.read_recent_ai_decisions(50)
        positions = strategy.get_local_positions()
        overview = md.get_market_overview()

        # 大盘分析
        market_analysis = ""
        for name, data in overview.items():
            pct = data.get('change_pct', 0)
            if pct < -2:
                market_analysis += f"{name}跌{abs(pct):.1f}%（大跌，注意风险）。"
            elif pct < -0.5:
                market_analysis += f"{name}微跌{abs(pct):.1f}%。"
            elif pct > 2:
                market_analysis += f"{name}涨{pct:.1f}%（大涨）。"
            elif pct > 0.5:
                market_analysis += f"{name}微涨{pct:.1f}%。"

        # 持仓深度分析
        holdings = []
        total_pnl = 0
        for pos in positions:
            df = md.get_stock_history(pos['code'], days=120)
            if len(df) < 30:
                continue
            ind = md.get_technical_indicators(df)
            # 用实时价格代替昨日收盘价
            quote = md.get_realtime_quote(pos['code'])
            current_price = quote.get('price', ind['close']) if quote else ind['close']
            pnl_pct = round((current_price - pos['buy_price']) / pos['buy_price'] * 100, 2)
            pnl_yuan = round((current_price - pos['buy_price']) * pos['quantity'], 2)
            total_pnl += pnl_yuan

            # 趋势分析
            if ind['ma5'] < ind['ma20'] * 0.97:
                trend = "明显下降"
                trend_advice = "趋势不好，考虑止损"
            elif ind['ma5'] < ind['ma20']:
                trend = "偏弱"
                trend_advice = "还没反转，观望"
            elif ind['ma5'] > ind['ma20']:
                trend = "偏强"
                trend_advice = "趋势向好，持有"
            else:
                trend = "震荡"
                trend_advice = "方向不明"

            # RSI解读
            rsi = ind['rsi']
            if rsi < 25:
                rsi_note = f"极度超卖({rsi:.0f})，历史上常反弹"
            elif rsi < 35:
                rsi_note = f"超卖({rsi:.0f})，有反弹可能"
            elif rsi > 70:
                rsi_note = f"超买({rsi:.0f})，注意回调"
            else:
                rsi_note = f"中性({rsi:.0f})"

            # MACD解读
            if ind['macd_hist'] < 0 and ind['macd_hist'] > ind['macd_hist_prev']:
                macd_note = "绿柱缩短，下跌动能减弱"
            elif ind['macd_hist'] < 0:
                macd_note = "绿柱放大，还在跌"
            elif ind['macd_hist'] > 0:
                macd_note = "红柱，上涨中"
            else:
                macd_note = "平衡"

            stop_loss = round(pos['buy_price'] * 0.95, 2)
            take_profit = round(pos['buy_price'] * 1.15, 2)

            holdings.append({
                'name': pos['name'],
                'code': pos['code'],
                'quantity': pos['quantity'],
                'buy_price': pos['buy_price'],
                'current_price': current_price,
                'pnl_pct': pnl_pct,
                'pnl_yuan': pnl_yuan,
                'rsi': round(rsi, 1),
                'rsi_note': rsi_note,
                'macd_note': macd_note,
                'trend': trend,
                'trend_advice': trend_advice,
                'ma5': ind['ma5'],
                'ma20': ind['ma20'],
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'distance_to_stop': round((ind['close'] - stop_loss) / ind['close'] * 100, 1),
                'distance_to_profit': round((take_profit - ind['close']) / ind['close'] * 100, 1),
            })

        # 自动生成持仓的K线图
        chart_files = []
        for h in holdings:
            df = md.get_stock_history(h['code'], days=120)
            if len(df) >= 30:
                path = chart_gen.generate_stock_chart(h['code'], h['name'], df)
                if path:
                    chart_files.append(os.path.basename(path))

        # 今日信号汇总
        signals = tlog.read_today_logs('signals')
        interesting_signals = [s for s in signals if s.get('signal') not in ('hold', None)]

        # 明日关注
        tomorrow_watch = []
        for h in holdings:
            if h['rsi'] < 30:
                tomorrow_watch.append(f"{h['name']}: RSI极度超卖，关注是否放量反弹")
            if h['distance_to_stop'] < 3:
                tomorrow_watch.append(f"{h['name']}: 距止损线仅{h['distance_to_stop']}%，密切关注")
            if '绿柱缩短' in h['macd_note']:
                tomorrow_watch.append(f"{h['name']}: MACD绿柱缩短，可能接近底部")

        review = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'market_overview': overview,
            'market_analysis': market_analysis or "大盘平稳",
            'holdings': holdings,
            'total_pnl': round(total_pnl, 2),
            'trades': trades[-10:] if trades else [],
            'decisions_count': len(decisions),
            'signals': interesting_signals[-10:] if interesting_signals else [],
            'chart_files': chart_files,
            'tomorrow_watch': tomorrow_watch,
            'learning': [
                "RSI低于30说明跌过头，但不保证马上涨，要等放量确认",
                "MACD绿柱缩短是最早的反转信号，比金叉出现更早",
                "价格在均线下方时不要抄底，等站上MA5再入场",
                "止损不是认输，是保护本金，留得青山在不怕没柴烧",
            ],
        }

        return review
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/signals")
async def api_signals():
    """获取今日AI推荐信号"""
    signals_file = os.path.join(config.DATA_DIR, 'dashboard_signals.json')
    if os.path.exists(signals_file):
        with open(signals_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


@app.get("/api/chart_analysis/{stock_code}")
async def api_chart_analysis(stock_code: str):
    """获取单只股票的详细分析"""
    try:
        df = md.get_stock_history(stock_code, days=120)
        if len(df) < 30:
            return {"error": "数据不足"}
        indicators = md.get_technical_indicators(df)
        rule = strategy.rule_signal(indicators)

        close = indicators['close']
        ma5 = indicators['ma5']
        ma20 = indicators['ma20']
        rsi = indicators['rsi']
        macd_hist = indicators['macd_hist']
        macd_hist_prev = indicators['macd_hist_prev']
        vol_ratio = indicators['vol_ratio']
        pct = indicators.get('pct_change', 0)

        # 趋势判断
        if ma5 < ma20 * 0.97:
            trend_desc = "明显下降"
            trend_detail = f"5日均价({ma5:.2f})比20日均价({ma20:.2f})低很多，像一辆车在陡坡上往下开"
        elif ma5 < ma20:
            trend_desc = "偏弱"
            trend_detail = f"5日均价({ma5:.2f})略低于20日均价({ma20:.2f})，整体在走下坡路"
        elif ma5 > ma20 * 1.03:
            trend_desc = "明显上升"
            trend_detail = f"5日均价({ma5:.2f})比20日均价({ma20:.2f})高很多，上坡中"
        elif ma5 > ma20:
            trend_desc = "偏强"
            trend_detail = f"5日均价({ma5:.2f})略高于20日均价({ma20:.2f})，缓慢上坡"
        else:
            trend_desc = "震荡"
            trend_detail = "两条均线贴在一起，方向不明"

        # MACD分析
        if macd_hist < 0 and macd_hist > macd_hist_prev:
            macd_desc = "绿柱缩短"
            macd_detail = "下跌力量在减弱，就像下坡时在踩刹车，可能快要到底了"
        elif macd_hist < 0 and macd_hist < macd_hist_prev:
            macd_desc = "绿柱放大"
            macd_detail = "下跌力量还在加大，就像下坡时在踩油门，还没跌完"
        elif macd_hist > 0 and macd_hist > macd_hist_prev:
            macd_desc = "红柱放大"
            macd_detail = "上涨力量在加大，就像上坡时在踩油门，趋势向好"
        elif macd_hist > 0 and macd_hist < macd_hist_prev:
            macd_desc = "红柱缩短"
            macd_detail = "上涨力量在减弱，就像上坡时开始减速，可能要回调"
        else:
            macd_desc = "变化不大"
            macd_detail = "买卖力量平衡中"

        # RSI分析
        if rsi < 20:
            rsi_desc = f"极度超卖({rsi:.0f})"
            rsi_detail = "弹簧被压到极限了。历史上这种位置经常反弹，但不保证立刻涨，可能还要再压一会儿"
        elif rsi < 30:
            rsi_desc = f"超卖({rsi:.0f})"
            rsi_detail = "跌得太猛了，短期可能有反弹。但注意：超卖≠马上涨，就像感冒≠马上好"
        elif rsi < 40:
            rsi_desc = f"偏弱({rsi:.0f})"
            rsi_detail = "在走弱但还没到极端，观望为主"
        elif rsi < 60:
            rsi_desc = f"中性({rsi:.0f})"
            rsi_detail = "买卖力量差不多，方向不明"
        elif rsi < 70:
            rsi_desc = f"偏强({rsi:.0f})"
            rsi_detail = "在走强但还没到极端"
        elif rsi < 80:
            rsi_desc = f"超买({rsi:.0f})"
            rsi_detail = "涨得太猛了，短期可能回调。就像跑太快会喘"
        else:
            rsi_desc = f"极度超买({rsi:.0f})"
            rsi_detail = "弹簧拉到极限了，随时可能弹回来"

        # 成交量分析
        if vol_ratio > 2:
            vol_desc = f"明显放量({vol_ratio:.1f}倍)"
            vol_detail = "今天的成交量是平时的2倍多，有大资金在进场或出场"
        elif vol_ratio > 1.5:
            vol_desc = f"温和放量({vol_ratio:.1f}倍)"
            vol_detail = "比平时活跃一点，值得关注"
        elif vol_ratio < 0.7:
            vol_desc = f"明显缩量({vol_ratio:.1f}倍)"
            vol_detail = "没人关注这只股票，成交量很低"
        else:
            vol_desc = f"正常({vol_ratio:.1f}倍)"
            vol_detail = "成交量跟平时差不多"

        # 价格位置
        bb_pos = (close - indicators['bb_lower']) / (indicators['bb_upper'] - indicators['bb_lower']) * 100 if indicators['bb_upper'] != indicators['bb_lower'] else 50
        if bb_pos < 20:
            pos_desc = "低位（接近布林下轨）"
            pos_detail = f"当前价{close}接近布林带下轨({indicators['bb_lower']:.2f})，属于近期低位区"
        elif bb_pos > 80:
            pos_desc = "高位（接近布林上轨）"
            pos_detail = f"当前价{close}接近布林带上轨({indicators['bb_upper']:.2f})，属于近期高位区"
        else:
            pos_desc = f"中间位置({bb_pos:.0f}%)"
            pos_detail = f"当前价{close}在布林带中间"

        # 综合建议
        rec = '买入' if rule == 'buy' else ('卖出' if rule == 'sell' else '观望')
        risk = '高' if rsi > 70 or rsi < 20 else ('低' if 40 < rsi < 60 else '中')

        # 下一步关注
        next_steps = []
        if rsi < 30:
            next_steps.append(f"等放量上涨（量比>2）+ RSI回升到30以上才考虑入场")
        if macd_hist < 0 and macd_hist > macd_hist_prev:
            next_steps.append("绿柱在缩短，关注是否出现金叉（DIF上穿DEA）")
        if ma5 < ma20:
            next_steps.append(f"等价格站上MA5({ma5:.2f})再考虑入场（现在在均线下方，别接飞刀）")
        if not next_steps:
            next_steps.append("当前信号不明显，继续观察")

        reasoning = (
            f"**趋势**：{trend_desc} — {trend_detail}\n"
            f"**MACD**：{macd_desc} — {macd_detail}\n"
            f"**RSI**：{rsi_desc} — {rsi_detail}\n"
            f"**成交量**：{vol_desc} — {vol_detail}\n"
            f"**位置**：{pos_desc} — {pos_detail}\n"
            f"**下一步**：{'；'.join(next_steps)}"
        )

        # 术语解释（初学者友好）
        glossary = (
            "**名词解释**\n"
            f"MA5/MA20：最近5天/20天的平均价格，用来判断短期和中期趋势\n"
            f"MACD：一种趋势指标，红柱=上涨力量，绿柱=下跌力量\n"
            f"RSI：衡量\"涨跌速度\"，0-100，低于30=跌太猛(超卖)，高于70=涨太快(超买)\n"
            f"量比：今天的成交量÷过去20天平均成交量，>1=比平时活跃\n"
            f"布林带：价格的\"正常波动范围\"，碰到上轨=偏贵，碰到下轨=偏便宜"
        )

        return {
            'recommendation': rec,
            'confidence': 0.6,
            'trend': '下降' if ma5 < ma20 else '上升',
            'risk_level': risk,
            'reasoning': reasoning.replace('\n', '<br>'),
            'glossary': glossary.replace('\n', '<br>'),
            'stop_loss': round(close * 0.95, 2),
            'take_profit': round(close * 1.15, 2),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/trailing_stops")
async def api_trailing_stops():
    """获取所有持仓的跟踪止损状态"""
    positions = strategy.get_local_positions()
    result = []
    for pos in positions:
        quote = md.get_realtime_quote(pos['code'])
        current = quote.get('price', 0) if quote else 0
        buy_price = pos['buy_price']
        gain_pct = round((current - buy_price) / buy_price * 100, 2) if current and buy_price else 0
        trailing_stop = pos.get('trailing_stop_price', round(buy_price * 0.95, 2))
        highest = pos.get('highest_price_since_buy', buy_price)
        distance_to_stop = round((current - trailing_stop) / current * 100, 1) if current and trailing_stop else 0

        result.append({
            'code': pos['code'],
            'name': pos['name'],
            'buy_price': buy_price,
            'current_price': current,
            'gain_pct': gain_pct,
            'trailing_stop': trailing_stop,
            'highest_since_buy': highest,
            'distance_to_stop': distance_to_stop,
        })
    return result


@app.get("/api/hot_sectors")
async def api_hot_sectors():
    """获取热门板块分析"""
    return md.get_hot_sectors()


@app.get("/api/prediction_accuracy")
async def api_prediction_accuracy():
    """获取AI预测准确率"""
    from prediction_tracker import get_accuracy_stats
    return get_accuracy_stats()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"仪表盘启动: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
    uvicorn.run(app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
