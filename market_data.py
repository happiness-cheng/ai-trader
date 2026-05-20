"""行情数据模块
东方财富 push2.eastmoney.com（大盘/实时）+ 新浪财经（历史K线，因为push2his被服务端断连）
"""
import json
import logging
import subprocess
import pandas as pd
import ta
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def _curl_get(url, params=None):
    """用 curl 发请求（绕过 Python requests 的代理 TLS 问题）"""
    if params:
        param_str = '&'.join(f'{k}={v}' for k, v in params.items())
        full_url = f"{url}?{param_str}"
    else:
        full_url = url

    cmd = [
        'curl', '-s', '--connect-timeout', '10', '--max-time', '30',
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        full_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding='utf-8')
        if result.returncode == 0 and result.stdout:
            return result.stdout
        else:
            logger.error(f"curl失败: code={result.returncode}")
            return None
    except Exception as e:
        logger.error(f"curl异常: {e}")
        return None


def _curl_json(url, params=None):
    """curl 获取并解析 JSON"""
    text = _curl_get(url, params)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error(f"JSON解析失败: {text[:200]}")
    return None


def get_stock_history(symbol, period="daily", days=120):
    """获取股票历史K线数据（新浪财经源）
    Args:
        symbol: 股票代码，如 '600519'
        days: 获取最近N天的数据
    Returns:
        DataFrame
    """
    try:
        # 新浪K线API格式: sh600519 或 sz000858
        if symbol.startswith('6'):
            sina_code = f'sh{symbol}'
        else:
            sina_code = f'sz{symbol}'

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
        text = _curl_get(url, {
            'symbol': sina_code,
            'scale': '240',  # 日K
            'ma': 'no',
            'datalen': str(days),
        })

        if not text:
            logger.warning(f"获取 {symbol} 历史数据为空")
            return pd.DataFrame()

        # 新浪返回的是 JSON（可能没有标准引号）
        # 修复可能的非标准 JSON
        text = text.replace("'", '"')
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试用eval解析（新浪有时返回Python字典格式）
            try:
                data = eval(text) if text.startswith('[') else []
            except:
                logger.error(f"无法解析新浪数据: {text[:200]}")
                return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        rows = []
        for item in data:
            rows.append({
                'date': item.get('day', ''),
                'open': float(item.get('open', 0)),
                'close': float(item.get('close', 0)),
                'high': float(item.get('high', 0)),
                'low': float(item.get('low', 0)),
                'volume': float(item.get('volume', 0)),
            })

        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # 补充计算涨跌幅
        df['pct_change'] = df['close'].pct_change() * 100
        df['change'] = df['close'].diff()
        df['amount'] = 0.0
        df['amplitude'] = 0.0
        df['turnover'] = 0.0

        logger.info(f"获取 {symbol} 历史数据: {len(df)} 条")
        return df

    except Exception as e:
        logger.error(f"获取 {symbol} 历史数据失败: {e}")
        return pd.DataFrame()


def get_realtime_quote(symbol):
    """获取实时行情（东方财富源）"""
    try:
        if symbol.startswith('6'):
            secid = f'1.{symbol}'
        else:
            secid = f'0.{symbol}'

        data = _curl_json('https://push2.eastmoney.com/api/qt/stock/get', {
            'secid': secid,
            'fields': 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117',
        })

        if not data or not data.get('data'):
            return {}

        d = data['data']
        price = d.get('f43', 0)
        if isinstance(price, (int, float)):
            price = price / 100 if price else 0

        return {
            'code': symbol,
            'name': d.get('f58', ''),
            'price': price,
            'high': (d.get('f44', 0) or 0) / 100,
            'low': (d.get('f45', 0) or 0) / 100,
            'open': (d.get('f46', 0) or 0) / 100,
            'close_prev': (d.get('f60', 0) or 0) / 100,
            'volume': d.get('f47', 0) or 0,
            'amount': d.get('f48', 0) or 0,
        }
    except Exception as e:
        logger.error(f"获取 {symbol} 实时行情失败: {e}")
        return {}


