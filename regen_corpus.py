"""语料库 register 多样化重写——治本方案第 1 步
从 prediction_tracker 读取 331 条结构化数据（不动 ground truth 字段），
用 LLM 按 4 种 register 重写 reasoning 文本，生成多样化语料。

输出：data/corpus_diverse.jsonl
每行: {pred_id, register, doc_id, text, metadata{...原字段}}
"""
import json
import os
import re
import time
import logging
from openai import OpenAI

import prediction_tracker

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def _load_cc_switch_config():
    """读 cc-switch 配置（~/.claude/settings.json 的 env 字段）作为生成通道。
    该通道经 Claude Code 连日使用验证稳定；Agnes 高峰期 Connection error 率过高降为备用。"""
    settings_path = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
    with open(settings_path, 'r', encoding='utf-8') as f:
        env = json.load(f).get('env', {})
    return {
        "api_key": env.get('ANTHROPIC_AUTH_TOKEN', ''),
        "base_url": env.get('ANTHROPIC_BASE_URL', ''),
        "model": re.sub(r'\[.*?\]$', '', env.get('ANTHROPIC_MODEL', '')).strip(),
    }


_cc = _load_cc_switch_config()
# cc-switch 的 base_url 不带 /v1 后缀（Anthropic 风格），OpenAI SDK 需要补上
_base = _cc['base_url'].rstrip('/')
client = OpenAI(api_key=_cc['api_key'], base_url=_base + '/v1')
MODEL = _cc['model']
logger.info(f"生成通道: {MODEL} @ {_base}/v1")

# 4 种 register 的生成指令
REGISTERS = {
    "research": {
        "desc": "券商研报体",
        "instruction": (
            "以专业券商研报的书面风格重写这段股票分析。要求：术语密集、句式严谨、无比喻、"
            "无口语、无教学语气；数据引用精确（如 RSI(14) 报 21.04、MA5=5.13）；"
            "结论表述使用规范措辞（如'建议保持谨慎''等待右侧信号''维持观望评级'）。"
            "保留原文的核心事实（价格、指标数值、结论方向），全部重写表达。输出一段 80~150 字的正文，不要标题。"
        ),
    },
    "desk": {
        "desc": "交易台快讯体",
        "instruction": (
            "以专业交易员在交易台内部沟通的极简风格重写这段股票分析。要求：短句、电报体、"
            "只用关键数字和判断（如'5.10 破位，RSI 21 超卖，不接，等柱翻红'）；"
            "不用标点修辞，不超过 60 字；保留结论方向不变。"
        ),
    },
    "forum": {
        "desc": "散户论坛体",
        "instruction": (
            "以散户在股票论坛发帖的口语风格重写这段股票分析。要求：像真实股民发言，"
            "情绪化、口语化、可以用'这票''干不干''慌不慌'这类表达，1~2 句；"
            "不出现任何专业术语原文（不说RSI/均线/MACD，用'跌成这样''能不能接'代替）；"
            "保留结论方向不变（观望的就表达犹豫，买入的就表达想冲）。"
        ),
    },
    # teaching register = 现有文档，不重新生成，直接复用
}

# 每条预测生成哪些 register（teaching 复用原文）
REGISTERS_PER_PRED = ["research", "desk", "forum"]


