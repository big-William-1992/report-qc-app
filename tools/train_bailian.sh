#!/usr/bin/env bash
# 百炼（Bailian/DashScope）云端微调 Runbook —— 放射报告质控 Qwen-3B LoRA
#
# 前置（一次性）：
#   1) 安装/升级 bl： npm install -g bailian-cli && bl skill init
#   2) 鉴权（标准 DashScope API Key，不要把 key 贴进对话）：
#        export DASHSCOPE_API_KEY=sk-xxxx
#        或持久保存： bl auth login --api-key sk-xxxx
#      （微调/数据集上传为 DashScope API 命令，用标准 key，非 Token Plan）
#   3) 数据已生成为百炼 ChatML： python3 tools/convert_to_bailian.py
#
# 合规：qc_sft_bailian.jsonl 为 gen_reports 纯合成数据（零 PHI），上传百炼合规。
#      若后续混入真实报告再微调，则必须改走院内算力，勿上传云端。
#
# 用法： bash tools/train_bailian.sh
set -e
set -u

BASE_MODEL="${BASE_MODEL:-qwen3-4b-instruct}"   # 训练底座；训练好的 teacher 也可替换
DATA="data/qc_sft_bailian.jsonl"
PURPOSE="fine-tune"

echo "== 1) 本地格式校验（无需鉴权）=="
bl dataset validate --file "$DATA" --schema chatml

echo "== 2) 上传数据集（返回 file-id）=="
FILE_ID=$(bl dataset upload --file "$DATA" --purpose "$PURPOSE" --schema chatml | tee /dev/stderr | grep -oE 'file-[A-Za-z0-9]+' | head -1)
echo "FILE_ID=$FILE_ID"

echo "== 3) 创建微调任务（先 dry-run 预览，确认无误后去掉 --dry-run 真正提交）=="
bl finetune text create --model "$BASE_MODEL" --training-type sft-lora \
    --datasets "$FILE_ID" --hyperparameters '{"lora_rank":16,"lora_alpha":32,"epochs":3}' \
    --dry-run
# 真正提交（去掉 --dry-run）：
# bl finetune text create --model "$BASE_MODEL" --training-type sft-lora \
#     --datasets "$FILE_ID" --hyperparameters '{"lora_rank":16,"lora_alpha":32,"epochs":3}'

echo "== 4) 监控 / 导出 / 部署（拿到 job-id 后）=="
# bl finetune watch --job-id ft-xxx
# bl finetune export --job-id ft-xxx --checkpoint ckpt-N --model-name qc-qwen3b-lora
# bl deploy text create --model qc-qwen3b-lora --name qc-svc
echo "提示：部署在百炼后，推理时真实报告会出网；生产请导出权重回院内本地部署（vLLM/Ollama）。"
