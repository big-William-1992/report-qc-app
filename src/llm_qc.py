"""LLM 语义质控编排层。

run_llm_qc(text, meta) 串联：RAG 检索 → 构造 prompt → 调用 LLM → 解析为 L1-* 发现。
返回结构便于上层（服务 / 融合仲裁）消费；模型不可用时优雅返回 available=False。
"""
from __future__ import annotations

from typing import Optional, List, Dict, Any

from llm_client import LLMClient, get_llm_client, load_llm_config
from llm_prompt import build_qc_prompt, build_qc_prompt_ft
from rag import Retriever, get_retriever
from llm_fusion import fuse


# ⚠️ 已知限制(2026-08-25): 微调模型 confidence 几乎恒为 1.0 (训练标注全是 1.0),
# 融合层的置信度分级对 LLM 来源发现实际不产生区分度。校准需真实 badcase
# 数据驱动(见 P1-badcase 回流), 勿硬编码折扣系数。
def _normalize(item: dict) -> dict:
    return {
        "rule_id": "L1-" + str(item.get("error_type", "UNKNOWN")),
        "error_type": item.get("error_type"),
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
        findings = [_normalize(it) for it in parsed]
    elif isinstance(parsed, dict):
        if "findings" in parsed:
            for it in (parsed.get("findings") or []):
                findings.append(_normalize(it))
        elif parsed:
            # 模型直接返回单条发现（未包成数组）；空对象 {} 视为无发现
            findings = [_normalize(parsed)]
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

