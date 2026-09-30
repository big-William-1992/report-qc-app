"""LLM 语义质控编排层。

run_llm_qc(text, meta) 串联：RAG 检索 → 构造 prompt → 调用 LLM → 解析为 L1-* 发现。
返回结构便于上层（服务 / 融合仲裁）消费；模型不可用时优雅返回 available=False。
"""
from __future__ import annotations

from typing import Optional, List

import logging

from llm_client import LLMClient, get_llm_client, load_llm_config
from llm_prompt import build_qc_prompt, build_qc_prompt_ft
from rag import Retriever, get_retriever
from llm_fusion import fuse

logger = logging.getLogger(__name__)


# ── error_type 白名单 ────────────────────────────────────────
# 背景(2026-09-30)：微调模型在分布外输入上会**拼造**不存在的错误码
# （实测造出 "R1-CONSISTENCY"/"R2-CONSISTENCY"，而 R1 是 R1-GENDER、
# R2 是 R2-LATERALITY、一致性是 R5-CONSISTENCY）。
# `_normalize` 原先把 rule_id 合成 "L1-" + error_type，等于把模型编的字符串
# 直接当成规则编号往下游传，绕过了 llm_engine 里已有的 ALLOWED_RULE_IDS 校验。
# 这里做**出口校验**：不在白名单内的码一律丢弃并留痕，避免幻觉编码流进结果。
#
# 白名单 = 规则引擎真实产出的规则号 ∪ ft 训练分布里的码 ∪ L1-OTHER(逃生舱)。
# 与 src/engine/ 的规则号保持同步；新增规则时同步更新此处。
ALLOWED_LLM_ERROR_TYPES: frozenset = frozenset({
    # 患者基本信息
    "R1-GENDER", "R21-GENDER-SITE", "R21-GENDER",
    # 部位/方位
    "R2-LATERALITY", "R6-SITE", "R18-COVERAGE",
    # 评分系统
    "R3-SCORE",
    # 单位与尺寸
    "R4-UNIT", "R22-UNIT", "R22-SIZE", "R22-SIZE-MISSING", "R22-QUAL",
    # 描述-结论一致性
    "R5-CONSISTENCY", "R17-PERREGION", "R14-NATURE", "R14-COUNT",
    "R14-NORMAL", "R15-NORMAL", "R15-PRESENCE",
    # 句内逻辑
    "R12-SENTENCE", "R9-CONFLICT", "R7-INTERNAL",
    # 定性与处置/随访
    "R24-ADVICE", "R25-TEMPORAL", "R16-FOLLOWUP",
    # 术语/错别字
    "R8-TYPO", "R19-HOMOPHONE", "R23-TRADITIONAL", "R19-WHITELIST",
    # 模板与要素
    "R10-TEMPLATE", "R11-ABNORMAL", "R20-TEMPLATE",
    # 逃生舱：模型认定属全新类型时使用（要求 rationale 说明）
    "L1-OTHER",
})


# ⚠️ 已知限制(2026-08-25): 微调模型 confidence 几乎恒为 1.0 (训练标注全是 1.0),
# 融合层的置信度分级对 LLM 来源发现实际不产生区分度。校准需真实 badcase
# 数据驱动(见 P1-badcase 回流), 勿硬编码折扣系数。
def _normalize(item: dict) -> Optional[dict]:
    """把模型返回的一条发现规整为内部结构；error_type 不合法时返回 None。"""
    error_type = str(item.get("error_type") or "").strip()
    if error_type not in ALLOWED_LLM_ERROR_TYPES:
        logger.warning(
            "丢弃 LLM 返回的非法 error_type=%r（不在白名单，疑似幻觉编码）；"
            "location=%r rationale=%r",
            error_type, item.get("location"), item.get("rationale"),
        )
        return None
    return {
        "rule_id": "L1-" + error_type,
        "error_type": error_type,
        "location": item.get("location"),
        "severity": item.get("severity", "low"),
        "confidence": item.get("confidence"),
        "rationale": item.get("rationale"),
        "source": "LLM",
    }


def run_llm_qc(text: str, meta: Optional[dict] = None, *,
               client: Optional[LLMClient] = None,
               retriever: Optional[Retriever] = None,
               rag_top_k: int = 4, rag_kind: str = "simple",
               config: Optional[dict] = None) -> dict:
    client = client or get_llm_client(config)
    if not client.available():
        return {"available": False,
                "error": "LLM 不可用（如本地 Ollama 未启动），规则结果不受影响。",
                "findings": []}
    cfg = config if config is not None else load_llm_config()
    prompt_mode = (cfg or {}).get("prompt_mode", "full")
    if prompt_mode == "ft":
        # 微调模型：与训练分布对齐，跳过 taxonomy/RAG（防幻觉）
        system, user = build_qc_prompt_ft(text)
        resp = client.chat(system, user, json_mode=True, temperature=0.1)
        ctx: list = []
    else:
        retriever = retriever or get_retriever(rag_kind)
        ctx = retriever.retrieve(text, top_k=rag_top_k)
        system, user = build_qc_prompt(text, meta=meta, rag_contexts=ctx)
        resp = client.chat(system, user, json_mode=True, temperature=0.1)
    if resp.error:
        return {"available": True, "error": resp.error,
                "model": resp.model, "findings": []}
    findings: List[dict] = []
    parsed = resp.parsed
    if isinstance(parsed, list):
        findings = [n for n in (_normalize(it) for it in parsed) if n]
    elif isinstance(parsed, dict):
        if "findings" in parsed:
            for it in (parsed.get("findings") or []):
                n = _normalize(it)
                if n:
                    findings.append(n)
        elif parsed:
            # 模型直接返回单条发现（未包成数组）；空对象 {} 视为无发现
            n = _normalize(parsed)
            findings = [n] if n else []
    return {"available": True, "model": resp.model, "error": None,
            "rag_contexts": ctx, "findings": findings, "raw": resp.text}


def run_full_qc(report: str, meta: Optional[dict] = None, *,
                run_rules: bool = True, run_llm: bool = True,
                config: Optional[dict] = None, rag_kind: str = "simple") -> dict:
    """统一质控入口：规则 + LLM 融合。返回 {rule_findings, llm_findings, fused, counts}。

    - 规则结果即时生成（确定性、高可信）；
    - LLM 不可用时优雅降级（llm_available=False，规则结果照常返回）。
    """
    from engine import RuleEngine, extract_meta

    if meta is None:
        meta = extract_meta(report)

    rule_findings = []
    if run_rules:
        rule_findings = RuleEngine().run(report, meta)

    llm_result = {"available": False, "findings": [], "error": "llm disabled"}
    if run_llm:
        llm_result = run_llm_qc(report, meta, config=config, rag_kind=rag_kind)

    fused = fuse(rule_findings, llm_result.get("findings") or [])
    fused["llm_available"] = llm_result.get("available", False)
    fused["llm_error"] = llm_result.get("error")
    fused["llm_model"] = llm_result.get("model")
    return fused

