"""Qwen2.5-7B LoRA 微调脚本
在云GPU上运行（AutoDL / Google Colab）

使用方法:
  1. 上传 finetune_data/ 目录到云端
  2. 上传本脚本到云端
  3. 运行: python finetune_qwen.py

硬件要求:
  - 最低: T4 16G (Google Colab免费)
  - 推荐: A100 40G (AutoDL约3元/小时)
  - 7B模型 + LoRA 约需 14G 显存
"""
import os
import json
import torch
from datasets import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TrainingArguments,
    Trainer,
    DataCollatorForSeq2Seq,
)
from peft import LoraConfig, get_peft_model, TaskType

# ========== 配置 ==========

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # 基础模型
DATA_DIR = "./finetune_data"              # 训练数据目录
OUTPUT_DIR = "./qwen-stock-lora"          # 输出目录

# LoRA 配置
LORA_CONFIG = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=8,                    # LoRA秩（越大表达能力越强，但显存占用越高）
    lora_alpha=32,          # 缩放因子
    lora_dropout=0.1,       # dropout
    target_modules=[        # 要微调的层
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
)

# 训练配置
TRAINING_ARGS = TrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=3,              # 训练轮数（3轮通常够了）
    per_device_train_batch_size=4,   # 批大小（T4用2，A100用4-8）
    per_device_eval_batch_size=4,
    gradient_accumulation_steps=4,   # 梯度累积（等效batch=16）
    learning_rate=2e-4,              # 学习率（LoRA通常用较大的lr）
    weight_decay=0.01,
    warmup_ratio=0.1,                # warmup比例
    logging_steps=10,                # 每10步打印一次loss
    eval_strategy="epoch",           # 每个epoch评估一次
    save_strategy="epoch",           # 每个epoch保存一次
    save_total_limit=2,              # 最多保留2个checkpoint
    load_best_model_at_end=True,     # 训练结束加载最优模型
    fp16=True,                       # 混合精度（省显存）
    report_to="none",                # 不上报到wandb等
    remove_unused_columns=False,
)


# ========== 数据处理 ==========

def load_data(data_dir):
    """加载训练数据"""
    datasets = {}
    for split in ['train', 'val']:
        path = os.path.join(data_dir, f'{split}.jsonl')
        data = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line))
        datasets[split] = data
        print(f"Loaded {split}: {len(data)} samples")
    return datasets


def format_chat(messages, tokenizer):
    """将messages格式化为模型输入"""
    # Qwen使用chat_template
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )
    return text


def preprocess_function(examples, tokenizer, max_length=1024):
    """预处理：tokenize + 标签处理"""
    model_inputs = {
        'input_ids': [],
        'attention_mask': [],
        'labels': [],
    }

    for messages in examples['messages']:
        # 格式化完整对话
        full_text = format_chat(messages, tokenizer)

        # 只格式化system+user部分（不包含assistant回复）
        prompt_messages = messages[:-1]  # 去掉assistant
        prompt_text = format_chat(prompt_messages, tokenizer)

        # tokenize
        full_tokens = tokenizer(full_text, truncation=True, max_length=max_length)
        prompt_tokens = tokenizer(prompt_text, truncation=True, max_length=max_length)

        input_ids = full_tokens['input_ids']
        attention_mask = full_tokens['attention_mask']

        # 标签：prompt部分设为-100（不计算loss），只对assistant部分计算loss
        labels = [-100] * len(prompt_tokens['input_ids']) + input_ids[len(prompt_tokens['input_ids']):]

        model_inputs['input_ids'].append(input_ids)
        model_inputs['attention_mask'].append(attention_mask)
        model_inputs['labels'].append(labels)

    return model_inputs


# ========== 主流程 ==========

def main():
    print("=" * 60)
    print("  Qwen2.5-7B LoRA Fine-tuning for Stock Prediction")
    print("=" * 60)

    # 1. 检查GPU
    print(f"\n[1/5] Checking GPU...")
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  GPU: {gpu_name} ({gpu_mem:.1f} GB)")
    else:
        print("  WARNING: No GPU detected. Training will be very slow on CPU.")
        print("  Use Google Colab or AutoDL for GPU access.")

    # 2. 加载模型
    print(f"\n[2/5] Loading base model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.enable_input_require_grads()  # LoRA需要

    # 3. 应用LoRA
    print(f"\n[3/5] Applying LoRA...")
    model = get_peft_model(model, LORA_CONFIG)
    model.print_trainable_parameters()
    # 预期: trainable params: ~4-8M (0.1% of 7B)

    # 4. 准备数据
    print(f"\n[4/5] Preparing dataset...")
    raw_datasets = load_data(DATA_DIR)

    train_dataset = Dataset.from_list(raw_datasets['train'])
    val_dataset = Dataset.from_list(raw_datasets['val'])

    # 预处理
    train_dataset = train_dataset.map(
        lambda x: preprocess_function(x, tokenizer),
        batched=True,
        remove_columns=train_dataset.column_names,
    )
    val_dataset = val_dataset.map(
        lambda x: preprocess_function(x, tokenizer),
        batched=True,
        remove_columns=val_dataset.column_names,
    )

    print(f"  Train: {len(train_dataset)} samples")
    print(f"  Val: {len(val_dataset)} samples")

    # 5. 训练
    print(f"\n[5/5] Starting training...")
    print(f"  Epochs: {TRAINING_ARGS.num_train_epochs}")
    print(f"  Batch size: {TRAINING_ARGS.per_device_train_batch_size} x {TRAINING_ARGS.gradient_accumulation_steps} = {TRAINING_ARGS.per_device_train_batch_size * TRAINING_ARGS.gradient_accumulation_steps}")
    print(f"  Learning rate: {TRAINING_ARGS.learning_rate}")
    print(f"  Output: {OUTPUT_DIR}")

    trainer = Trainer(
        model=model,
        args=TRAINING_ARGS,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer, padding=True),
    )

    # 开始训练
    train_result = trainer.train()

    # 保存结果
    print(f"\n{'='*60}")
    print("Training Complete!")
    print(f"{'='*60}")
    print(f"  Train loss: {train_result.training_loss:.4f}")
    print(f"  Steps: {train_result.global_step}")

    # 保存LoRA权重
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"  Model saved: {OUTPUT_DIR}")

    # 打印训练日志
    log_history = trainer.state.log_history
    print(f"\nTraining log:")
    for log in log_history[-10:]:
        if 'loss' in log:
            print(f"  Step {log.get('step', '?')}: loss={log['loss']:.4f}")


if __name__ == "__main__":
    main()
