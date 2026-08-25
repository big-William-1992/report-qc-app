"""report_qc_app 质控引擎包 (由原 engine.py 单文件拆分, 对外接口完全兼容)"""
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
from .models import Entity, Finding  # noqa: F401
_pull_symbols("models", "textsplit", "lexicon_region", "claims",
              "config_store", "ner", "scoring", "meta_extract",
              "rules_meta", "rules_typo", "rules_consistency",
              "rules_size", "rules_sentence", "rules_template",
              "engine_core")
from .engine_core import RuleEngine  # noqa: F401  (显式确保类可见)

# ── 全量符号互补 (2026-08-25) ──────────────────────────────────────────
# 循环导入会让部分子模块在"邻居半初始化"阶段互相拉取符号而遗漏;
# 全部加载完成后此处统一补齐: 任一模块有、兄弟模块没有的符号一律注入。
import sys as _sys
import importlib as _il

_ALL_SUBS = ("models", "textsplit", "lexicon_region", "claims", "config_store",
             "ner", "scoring", "meta_extract", "rules_meta", "rules_typo",
             "rules_consistency", "rules_size", "rules_sentence",
             "rules_template", "engine_core")

for _n in _ALL_SUBS:
    _mod = _il.import_module("." + _n, __name__)
    for _k, _v in list(vars(_mod).items()):
        if _k.startswith("__"):
            continue
        for _on in _ALL_SUBS:
            if _on == _n:
                continue
            _om = _sys.modules.get(__name__ + "." + _on)
            if _om is not None and not hasattr(_om, _k):
                setattr(_om, _k, _v)


# 兼容层: 原 engine.py 顶部 star 导入的词表符号继续对外可见
from ._compat_lexicons import *  # noqa: F401,F403
