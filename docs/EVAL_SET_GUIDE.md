# 语义评测集：标注与打分指南

> 目标：用**医生标注**的数据回答两个**分开**的问题 ——
> **A. 规则引擎相对人工质控的检出效能与一致性是多少？**
> **B. LLM 语义层在"规则漏报区"的增量价值是多少（Δrecall / ΔFP）？**
>
> 这两问不许合成一个指标。方法与样本量见 [CLINICAL_VALIDATION_PLAN.md](CLINICAL_VALIDATION_PLAN.md)。

---

## 0. 先说清楚：为什么不能用现有那 200 例

| | 现有 `data/qc_sft_eval.jsonl`（200 例） | 本指南的人工语义评测集 |
|---|---|---|
| 标签来源 | **规则引擎自身蒸馏/注入**（银标） | **医生人工判定**（金标准） |
| 规则表现 | 100% / 100% —— **自证**，不是效能证据 | 待测 |
| LLM 表现 | 9.9% 召回（该集几乎全是确定性错误） | 测它真正该抓的**语义漏报** |
| 用途 | 只作**回归护栏**（防止引擎行为漂移） | 效能结论 / 合规与投标证据 / 论文 |

一句话：**规则给自己出题、自己判卷，考 100 分不能说明什么。**

---

## 0.5 已备好的小样本（10 条，可直接开跑）

为了把"从零开始"的成本压到最低，已生成一组**10 条样例候选**与医生说明：

| 文件 | 用途 |
|---|---|
| [`data/semantic_eval/candidates_sample10.jsonl`](../data/semantic_eval/candidates_sample10.jsonl) | 10 份脱敏报告（胸腔/腹部/头颅/盆腔/甲状腺/乳腺/腰椎/膝/颈椎/肱骨），`label` 已留空，并附**系统当时的判定** `engine_snapshot_at_creation` 供对照 |
| [`docs/医生标注说明_10条样例.md`](医生标注说明_10条样例.md) | 给医生的**一页纸**说明：填哪 5 个字段、错误类型怎么选、3 条判定原则、填好的样子 |

流程：医生照说明填 `label` 并把 `label_source` 改成 `human` → 交回 → 跑

```bash
python3 tools/semantic_eval.py validate data/semantic_eval/candidates_sample10.jsonl
python3 tools/semantic_eval.py score    data/semantic_eval/candidates_sample10.jsonl --json
```

即可得到**检出率 / 误报率**与分层结果。
> 10 条只够"跑通链路"，不足以作为效能结论；正式评测建议按 §2 扩到 100–300 条。

## 1. 三分钟流程

```bash
# ① 生成待标注模板（二选一）
python3 tools/semantic_eval.py template --from-feedback \
        --out data/semantic_eval/from_feedback.jsonl      # 医生反馈（已富集漏报）
python3 tools/semantic_eval.py template --from-jsonl data/reports.jsonl --limit 300 \
        --out data/semantic_eval/pool.jsonl               # 报告池（代表性抽样）

# ② 医生按第 3 节填写 label 与 label_source="human"（可用 Excel/文本编辑器批量改）

# ③ 校验 + 打分（研究 A/B 分开输出）
python3 tools/semantic_eval.py validate data/semantic_eval/labeled.jsonl
python3 tools/semantic_eval.py score data/semantic_eval/labeled.jsonl --llm \
        --json --save benchmarks/semantic_eval_<日期>.json
```

---

## 2. 抽样与偏倚（决定结论能不能外推）

- **两类来源，结论口径不同**：
  - `--from-feedback`：来自医生点"漏报/误报"的反馈 → **富集了规则漏报**，
    用它算出的召回率**偏高**，只能回答"LLM 能不能补规则"，**不能**当作总体召回率。
  - `--from-jsonl`（报告池 + 连续入组）：可代表科室总体，才能算总体 Se/Sp。
- 混合集必须在报告里按 `meta.origin` 分层展示（工具输出里已给 `origins` 计数）。
- 若只能拿到富集集，请按临床方案里的 **逆概率加权（IPW）** 回推总体，并明确写出假设。

---

## 3. 每条记录怎么填

```json
{
  "id": "pool-12",
  "report_text": "患者男，58岁。……",
  "label_source": "human",              // ← 必须是 human，银标会被拒收
  "label": {
    "is_true_error": true,               // 必填：这份报告是否存在质控意义上的错误
    "error_type": "SEM-FOLLOWUP",         // 错误类型（见下表；有错必填）
    "location": "建议随访",               // 定位片段：要能在报告里找到，便于与引擎输出核对
    "severity": "medium",                 // high | medium | low
    "note": "结节≥8mm 未给随访建议"        // 判定理由，尤其写清"为什么规则抓不到"
  },
  "meta": { "origin": "pool" }
}
```

### 错误类型建议取值

