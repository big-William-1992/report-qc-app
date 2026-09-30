# LLM 质控模型部署指南

> 规则引擎开箱即用；LLM 语义质控是**可选增强**，按本文配置后启用。
> 三种形态效果等价（同一份 LoRA adapter），按部署环境选择。
>
> 📌 2026-09-30：本文原为 `DEPLOYMENT.md`，在 v4.3.6 提交里被「科室多机部署手册」
> 整体覆盖（应用部署与模型部署是两件事，不该互相覆盖）。现恢复为独立文档，
> 与 `DEPLOYMENT.md`（多机部署）并存；并补入 `prompt_mode` 的实测对照。

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
| `prompt_mode: "ft"` | **微调模型必设**。使用与训练分布对齐的简洁 prompt；不设会用完整 taxonomy prompt，会诱导幻觉（凑错误类型）。<br>2026-09-30 复测（留出集 `data/mlx_data/valid.jsonl` 59 条，走线上真实路径）：`ft` → recall **95.5%** / **specificity 100%**；`full` → **不要用**（长 taxonomy prompt 与训练分布不符，会编造不存在的 error_type）。模型能力边界见第九节，引用指标前**务必先读**。 |
| `provider` | `ollama` / `mlx` / `cloud` 三选一 |
| `timeout` | MLX 冷启动首次加载约 10~30s，建议 ≥180 |

## 八、故障排查

| 现象 | 处理 |
|------|------|
| `available: False` | Ollama: `curl localhost:11434` 探活；MLX: 确认 `import mlx_lm` 成功 |
| 输出自由文本而非 JSON | 检查是否漏配 `"prompt_mode": "ft"` |
| 首次调用超时 | MLX 冷加载属正常，调大 timeout 或预热一次 |
| GGUF 回答乱码 | Modelfile 的 TEMPLATE/stop 必须保留 Qwen3 ChatML 结构 |
| confidence 恒为 1.0 | 已知限制: 训练标注全 1.0 所致; 待 badcase 回流后增量精调校准 |
| **对语义级错误静默返回 `[]`** | **预期行为**，非故障 —— 该 adapter 只学过 3 种错误码。见第九节 |

---

## 九、能力边界与排查结论（2026-09-30 实测）

> 本节记录一次完整排查的**结论**。目的是防止后续再花时间怀疑模型/prompt，
> 以及防止把下面这个 recall 数字**外推**到真实临床报告。

### 9.1 实测成绩（留出集，走线上真实路径）

留出集 `data/mlx_data/valid.jsonl`（59 条，训练时未见过），
调用 `run_llm_qc(config={"provider":"ollama","model":"qc-qwen3","prompt_mode":"ft"})`：

| 指标 | 结果 |
|---|---|
| recall（错误样本检出率） | **95.5%**（21/22） |
| specificity（正常样本零误报） | **100.0%**（37/37） |

**但这个数字不可外推到真实报告**，原因见下。

### 9.2 根因：adapter 的训练分布极窄

`adapter_config.json` 的 `data` 字段指明 lora-v2 实际训练集是 `data/mlx_data`
（593 条，由 `tools/gen_v2_robust_dataset.py` 生成）。
该训练集里**只出现过 3 种错误码**，且极度不均：

| 训练集中出现的 error_type | 样本数 |
|---|---|
| `R8-TYPO`（错别字） | 235 |
| `R19-HOMOPHONE`（同音字） | 8 |
| `R5-CONSISTENCY`（描述-结论矛盾） | **1** |

结论：**模型只会检出字符级错误（错别字/同音字）**。
对语义级错误（左右侧矛盾、性别-器官矛盾、描述-结论矛盾），
在分布外输入上**静默返回 `[]`**（0.5s、6 token、无报错）——这是训练分布太窄
的必然结果，不是配置问题。`R5-CONSISTENCY` 仅 1 条训练样本，学不会属正常。

### 9.3 排查中容易踩的三个坑（都实际发生过）

1. **认错训练集**：`data/` 下有 `mlx`(1800条)、`mlx_data`(593条)、`mlx50`(50条)
   三个目录。**只有 `adapter_config.json` 里 `data` 指向的那个**才是该 adapter
   的训练集；用别的目录的 system prompt 去比对，会得出「prompt 没对齐」的错误结论。
2. **把 taxonomy 喂给 ft 模型再测**：往 prompt 里加完整「错误分类法」会**诱导**
   模型拼造错误码（实测造出 `R1-CONSISTENCY`/`R2-CONSISTENCY`——R1 是
   `R1-GENDER`、R2 是 `R2-LATERALITY`、一致性是 `R5-CONSISTENCY`，
   这两个码**根本不存在**）。这是「用错 prompt 测出来的幻觉」，不是模型本身的缺陷。
3. **手写用例与训练分布不同构**：训练样本的 user 侧是**纯报告原文**
   （无 taxonomy、无 RAG 注入）。用带附加内容的 prompt 测，结果无意义。

### 9.4 两个已加的门禁

| 测试 | 守什么 |
|---|---|
| `tests/test_llm_prompt_alignment.py` | ft prompt 必须与 adapter 训练集逐字对齐（从 `adapter_config.json` 反查真实训练集）；不一致即失败 —— 改了 prompt 不重训会**静默失效**，此前 pytest 仍全绿 |
| `tests/test_llm_error_type_whitelist.py` | LLM 返回的 `error_type` 必须在白名单内，幻觉编码一律丢弃 + 告警<br>（修的是一个**真 bug**：`llm_qc._normalize` 原先把 rule_id 合成 `"L1-" + error_type`，等于把模型编的字符串当规则号传给下游，**绕过**了 `llm_engine` 已有的 `ALLOWED_RULE_IDS` 校验） |

### 9.5 引用指标时的三条硬规矩

1. 报 recall 必须**同时**报 `prompt_mode`、模型名、adapter、留出集来源；
2. 留出集必须与训练集**同构但不相交**，否则数字无意义；
3. **不要**把这个 adapter 的指标写成「语义质控 recall」——它目前只覆盖字符级错误。

### 9.6 后续方向

瓶颈在**训练数据**，不在代码。要覆盖语义级错误，需要补充对应类型的标注样本
（描述-结论矛盾、左右侧矛盾等），再增量精调。
评估集建设流程见 `docs/EVAL_SET_GUIDE.md`（含 §5.5 同一份边界说明）。
