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

class MetaRulesMixin:
    def _r1_gender(self, text, ents, meta) -> List[Finding]:
        out = []
        # 性别来源：优先元信息，其次从报告正文解析（契合剪贴板/无元信息场景）
        rg = _norm_gender(meta.get("gender"))
        src = "报告头"
        if not rg:
            rg = _parse_gender_from_text(text)
            src = "报告正文"
        if not rg:
            return out  # 无法确定性别，不做判断（避免臆断）
        # 检查部位本身为乳腺/钼靶时，正文『乳腺/乳房』是检查对象而非女性专属器官，
        # 豁免（男性乳腺超声/钼靶检查 2026-08-18 修复，避免与 R21 口径不一致）。
        _breast_exam = any(k in ((meta.get("applied_site") or "") + (meta.get("modality") or ""))
                           for k in ("乳腺", "乳房", "钼靶"))
        seen = set()
        for e in [x for x in ents if x.label == "gender_organ"]:
            if _breast_exam and e.text in ("乳腺", "乳房"):
                continue
            expect = self.kg.expected_gender_for_organ(e.text)  # "male"/"female"
            if expect and expect != rg and e.text not in seen:
                # 否定豁免（2026-08-20）：该异性别器官在文本中出现且被『未见/无…』修饰
                # （如『前列腺未见异常』）属合法否定表述，不报性别矛盾；与 R9/R17 否定口径统一。
                # 注意：器官仅经 NER 实体识别、文本无字面词时，实体即断言，不豁免。
                if e.text in text and not _organ_asserted(text, e.text):
                    continue
                seen.add(e.text)
                box = {"findings": "影像描述段", "impression": "影像结论段"}.get(e.section, "报告正文")
                out.append(Finding("R1-GENDER", "性别矛盾", "high",
                    f"{src}性别为{_zh(rg)}，但{box}出现{_zh(expect)}性专属器官「{e.text}」"
                    f"（{'男性不应有子宫/卵巢等' if expect=='female' else '女性不应有前列腺/睾丸等'}）",
                    e.text, (e.start, e.end)))
        # 原 R11-GENDER 维度：信息框性别 vs 正文解析性别不一致（正文无明确异性别器官时兜底，避免与上面器官维度双报）
        if not out:
            rg_meta = _norm_gender(meta.get("gender"))
            rg_text = _parse_gender_from_text(text)
            if rg_meta and rg_text and rg_meta != rg_text:
                out.append(Finding("R1-GENDER", "性别矛盾", "high",
                    f"患者基础信息性别为『{_zh(rg_meta)}』，但报告正文解析出性别『{_zh(rg_text)}』，二者矛盾",
                    "", (-1, -1)))
        return out

    # R2 左右混淆（同一解剖族：描述段 vs 印象段方位互斥）
    def _r3_score(self, text, meta) -> List[Finding]:
        out = []
        modality = (meta.get("modality") or meta.get("applied_site") or "").strip().lower()
        # 2026-08-18：modality 为空时回退 applied_site（临床常只填申请部位）
        # 2026-08-21：归一别名（乳腺X线/乳腺钼靶/双乳/乳房… → 乳腺），否则 MODALITY_SCORE
        # 查不到返回 None，乳腺报告无 BI-RADS 不告警（漏报 R3）。
        modality = MODALITY_ALIASES.get(modality, modality)
        if not modality:
            return out
        required = self.kg.required_score_for_modality(modality)
        if not required:
            return out
        # 间隔词容忍（2026-08-18）：『BI-RADS分级4a』『PI-RADS评分3分』等标配写法
        pat = {"BI-RADS": r"BI-?RADS\s*(?:分级|评分|级|类别)?\s*[:：]?\s*\d",
               "PI-RADS": r"PI-?RADS\s*(?:分级|评分|级|类别)?\s*[:：]?\s*\d"}.get(required)
        # 阴性报告豁免（2026-08-18）：『乳腺未见异常』『前列腺未见明显异常』等
        # 整体未见异常声明可不强制分级评分，避免阴性报告被无谓扣分。
        _neg_report = re.search(r"未见异常|未见明显异常|未见实质性病变|未见占位性病变|正常$", text)
        if pat and not re.search(pat, text, re.I) and not _neg_report:
            out.append(Finding("R3-SCORE", "评分标准缺失", "medium",
                f"检查部位={modality} 要求包含 {required}，但报告未检出", "", (-1, -1)))
        return out

    # R4 单位错误
    def _r6_site(self, text, meta) -> List[Finding]:
        out = []
        applied = meta.get("applied_site", "")
        if not applied:
            return out
        # 多区域申请拆分（2026-08-18：『胸部、上腹部』按分隔符拆成多族，
        # 正文覆盖任一申请区域即视为相符；此前只归一为单族导致误报 R6）
        _applied_fams = set()
        for _part in re.split(r"[、,，;；\s]+", applied):
            _f = self.kg.norm_site(_part.strip())
            if _f:
                _applied_fams.add(_f)
        norm_applied = next(iter(_applied_fams)) if _applied_fams else self.kg.norm_site(applied)
        if not norm_applied:
            return out
        # 仅扫描报告正文（检查所见 + 诊断印象），排除元信息头部，避免"申请部位"自身被计入
        secs = self._split_for_r5(text)
        body = secs["findings"] + "\n" + secs["impression"]
        found_families = set()
        # 多字部位键直接扫描；单字键（脑/肺/胰/脾/肾）需满足侧别/病变语境，避免
        # 『未见脑转移』等偶发提及误报（2026-08-18 修复）。
        _multi = [k for k in SITE_NORM if k not in _RISKY_SINGLE]
        for m in re.finditer(r"|".join(map(re.escape, _multi)), body):
            fam = self.kg.norm_site(m.group(0))
            if fam:
                found_families.add(fam)
        for k in _RISKY_SINGLE:
            if _r6_site_single_key_hits(body, k):
                fam = self.kg.norm_site(k)
                if fam:
                    found_families.add(fam)
        if found_families and not (_applied_fams & found_families):
            out.append(Finding("R6-SITE", "登记部位不符", "high",
                f"申请部位归一化={sorted(_applied_fams)}，但报告内容涉及{found_families}", "", (-1, -1)))
        return out

    # R18 检查部位器官漏写（登记区域声明 → 影像描述段应含该区域器官）
    # 与 R6(登记部位错配) 互补：R6 抓『申请胸部却写腹部』，R18 抓『申请了上腹部但描述段
    # 对肝/胆/胰/脾/肾等上腹部器官一个都没提』。仅查检查所见段；整段"未见异常"整体声明
    # （不点名器官）视为已覆盖，避免"上腹部CT未见异常"被误报。多区域分别校验、互不牵连。
    def _infer_exam_type(self, text: str, meta: dict):
        """推断检查类型（原 R20 逻辑下沉）：优先用登记部位/检查方式，其次用报告正文头部（OCR 场景）。"""
        applied = (meta.get("applied_site") or "").strip().lower()
        modality = (meta.get("modality") or "").strip().lower()
        src = " | ".join([applied, modality]).lower()
        for tname, kws in _TYPE_KEYWORDS:
            if any(kw in src for kw in kws):
                return tname
        head = text[:200].lower()
        for tname, kws in _TYPE_KEYWORDS:
            if any(kw in head for kw in kws):
                return tname
        return None

    def _r21_gender_site(self, text, meta) -> List[Finding]:
        out = []
        rg = _norm_gender(meta.get("gender"))
        if not rg:
            rg = _parse_gender_from_text(text)
        if not rg:
            return out
        applied = (meta.get("applied_site") or "").strip().lower()
        modality = (meta.get("modality") or "").strip().lower()
        src = applied + " " + modality
        female_only = ["子宫", "卵巢", "宫颈", "阴道", "输卵管"]
        male_only = ["前列腺", "睾丸", "阴茎", "精囊", "阴囊"]
        # 乳腺：仅钼靶/乳腺X线判女性专属——男性乳腺超声（乳房发育）是合理临床
        # 适应证，2026-08-18 修复此前『男+乳腺超声』误报。
        if rg == "male" and ("钼靶" in src or "乳腺x线" in src or "乳腺x线" in modality or "钼" in src):
            female_only = ["钼靶"] + female_only
        for kw in female_only:
            if kw in src and rg == "male":
                out.append(Finding("R21-GENDER-SITE", "检查部位与性别不符", "high",
                    f"登记检查部位含「{kw}」（女性专属检查），与{_zh(rg)}性别不符",
                    kw, (-1, -1)))
                break
        for kw in male_only:
            if kw in src and rg == "female":
                out.append(Finding("R21-GENDER-SITE", "检查部位与性别不符", "high",
                    f"登记检查部位含「{kw}」（男性专属检查），与{_zh(rg)}性别不符",
                    kw, (-1, -1)))
                break
        return out

    # R7 描述内部矛盾（同一描述段内出现男女专属器官混用 —— 真实自相矛盾）
    # R7（原「描述内部矛盾」：影像描述段男女专属器官混用）已并入 R12-SENTENCE（见 _r12_sentence
    # 分支3 的整段级兜底），避免与 R12 句内男女器官混用重复告警；R7-INTERNAL 不再单独产出。

    # R8 同音/近音错别字（多由语音录入产生：词典由 rules_config.json 维护，可在 GUI 增删）
