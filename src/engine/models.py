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

@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int
    section: str = ""
    canonical: Optional[str] = None


@dataclass
class Finding:
    rule_id: str
    error_type: str
    severity: str      # high / medium / low
    message: str
    snippet: str = ""
    span: tuple = (-1, -1)
    suggestion: str = ""   # 可自动修正的建议值（仅 R8 错别字填充）


# 确凿繁体独有字（简体不使用这些字形；放射报告高频，2026-08-18 新增检测用）。
# 注意：只收录「简体绝不会出现」的字形。简繁同形字（描/查/告/囊/腺/骨、以及
# 象限/系统/空间/环境/液体/处置/操作/登录/疑虑/权限 中的
# 限/空/境/系/液/置/作/登/疑/操）若误收会在简体报告上系统性误报。
# 2026-08-20 复核：原字符串声称「已修正」但本体仍含上述同形字，本次全量剔除。
# 单位白名单小写化 (原 engine.py L25, 2026-08-18)
_VALID_UNITS_LOWER = {u.lower() for u in VALID_UNITS}
