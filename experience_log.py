"""交易经验记忆日志——TradingAgents 式 append-only 结构化日志
替代向量 RAG（rag_store.py）作为 ai-trader 的经验记忆层。

设计参照 TauricResearch/TradingAgents agents/utils/memory.py：
- append-only markdown 日志，HTML 注释分隔符
- pending → resolved 两阶段（结果回填后才有"经验"资格）
- 规则注入：同股票 N 条完整决策 + 跨股票 M 条反思教训
- as_of 时间点过滤（回测不偷看未来）

差异（适配本项目）：
- 数据源直接挂 prediction_tracker（已有 outcome/actual_change_pct 字段）
- 决策文本 = prediction_tracker 的 reasoning（不重复存储，日志存引用+摘要）
"""
import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

LOG_PATH = os.path.join('data', 'experience_log.md')
SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"

# 注入配额（TradingAgents 默认：同标的5 + 跨标的3）
N_SAME = 5
N_CROSS = 3
# 反思字数上限（ TradingAgents 反思 prompt：2-4 句约束的落地）
REFLECTION_MAX_CHARS = 150

_TAG_RE = re.compile(
    r'^\[(?P<date>[\d\-]+) \| (?P<code>[^|\]]+?) \| (?P<name>[^|\]]+?) \| '
    r'(?P<recommendation>[^|\]]+?) \| (?P<confidence>\d+%) \| '
    r'(?P<status>pending|resolved)'
    r'(?: \| (?P<change>[+\-][\d.]+%))?'
    r'\]$'
)
# REFLECTION 条目 tag 与决策条目不同：confidence 位是结果百分比（可带%号）
_REFLECTION_TAG_RE = re.compile(
    r'^\[(?P<date>[\d\-]+) \| (?P<code>\d{6}) \| (?P<name>[^|\]]+?) \| REFLECTION \| '
    r'(?P<change>[+\-][\d.]+%) \| resolved\]$'
)


def _load_predictions():
    import prediction_tracker
    return prediction_tracker._load()


def build_log(preds=None):
    """从 prediction_tracker 全量重建日志（幂等，首次迁移用）"""
    preds = preds or _load_predictions()
    lines = []
    for p in preds:
        code = p.get('code', '')
        if not re.fullmatch(r'\d{6}', code or ''):
            continue  # 脏数据（未知股票）不进日志
        outcome = p.get('outcome') or 'pending'
        resolved = bool(p.get('resolved_date') or (outcome not in ('pending', '待验证', None)))
        status = 'resolved' if resolved else 'pending'
        change = p.get('actual_change_pct')
        tag = (f"[{p.get('date','')[:10]} | {code} | {p.get('name','')} | "
               f"{p.get('recommendation','')} | {round((p.get('confidence') or 0)*100)}% | {status}")
        if resolved and change is not None:
            # actual_change_pct 存的是数值百分比（-6.96 = -6.96%），需 /100 转比例
            tag += f" | {change/100:+.1%}"
        tag += "]"
        lines.append(
            f"{tag}\n\nDECISION:\n{p.get('reasoning', '').strip()}{SEPARATOR}"
        )
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        f.write(''.join(lines))
    logger.info(f"经验日志重建: {len(lines)} 条 → {LOG_PATH}")
    return len(lines)


