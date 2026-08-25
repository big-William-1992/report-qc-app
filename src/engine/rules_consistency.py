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

class ConsistencyRulesMixin:
    def _r2_laterality(self, text, ents) -> List[Finding]:
        out = []
        # 跨段（影像描述 ↔ 诊断印象）左右侧矛盾 —— 统一规则 R2-LATERALITY。
        # 合并原 R2（NER L-/R- 规范实体，器官族级）与原 R14-SIDE（文本级中/英文器官表兜底）：
        # 二者逻辑一致（同一器官在描述段与结论段方位相反），现统一以 R2-LATERALITY 产出，
        # 并显式按器官族去重，避免同一错误重复告警（原实现靠「R14 排除 R2_COVERED」的分工
        # 规避重复，现改为单一规则 + 器官族去重，更直观且消除潜在重叠漏报）。
        # ---- 分支1：NER 带 L-/R- 前缀的规范解剖实体（器官族级）----
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
        ner_fams = set(fam_sides.keys())
        # ---- 分支2：文本级中/英文器官左右矛盾兜底（覆盖 NER 未可靠侧别化的器官）----
        secs = self._split_for_r5(text)
        f_txt, i_txt = secs["findings"], secs["impression"]
        if not f_txt or not i_txt:
            return out
        # 中文器官短名 → NER 器官族（用于与分支1去重对齐）
        zh_organ_to_fam = {
            "肺": "lung", "肾上腺": "adrenal", "卵巢": "ovary", "睾丸": "testis",
            "附件": "ovary", "股骨": "femur", "肱骨": "humerus", "甲状腺": "thyroid",
            "乳腺": "breast", "腮腺": "parotid", "膝关节": "knee", "髋关节": "hip",
            "肩关节": "shoulder", "肝": "liver", "输卵管": "tube", "精囊": "testis",
            "锁骨": "clavicle", "肋骨": "rib",
            # 2026-08-21 补全：单字高危器官此前不在文本分支覆盖，致游离文本侧别矛盾漏报
            "肾": "kidney", "胰": "pancreas", "脾": "spleen",
        }
        zh_fired = False
        for o in ORGAN_SIDE_LIST:
            fam = zh_organ_to_fam.get(o)
            if fam and fam in ner_fams:
                continue  # 该器官族已由 NER 分支覆盖，跳过避免重复告警
            fs = _organ_sides_in_text(f_txt, o)
            isd = _organ_sides_in_text(i_txt, o)
            if len(fs) == 1 and len(isd) == 1 and fs != isd:
                out.append(Finding("R2-LATERALITY", "左右侧混淆", "high",
                    f"同一器官「{o}」在影像描述为『{'左' if 'left' in fs else '右'}』侧、"
                    f"影像结论为『{'左' if 'left' in isd else '右'}』侧，方位前后矛盾",
                    "", (-1, -1)))
                zh_fired = True
                break
        # 英文报告（MIMIC-CXR / IU-Xray 风格）左右跨段矛盾：与中文分支互斥（取首个命中），
        # 同样按器官族与 NER 分支去重。
        if not zh_fired and re.search(r"[A-Za-z]", f_txt + i_txt):
            for key, aliases in EN_SIDE_ORGANS.items():
                if key in ner_fams:
                    continue
                fs = _organ_sides_en(f_txt, aliases)
                isd = _organ_sides_en(i_txt, aliases)
                if len(fs) == 1 and len(isd) == 1 and fs != isd:
                    out.append(Finding("R2-LATERALITY", "左右侧混淆", "high",
                        f"同一器官『{key}』在影像描述为『left』、影像结论为『right』，方位前后矛盾（英文报告）",
                        "", (-1, -1)))
                    break
        return out

    # R3 评分缺失
    def _r5_consistency(self, text, ents) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt, i_txt = secs["findings"], secs["impression"]
        f0 = secs.get("findings_start", 0)
        # 按"规范器官族"归组：描述段出现阳性征的器官族
        fam_mk = {}  # 器官族 -> 展示名
        for e in ents:
            if e.label != "anatomy" or e.section != "findings" or not e.canonical:
                continue
            fam = _r5_fam(e.canonical)
            if not fam:
                continue
            seg = f_txt[max(0, (e.start - f0) - 20): max(0, (e.end - f0)) + 20]
            if any(_word_effectively_present(seg, k) for k in POSITIVE_MARKERS):
                fam_mk.setdefault(fam, e.text)
        for fam, name in fam_mk.items():
            # 印象段是否就该器官族给出结论（任一含该器官族同义词的实体 + 结论词）
            fam_organs = {w for w, c in ANATOMY_SYNONYMS.items() if _r5_fam(c) == fam}
            # 补充双侧/双/两 前缀措辞（如『两肺/双肾未见异常』），避免阴性一致报告误报。
            # 由带侧别词（右肺/左肾）剥前缀得到基名（肺/肾），再拼『双/两/双侧』（2026-08-18 修复）。
            _base = {w.lstrip("左右双两") for w in fam_organs if len(w) >= 2 and w.lstrip("左右双两")}
            mentioned = any(o in i_txt for o in fam_organs) or \
                any((p + b) in i_txt for p in ("双", "两", "双侧") for b in _base)
            concluded = any(k in i_txt for k in _R5_CONCLUDED)
            if mentioned and concluded:
                continue
            out.append(Finding("R5-CONSISTENCY", "描述-结论矛盾", "medium",
                f"影像描述段器官「{name}」（族={fam}）提示阳性征，但诊断印象段未就该器官给出对应结论",
                name, (-1, -1)))
        # 文本级兜底：NER 漏标的高频器官（如「肝」）在描述段有阳性征、印象段未结论时仍报 R5。
        # 仅对 NER 路径未覆盖的器官族补报，避免与上方 NER 路径重复。
        _r5_fired = set(fam_mk.keys())
        for w, fam in R5_TEXT_ORGANS.items():
            if fam in _r5_fired:
                continue
            pos = f_txt.find(w)
            if pos == -1:
                continue
            seg = f_txt[max(0, pos - 20): pos + 20]
            if not any(_word_effectively_present(seg, k) for k in POSITIVE_MARKERS):
                continue
            # 印象段是否就同族器官给结论（复用同族词表 + _R5_CONCLUDED）
            fam_words = [ow for ow, f in R5_TEXT_ORGANS.items() if f == fam]
            mentioned = any(ow in i_txt for ow in fam_words)
            concluded = any(k in i_txt for k in _R5_CONCLUDED)
            if mentioned and concluded:
                continue
            out.append(Finding("R5-CONSISTENCY", "描述-结论矛盾", "medium",
                f"影像描述段器官「{w}」（族={fam}）提示阳性征，但诊断印象段未就该器官给出对应结论",
                w, (-1, -1)))
        return out

    @staticmethod
    def _split_for_r5(text: str) -> dict:
        """按段落标题切分描述/印象原文（单一实现 _sectionize，与 NER 同一数据源）。
        返回 dict 额外含 findings_start：findings 段在原始 text 中的起始偏移，
        供 R5 用相对偏移取实体附近子段（避免用绝对偏移索引子串导致错位）。"""
        sections = _sectionize(text)
        f0 = next((s for s in sections if s[0] == "findings"), None)
        i0 = next((s for s in sections if s[0] == "impression"), None)
        res = {"findings": text, "impression": "", "findings_start": 0}
        if f0 and i0 and i0[1] > f0[1]:
            res["findings"] = text[f0[1]:i0[1]]
            res["impression"] = text[i0[1]:]
            res["findings_start"] = f0[1]
        elif i0:
            res["impression"] = text[i0[1]:]
        return res

    # R6 登记部位不符（申请部位 vs 报告主体解剖）
    def _r14_cross(self, text, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        if not f_txt or not i_txt:
            return out
        # R14-1 描述正常 → 结论异常：已下放到 R17 逐部位精确比对（同 R11-2 说明）。
        # R14-2 良恶性定性矛盾（描述段与结论段方向相反）
        f_mal = _has_marker_unnegated(f_txt, MALIGNANT_MARKERS)
        f_ben = _has_marker_unnegated(f_txt, BENIGN_MARKERS)
        i_mal = _has_marker_unnegated(i_txt, MALIGNANT_MARKERS)
        i_ben = _has_marker_unnegated(i_txt, BENIGN_MARKERS)
        if (f_mal and i_ben) or (f_ben and i_mal):
            out.append(Finding("R14-NATURE", "前后文逻辑错误-良恶性矛盾", "high",
                "影像描述与影像结论在病灶良恶性定性上相互矛盾（一称恶性倾向、一称良性倾向）",
                "", (-1, -1)))
        # R14-3 病灶数量前后不一致（2026-08-18：cf/ci 可为 "multi"=多发≥2）
        cf = _extract_lesion_count(f_txt)
        ci = _extract_lesion_count(i_txt)

        def _count_conflict(a, b):
            if a is None or b is None or a == b:
                return False
            if a == "multi" and isinstance(b, int) and b >= 2:
                return False  # 多发与 2 枚以上不矛盾
            if b == "multi" and isinstance(a, int) and a >= 2:
                return False
            return True

        if _count_conflict(cf, ci):
            out.append(Finding("R14-COUNT", "前后文逻辑错误-数量不一致", "medium",
                f"影像描述段提及病灶约 {cf} 枚/个，影像结论段提及约 {ci} 枚/个，数量前后不一致",
                "", (-1, -1)))
        # R14-4 / R14-4b 同器官左右跨段矛盾：已统一合并至 R2-LATERALITY（见 _r2_laterality），
        # 此处不再单独产出，避免与 R2 重复告警。R14 现仅负责良恶性定性(R14-NATURE)与数量(R14-COUNT)。
        return out

    # R15 上下文逻辑错误（同一描述段内跨句一致性）
    def _r15_internal(self, text) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        if not f_txt:
            return out
        sents = _split_sentences(f_txt)
        # R15-1 段首称未见异常但段内描述阳性征
        # 段首句须为『纯正常声明』（本身不含阳性征），避免『右肺上叶见结节，余两肺未见异常』
        # 这类标准报告被误报（2026-08-05 加固）
        if sents and _claims_normal(sents[0]) and not _has_positive(sents[0]) and _has_positive(f_txt):
            out.append(Finding("R15-NORMAL", "上下文逻辑错误-段内自相矛盾", "high",
                "影像描述段开头称『未见异常/正常』，但段内又描述阳性征，前后矛盾",
                sents[0][:30], (-1, -1)))
        # R15-2（已删除 2026-08-18）：同器官描述段内前后左右矛盾——与 R2-LATERALITY
        # 同属左右矛盾检测，用户确认删除；左右矛盾统一由 R2（跨段）与 R17-PERREGION
        # （逐部位精确比对）负责，段内左右不一致场景仍会被 R17 段级兜底覆盖。
        # R15-3 同一病灶先见后无（描述段内，同句或跨句均判：任一阳性征出现位置早于
        # 任一消失/吸收类词出现位置即视为矛盾）。2026-08-21 放宽：原仅跨句（句序 absn>pres），
        # 现同句内『先见阳性词、后出现消失类词』也判定（如『左肺见结节，但结节已吸收』）。
        for lw in LESION_WORDS:
            pres_pos, abs_pos = [], []
            for m in re.finditer(re.escape(lw), f_txt):
                # 方向性窗口：阳性征动词（见/示/可见…）应在病灶词之前；
                # 消失/吸收类词（未见/消失/吸收…）应在病灶词之后（含紧邻前缀）。
                # 2026-08-21 修复：旧实现用对称 ±15 字窗口，『左肺见结节。上述结节未见』
                # 两次出现均被同时标记 pres/abs 致 min 相等、误不触发；改为方向性判定后，
                # 同句『见结节，但…已吸收』与跨句『见结节。…未见』均正确触发。
                before = f_txt[max(0, m.start() - 12): m.start()]
                after = f_txt[m.end(): m.end() + 4]
                near = f_txt[max(0, m.start() - 4): m.end() + 4]
                is_pres = (any(re.search(re.escape(v), before) for v in _PRESENCE_VERBS)
                           and not any(re.search(re.escape(v), near) for v in _ABSENCE_VERBS))
                is_abs = any(re.search(re.escape(v), after) for v in _ABSENCE_VERBS)
                if is_pres:
                    pres_pos.append(m.start())
                if is_abs:
                    abs_pos.append(m.start())
            if pres_pos and abs_pos and min(abs_pos) > min(pres_pos):
                out.append(Finding("R15-PRESENCE", "上下文逻辑错误-先见后无", "medium",
                    f"影像描述段内对同一「{lw}」先描述存在、后又称未见/消失，前后矛盾",
                    "", (-1, -1)))
                break
        return out

    # R17 逐部位精确比对（描述段 ↔ 结论段，按 器官 + 侧别 精确到同一部位）
    def _r17_cross_region(self, text, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        if not f_txt or not i_txt:
            return out
        f_spans = _region_spans_in_text(f_txt)
        i_spans = _region_spans_in_text(i_txt)
        f_assert = _region_assertions_in_section(f_txt, f_spans)
        i_assert = _region_assertions_in_section(i_txt, i_spans)
        # 整段级正常声明（无阳性征、含 NORMAL_CLAIM、且无具体部位提及）作为全局正常，
        # 覆盖该段所有提及部位；部位级正常（如『小脑正常』）不触发全局扩展。
        f_global_normal = _is_segment_global_normal(f_txt, f_spans)
        i_global_normal = _is_segment_global_normal(i_txt, i_spans)
        found = False
        for region in sorted(set(f_assert) | set(i_assert),
                             key=lambda r: (r[0], r[1] or "")):
            organ, _ = region
            d = set(f_assert.get(region, set()))
            c = set(i_assert.get(region, set()))
            # 器官级正常声明（side 为 None/bilateral/both/double，如结论『心肺未见异常』
            # 『肺未见异常』『双肺未见异常』『双乳未见异常』）视作覆盖该器官所有侧别，
            # 兼容高发结论写法——此前因点名器官/双侧被 _is_segment_global_normal 排除而漏报 R17 矛盾。
            # 2026-08-24 修复：仅对无侧别前缀的器官级正常声明做扩展，单侧（如"左小脑正常"）不扩展
            for (o, s), v in f_assert.items():
                if o == organ and s in (None, "bilateral", "both", "double") and "normal" in v:
                    # 检查是否为显式单侧声明（排除单侧正常扩展为全器官）
                    is_unilateral = any(
                        side_char in f_txt[max(0, span_start - 3):span_start]
                        for sk, ss, span_start, span_end in f_spans if sk == o
                        for side_char in ("左", "右")
                        if side_char in f_txt[max(0, span_start - 3):span_start]
                    )
                    if not is_unilateral:
                        d.add("normal")
            for (o, s), v in i_assert.items():
                if o == organ and s in (None, "bilateral", "both", "double") and "normal" in v:
                    c.add("normal")
            d_eff = d
            c_eff = c
            if f_global_normal:
                d_eff.add("normal")
            if i_global_normal:
                c_eff.add("normal")
            # 仅当某一侧声明『明确且唯一』时才报矛盾，避免同侧既正常又异常时的歧义双报
            d_only_normal = (d_eff == {"normal"})
            d_only_pos = (d_eff == {"positive"})
            c_only_normal = (c_eff == {"normal"})
            c_only_pos = (c_eff == {"positive"})
            name = _region_cn_name(*region)
            if d_only_normal and c_only_pos:
                out.append(Finding("R17-PERREGION", "前后文逻辑错误-描述正常结论异常", "high",
                    f"影像描述段「{name}」称正常/未见异常，但影像结论段对「{name}」给出阳性诊断，"
                    f"同一部位描述与结论矛盾（描述正常、结论异常）", name, (-1, -1)))
                found = True
            elif d_only_pos and c_only_normal:
                out.append(Finding("R17-PERREGION", "上下文逻辑错误-描述结论矛盾", "high",
                    f"影像描述段「{name}」提示阳性征（异常表现），但影像结论段称「{name}」正常/未见异常，"
                    f"同一部位描述与结论矛盾（描述异常、结论正常）", name, (-1, -1)))
                found = True
        # 段级兜底（无法归属到具体部位时，保持原 R11/R14 语义，避免漏检）。
        # 注意：必须用严格『段级全局正常』（_is_segment_global_normal），不可用 _claims_normal——
        # 后者会匹配『部位+正常』（如『小脑正常』）而误把局部正常当整段正常外溢。
        if not found:
            # 段级『正常/阴性』口径：全局正常声明，或整段无阳性征但含否定式阴性声明
            # （如『未见实质性病变』『无占位』，2026-08-18 修复后 _has_positive 已正确判负）。
            f_neg_normal = f_global_normal or (not _has_positive(f_txt) and _is_negative_claim(f_txt))
            i_neg_normal = i_global_normal or (not _has_positive(i_txt) and _is_negative_claim(i_txt))
            if _has_positive(f_txt) and i_neg_normal:
                out.append(Finding("R17-PERREGION", "上下文逻辑错误-描述结论矛盾", "high",
                    "影像描述提示阳性征（异常表现），但影像结论称『未见异常/正常』，二者矛盾",
                    i_txt[:30], (-1, -1)))
            elif f_neg_normal and _has_positive(i_txt):
                out.append(Finding("R17-PERREGION", "前后文逻辑错误-描述正常结论异常", "high",
                    "影像描述称『未见异常/正常』，但影像结论给出阳性诊断，结论与描述不符",
                    i_txt[:30], (-1, -1)))
        return out

    # R16 随访时限缺失（中文 NER 驱动；默认关闭，由 rules_config.enable_r16 开启）
    # 许多中文报告写『建议复查』却不给具体间隔，属模板合规瑕疵。医疗上默认关闭，
    # 避免对常规『定期复查』过度告警；需在真实样本上验证后再开启。
    @staticmethod
    def _r25_subject_key(clause: str):
        """从『较前…』之前的子句提取主体键 (最近病灶关键词, 侧别字符)。
        子句内无病灶关键词时返回 None（主体不明 → 窄模式不报）。"""
        best_i, kw_hit = -1, None
        for kw in _R25_SUBJECT_KW:
            i = clause.rfind(kw)
            if i > best_i:
                best_i, kw_hit = i, kw
        if kw_hit is None:
            return None
        side = ""
        window = clause[max(0, best_i - 4):best_i]
        for ch in ("双", "左", "右"):
            if ch in window:
                side = ch
                break
        return (kw_hit, side)

    def _r25_temporal_direction(self, text: str) -> List[Finding]:
        out = []
        if not text or "较前" not in text:
            return out
        hits = []   # (direction, key, abs_start, abs_end)
        for m in _R25_TEMPORAL_RE.finditer(text):
            s0 = max([text.rfind(d, 0, m.start()) for d in "。！？!?；;，,\n"] + [-1])
            key = self._r25_subject_key(text[s0 + 1:m.start()])
            if key is None:
                continue
            hits.append((m.group(1), key, m.start(), m.end()))
        for up_w, down_w in (("增大", "缩小"), ("增多", "减少")):
            ups = [h for h in hits if h[0] == up_w]
            downs = [h for h in hits if h[0] == down_w]
            shared = ({h[1] for h in ups} & {h[1] for h in downs})
            if not shared:
                continue
            ref = next(h for h in downs if h[1] in shared)
            snippet = text[max(0, ref[2] - 14):min(len(text), ref[3] + 10)]
            out.append(Finding(
                "R25-TEMPORAL", "时序方向矛盾", "medium",
                f"同一报告内对同一「{ref[1][0]}」的时序方向描述自相矛盾"
                f"（既称较前{up_w}又称较前{down_w}），建议核实随访对比结论",
                snippet, (ref[2], ref[3])))
        return out


