# 星衍放射质控系统 · LLM 质控层技术方案

> 版本：v1.0（P0 原型阶段）
> 日期：2026-08-22
> 定位：在既有「第一代确定性规则引擎（R1–R23）」之上，叠加以大模型为底座的语义质控层，
>       形成「硬门禁（规则）+ 语义复核（LLM）」的混合双引擎架构。

---

## 1. 背景与目标

### 1.1 现状
- 核心引擎 `src/engine.py`（`RuleEngine`，R1–R23）为**确定性、可解释、低误报**管线：
  文本 → 分段 → NER → 知识图谱约束 → 规则引擎 → 错误 + 多维评分，单条 `run()` 约 **39ms**。
- 已覆盖：性别矛盾、左右混淆、评分缺失、单位错误、描述-结论矛盾、登记部位不符、
  错别字/术语（R8/R19/R22）、良恶性/数量矛盾等。
- 技术路线文档已明确「一代 → 二代平滑衔接：KG 检索逐步替换为 RAG 检索增强」。

### 1.2 痛点（确定性引擎的边界）
- **召回天花板**：规则依赖显式词典/正则/部位对齐，对**语义级、语境级**错误无能为力，例如：
  - 描述写了「建议随访」但结论段未对应任何随访建议；
  - 推荐检查/处置与影像表现不匹配（如未见恶性征象却直接建议穿刺）；
  - 报告叙事质量、逻辑连贯性、用语规范（口语化、冗余）；
  - 罕见/新类型错误、跨句长程逻辑矛盾。
- **泛化弱**：词典外的新错法、新检查类型需人工补词表。

### 1.3 目标
| 维度 | 目标 |
|---|---|
| 召回（Recall） | 在规则基础上，额外捕获语义/语境类错误（规则漏报区），提升整体错误检出 |
| 精度（Precision） | LLM 不单独定错：经 **RAG 接地 + 置信度门控 + 人工复核兜底** 控制误报 |
| 合规 | 默认**本地推理**（数据不出院）；全程留痕可追溯 |
| 体验 | 确定性结果即时返回，LLM 语义建议异步流式补入，不阻塞 UI |
| 可演进 | P3 可用人工标注数据微调医疗小模型，替代通用 LLM 降成本 |

---

## 2. 总体架构（混合双引擎）

```
                        报告文本 / 元信息(meta)
                                  │
            ┌─────────────────────┴─────────────────────┐
            ▼                                            ▼
  ① 确定性引擎 RuleEngine（R1–R23）              ② LLM 语义层（异步后台）
     同步 · 39ms · 高精 · 可审计                      秒级 · 抓「软错误」
            │                                            │
            │                                            ├─ RAG 检索（本地向量库）
            │                                            │     语料：放射规范/ACR/RadLex/
            │                                            │           本院 KG / 历史修正报告
            │                                            ├─ 结构化输出（JSON）
            │                                            │     {error_type, location,
            │                                            │      severity, confidence, rationale}
            │                                            └─ 置信度门控（高→建议 / 低→人工复核）
            └─────────────────────┬─────────────────────┘
                                  ▼
                    ③ 融合仲裁层（Fusion）
       最终质控结论 + 每条来源标记（rule / llm / 双确认）
```

**设计信条**：确定性结果是「硬门禁」，LLM 是「第二阅片人」。二者结论可交叉验证——
同一错误被规则与 LLM **双确认** → 可信度提升；仅 LLM 发现且低置信 → 标「人工复核」，不自动判错。

---

## 3. 模块拆分与落点

> 所有新增模块位于 `src/`，与既有 `engine.py` 解耦，通过新接口接入 `server/` 与桌面壳。