def get_technical_indicators(df):
    """计算技术指标"""
    if len(df) < 30:
        logger.warning("数据量不足以计算技术指标")
        return {}

    try:
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']

        macd = ta.trend.MACD(close, window_slow=config.MACD_SLOW,
                             window_fast=config.MACD_FAST,
                             window_sign=config.MACD_SIGNAL)
        rsi = ta.momentum.RSIIndicator(close, window=config.RSI_PERIOD)
        ma_short = close.rolling(window=config.MA_SHORT).mean()
        ma_long = close.rolling(window=config.MA_LONG).mean()
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        vol_ma5 = volume.rolling(window=5).mean()
        vol_ma20 = volume.rolling(window=20).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        indicators = {
            'macd_line': round(macd.macd().iloc[-1], 4),
            'macd_signal': round(macd.macd_signal().iloc[-1], 4),
            'macd_hist': round(macd.macd_diff().iloc[-1], 4),
            'macd_hist_prev': round(macd.macd_diff().iloc[-2], 4) if len(df) > 1 else 0,
            'rsi': round(rsi.rsi().iloc[-1], 2),
            'ma5': round(ma_short.iloc[-1], 2),
            'ma20': round(ma_long.iloc[-1], 2),
            'ma5_prev': round(ma_short.iloc[-2], 2) if len(df) > 1 else 0,
            'ma20_prev': round(ma_long.iloc[-2], 2) if len(df) > 1 else 0,
            'bb_upper': round(bb.bollinger_hband().iloc[-1], 2),
            'bb_middle': round(bb.bollinger_mavg().iloc[-1], 2),
            'bb_lower': round(bb.bollinger_lband().iloc[-1], 2),
            'vol_ma5': round(vol_ma5.iloc[-1], 0),
            'vol_ma20': round(vol_ma20.iloc[-1], 0),
            'vol_ratio': round(volume.iloc[-1] / vol_ma20.iloc[-1], 2) if vol_ma20.iloc[-1] > 0 else 1,
            'close': round(float(latest['close']), 2),
            'close_prev': round(float(prev['close']), 2),
            'high': round(float(latest['high']), 2),
            'low': round(float(latest['low']), 2),
            'pct_change': round(float(latest.get('pct_change', 0)), 2),
            'date': str(latest['date'].date()) if hasattr(latest['date'], 'date') else str(latest['date']),
        }

        indicators['macd_golden_cross'] = bool(
            indicators['macd_hist'] > 0 and indicators['macd_hist_prev'] <= 0
        )
        indicators['macd_death_cross'] = bool(
            indicators['macd_hist'] < 0 and indicators['macd_hist_prev'] >= 0
        )
        indicators['ma_golden_cross'] = bool(
            indicators['ma5'] > indicators['ma20'] and indicators['ma5_prev'] <= indicators['ma20_prev']
        )
        indicators['rsi_oversold'] = bool(indicators['rsi'] < 30)
        indicators['rsi_overbought'] = bool(indicators['rsi'] > 70)
        indicators['price_above_ma5'] = bool(indicators['close'] > indicators['ma5'])
        indicators['price_above_ma20'] = bool(indicators['close'] > indicators['ma20'])
        indicators['volume_surge'] = bool(indicators['vol_ratio'] > 2.0)

        return indicators

    except Exception as e:
        logger.error(f"计算技术指标失败: {e}")
        return {}


def get_market_overview():
    """获取大盘概况（东方财富源）"""
    try:
        indices = {}
        for name, secid in [('上证指数', '1.000001'), ('深证成指', '0.399001'), ('创业板指', '0.399006')]:
            data = _curl_json('https://push2.eastmoney.com/api/qt/stock/get', {
                'secid': secid,
                'fields': 'f43,f44,f45,f46,f47,f48,f58,f60',
            })
            if data and data.get('data'):
                d = data['data']
                price = (d.get('f43', 0) or 0) / 100
                prev = (d.get('f60', 0) or 0) / 100
                change_pct = round((price - prev) / prev * 100, 2) if prev else 0
                indices[name] = {
                    'price': price,
                    'change_pct': change_pct,
                    'change': round(price - prev, 2),
                }
        return indices
    except Exception as e:
        logger.error(f"获取大盘概况失败: {e}")
        return {}


def get_hot_sectors():
    """获取热门板块"""
    try:
        data = _curl_json('https://push2.eastmoney.com/api/qt/clist/get', {
            'pn': '1', 'pz': '10', 'po': '1', 'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2', 'invt': '2',
            'fid': 'f3',
            'fs': 'm:90+t:2',
            'fields': 'f2,f3,f4,f8,f14,f128,f136,f115',
        })
        if not data or not data.get('data', {}).get('diff'):
            return []

        sectors = []
        for item in data['data']['diff'][:10]:
            sectors.append({
                'name': item.get('f14', ''),
                'change_pct': round((item.get('f3', 0) or 0), 2),
            })
        return sectors
    except Exception as e:
        logger.error(f"获取热门板块失败: {e}")
        return []


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    print("=== 大盘概况 ===")
    for name, data in get_market_overview().items():
        print(f"  {name}: {data['price']} ({data['change_pct']}%)")

    print("\n=== 贵州茅台 ===")
    df = get_stock_history('600519', days=60)
    if len(df) > 0:
        print(f"  数据: {len(df)} 条")
        ind = get_technical_indicators(df)
        print(f"  MACD: {ind.get('macd_hist')}")
        print(f"  RSI: {ind.get('rsi')}")
        print(f"  MA5: {ind.get('ma5')} / MA20: {ind.get('ma20')}")
        print(f"  收盘: {ind.get('close')}")
    else:
        print("  无数据")
