#!/usr/bin/env python3
"""本地 MLX LoRA 微调 Qwen3-4B（小样本快速验证）。

使用 mlx-lm 在 Mac 上本地微调，无需 GPU / 云端 API。
数据格式：ChatML JSONL（与百炼格式一致）。

用法：
  python3 local_lora_train.py --data data/sft_qc.jsonl --iters 50
"""
import argparse
import json
import os
import sys
import subprocess
from pathlib import Path


def check_mlx():
    """检查 MLX 环境。"""
    try:
        import mlx.core as mx
        import mlx_lm
        print(f"✅ MLX {mx.__version__} 就绪")
        return True
    except ImportError as e:
        print(f"❌ MLX 未安装: {e}")
        print("   安装: pip install mlx mlx-lm")
        return False


def check_model(model_path: str) -> bool:
    """检查模型是否可用。"""
    if os.path.isdir(model_path):
        print(f"✅ 本地模型: {model_path}")
        return True
    # 尝试从 HuggingFace 下载
    print(f"⚠️  模型不在本地，将从 HuggingFace 下载...")
    return True


def prepare_mlx_data(jsonl_path: str, out_dir: str, val_ratio: float = 0.1):
    """将 ChatML JSONL 转为 MLX 训练格式（train.jsonl + valid.jsonl）。"""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # MLX 需要 messages 格式
            if "messages" in rec:
                records.append(rec)
            else:
                # Alpaca 格式转 ChatML
                messages = [
                    {"role": "system", "content": rec.get("instruction", "")},
                    {"role": "user", "content": rec.get("input", "")},
                    {"role": "assistant", "content": rec.get("output", "")},
                ]
                records.append({"messages": messages})

    # 打乱并切分
    import random
    random.seed(42)
    random.shuffle(records)

    n_val = max(1, int(len(records) * val_ratio))
    val_data = records[:n_val]
    train_data = records[n_val:]

    os.makedirs(out_dir, exist_ok=True)
    train_path = os.path.join(out_dir, "train.jsonl")
    val_path = os.path.join(out_dir, "valid.jsonl")

    with open(train_path, "w", encoding="utf-8") as f:
        for rec in train_data:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(val_path, "w", encoding="utf-8") as f:
        for rec in val_data:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"✅ 数据准备完成:")
    print(f"   训练集: {len(train_data)} 条 → {train_path}")
    print(f"   验证集: {len(val_data)} 条 → {val_path}")
    return out_dir


def run_lora_training(model: str, data_dir: str, output_dir: str,
                       iters: int = 50, batch_size: int = 1,
                       learning_rate: float = 1e-4, lora_rank: int = 8):
    """运行 MLX LoRA 训练。"""
    cmd = [
        "mlx_lm.lora",
        "--model", model,
        "--train",
        "--data", data_dir,
        "--fine-tune-type", "lora",
        "--iters", str(iters),
        "--batch-size", str(batch_size),
        "--learning-rate", str(learning_rate),
        "--adapter-path", output_dir,
        "--save-every", str(max(1, iters // 5)),
        "--steps-per-eval", str(max(1, iters // 5)),
        "--steps-per-report", str(max(1, iters // 10)),
    ]

    print(f"\n🚀 开始训练:")
    print(f"   模型: {model}")
    print(f"   迭代次数: {iters}")
    print(f"   Batch size: {batch_size}")
    print(f"   学习率: {learning_rate}")
    print(f"   LoRA rank: {lora_rank}")
    print(f"   输出: {output_dir}")
    print(f"\n   命令: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=os.getcwd())
    return result.returncode == 0


def test_model(model: str, adapter_path: str, test_prompt: str):
    """测试微调后的模型。"""
    from mlx_lm import load, generate

    print(f"\n🧪 加载模型测试...")
    model_obj, tokenizer = load(model, adapter_path=adapter_path)

    messages = [
        {"role": "system", "content": "你是资深放射科质控专家，审查放射诊断报告中的错误。"},
        {"role": "user", "content": test_prompt},
    ]

    response = generate(model_obj, tokenizer, messages=messages, max_tokens=512)
    print(f"\n📋 测试结果:")
    print(f"   输入: {test_prompt[:100]}...")
    print(f"   输出: {response}")


def main():
    parser = argparse.ArgumentParser(description="本地 MLX LoRA 微调 Qwen3-4B")
    parser.add_argument("--model", default="Qwen/Qwen3-4B",
                        help="模型路径或 HuggingFace repo")
    parser.add_argument("--data", default="data/sft_qc.jsonl",
                        help="训练数据（ChatML JSONL）")
    parser.add_argument("--output", default="saves/qwen3-4b-qc-lora",
                        help="LoRA 适配器输出目录")
    parser.add_argument("--iters", type=int, default=50,
                        help="训练迭代次数（小样本测试用50-100）")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Batch size（16GB 内存建议1-2）")
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test", action="store_true",
                        help="训练后测试模型")
    parser.add_argument("--skip-train", action="store_true",
                        help="跳过训练，直接测试")
    args = parser.parse_args()

    # 环境检查
    if not check_mlx():
        sys.exit(1)

    check_model(args.model)

    # 数据准备
    data_dir = os.path.join(os.path.dirname(args.data), "mlx_data")
    prepare_mlx_data(args.data, data_dir, args.val_ratio)

    # 训练
    if not args.skip_train:
        success = run_lora_training(
            model=args.model,
            data_dir=data_dir,
            output_dir=args.output,
            iters=args.iters,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            lora_rank=args.lora_rank,
        )
        if not success:
            print("❌ 训练失败")
            sys.exit(1)
        print(f"\n✅ 训练完成！LoRA 适配器保存在: {args.output}")

    # 测试
    if args.test or args.skip_train:
        test_prompt = (
            "患者女，45岁。检查部位：胸部。\n"
            "检查所见：右肺上叶见一姐姐，边界清，大小约0.8cm。\n"
            "诊断印象：右肺上叶磨玻璃结节，建议定期随访。"
        )
        test_model(args.model, args.output, test_prompt)


if __name__ == "__main__":
    main()
