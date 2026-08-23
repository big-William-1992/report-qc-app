"""规则 + LLM 融合仲裁层。

把确定性引擎发现（engine.Finding）与大模型语义发现（llm_qc 的 dict）统一为同构结构，
按「错误类型 + 片段」去重，并依据 LLM 置信度做门控：

  - confidence >= 0.8            → status="suggestion"       （高置信建议，可纳入结论）
  - 0.5 <= confidence < 0.8      → status="suggestion_pending"
  - confidence < 0.5 或缺失      → status="needs_review"     （人工复核，不自动判错）

规则发现一律 status="rule"（硬门禁，高可信）。
双确认：同一 (error_type, 片段) 同时被规则与 LLM 命中 → dual_confirmed=True（可信度提升）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_CONF_HIGH = 0.8
_CONF_MID = 0.5
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def _norm(s: Optional[str]) -> str:
    return "".join((s or "").split()).lower()


def rule_finding_to_dict(f: Any) -> Dict[str, Any]:
    return {
        "rule_id": getattr(f, "rule_id", ""),
        "error_type": getattr(f, "error_type", ""),
        "severity": getattr(f, "severity", "low"),
        "message": getattr(f, "message", ""),
        "snippet": getattr(f, "snippet", ""),
        "span": list(getattr(f, "span", (-1, -1))),
        "source": "rule",
        "confidence": 1.0,
        "status": "rule",
        "dual_confirmed": False,
    }


def llm_finding_to_dict(it: Dict[str, Any]) -> Dict[str, Any]:
    conf = it.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except Exception:
        conf = None
    if conf is None or conf < _CONF_MID:
        status = "needs_review"
    elif conf < _CONF_HIGH:
        status = "suggestion_pending"
    else:
        status = "suggestion"
    return {
        "rule_id": it.get("rule_id") or ("L1-" + str(it.get("error_type", "UNKNOWN"))),
        "error_type": it.get("error_type"),
        "severity": it.get("severity", "low"),
        "message": it.get("rationale") or "",
        "snippet": it.get("location") or "",
        "span": [-1, -1],
        "source": "llm",
        "confidence": conf,
        "status": status,
        "dual_confirmed": False,
    }


def _key(d: Dict[str, Any]):
    return (_norm(d.get("error_type") or d.get("rule_id")), _norm(d.get("snippet")))


def fuse(rule_findings: List[Any], llm_findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    rule_dicts = [rule_finding_to_dict(f) for f in (rule_findings or [])]
    llm_dicts = [llm_finding_to_dict(it) for it in (llm_findings or [])]

    rule_by_key: Dict[Any, List[Dict[str, Any]]] = {}
    for d in rule_dicts:
        rule_by_key.setdefault(_key(d), []).append(d)

    for d in llm_dicts:
        matched = rule_by_key.get(_key(d))
        if matched:
            d["dual_confirmed"] = True
            for r in matched:
                r["dual_confirmed"] = True

    fused = rule_dicts + llm_dicts
    fused.sort(key=lambda d: (0 if d["source"] == "rule" else 1,
                              -_SEV_RANK.get(d.get("severity"), 0)))

    return {
        "rule_findings": rule_dicts,
        "llm_findings": llm_dicts,
        "fused": fused,
        "counts": {
            "rule": len(rule_dicts),
            "llm": len(llm_dicts),
            "dual_confirmed": sum(1 for d in llm_dicts if d["dual_confirmed"]),
            "needs_review": sum(1 for d in llm_dicts if d["status"] == "needs_review"),
            "suggestion": sum(1 for d in llm_dicts if d["status"] == "suggestion"),
        },
    }
