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

class ChineseRadiologyNER:
    # 2026-08-21 架构收敛：段落切分统一走模块级 _sectionize()（与 R5._split_for_r5 同源），
    # SECTION_MAP 保留为兼容别名（指向同一数据源 _SECTION_SPANS_SRC）。
    SECTION_MAP = _SECTION_SPANS_SRC

    def _split_sections(self, text: str):
        return _sectionize(text)

    def _section_of(self, sections, pos: int) -> str:
        for sec, s, e in sections:
            if s <= pos < e:
                return sec
        return sections[-1][0] if sections else "findings"

    def extract(self, text: str) -> List[Entity]:
        sections = self._split_sections(text)
        ents: List[Entity] = []
        # 方位词 + 解剖同义词：统一按词长降序做最长匹配，避免短词（"左"）抢先占用
        # 长词（"双侧""左肾"）的区间，使短词（"左""右""双"）被跳过。
        matched = []  # 所有已抽取实体的字符区间，用于跳过被覆盖的短词

        def _add(word, label, canon):
            for m in re.finditer(re.escape(word), text):
                s, e = m.start(), m.end()
                if any((ms <= s < me) or (ms < e <= me) or (s < ms and e > me)
                       for ms, me in matched):
                    continue
                ents.append(Entity(word, label, s, e,
                                    self._section_of(sections, s), canon))
                matched.append((s, e))

        combined = [(w, "laterality", c) for w, c in LATERALITY.items()] + \
                   [(w, "anatomy", c) for w, c in ANATOMY_SYNONYMS.items()]
        for word, label, canon in sorted(combined, key=lambda kv: -len(kv[0])):
            _add(word, label, canon)
        for word, gender in GENDER_ORGANS.items():
            for m in re.finditer(re.escape(word), text):
                ents.append(Entity(word, "gender_organ", m.start(), m.end(),
                                    self._section_of(sections, m.start()), gender))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([A-Za-z°][A-Za-z°/0-9]*|\u00b0)", text):
            unit = m.group(2).lower()
            # 2026-08-18 修复：① 单位首字符不允许 /（血压『120/80mmHg』不再被
            # 吞成 /80mmhg）；② VALID_UNITS 大写 HU 比对时先小写化（此前 CT 值
            # 『35HU』必误报 R4）。
            label = "measurement" if unit in _VALID_UNITS_LOWER else "bad_unit"
            ents.append(Entity(m.group(0), label, m.start(), m.end(),
                                self._section_of(sections, m.start()), unit))
        # —— 中文征象 / 随访 / 程度 实体（项目自建 zh_ner，离线词典式）——
        # 仅补充引擎既有 NER 未覆盖的 sign/followup/degree 三类；anatomy/laterality
        # 仍由上方 ANATOMY_SYNONYMS / LATERALITY 负责，避免重复抽取。
        # 【2026-08-18 架构说明】zh_ner 的「部位实体」有意不接入：ANATOMY 全量词表
        # 含征象别名（胸水/心影增大等）且匹配面宽，接入会放大 R5/R2 误报；
        # normalize_text（同义词归一）同样未进 run() 预通道——会在归一文本上产出
        # span，与其余规则基于原文本的坐标体系冲突（R19 span 教训）。如后续要
        # 扩大器官覆盖，应扩充 ANATOMY_SYNONYMS 而非接入 zh_ner 全量。
        if _ZH_NLP_OK:
            for ze in _zh_ner_entities(text):
                eng_label = _ZH_ENT_LABEL_MAP.get(ze.label)
                if eng_label is None:
                    continue
                if any((ms <= ze.start < me) or (ms < ze.end <= me)
                       or (ze.start < ms and ze.end > me) for ms, me in matched):
                    continue
                sec = self._section_of(sections, ze.start)
                ents.append(Entity(ze.text, eng_label, ze.start, ze.end, sec, ze.canonical))
                matched.append((ze.start, ze.end))
        return ents

