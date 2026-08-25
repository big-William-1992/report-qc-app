# LLM 质控模型部署指南

> 规则引擎开箱即用；LLM 语义质控是**可选增强**，按本文配置后启用。
> 三种形态效果等价（同一份 LoRA adapter），按部署环境选择。

---

## 一、三种形态怎么选

| 形态 | 适用场景 | 数据出域 | 延迟 | 依赖 |
|------|---------|---------|------|------|
| **A. Ollama**（推荐生产） | 目标机器常驻服务 | ❌ 不出 | 0.6~5s | Ollama ≥ 0.31 |
| B. MLX 直连 | macOS 开发机/一体机 | ❌ 不出 | 0.6~4s | mlx + mlx-lm |
| C. 云端 API | 内网演示、无 GPU 机器 | ⚠️ 报告上云 | 1~3s | 网络 + API Key |

> 合规提示：形态 A/B 全程本地推理，满足「数据不出院」；形态 C 仅限脱敏数据。

---

## 二、获取模型

### 方式 1：git clone（adapter 已入 LFS）

```bash
git clone https://github.com/big-William-1992/report-qc-app.git
cd report-qc-app
git lfs pull --include "saves/qwen3-4b-qc-lora-v2/*"
# 得到: saves/qwen3-4b-qc-lora-v2/adapters.safetensors (~28MB)
```

### 方式 2：从训练源头重建

```bash
# 需要本地基座 Qwen3-4B-Instruct-2507 (MLX 4bit)
python3 -m mlx_lm lora \
  --model ~/.cache/huggingface/hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/<hash> \
  --train --data data/mlx_data \
  --fine-tune-type lora --iters 1200 --batch-size 1 \
  --learning-rate 1e-4 --adapter-path saves/qwen3-4b-qc-lora-v2
# 训练集: data/sft_qc_v2.jsonl (593条, 由 tools/gen_v2_robust_dataset.py 生成)
```

---

## 三、形态 A：Ollama 部署（推荐）

```bash
# 1) 安装 Ollama (mac/win 通用): https://ollama.com/download

# 2) 用仓库内 GGUF 打包模型 (q8_0, 4.0GB)
cd merged   # 若无 GGUF, 见下方「重建 GGUF」
ollama create qc-qwen3 -f Modelfile

# 3) 冒烟测试
curl http://localhost:11434/api/generate -d '{"model":"qc-qwen3","prompt":"回复OK","stream":false}'
```

### 重建 GGUF（merged/qc-qwen3-4b-q8_0.gguf 不在仓库时）

```bash
# 基座 + adapter 合并并解量化 (输出 ~7.5GB f16 分片)
python3 -m mlx_lm fuse --model <本地MLX基座路径> \
  --adapter-path saves/qwen3-4b-qc-lora-v2 \
  --save-path merged/qc-qwen3-4b --dequantize

# 转 GGUF (需要 llama.cpp 的 convert_hf_to_gguf.py + pip install gguf torch)
python3 convert_hf_to_gguf.py merged/qc-qwen3-4b \
  --outfile merged/qc-qwen3-4b-q8_0.gguf --outtype q8_0

# Modelfile 在 merged/Modelfile, 含 Qwen3 ChatML 模板与 stop token
ollama create qc-qwen3 -f merged/Modelfile
```

### 配置 `src/llm_config.json`（此文件不入库，手工创建）

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434",
  "model": "qc-qwen3",
  "timeout": 180,
  "prompt_mode": "ft"
}
```

---

## 四、形态 B：MLX 直连（macOS）

```json
{
  "provider": "mlx",
  "model": "<MLX基座快照的绝对路径>",
  "adapter_path": "<仓库>/saves/qwen3-4b-qc-lora-v2",
  "max_tokens": 512,
  "timeout": 300,
  "prompt_mode": "ft"
}
```

依赖：`pip install mlx mlx-lm`（仅 Apple Silicon）。

---

## 五、形态 C：云端 API

支持任何 OpenAI 兼容端点（百炼精调部署 / vLLM 等）：

```json
{
  "provider": "cloud",
  "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key": "<环境变量 LLM_API_KEY 或此处>",
  "model": "<部署后的模型ID>",
  "timeout": 120,
  "prompt_mode": "ft"
}
```

> Qwen3 系非流式调用需关闭思考模式，客户端已自动处理。

---

## 六、验证

```bash
cd src && python3 - << 'EOF'
from llm_qc import run_full_qc
r = run_full_qc("患者女，48岁。检查部位：甲状腺。\n检查所见：峡部见一低回声洁节，边界清。\n诊断印象：峡部结节，TI-RADS 3类。")
print("llm_available:", r["llm_available"])       # True
print("llm_findings:", r["llm_findings"])          # 应含 R8-TYPO「洁节」
EOF
```

期望：检出 `[L1-R8-TYPO] 洁节`；对正常报告返回空数组（零误报）。

---

## 七、关键参数说明

| 字段 | 说明 |
|------|------|
| `prompt_mode: "ft"` | **微调模型必设**。使用与训练分布对齐的简洁 prompt；不设会用完整 taxonomy prompt，会诱导幻觉（凑错误类型） |
| `provider` | `ollama` / `mlx` / `cloud` 三选一 |
| `timeout` | MLX 冷启动首次加载约 10~30s，建议 ≥180 |

## 八、故障排查

| 现象 | 处理 |
|------|------|
| `available: False` | Ollama: `curl localhost:11434` 探活；MLX: 确认 `import mlx_lm` 成功 |
| 输出自由文本而非 JSON | 检查是否漏配 `"prompt_mode": "ft"` |
| 首次调用超时 | MLX 冷加载属正常，调大 timeout 或预热一次 |
| GGUF 回答乱码 | Modelfile 的 TEMPLATE/stop 必须保留 Qwen3 ChatML 结构 |
