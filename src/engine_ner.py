"""
engine_ner.py — 中文放射 NER（P2 拆分自 engine.py）
====================================================
- ChineseRadiologyNER：段落切分 + 方位/解剖/性别器官/测量单位实体抽取，
  兼合项目自建 zh_ner 的 征象/随访/程度 三类实体。
- _ZH_NLP_OK：zh_radiology_synonyms / zh_ner 导入降级开关（R16 也依赖）。
"""
import re
from typing import List

from _lexicons import *  # noqa: F401,F403

from engine_types import Entity

# 中文放射同义词归一 + 中文临床 NER（项目自建，离线可用；详见 dataset_catalog ZH 资源）
# 以 try/except 降级：极端打包缺失时不影响既有规则。
try:
    from zh_ner import extract_entities as _zh_ner_entities
    _ZH_NLP_OK = True
except Exception:  # pragma: no cover - 仅防御性降级
    _ZH_NLP_OK = False

# zh_ner 中文标签 → 引擎内部英文标签（与 anatomy / laterality 等保持一致）
_ZH_ENT_LABEL_MAP = {"征象": "sign", "随访": "followup", "程度": "degree"}


class ChineseRadiologyNER:
    SECTION_MAP = [
        (re.compile(r"检查所见|影像描述|表现|imaging findings|findings|radiographic findings", re.I), "findings"),
        (re.compile(r"诊断印象|印象|诊断意见|结论|impression", re.I), "impression"),
        (re.compile(r"患者信息|患者|性别|年龄|检查部位|申请"), "meta"),
    ]

    def _split_sections(self, text: str):
        spans = []
        for pat, sec in self.SECTION_MAP:
            for m in pat.finditer(text):
                spans.append((m.start(), sec))
        spans.sort()
        sections = []
        for i, (start, sec) in enumerate(spans):
            end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
            sections.append((sec, start, end))
        if not sections:
            sections = [("findings", 0, len(text))]
        return sections

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
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([A-Za-z°/][A-Za-z°/0-9]*|°)", text):
            unit = m.group(2).lower()
            label = "measurement" if unit in VALID_UNITS else "bad_unit"
            ents.append(Entity(m.group(0), label, m.start(), m.end(),
                                self._section_of(sections, m.start()), unit))
        # —— 中文征象 / 随访 / 程度 实体（项目自建 zh_ner，离线词典式）——
        # 仅补充引擎既有 NER 未覆盖的 sign/followup/degree 三类；anatomy/laterality
        # 仍由上方 ANATOMY_SYNONYMS / LATERALITY 负责，避免重复抽取。
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