| 前缀 | 含义 | 例 |
|---|---|---|
| `R*` | 确定性错误（规则应覆盖）：`R1-GENDER` 性别矛盾、`R19-HOMOPHONE` 同音错字、`R10-TEMPLATE` 模板缺失… | 女性报告出现"前列腺" |
| `SEM-FOLLOWUP` | **语义**：结节/占位未给随访或复查建议 | 右肺 9mm 结节，无随访建议 |
| `SEM-CONCLUSION` | **语义**：结论与描述/病理倾向不匹配 | 描述"考虑恶性可能"，结论"未见异常" |
| `SEM-RECOMMEND` | **语义**：处置建议与所见不匹配（该建议穿刺却写"无需处理"） | — |
| `SEM-NARRATIVE` | **语义**：叙事规范/逻辑（前后矛盾、指代不清、缺关键阴性征象） | 前文"双侧"后文只写"右" |
| `OTHER` | 其他（请在 note 说明） | — |

> `SEM-*` 才是 LLM 的目标区域。只标 `R*` 等于又在自证——**语义类样本占比建议 ≥ 40%**。

---

## 4. 打分输出怎么看

```
===== [研究 A：规则引擎 vs 人工金标准] =====
错误样本检出率(recall):        x/y = ..%
正常样本零误报率(specificity): x/y = ..%

===== 研究 B：LLM 在规则漏报区的增量（不与研究 A 合并）=====
增量召回 Δrecall = a/b = ..%     # 分母 = 人工判有错但规则没发现的样本
新增误报 ΔFP     = c/d = ..%     # 分母 = 人工判无错且规则干净的样本
```

判断 LLM 该不该上的标准（写进结论里，避免"感觉有用"）：

- **Δrecall 明显 > 0** 才有存在价值；
- **ΔFP 必须可接受**（每 100 份正常报告新增多少误报，医生会不会因此关掉提示）；
- 只有 Δrecall 高而 ΔFP 也高时，考虑**只对特定类型/特定严重度开启**，而不是全量开。

---

## 5. 常见错误（务必避免）

1. **用规则输出当标签**（自证）→ 工具会直接拒收 `label_source != "human"`。
2. **只标有错的报告**（没有正常样本）→ 算不出特异度，也就不知道误报代价。
3. **把"规则+LLM"合成一个指标上报** → 无法判断增量来自谁；工具已把两研究拆开。
4. **报告里不写版本号** → 结果无法复现；请同时记录 `version.APP_VERSION`、
   git commit、`prompt_mode`（ft/full）与模型名。
5. **拿富集集当总体召回率** → 见第 2 节。

---

## 5.5 本地微调模型 qc-qwen3 的**能力边界**（2026-09-30 实测，必读）

用留出集 `data/mlx_data/valid.jsonl`（59 条，模型训练时没见过），
走**线上真实路径** `run_llm_qc(config={"provider":"ollama","model":"qc-qwen3","prompt_mode":"ft"})`：

| 指标 | 结果 |
|---|---|
| recall（错误样本检出率） | **95.5%**（21/22） |
| specificity（正常样本零误报） | **100.0%**（37/37） |

**但必须连同下面这条边界一起引用，否则会严重高估：**

该 adapter 的训练分布里**只有 3 种错误码**，样本数极度不均：

| 训练集中出现的 error_type | 样本数 |
|---|---|
| `R8-TYPO`（错别字） | 235 |
| `R19-HOMOPHONE`（同音字） | 8 |
| `R5-CONSISTENCY`（描述-结论矛盾） | **1** |

实测结论：**模型只会检出错别字/同音字这类字符级错误**；对训练里几乎没见过的
语义级错误（左右侧矛盾、性别-器官矛盾、描述-结论矛盾）在分布外输入上**静默返回 `[]`**。
上面 95.5% 的 recall 是在"与训练分布同构"的留出集上取得的，
**不能外推到真实临床报告**。

> 复现：`R5-CONSISTENCY` 在 train 里只有 1 条，模型学不会；
> 用真实矛盾报告（如"描述考虑肝囊肿 / 结论考虑肝癌"）实测返回 `[]`。

### 两个已加的门禁（防止此类问题再次静默发生）

| 测试 | 守什么 |
|---|---|
| `tests/test_llm_prompt_alignment.py` | ft prompt 必须与 adapter 训练集逐字对齐；不一致即失败（否则线上静默退化） |
| `tests/test_llm_error_type_whitelist.py` | LLM 返回的 error_type 必须在白名单内，幻觉编码（实测出现过 `R1-CONSISTENCY`/`R2-CONSISTENCY` 这类**不存在**的码）一律丢弃并告警 |

### 引用指标时的三条硬规矩

1. 报 recall 必须**同时**报 `prompt_mode`、模型名、adapter、留出集来源；
2. 留出集必须与训练集**同构但不相交**，否则数字无意义；
3. **不要**把该 adapter 的指标写成"语义质控 recall"——它目前只覆盖字符级错误。

---

## 6. 与精调数据的关系

同一份标注数据可以两用：
- `label` 齐全 → **评测集**（只用于打分，不参与训练，避免污染）；
- 若要用于精调（SFT），请**另存一份副本**并留出独立评测集，
  否则"训练集=评测集"，指标又变成自证。

医生反馈 → 标注模板的通道已经打通（`--from-feedback`），
反馈越多、标注越规范，这条飞轮越值钱。
