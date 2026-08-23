#!/usr/bin/env bash
# 训练入口：需先安装 LlamaFactory + torch + 对应 CUDA，并把数据集放到 LlamaFactory 的 data/ 目录。
set -e

# 1) 生成数据集（任选其一）
#   a) 按质控规则合成（自带标签，可复现，无需外部数据）：
python3 ../tools/gen_reports.py --out data/qc_sft.jsonl --n 2000
#   b) 真实语料：规则蒸馏 / LLM 注入（见 tools/build_cn_dataset.py）
# python3 ../tools/build_cn_dataset.py --chinese-reports /path/to/raw_reports --out data/qc_sft.jsonl --mode rule
# cp data/qc_sft.jsonl <LlamaFactory>/data/qc_sft.jsonl
# cp dataset_info.json <LlamaFactory>/data/dataset_info.json

# 2) 启动训练
llamafactory-cli train "$(dirname "$0")/qwen3b_lora.yaml"