def parse_entries(path=LOG_PATH):
    """解析日志 → 条目列表"""
    if not os.path.exists(path):
        return []
    text = open(path, 'r', encoding='utf-8').read()
    entries = []
    for block in text.split(SEPARATOR):
        block = block.strip()
        if not block:
            continue
        lines = block.split('\n', 1)
        tag_line = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ''
        m = _REFLECTION_TAG_RE.match(tag_line)
        if m:
            # body 形如 "DECISION:\n<正文>\n\nREFLECTION:\n<反思>"，剥掉 DECISION: 前缀
            dm = re.search(r'DECISION:\n(.*?)(?:\nREFLECTION:\n(.*))?$', body, re.DOTALL)
            decision_text = (dm.group(1).strip() if dm and dm.group(1) else body)
            reflection_text = (dm.group(2).strip() if dm and dm.group(2) else '')
            entries.append({**m.groupdict(), 'confidence': '', 'status': 'resolved',
                            'recommendation': 'REFLECTION',
                            'decision': decision_text,
                            'reflection': reflection_text})
            continue
        m = _TAG_RE.match(tag_line)
        if not m:
            continue
        dm = re.search(r'DECISION:\n(.*)', body, re.DOTALL)
        entries.append({
            **m.groupdict(),
            'decision': dm.group(1).strip() if dm else '',
        })
    return entries


def mark_resolved(preds=None):
    """每日收盘后跑：outcome 已验证的 pending 条目回填结果（两阶段第二阶段）"""
    preds = {p['id']: p for p in (preds or _load_predictions())}
    entries = parse_entries()
    updated = 0
    for e in entries:
        if e['status'] != 'pending':
            continue
        # 按日期+代码匹配 prediction_tracker
        match = next((p for p in preds.values()
                      if p.get('code') == e['code'] and p.get('date', '')[:10] == e['date']
                      and (p.get('resolved_date') or
                           (p.get('outcome') not in ('pending', '待验证', None)))),
                     None)
        if not match:
            continue
        change = match.get('actual_change_pct')
        e['status'] = 'resolved'
        if change is not None:
            # 存数值百分比（-6.96 = -6.96%），转比例字符串
            e['change'] = f'{change/100:+.1%}'
        updated += 1
    if updated:
        _rewrite_entries(entries)
        logger.info(f"回填 {updated} 条结果")
    return updated


def _rewrite_entries(entries):
    blocks = []
    for e in entries:
        tag = (f"[{e['date']} | {e['code']} | {e['name']} | "
               f"{e['recommendation']} | {e['confidence']} | {e['status']}")
        if e['status'] == 'resolved' and e.get('change'):
            tag += f" | {e['change']}"
        tag += "]"
        blocks.append(f"{tag}\n\nDECISION:\n{e['decision']}{SEPARATOR}")
    tmp = LOG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(''.join(blocks))
    os.replace(tmp, LOG_PATH)  # 原子替换，崩溃不损坏


def get_past_context(code, as_of=None, n_same=N_SAME, n_cross=N_CROSS):
    """规则注入上下文——给 LLM prompt 的历史经验段
    同股票: 最近 n_same 条（完整 DECISION，含结果）
    跨股票: 最近 n_cross 条已验证教训（反思用途，截断防膨胀）
    as_of: 只用该日期前已 resolved 的条目（回测防泄漏）
    """
    entries = [e for e in parse_entries() if e['status'] == 'resolved']
    if as_of:
        entries = [e for e in entries if e['date'] <= as_of]
    if not entries:
        return ""

    same, cross = [], []
    for e in reversed(entries):  # 最新在前
        if len(same) >= n_same and len(cross) >= n_cross:
            break
        if e['code'] == code and len(same) < n_same:
            same.append(e)
        elif e['code'] != code and len(cross) < n_cross:
            cross.append(e)

    parts = []
    if same:
        parts.append(f"## {code} 的历史决策与结果（最近在前）")
        for e in same:
            change = e.get('change', 'n/a')
            parts.append(f"- [{e['date']}] {e['recommendation']}({e['confidence']}) "
                         f"实际{change}：{e['decision'][:120]}")
    if cross:
        parts.append("## 其他股票的已验证教训")
        for e in cross:
            change = e.get('change', 'n/a')
            parts.append(f"- [{e['date']}|{e['name']}] {e['recommendation']}→{change}："
                         f"{e['decision'][:80]}")
    return '\n'.join(parts)


