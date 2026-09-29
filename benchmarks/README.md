# 回归评测基准（benchmarks/）

改规则、换模型、调 prompt 之后**跑一遍再对比**，避免"感觉变好了"式的回归。

## 怎么跑

```bash
# 仅确定性规则（默认，秒级）
python3 tools/run_eval.py

# 规则 + LLM 语义层（用当前 src/llm_config.json 的 provider/model）
python3 tools/run_eval.py --llm

# 通过后写成新基线
python3 tools/run_eval.py --save
```

评测集来自 `data/qc_sft_eval.jsonl`（不入库，本地生成）：
`tools/run_eval.py` 会把它拆成「有缺陷样本」与「干净样本」两组，分别算

- `recall`（错误样本检出率）——漏检视角
- `specificity`（正常样本零误报率）——误报视角

## 当前基线（benchmarks/eval_baseline.json）

| 链路 | recall | specificity | 样本量 | 记录时间 |
|---|---|---|---|---|
| `rules` | 100.0% | 100.0% | 91 错 + 109 正常 = 200 | 2026-08-25 |
| `llm` | **未记录** | **未记录** | — | — |

> ⚠️ **LLM 基线尚未记录**，需要在装有模型的机器上跑一次 `python3 tools/run_eval.py --llm --save`。
> 两种可用形态（见 `DEPLOYMENT.md`）：
> - Ollama：`ollama create qc-qwen3 -f merged/Modelfile` 后把 `src/llm_config.json` 的
>   `provider=ollama, model=qc-qwen3, prompt_mode=ft`；
> - MLX：`pip install mlx mlx-lm` 后 `provider=mlx` + `adapter_path=saves/qwen3-4b-qc-lora-v2`。
>
> 只装了 `onnxruntime` 而没有 `mlx-lm` / Ollama 的环境，`--llm` 会走优雅降级
> （`available=false`，规则结果不受影响，见 `tests/test_llm_degrade.py`），
> 此时**不会**产出有意义的 LLM 指标——不要把这种运行结果当成基线保存。

## 读指标时的两个坑

1. **规则 100%/100% 是银标自证**：`data/qc_sft_eval.jsonl` 的标签由确定性引擎
   自己蒸馏而来，所以规则链路必然接近满分。它衡量的是**引擎行为是否发生意外漂移**
   （回归护栏），**不能**当作对外的准确率承诺。
2. **LLM 在银标集上主要反映"与规则的一致性"**，不是独立质量。要评估真实语义能力，
   需要用人工标注样本（见 `docs/LLM质控数据标注范式.md`）单独建集。

## 相关文件

- `tools/run_eval.py` — 评测入口（读/写本目录基线）
- `tools/eval_corpus.py` — 按目录/文件跑评测，支持 `--llm --json` 导出明细
- `tools/eval_finetune.py`、`tools/eval_mcscset.py` — 微调产物与公开数据集评测