| 模块 | 文件 | 职责 | 依赖 |
|---|---|---|---|
| LLM 客户端抽象 | `src/llm_client.py` | Provider 统一接口：本地 Ollama/vLLM（默认）、可选云 API；结构化(JSON)输出；优雅降级 | 标准库 `urllib`（无新硬依赖） |
| 提示词工程 | `src/llm_prompt.py` | 以 R1–R23 分类法做 few-shot，构造 system/user prompt，要求输出对齐既有错误 taxonomy 的 JSON | — |
| 检索增强 RAG | `src/rag.py` | `Retriever` 接口 + `SimpleCorpusRetriever`（内存关键词，零依赖）+ `ChromaRetriever`（可选 chromadb）；默认语料来自 `highfreq_lexicon`/`anatomy_lexicon` 及历史修正登记表 | chromadb 可选 |
| 编排层 | `src/llm_qc.py` | `run_llm_qc(text, meta)`：检索→构造 prompt→调用→解析→返回 `L1-*` 发现 | 上述三模块 |
| 接口层 | `server/llm_api.py` | `/api/v1/qc/llm` 异步接口，返回流式/最终建议 | 现有 FastAPI 单例 |
| 评估扩展 | `tools/eval_corpus.py`（增强） | 增加 `--llm` 模式，输出 LLM 发现供人工抽检精度 | — |
| 演示 | `tools/llm_qc_demo.py` | 单报告跑通：构造 prompt + 真实调用或 dry-run 展示 | — |

### 3.1 `llm_client.py` 接口契约
```python
@dataclass
class LLMResponse:
    text: str                 # 原始模型输出
    parsed: Optional[dict]    # 解析后的结构化对象（format=json 时）
    model: str
    elapsed_ms: int
    error: Optional[str]

class LLMClient:
    def chat(self, system: str, user: str, *, json_mode=True,
             temperature=0.1) -> LLMResponse: ...

def get_llm_client(config=None) -> LLMClient:
    """默认 OllamaClient(base_url=env OLLAMA_BASE_URL or localhost:11434,
       model=env OLLAMA_MODEL or 'qwen2.5:32b')；可切 CloudAPIClient。"""
```
- **合规默认本地**：`OllamaClient` 走 `http://localhost:11434/api/chat`，数据不出本机。
- **降级**：模型不可用/超时 → 返回 `error` 非空、`parsed=None`，上层跳过 LLM 层，规则结果照常返回（不致命）。

### 3.2 `llm_prompt.py` 设计
- **System**：你是资深放射科质控专家；仅报告「确定性规则可能漏掉的语义/语境类错误」；
  输出严格 JSON 数组；每条含 `error_type / location / severity(high|medium|low) /
  confidence(0~1) / rationale`；无问题返回 `[]`；**不重复**规则已能判定的硬错误。
- **User**：报告全文 + 元信息 + 检索到的参考规范片段（`[参考]` 块）+ 输出 schema 示例。
- **Few-shot 锚定**：内置 R1–R23 错误分类法摘要，使 LLM 输出与既有 taxonomy 对齐，
  便于融合层在必要时映射到 `L1-*` 前缀。

### 3.3 `rag.py` 设计
- `Retriever.retrieve(query, top_k=4) -> List[str]`（返回规范片段文本）。
- `SimpleCorpusRetriever`：内置语料（`build_default_corpus()`）来自
  `highfreq_lexicon._HIGHFREQ_WORDS` 术语白名单 + `anatomy_lexicon` 同义表 +
  **历史修正登记表**（若 `放射科报告修正登记表.docx` 存在则抽取条目）做内存关键词召回，零依赖零新包可跑。
- `ChromaRetriever`：`import chromadb` 守卫，生产环境托管向量库（RadLex/ACR/本院规范）。
- 所有检索片段进 prompt 时标注来源，便于 LLM 引用与审计。

### 3.4 融合仲裁（P1 落地，P0 先留接口）
- 每条 LLM 发现带 `confidence`：
  - `≥0.8` → 标「建议（高置信）」，可纳入最终结论；
  - `0.5~0.8` → 标「建议（待确认）」；
  - `<0.5` 或模型自判不确定 → 标「人工复核」，不自动判错。
- 与规则结论同源（同部位/同错误类型）→ 双确认，提升该条可信度权重。
- 最终结论区分为 `rule_findings` / `llm_findings` / `needs_review` 三类，前端分色展示。

---

## 4. 分阶段实施计划

