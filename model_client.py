"""本地模型推理客户端
加载 v5 微调模型，提供 predict() 接口
自动检测 GPU，显存不够则跳过
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

try:
    import torch
except ImportError:
    torch = None
    logger.warning("torch未安装，本地模型不可用")

_model = None
_tokenizer = None
_device = None
_load_failed = False


def _load_model():
    """懒加载模型（只加载一次）"""
    global _model, _tokenizer, _device, _load_failed

    if _model is not None:
        return True
    if _load_failed:
        return False
    if torch is None:
        _load_failed = True
        return False

    import os
    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qwen-stock-lora-v6')
    if not os.path.exists(model_path):
        logger.warning("模型目录不存在，跳过本地模型")
        _load_failed = True
        return False

    try:
        from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
        from peft import PeftModel

        logger.info("加载本地模型（首次，约需30秒）...")

        # 检测 GPU 显存
        if torch.cuda.is_available():
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
            logger.info(f"检测到GPU: {torch.cuda.get_device_name(0)} ({gpu_mem:.1f}GB)")
            if gpu_mem < 1.5:
                logger.warning(f"GPU显存不足({gpu_mem:.1f}GB < 1.5GB)，尝试CPU推理")
                _use_cpu = True
            else:
                _use_cpu = False
            try:
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                )
                if _use_cpu:
                    base_model = AutoModelForCausalLM.from_pretrained(
                        "Qwen/Qwen2.5-1.5B-Instruct",
                        torch_dtype=torch.float32,
                        device_map="cpu",
                        trust_remote_code=True,
                    )
                    _device = "cpu"
                    logger.info("模型加载到 CPU（较慢但可用）")
                else:
                    base_model = AutoModelForCausalLM.from_pretrained(
                        "Qwen/Qwen2.5-1.5B-Instruct",
                    quantization_config=bnb_config,
                    device_map="auto",
                    trust_remote_code=True,
                )
                _device = "cuda"
                logger.info("模型加载到 GPU (4-bit)")
            except Exception as e:
                logger.warning(f"GPU加载失败: {e}。跳过本地模型。")
                _load_failed = True
                return False
        else:
            logger.info("无GPU，尝试CPU推理（1.5B模型，每条约5-10秒）")
            try:
                base_model = AutoModelForCausalLM.from_pretrained(
                    "Qwen/Qwen2.5-1.5B-Instruct",
                    torch_dtype=torch.float32,
                    device_map="cpu",
                    trust_remote_code=True,
                )
                _device = "cpu"
                logger.info("模型加载到 CPU")
            except Exception as e:
                logger.warning(f"CPU加载失败: {e}")
                _load_failed = True
                return False

        _model = PeftModel.from_pretrained(base_model, model_path)
        _model.eval()

        _tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        if _tokenizer.pad_token is None:
            _tokenizer.pad_token = _tokenizer.eos_token

        logger.info(f"模型加载完成 (device={_device})")
        return True

    except Exception as e:
        logger.warning(f"模型加载失败: {e}，跳过本地模型")
        _load_failed = True
        return False


# 批量预测缓存
_batch_cache = None
_batch_cache_date = None


def _get_batch_prediction(stock_name, indicators):
    """从每天批量预测文件中查找结果"""
    global _batch_cache, _batch_cache_date

    import os
    from datetime import datetime

    today = datetime.now().strftime('%Y-%m-%d')

    # 只加载一次/天
    if _batch_cache_date != today:
        pred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'model_predictions.json')
        if os.path.exists(pred_file):
            try:
                with open(pred_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                file_date = data.get('date', '')[:10]
                if file_date == today:
                    _batch_cache = data.get('predictions', {})
                    _batch_cache_date = today
                    logger.info(f"加载今日批量预测: {len(_batch_cache)} 只股票")
                else:
                    logger.debug(f"批量预测文件已过期: {file_date} (今天是{today})")
                    _batch_cache = {}
                    _batch_cache_date = today
            except Exception as e:
                logger.debug(f"读取批量预测失败: {e}")
                _batch_cache = {}
                _batch_cache_date = today
        else:
            _batch_cache = {}
            _batch_cache_date = today

    # 按股票名称查找
    if _batch_cache:
        for code, data in _batch_cache.items():
            if data.get('name') == stock_name:
                return data.get('prediction')

    return None


def predict(stock_name, indicators):
    """预测股票后续涨跌幅

    Args:
        stock_name: 股票名称
        indicators: 技术指标字典（从 market_data.get_technical_indicators 获取）

    Returns:
        dict: {"1d": float, "1w": float, "1m": float} 涨跌幅预测
        或 None（模型不可用）
    """
    # 先查批量预测文件（每天早上在AutoDL上生成一次）
    batch_result = _get_batch_prediction(stock_name, indicators)
    if batch_result:
        return batch_result

    # 回退到本地模型推理
    if not _load_model():
        return None

    import math

    def sf(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0
        return round(float(v), 4)

    rsi = sf(indicators.get('rsi'))
    macd_hist = sf(indicators.get('macd_hist'))
    macd_hist_prev = sf(indicators.get('macd_hist_prev'))
    ma5 = sf(indicators.get('ma5'))
    ma20 = sf(indicators.get('ma20'))
    close = sf(indicators.get('close'))
    vol_ratio = sf(indicators.get('vol_ratio'))
    pct = sf(indicators.get('pct_change'))
    macd_speed = round(macd_hist - macd_hist_prev, 4)

    system_msg = (
        "你是A股技术分析助手。根据技术指标预测后续涨跌幅(百分比)。"
        "只输出JSON如{\"1d\":-0.5,\"1w\":2.3,\"1m\":-1.8}"
    )
    user_msg = (
        f"分析以下数据，预测后续涨跌幅:\n\n"
        f"股票: {stock_name}\n"
        f"当前价: {close:.2f}\n"
        f"RSI: {rsi:.1f}\n"
        f"MACD柱: {macd_hist:.4f}(变化:{macd_speed:+.4f})\n"
        f"MA5: {ma5:.2f}, MA20: {ma20:.2f}\n"
        f"量比: {vol_ratio:.2f}\n"
        f"当日涨跌: {pct:+.2f}%\n"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    try:
        text = _tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = _tokenizer(text, return_tensors="pt").to(_model.device)

        with torch.no_grad():
            outputs = _model.generate(**inputs, max_new_tokens=64, do_sample=False)

        resp = _tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

        # 解析 JSON
        match = re.search(r'\{[^}]+\}', resp)
        if match:
            pred = json.loads(match.group())
            result = {
                '1d': round(float(pred.get('1d', 0)), 2),
                '1w': round(float(pred.get('1w', 0)), 2),
                '1m': round(float(pred.get('1m', 0)), 2),
            }
            logger.info(f"模型预测 {stock_name}: 1d={result['1d']:+.2f}% 1w={result['1w']:+.2f}% 1m={result['1m']:+.2f}%")
            return result
        else:
            logger.warning(f"模型输出解析失败: {resp[:100]}")
            return None

    except Exception as e:
        logger.error(f"模型推理失败: {e}")
        return None


def get_model_status():
    """获取模型状态"""
    if _model is None:
        return {"loaded": False, "device": None}
    return {"loaded": True, "device": _device}


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    test_indicators = {
        'rsi': 28.5, 'macd_hist': -2.3, 'macd_hist_prev': -3.1,
        'ma5': 1350.0, 'ma20': 1380.0, 'close': 1345.0,
        'vol_ratio': 1.2, 'pct_change': -0.8,
    }
    result = predict("贵州茅台", test_indicators)
    print(f"预测结果: {result}")
