"""
rules_meta.py — 患者元信息规则（R1/R2/R3/R4/R6/R11/R21）（P2 拆分自 engine.py）
==============================================================================
- MetaRulesMixin：_r1_gender（性别矛盾）、_r2_laterality（左右混淆）、
  _r3_score（评分缺失）、_r4_unit（单位错误）、_r6_site（登记部位不符）、
  _r11_context（上下文逻辑错误）、_r21_gender_site（性别-部位联动）

依赖：_lexicons、engine_helpers、engine_types。无反向依赖。
"""
import re
from typing import List

from _lexicons import *  # noqa: F401,F403

from engine_helpers import (_norm_gender, _parse_gender_from_text, _zh,
                            _project_side, _detect_side_in_text)
from engine_types import Finding


class MetaRulesMixin:
    # R1 性别矛盾
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
        seen = set()
        for e in [x for x in ents if x.label == "gender_organ"]:
            expect = self.kg.expected_gender_for_organ(e.text)  # "male"/"female"
            if expect and expect != rg and e.text not in seen:
                seen.add(e.text)
                box = {"findings": "影像描述段", "impression": "影像结论段"}.get(e.section, "报告正文")
                out.append(Finding("R1-GENDER", "性别矛盾", "high",
                    f"{src}性别为{_zh(rg)}，但{box}出现{_zh(expect)}性专属器官「{e.text}」"
                    f"（{'男性不应有子宫/卵巢等' if expect=='female' else '女性不应有前列腺/睾丸等'}）",
                    e.text, (e.start, e.end)))
        return out

    # R2 左右混淆（同一解剖族：描述段 vs 印象段方位互斥）
    def _r2_laterality(self, text, ents) -> List[Finding]:
        out = []
        # 取带左右前缀的解剖实体（左肾→L-kidney 等），按器官族归组
        fam_of = lambda c: (c or "").split("-", 1)[-1] if (c or "").startswith(("L-", "R-")) else None
        fam_sides = {}
        for e in ents:
            if e.label != "anatomy" or not e.canonical:
                continue
            if e.section not in ("findings", "impression"):  # 仅在描述段与印象段间比较
                continue
            fam = fam_of(e.canonical)
            if not fam:
                continue
            side = "left" if e.canonical.startswith("L-") else "right"
            fam_sides.setdefault(fam, {"findings": set(), "impression": set()})
            fam_sides[fam][e.section].add(side)
        for fam, sides in fam_sides.items():
            f, i = sides["findings"], sides["impression"]
            if f and i and f.isdisjoint(i):
                out.append(Finding("R2-LATERALITY", "左右侧混淆", "high",
                    f"同一解剖部位族「{fam}」在影像描述段为{f}侧、诊断印象段为{i}侧，方位矛盾",
                    "", (-1, -1)))
        return out

    # R3 评分缺失
    def _r3_score(self, text, meta) -> List[Finding]:
        out = []
        modality = meta.get("modality")
        if not modality:
            return out
        required = self.kg.required_score_for_modality(modality)
        if not required:
            return out
        pat = {"BI-RADS": r"BI-?RADS\s*[:：]?\s*\d", "PI-RADS": r"PI-?RADS\s*[:：]?\s*\d"}.get(required)
        if pat and not re.search(pat, text, re.I):
            out.append(Finding("R3-SCORE", "评分标准缺失", "medium",
                f"检查部位={modality} 要求包含 {required}，但报告未检出", "", (-1, -1)))
        return out

    # R4 单位错误
    def _r4_unit(self, ents) -> List[Finding]:
        out = []
        for e in [x for x in ents if x.label == "bad_unit"]:
            out.append(Finding("R4-UNIT", "计量单位错误", "low",
                f"检出非常规单位表示「{e.text}」（单位={e.canonical}）", e.text, (e.start, e.end)))
        return out

    # R6 登记部位不符（申请部位 vs 报告主体解剖）
    def _r6_site(self, text, meta) -> List[Finding]:
        out = []
        applied = meta.get("applied_site", "")
        if not applied:
            return out
        norm_applied = self.kg.norm_site(applied)
        if not norm_applied:
            return out
        # 仅扫描报告正文（检查所见 + 诊断印象），排除元信息头部，避免"申请部位"自身被计入
        secs = self._secs(text)
        body = secs["findings"] + "\n" + secs["impression"]
        found_families = set()
        for m in re.finditer(r"|".join(map(re.escape, SITE_NORM.keys())), body):
            fam = self.kg.norm_site(m.group(0))
            if fam:
                found_families.add(fam)
        if found_families and norm_applied not in found_families:
            out.append(Finding("R6-SITE", "登记部位不符", "high",
                f"申请部位归一化={norm_applied}，但报告内容涉及{found_families}", "", (-1, -1)))
        return out

    # R11 上下文逻辑错误（信息框 vs 描述框/结论框 跨框比对）
    def _r11_context(self, text, meta, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        # R11-1 左右一致性：项目/检查部位侧别 与 描述/结论提及的方位 比对
        # 侧别来源自动兜底：meta.laterality > 报告内『检查项目/检查部位』标签 > meta.applied_site
        # （用户常把左右写在 项目 自由文本、不填独立侧别框，此前规则因 laterality 空而整条跳过）
        info_side = _project_side(text, meta)
        if info_side and info_side != "bilateral":
            for label, seg in (("影像描述", f_txt), ("影像结论", i_txt)):
                sides = _detect_side_in_text(seg)
                if not sides or "bilateral" in sides:
                    continue
                # 放宽（消除误报）：R11-SIDE 原在『报告只提对侧、未提本侧』时误报为左右矛盾，
                # 但本侧可能正常未描述，属『未涉及』而非矛盾。现改为：
                #   - 本侧未提及（info_side 不在 sides）→ 未涉及，不报；
                #   - 本侧被提及（info_side 在 sides）→ 与信息框侧别一致，不报。
                # 即仅凭单侧/双侧的“提及”不再判定左右矛盾（真正矛盾交由 R17 逐部位精确比对）。
                if info_side == "left" and "left" not in sides:
                    continue
                if info_side == "right" and "right" not in sides:
                    continue
                # 本侧已被提及：一致，不报（双侧均描述也不构成矛盾）
        # R11-2 描述异常 → 结论正常 矛盾：已下放到 R17 逐部位精确比对
        # （按器官+侧别精确到同一部位；无法归属具体部位时由 R17 段级兜底保持原语义）。
        # R11-3 信息框性别 vs 正文解析性别 矛盾（上下文不一致）
        rg_meta = _norm_gender(meta.get("gender"))
        rg_text = _parse_gender_from_text(text)
        if rg_meta and rg_text and rg_meta != rg_text:
            out.append(Finding("R11-GENDER", "上下文逻辑错误-性别矛盾", "high",
                f"患者基础信息性别为『{_zh(rg_meta)}』，但报告正文解析出性别『{_zh(rg_text)}』，二者矛盾",
                "", (-1, -1)))
        return out

    # R21 性别-部位联动（检查类型级）：男性检查乳腺/子宫/卵巢，女性检查前列腺/睾丸。
    # 与 R1(正文出现异性别器官) 互补：R1 抓正文描述，R21 抓『检查部位登记』层面——
    # 登记部位/检查方式与性别不匹配（如男性做钼靶、女性做前列腺 MR）。
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
        female_only = ["乳腺", "乳房", "钼靶", "子宫", "卵巢", "宫颈", "阴道", "输卵管"]
        male_only = ["前列腺", "睾丸", "阴茎", "精囊", "阴囊"]
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