def build_prompt(pred, register):
    """从结构化字段构造生成 prompt——ground truth 全部来自字段，不依赖原 reasoning 文风"""
    signals = pred.get('key_signals') or []
    signals_str = '；'.join(signals) if isinstance(signals, list) else str(signals)
    conclusion = pred.get('recommendation', '')
    # 先给结论方向定调，减少模型对"结论是否要改"的犹豫式思考（15500+字思考的根因）
    if '买' in conclusion:
        stance = "结论方向：看多，输出文本应表达看多/建议参与的立场。"
    elif '卖' in conclusion or '止损' in conclusion:
        stance = "结论方向：看空，输出文本应表达离场/回避的立场。"
    else:
        stance = "结论方向：观望/谨慎，输出文本应表达犹豫、等待、不急于进场的立场。"
    return (
        f"{REGISTERS[register]['instruction']}\n\n"
        f"【原始事实】\n"
        f"股票：{pred.get('name')}({pred.get('code')})\n"
        f"日期：{pred.get('date','')[:10]}\n"
        f"当时价格：{pred.get('price_at_prediction')}\n"
        f"关键信号：{signals_str}\n"
        f"结论：{conclusion}（置信度{pred.get('confidence',0):.0%}）\n"
        f"{stance} 不要改结论方向，不要纠结措辞是否完美，直接按要求的风格写出正文。\n"
        f"直接输出正文，禁止输出任何解释、思考过程或元评论。"
    )


def rewrite(pred, register, retry=4):
    for attempt in range(retry + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                # 推理模型思考 token 计入 max_tokens，8000 防截断；
                # 温度随重试递增，避免同一思路反复烧 token
                max_tokens=8000,
                temperature=0.8 + attempt * 0.4,
                messages=[{'role': 'user', 'content': build_prompt(pred, register)}],
            )
            msg = resp.choices[0].message
            content = (msg.content or '').strip()
            if not content and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
                logger.warning(f"{pred['id']}/{register} 第{attempt+1}次 content空"
                               f"(reasoning {len(msg.reasoning_content)}字, finish={resp.choices[0].finish_reason})，重试")
                continue
            if content:
                time.sleep(0.5)  # 限速，防触发服务端限流
                return content
        except Exception as e:
            logger.warning(f"重写失败 {pred['id']}/{register} 第{attempt+1}次: {e}")
            time.sleep(2 ** attempt)
    return None


def sanitize(text):
    """防止 LLM 把禁用结构泄漏进正文（如把指令复读出来）"""
    text = re.sub(r'【[^】]*】', '', text)
    return text.strip()


def main():
    preds = prediction_tracker._load()
    logger.info(f"读取 {len(preds)} 条预测记录")

    out_path = os.path.join('data', 'corpus_diverse.jsonl')
    done_keys = set()
    if os.path.exists(out_path):
        # 断点续传
        with open(out_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done_keys.add((r['pred_id'], r['register']))
                except json.JSONDecodeError:
                    continue
        logger.info(f"已有 {len(done_keys)} 条完成，断点续传")

    total = len(preds) * len(REGISTERS_PER_PRED)
    count = 0
    with open(out_path, 'a', encoding='utf-8') as f:
        for pred in preds:
            # 跳过股票名未知的脏数据
            if not pred.get('name') or pred.get('name') == '未知':
                continue
            for register in REGISTERS_PER_PRED:
                key = (pred['id'], register)
                if key in done_keys:
                    continue
                text = rewrite(pred, register)
                if text is None:
                    logger.error(f"放弃: {pred['id']}/{register}")
                    continue
                text = sanitize(text)
                record = {
                    "pred_id": pred['id'],
                    "register": register,
                    "doc_id": f"pred_{pred['id']}__{register}",
                    "text": text,
                    "metadata": {
                        "code": pred.get('code', ''),
                        "name": pred.get('name', ''),
                        "date": pred.get('date', '')[:10],
                        "recommendation": pred.get('recommendation', ''),
                        "confidence": float(pred.get('confidence') or 0),
                        "outcome": pred.get('outcome', '待验证'),
                        "key_signals": json.dumps(pred.get('key_signals') or [], ensure_ascii=False),
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
                f.flush()  # Windows 后台重定向下缓冲不自动刷盘，实时落盘保证断点有效
                count += 1
                if count % 20 == 0:
                    logger.info(f"进度 {count}/{total} ({count/total:.0%})")
    logger.info(f"完成，新生成 {count} 条 → {out_path}")


if __name__ == '__main__':
    main()