| 阶段 | 目标 | 交付 | 周期 |
|---|---|---|---|
| **P0 原型** | 跑通 LLM 调用链路 | `llm_client`+`llm_prompt`+`rag`+`llm_qc`+`llm_qc_demo`；本地 Ollama 起 Qwen2.5 可端到端验证「抓到规则漏的错误」 | 1–2 周 |
| **P1 融合** | 置信度门控 + 仲裁 | `server/llm_api.py` 异步接口；融合层；前端分色展示；低置信转人工复核 | 2–3 周 |
| **P2 RAG 深化** | 降幻觉、可引用 | 灌入本院规范/RadLex/历史修正报告向量库；LLM 引用条款出报告 | 持续 |
| **P3 自训小模型** | 降成本/延迟 | 用 P0–P2 人工标注微调医疗小模型（Qwen-7B/14B）+ 保留 RAG | 视数据量 |

---

## 5. 评估指标（对齐「提检出、降误报」诉求）

- **召回**：在带标注/人工抽检集上，`rule ∪ llm` 相对 `rule` 单独的提升（重点看语义类漏报补获）。
- **精度**：LLM 单独发现的 precision（经 `tools/eval_corpus.py --llm` 抽样人工判定）；
  双确认条目精度应显著高于 LLM 单发。
- **门控有效性**：低置信转人工复核的比例与其中真错占比。
- **性能**：LLM 单报告耗时（P0 目标 < 8s @ 32B 量化），不影响确定性 39ms 即时返回。
- **合规**：100% 本地推理率（默认）、LLM 调用留痕率 100%。

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 幻觉/误报 | RAG 接地 + 置信度门控 + 人工复核兜底；LLM 不单独定错 |
| 延迟 | 确定性同步、LLM 异步；UX「先硬结果、后语义建议」 |
| 合规/隐私 | 默认本地推理；云 API 仅脱敏/开发环境；调用全留痕 |
| 成本 | P3 自训小模型替代通用 LLM |
| 与规则重复告警 | 融合层按（部位+错误类型）去重/双确认，避免刷屏 |

---

## 7. 与现有代码的关系（不破坏既有）

- 现有 `engine.py` / `highfreq_lexicon.py` / `anatomy_lexicon.py` **只读复用**（RAG 语料来源、taxonomy 锚定）。
- 新增模块为独立文件，不修改 R1–R23 逻辑；既有 281 测试零回退。
- 接入点：`server/main.py` 增加 LLM 异步路由；`desktop_app.py` 在结果区增加「AI 语义建议」面板。

---

## 附录 B：Qwen-3B 底座与模型替换（2026-08-22 已落地）

P0 骨架已以 **Qwen2.5-3B** 为底座搭好；teacher / 训练好的模型**只需改模型名即可替换**，无需改代码。

### B.1 推理端（`src/llm_client.py`）
- 默认模型 `qwen2.5:3b`；Provider 支持 `ollama`（默认本地）/ `vllm`（本地 OpenAI 兼容，部署微调模型常用）/ `cloud`（脱敏开发）。
- 配置优先级：函数入参 > `src/llm_config.json` > 环境变量。
- 替换模型：编辑 `src/llm_config.json` 的 `model` 字段，例如训练好后改为 `"model": "qwen2.5:3b-qc"`，`provider` 保持 `ollama`（或 `vllm` 指向本地服务）即上线。

### B.2 蒸馏 / 训练端（`train/` + `tools/build_qc_dataset.py`）
- `tools/build_qc_dataset.py --mode rule|teacher|both`：从确定性规则（银标）或本地 teacher（模型蒸馏）生成 LlamaFactory 可用的 alpaca jsonl，schema 与推理输出一致。
- `train/qwen3b_lora.yaml`：LlamaFactory LoRA 训练配置（基座 Qwen2.5-3B-Instruct，`model_name_or_path` 可换更小基座做进一步蒸馏）。
- 训练：`bash train/train.sh` → 导出 → `ollama create qc-qwen3b` → 改 `llm_config.json` 即上线。

### B.3 两层蒸馏落地路径（对应正文第 7 节）
- 规则蒸馏：`--mode rule` 把 RuleEngine 行为蒸馏进 3B（立即可用，无需 GPU 标注）。
- 模型蒸馏：`--mode teacher` 用本地微调 teacher 产出标签 → 将 `qwen3b_lora.yaml` 的基座换成更小模型（如 Qwen2.5-1.5B）重训，得到更轻的学生。

> 合规：teacher 若走 cloud 必须先脱敏；student 部署一律本地，PHI 不落地外部。
