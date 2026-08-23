# 星衍质控 · Qwen-3B 微调 / 蒸馏训练框架

底座为 **Qwen2.5-3B**，teacher / 训练好的模型只需换 `model_name_or_path` 即可，架构无需改动。

## 目录
- `qwen3b_lora.yaml` — LlamaFactory LoRA 训练配置（基座 Qwen2.5-3B-Instruct）
- `dataset_info.json` — 数据集 schema（alpaca 格式，对齐推理端输出）
- `train.sh` — 训练启动脚本
- `../tools/build_qc_dataset.py` — SFT 数据集生成器（规则蒸馏 + 模型蒸馏）

## 数据流
```
原始报告目录
   │  tools/build_qc_dataset.py  (--mode rule | teacher | both)
   ▼
data/qc_sft.jsonl   (alpaca: instruction / input / output=JSON数组)
   │  复制到 LlamaFactory 的 data/ 并放 dataset_info.json
   ▼
llamafactory-cli train qwen3b_lora.yaml
   │  产出 saves/qwen3b-qc-lora
   ▼
导出权重 → ollama create qc-qwen3b  （或 vLLM 部署）
   │  改 ../src/llm_config.json 的 model 字段
   ▼
推理端 llm_client 自动加载训练好的模型
```

## 两层蒸馏
1. **规则蒸馏**：`--mode rule` 用确定性 `RuleEngine` 在原始报告上产出银标（高精），蒸馏规则行为到 3B。
2. **模型蒸馏**：`--mode teacher` 调用本地微调 teacher 产出标签，蒸馏大模型能力到更小 student
   （把 `qwen3b_lora.yaml` 的 `model_name_or_path` 换成更小基座如 Qwen2.5-1.5B 重训即可）。

## 合规
- student 部署一律本地（ollama / vllm），PHI 不落地外部。
- 若 teacher 走云端 API，原始报告必须先脱敏（去姓名/ID/日期）再调用。

## 验收门槛（参考）
- 输出 JSON 合法率 > 98%
- 在规则银标测试集上与确定性引擎一致性高（蒸馏不退化）
- 在人工语义测试集上 recall 明显高于纯规则、误报率持平或更低