def reflect_and_append(code, name, decision_text, actual_change, reflection):
    """结果验证后追加反思（由每日复盘任务调用；reflection 由 LLM 生成，2-4 句）
    """
    if len(reflection) > REFLECTION_MAX_CHARS:
        reflection = reflection[:REFLECTION_MAX_CHARS] + '…'
    tag = (f"[{datetime.now().strftime('%Y-%m-%d')} | {code} | {name} | "
           f"REFLECTION | {actual_change:+.1%} | resolved]")
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"{tag}\n\nDECISION:\n{decision_text}\n\nREFLECTION:\n{reflection}{SEPARATOR}")


REFLECTION_PROMPT = (
    "你是一个交易复盘助手。下面是一次股票分析决策和它的真实结果。\n"
    "写 2-4 句反思，纯文本无格式。按顺序覆盖：\n"
    "1. 方向判断对不对（引用实际涨跌数字）\n"
    "2. 论证里哪个环节成立/失效\n"
    "3. 一条下次分析同类情况的具体教训\n"
    "简洁、具体。你的输出会被存档并供未来分析参考，每个字都要有信息量。\n\n"
    "决策时的分析：\n{decision}\n\n"
    "实际结果：{actual:+.1%}（结论是{rec}）"
)


def reflect_predictions(limit=None):
    """每日复盘：扫描已验证但还没有对应反思的决策 → LLM 生成反思 → 追加日志
    判定依据：日志中 REFLECTION 条目的 decision 字段（已剥前缀）与 reasoning 前80字比对
    Returns: 生成反思的条数
    """
    entries = parse_entries()
    existing = {e['decision'].strip()[:80]
                for e in entries if e['recommendation'] == 'REFLECTION'}

    import prediction_tracker
    preds = prediction_tracker._load()
    # 只取已验证（有实际涨跌）的预测
    done = [p for p in preds
            if p.get('actual_change_pct') is not None
            and re.fullmatch(r'\d{6}', p.get('code', '') or '')
            and p.get('reasoning')]
    if limit:
        done = done[:limit]

    generated = 0
    for p in done:
        fingerprint = p['reasoning'].strip()[:80]
        if fingerprint in existing:
            continue  # 已反思过，幂等跳过
        decision = f"{p.get('name','')}({p['code']}) {p.get('recommendation','')} {p['reasoning']}"
        prompt = REFLECTION_PROMPT.format(
            decision=decision[:800],
            actual=p['actual_change_pct'] / 100,
            rec=p.get('recommendation', ''),
        )
        reflection = _call_llm_reflection(prompt)
        if not reflection:
            continue
        reflect_and_append(
            p['code'], p.get('name', ''), p['reasoning'][:300],
            p['actual_change_pct'] / 100, reflection,
        )
        generated += 1
    logger.info(f"反思生成: {generated} 条")
    return generated


def _call_llm_reflection(prompt):
    """反思生成的 LLM 调用（cc-switch 通道，与 regen_corpus 同配置）"""
    try:
        from openai import OpenAI
        settings_path = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
        with open(settings_path, 'r', encoding='utf-8') as f:
            env = json.load(f).get('env', {})
        client = OpenAI(
            api_key=env.get('ANTHROPIC_AUTH_TOKEN', ''),
            base_url=env.get('ANTHROPIC_BASE_URL', '').rstrip('/') + '/v1',
        )
        model = re.sub(r'\[.*?\]$', '', env.get('ANTHROPIC_MODEL', '')).strip()
        resp = client.chat.completions.create(
            model=model, max_tokens=4000, temperature=0.5,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return (resp.choices[0].message.content or '').strip() or None
    except Exception as e:
        logger.warning(f"反思生成失败: {e}")
        return None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    n = build_log()
    entries = parse_entries()
    resolved = [e for e in entries if e['status'] == 'resolved']
    print(f"总条目: {len(entries)}, 已验证: {len(resolved)}")
    print("\n=== 茅台(600519)注入示例 ===")
    ctx = get_past_context('600519')
    print(ctx[:600] or '（无已验证条目）')
