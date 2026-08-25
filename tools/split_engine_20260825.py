#!/usr/bin/env python3
"""engine.py → src/engine/ 包 机械拆分脚本 (2026-08-25)"""
import os, shutil
from pathlib import Path

SRC = Path("/tmp/engine_orig.py")
PKG = Path("src/engine")
lines = SRC.read_text(encoding="utf-8").split("\n")  # 0-indexed

def seg(a, b):  # 1-indexed 闭区间
    return "\n".join(lines[a-1:b])

HEADER = '''# 本文件由机械拆分脚本从原 engine.py 迁移而来 (2026-08-25)
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
'''

DEPS = '''from ._compat_lexicons import _pull_symbols  # noqa: F401
_pull_symbols("models", "textsplit", "lexicon_region", "claims",
              "config_store", "ner", "scoring", "meta_extract")
'''

files = {
    "models.py":       HEADER + "\n" + seg(199, 224),
    "textsplit.py":    HEADER + "\n" + seg(57, 80) + "\n" + seg(98, 198),
    "lexicon_region.py": HEADER + "\n" + seg(225, 536) + "\n" + seg(541, 606),
    "claims.py":       HEADER + DEPS + "\n" + seg(607, 891),
    "config_store.py": HEADER + DEPS + "\n" + seg(892, 1109),
    "ner.py":          HEADER + DEPS + "\n" + seg(1111, 1179),
    "scoring.py":      HEADER + DEPS + "\n" + seg(2353, 2387),
    "meta_extract.py": HEADER + DEPS + "\n" + seg(2388, len(lines)),
    # ---- 规则 mixins ----
    "rules_typo.py":       HEADER + DEPS + "\nclass TypoRulesMixin:\n" + seg(1240,1252) + "\n" + seg(1783,1928) + "\n",
    "rules_meta.py":       HEADER + DEPS + "\nclass MetaRulesMixin:\n" + seg(1293,1334) + "\n" + seg(1408,1431) + "\n" + seg(1511,1564) + "\n" + seg(1748,1782) + "\n",
    "rules_consistency.py":HEADER + DEPS + "\nclass ConsistencyRulesMixin:\n" + seg(1335,1407) + "\n" + seg(1440,1510) + "\n" + seg(2065,2147) + "\n" + seg(2148,2227) + "\n" + seg(2284,2328) + "\n",
    "rules_size.py":       HEADER + DEPS + "\nclass SizeRulesMixin:\n" + seg(1432,1439) + "\n" + seg(1623,1747) + "\n",
    "rules_sentence.py":   HEADER + DEPS + "\nclass SentenceRulesMixin:\n" + seg(1929,2010) + "\n" + seg(2011,2064) + "\n",
    "rules_template.py":   HEADER + DEPS + "\nclass TemplateRulesMixin:\n" + seg(1986,2010) + "\n" + seg(1565,1622) + "\n" + seg(2228,2239) + "\n" + seg(2240,2283) + "\n",
    # ---- 主类 ----
    "engine_core.py": (HEADER + DEPS +
        "from .rules_meta import MetaRulesMixin\n"
        "from .rules_typo import TypoRulesMixin\n"
        "from .rules_consistency import ConsistencyRulesMixin\n"
        "from .rules_size import SizeRulesMixin\n"
        "from .rules_sentence import SentenceRulesMixin\n"
        "from .rules_template import TemplateRulesMixin\n\n"
        + seg(2329,2352) + "\n\n" +
        "class RuleEngine(MetaRulesMixin, TypoRulesMixin, ConsistencyRulesMixin,\n"
        "                 SizeRulesMixin, SentenceRulesMixin, TemplateRulesMixin):\n"
        + seg(1181,1239) + "\n" + seg(1253,1292) + "\n"),
}

INIT = ('''"""report_qc_app 质控引擎包 (由原 engine.py 单文件拆分, 对外接口完全兼容)"""'''
        + "\n" + HEADER.replace("# 本文件由机械拆分脚本从原 engine.py 机械拆分 (2026-08-25)\n", "")
        + '''
from ._compat_lexicons import _pull_symbols  # noqa: F401
from .models import Entity, Finding  # noqa: F401
_pull_symbols("models", "textsplit", "lexicon_region", "claims",
              "config_store", "ner", "scoring", "meta_extract",
              "rules_meta", "rules_typo", "rules_consistency",
              "rules_size", "rules_sentence", "rules_template",
              "engine_core")
from .engine_core import RuleEngine  # noqa: F401  (显式确保类可见)

# 兼容层: 原 engine.py 顶部 star 导入的词表符号继续对外可见
from ._compat_lexicons import *  # noqa: F401,F403
''')

COMPAT = '''# 原 engine.py 顶部词表导入的兼容层: 保证 `from engine import XXX` 对外不变
from _lexicons import *  # noqa: F401,F403
from _utils import *     # noqa: F401,F403
from engine_text import *  # noqa: F401,F403


def _pull_symbols(*module_names):
    """将兄弟模块全部符号(含下划线私有)注入调用方 globals; 先到先得不覆盖。"""
    import importlib
    import inspect
    frame = inspect.currentframe().f_back
    g = frame.f_globals
    pkg = g.get("__package__")
    for name in module_names:
        m = importlib.import_module("." + name, package=pkg)
        for k, v in list(vars(m).items()):
            if k.startswith("__"):
                continue
            if k not in g:
                g[k] = v
'''

if PKG.exists():
    shutil.rmtree(PKG)
PKG.mkdir()
for name, content in files.items():
    (PKG / name).write_text(content, encoding="utf-8")
(PKG / "__init__.py").write_text(INIT, encoding="utf-8")
(PKG / "_compat_lexicons.py").write_text(COMPAT, encoding="utf-8")

# 备份原文件并替换为占位说明
shutil.copy(SRC, "src/_engine_legacy_backup.py")
SRC.write_text('"""已迁移至 src/engine/ 包; 此文件仅为防止旧缓存误用, 请勿使用"""\n', encoding="utf-8")
print(f"✅ 拆分完成: {len(files)} 个模块 + __init__ + compat")
for name in files:
    print(f"   {name}: {len((PKG/name).read_text().splitlines())} 行")
