# 本文件由机械拆分脚本从原 engine.py 迁移而来 (2026-08-25)
# 原单文件按规则族/职责切分为包结构; 对外接口经 src/engine/__init__.py 完全兼容
import re, os, sys, json, shutil, logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple
from _lexicons import *  # noqa: F401,F403
from _utils import *     # noqa: F401,F403
from engine_text import *  # noqa: F401,F403
from anatomy_lexicon import SIDE_CHECK_ORGANS, R2_COVERED, EN_SIDE_ORGANS  # noqa: F401
try:
    from zh_radiology_synonyms import (
        normalize_text as _zh_norm_text,
        extract_followup as _zh_extract_followup,
    )
    from zh_ner import extract_entities as _zh_ner_entities
    _ZH_NLP_OK = True
except Exception:  # pragma: no cover
    _ZH_NLP_OK = False
try:
    from highfreq_lexicon import (
        segment_candidates as _hf_segment_candidates,
        highfreq_words as _hf_highfreq_words,
        is_pinyin_available as _hf_pinyin_available,
    )
    _HF_OK = True
except Exception:  # pragma: no cover
    _HF_OK = False
from ._compat_lexicons import _pull_symbols  # noqa: F401
_pull_symbols("models", "textsplit", "lexicon_region", "claims",
              "config_store", "ner", "scoring", "meta_extract")

SEVERITY_WEIGHT = {"high": 30, "medium": 15, "low": 5}


def score(findings: List[Finding]) -> Dict[str, dict]:
    """返回每维度评分明细：{维度: {"score": int, "deductions": [{"rule","delta","reason"}]}}。
    旧消费方如需简单 {维度:int} 请用 score_summary()。"""
    total_penalty = sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in findings)
    acc_ded = [{"rule": f.rule_id, "delta": -SEVERITY_WEIGHT.get(f.severity, 0),
                "reason": f.error_type} for f in findings]
    completeness, comp_ded = 100, []
    if any(f.rule_id == "R3-SCORE" for f in findings):
        completeness, comp_ded = 80, [{"rule": "R3-SCORE", "delta": -20, "reason": "评分标准缺失"}]
    norm = 90 if findings else 100
    norm_ded = [{"rule": "综合", "delta": -10, "reason": "存在质控问题"}] if findings else []
    return {
        "准确性": {"score": max(0, 100 - total_penalty), "deductions": acc_ded},
        "完整性": {"score": completeness, "deductions": comp_ded},
        "规范性": {"score": norm, "deductions": norm_ded},
        "及时性": {"score": 100, "deductions": []},
    }


def score_summary(scores: Dict[str, dict]) -> Dict[str, int]:
    """从 score() 的新结构提取 {维度: 分数(int)}，兼容旧消费方（驾驶舱/导出）。"""
    return {dim: (v.get("score", 100) if isinstance(v, dict) else v)
            for dim, v in scores.items()}


def error_type_counts(findings: List[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.error_type] = counts.get(f.error_type, 0) + 1
    return counts

