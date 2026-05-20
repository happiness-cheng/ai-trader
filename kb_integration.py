"""知识库集成模块
将交易日志总结成 Markdown 写入知识库，从知识库读取历史经验辅助决策
"""
import json
import logging
import os
from datetime import datetime, timedelta

import config
import trade_logger as tlog

logger = logging.getLogger(__name__)

# 知识库 API 地址（本地运行）
KB_API = "http://127.0.0.1:8766"

# 本地 Markdown 存储（作为缓存，也方便知识库导入）
KB_DIR = os.path.join(config.LOG_DIR, 'kb_notes')
os.makedirs(KB_DIR, exist_ok=True)


def _kb_request(method, endpoint, data=None):
    """调用知识库 API"""
    import requests
    try:
        url = f"{KB_API}{endpoint}"
        if method == 'GET':
            resp = requests.get(url, timeout=10)
        elif method == 'POST':
            resp = requests.post(url, json=data, timeout=10)
        else:
            return None
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        logger.debug(f"知识库 API 不可用: {e}")
        return None


# ========== 交易记录 → Markdown ==========

def generate_daily_markdown(date_str=None):
    """将某天的交易记录总结成 Markdown"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    trades = tlog.read_recent_trades(100)
    decisions = tlog.read_recent_ai_decisions(100)
    signals = tlog.read_today_logs('signals')
    logs = tlog.read_today_logs()

    md = f"# 交易日志 - {date_str}\n\n"

    # 大盘概况
    market_logs = [l for l in logs if l.get('type') == 'market']
    if market_logs:
        latest = market_logs[-1]
        md += "## 大盘概况\n\n"
        for name, data in latest.get('data', {}).items():
            pct = data.get('change_pct', 0)
            md += f"- **{name}**: {data.get('price', 0)} ({'+' if pct > 0 else ''}{pct}%)\n"
        md += "\n"

    # 交易记录
    if trades:
        md += "## 交易记录\n\n"
        md += "| 时间 | 操作 | 股票 | 数量 | 价格 | 结果 | 原因 |\n"
        md += "|------|------|------|------|------|------|------|\n"
        for t in trades:
            time_str = t.get('time', '')[-8:]
            action = t.get('action', '')
            name = t.get('name', '')
            qty = t.get('quantity', 0)
            price = t.get('price', 0)
            success = '✅' if t.get('success') else '❌'
            reason = t.get('reason', '')[:50]
            md += f"| {time_str} | {action} | {name} | {qty} | {price} | {success} | {reason} |\n"
        md += "\n"

    # AI 决策记录
    if decisions:
        md += "## AI 决策记录\n\n"
        for d in decisions:
            time_str = d.get('time', '')[-8:]
            name = d.get('name', '')
            rec = d.get('recommendation', '')
            conf = d.get('confidence', 0)
            risk = d.get('risk_level', '')
            reasoning = d.get('reasoning', '')[:200]
            md += f"### {time_str} - {name}\n\n"
            md += f"- **建议**: {rec} (置信度 {conf:.0%})\n"
            md += f"- **风险**: {risk}\n"
            md += f"- **分析**: {reasoning}\n\n"

    # 信号记录
    if signals:
        md += "## 技术信号\n\n"
        for s in signals:
            if s.get('signal') not in ('hold',):
                md += f"- {s.get('time', '')[-8:]} **{s.get('name', '')}**: {s.get('signal', '')} - {s.get('details', '')}\n"
        md += "\n"

    # 经验总结（当天学到的）
    md += "## 经验总结\n\n"
    md += "_（每天收盘后由 AI 自动填写）_\n\n"

    return md


def save_daily_note_to_kb(date_str=None):
    """保存当天笔记到知识库"""
    md_content = generate_daily_markdown(date_str)
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    # 保存到本地
    filepath = os.path.join(KB_DIR, f'{date_str}.md')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md_content)
    logger.info(f"笔记已保存: {filepath}")

    # 尝试写入知识库
    result = _kb_request('POST', '/api/nodes', {
        'title': f'交易日志 {date_str}',
        'content': md_content,
        'tags': ['交易日志', 'AI Trader'],
    })
    if result:
        logger.info(f"笔记已写入知识库")
    else:
        logger.info("知识库不可用，仅保存本地")

    return filepath


# ========== 知识库 → 历史经验 ==========

def get_trading_lessons():
    """从知识库获取历史交易经验（用于注入 AI 分析 prompt）"""
    # 先尝试从知识库 API 搜索
    result = _kb_request('GET', '/api/entries?tag=交易经验&limit=10')
    if result and result.get('entries'):
        lessons = []
        for entry in result['entries']:
            lessons.append(entry.get('content', '')[:500])
        return '\n\n'.join(lessons)

    # 知识库不可用，从本地读取
    lessons_file = os.path.join(config.DATA_DIR, 'lessons.json')
    if os.path.exists(lessons_file):
        with open(lessons_file, 'r', encoding='utf-8') as f:
            lessons = json.load(f)
        return '\n\n'.join(lessons[-10:])  # 最近10条

    return ''


def save_lesson(title, content):
    """保存一条经验教训"""
    lessons_file = os.path.join(config.DATA_DIR, 'lessons.json')
    lessons = []
    if os.path.exists(lessons_file):
        with open(lessons_file, 'r', encoding='utf-8') as f:
            lessons = json.load(f)

    lessons.append({
        'date': datetime.now().strftime('%Y-%m-%d'),
        'title': title,
        'content': content,
    })

    with open(lessons_file, 'w', encoding='utf-8') as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)

    logger.info(f"保存经验: {title}")


def get_lessons_prompt_context():
    """生成经验注入的 prompt 片段"""
    lessons = get_trading_lessons()
    if not lessons:
        return ''
    return f"\n\n## 历史经验教训（请参考这些经验做决策）\n{lessons}\n"


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("=== 生成今日 Markdown ===")
    md = generate_daily_markdown()
    print(md[:500])

    print("\n=== 保存到知识库 ===")
    path = save_daily_note_to_kb()
    print(f"保存到: {path}")
