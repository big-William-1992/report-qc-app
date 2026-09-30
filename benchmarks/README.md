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

| 链路 | recall | specificity | 样本量 | 耗时 | 记录时间 |
|---|---|---|---|---|---|
| `rules` | 100.0% | 100.0% | 91 错 + 109 正常 = 200 | 20s | 2026-09-30 |
| `llm`（微调 Qwen3-4B LoRA，MLX，`prompt_mode=ft`） | **9.9%** | 100.0% | 同上 | 479s（≈2.4s/例） | 2026-09-30 |

跑法（本机 `mlx_lm` 装在 Homebrew python，不在项目托管 venv 里）：

```bash
/opt/homebrew/bin/python3 tools/run_eval.py --llm --save
```

### 怎么读这个 9.9%（重要，别误读）

1. **不是"模型坏了"**：同一份实测显示它 `specificity=100%`——109 份正常报告
   **零误报**；200 例中有大量报告它直接输出 `[]`（保守）。基线里保留了 20 条漏检
   与 10 条误报示例可供复盘。
2. **也不是"LLM 没用"**：这套评测集的标签是**规则注入/规则蒸馏**的确定性错误
   （错别字、左右混淆、描述-结论矛盾…），而 LLM 层的设计定位恰是抓
   **规则抓不到的语义级错误**，且系统提示里明确要求"不重复规则已能判定的硬错误"。
   → 该集**测不出 LLM 的目标价值**，只测"它与规则的重合度"。
3. **`prompt_mode` 必须保持 `ft`**，这一点有实测对照（同 20 例、同一模型）：
   | prompt_mode | recall | specificity | 发现数 | 零输出例数 |
   |---|---|---|---|---|
   | `ft`（简洁，与训练分布对齐） | 18.2%* | **100.0%** | 6 | 15/20 |
   | `full`（taxonomy + RAG） | 0.0% | **0.0%** | 25 | 0/20 |
   `full` 模式对**每一份**报告都报错（含全部正常报告），还会编造出不在允许清单里的
   `error_type` —— 这就是 `docs/LLM_MODEL_DEPLOYMENT.md`（第七节关键参数说明）里"会诱导幻觉（凑错误类型）"的实测证据。

   > ⚠️ **2026-09-30 复测修正（重要，请优先采信）**：
   > 上表 `ft` 的 **18.2% 已作废**，它是在**与训练分布不同构**的评测集上测的
   > （该 20 例含左右矛盾、性别-器官矛盾等语义级错误，而 adapter 的训练集里
   > **根本没有**这些类型）。改用在**同构留出集** `data/mlx_data/valid.jsonl`
   > （59 条，训练时未见过）、走**线上真实路径** `run_llm_qc(prompt_mode=ft)` 复测：
   >
   > | 指标 | 结果 |
   > |---|---|
   > | recall | **95.5%**（21/22） |
   > | specificity | **100.0%**（37/37） |
   >
   > 但**该数字仍不可外推到真实临床报告**：该 adapter 训练集只有 3 种错误码
   > （`R8-TYPO` 235 / `R19-HOMOPHONE` 8 / `R5-CONSISTENCY` **1**），
   > 实测它**只会检出字符级错误（错别字/同音字）**；对语义级错误在分布外输入上
   > **静默返回 `[]`**（0.5s、6 token、无报错）——这是训练分布太窄的必然结果。
   >
   > 完整排查结论、三个易踩的坑（认错训练集 / 给 ft 模型喂 taxonomy 反被诱导出
   > 幻觉码 / 手写用例与训练分布不同构）见 `docs/LLM_MODEL_DEPLOYMENT.md` 第九节，
   > 与 `docs/EVAL_SET_GUIDE.md` §5.5。守这两件事的测试：
   > `tests/test_llm_prompt_alignment.py`、`tests/test_llm_error_type_whitelist.py`。

### 因此，下一步该做的不是"调 prompt"，而是补评测集

- 建一套**语义级错误**的人工标注集（随访建议缺失、良恶性与处置建议不匹配、
  叙事/用语规范等），才能量化 LLM 的增量价值；标注范式见
  `docs/LLM质控数据标注范式.md`。
- 若还希望 LLM 兼任"确定性错误的第二道复核"，则要针对性地扩充对应训练数据，
  否则就明确把 LLM 定位为**语义补充**，不要用本集数字当它的 KPI。

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
