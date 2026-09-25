"""测试微调后的模型
加载LoRA权重，对测试集做预测，与实际结果对比

用法:
  python test_finetuned.py --model ./qwen-stock-lora --data ./finetune_data
"""
import json
import os
import sys
import argparse
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel


def load_model(base_model, lora_path):
    """加载微调后的模型"""
    tokenizer = AutoTokenizer.from_pretrained(lora_path, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.float16,
        device_map="auto", trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, lora_path)
    model.eval()
    return model, tokenizer


def predict(model, tokenizer, messages):
    """对一条输入做预测"""
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=512,
            temperature=0.1,    # 低温度 = 更确定性的输出
            do_sample=False,    # 贪心解码
        )

    response = tokenizer.decode(outputs[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True)
    return response


def evaluate_predictions(predictions, actuals):
    """评估预测质量"""
    import math

    brier_scores = {'1d': [], '1w': [], '1m': []}
    direction_correct = {'1d': 0, '1w': 0, '1m': 0}
    total = 0

    for pred, actual in zip(predictions, actuals):
        if pred is None:
            continue
        total += 1

        for period in ['1d', '1w', '1m']:
            if period not in pred or f'actual_{period}' not in actual:
                continue

            pred_probs = pred[period]
            actual_ret = actual[f'actual_{period}']

            # 实际方向
            if actual_ret > 1:
                actual_vec = {'up': 1, 'flat': 0, 'down': 0}
            elif actual_ret < -1:
                actual_vec = {'up': 0, 'flat': 0, 'down': 1}
            else:
                actual_vec = {'up': 0, 'flat': 1, 'down': 0}

            # Brier Score
            bs = 0
            for key in ['up', 'flat', 'down']:
                pred_p = pred_probs.get(key, 0.33)
                actual_p = actual_vec[key]
                bs += (pred_p - actual_p) ** 2
            brier_scores[period].append(bs / 3)

            # 方向准确率
            pred_direction = max(pred_probs, key=pred_probs.get)
            if pred_direction == 'up' and actual_ret > 1:
                direction_correct[period] += 1
            elif pred_direction == 'down' and actual_ret < -1:
                direction_correct[period] += 1
            elif pred_direction == 'flat' and -1 <= actual_ret <= 1:
                direction_correct[period] += 1

    print(f"\n{'='*60}")
    print("  Evaluation Results")
    print(f"{'='*60}")
    print(f"  Total predictions: {total}")

    for period in ['1d', '1w', '1m']:
        bs_list = brier_scores[period]
        if bs_list:
            avg_bs = sum(bs_list) / len(bs_list)
            acc = direction_correct[period] / len(bs_list) * 100
            print(f"\n  {period}:")
            print(f"    Brier Score: {avg_bs:.4f} (lower is better, random=0.222)")
            print(f"    Direction Accuracy: {acc:.1f}%")
            print(f"    Samples: {len(bs_list)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='./qwen-stock-lora', help='LoRA model path')
    parser.add_argument('--base', default='Qwen/Qwen2.5-7B-Instruct', help='Base model')
    parser.add_argument('--data', default='./finetune_data', help='Data directory')
    parser.add_argument('--limit', type=int, default=50, help='Max test samples')
    args = parser.parse_args()

    print("Loading model...")
    model, tokenizer = load_model(args.base, args.model)

    # 加载测试数据
    test_path = os.path.join(args.data, 'test.jsonl')
    test_data = []
    with open(test_path, 'r', encoding='utf-8') as f:
        for line in f:
            test_data.append(json.loads(line))

    print(f"Test samples: {len(test_data)}")
    print(f"Testing on {min(args.limit, len(test_data))} samples...")

    predictions = []
    actuals = []
    errors = 0

    for i, messages in enumerate(test_data[:args.limit]):
        # 输入（system + user）
        input_messages = messages[:-1]
        # 正确答案
        expected = json.loads(messages[-1]['content'])

        try:
            response = predict(model, tokenizer, input_messages)
            pred = json.loads(response.strip())
            predictions.append(pred)
            actuals.append(expected)

            if i < 3:  # 打印前3个
                print(f"\n--- Sample {i+1} ---")
                print(f"  Expected: 1d={expected['actual_1d']:+.2f}%  1w={expected['actual_1w']:+.2f}%  1m={expected['actual_1m']:+.2f}%")
                if '1d' in pred:
                    p = pred['1d']
                    print(f"  Predicted 1d: up={p.get('up',0):.2f} flat={p.get('flat',0):.2f} down={p.get('down',0):.2f}")
                if '1w' in pred:
                    p = pred['1w']
                    print(f"  Predicted 1w: up={p.get('up',0):.2f} flat={p.get('flat',0):.2f} down={p.get('down',0):.2f}")

        except Exception as e:
            errors += 1
            predictions.append(None)
            actuals.append(expected)

    print(f"\nParse errors: {errors}/{min(args.limit, len(test_data))}")

    # 评估
    evaluate_predictions(predictions, actuals)


if __name__ == "__main__":
    main()
