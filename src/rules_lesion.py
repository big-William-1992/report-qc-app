"""
rules_lesion.py — 病灶/一致性规则（R5/R7/R9/R12/R14/R15/R22）（P2 拆分自 engine.py）
================================================================================
- MALIGNANT/BENIGN_MARKERS、LESION_WORDS、ORGAN_SIDE_LIST(_INTERNAL)：本组词表
- LesionRulesMixin：_r5_consistency（描述-结论矛盾）、_r7_internal（描述内部矛盾）、
  _r9_conflict（自定义互斥）、_r12_sentence（句级矛盾）、_r14_cross（前后文矛盾）、
  _r15_internal（段内跨句矛盾）、_r22_lesion_size（尺寸-术语一致性）

依赖：_lexicons、_utils、anatomy_lexicon、engine_helpers、rules_region、engine_types。
"""
import re
from typing import List

from _lexicons import *  # noqa: F401,F403
from _utils import _r5_fam

from anatomy_lexicon import SIDE_CHECK_ORGANS, R2_COVERED, EN_SIDE_ORGANS
from engine_helpers import (_has_marker_unnegated, _extract_lesion_count,
                            _organ_sides_in_text, _organ_sides_en, _claims_normal,
                            _has_positive, _split_sentences, _word_effectively_present)
from rules_region import _r12_same_region
from engine_types import Finding


# 良恶性定性标记（用于前后文定性矛盾 R14-NATURE）
MALIGNANT_MARKERS = ["恶性", "癌", "转移", "浸润", "侵犯", "恶变", "ca", "mt"]
BENIGN_MARKERS = ["良性", "炎性", "炎症", "符合良性", "考虑良性"]

# 描述段内「先见后无」检测的阳性/阴性动词
_PRESENCE_VERBS = ["见", "示", "可见", "探及", "发现", "查见", "考虑", "提示", "显示"]
_ABSENCE_VERBS = ["未见", "消失", "吸收", "已吸收", "消退"]
LESION_WORDS = ["结节", "占位", "肿块", "病灶", "囊肿", "结石", "骨折", "积液",
                "阴影", "斑片", "异常信号"]

# 需做左右侧一致性比对的成对解剖结构
# —— 跨段比对(R14)用此表：文本级兜底，覆盖 R2（NER 带 L-/R- 前缀规范节点）未能可靠覆盖的器官。
#    来源：src/anatomy_lexicon.py（RadLex 器官族 + 侧别建模）动态派生；保留原有的 R2 跨段覆盖分离，
#    排除 R2 已覆盖的「肾/股骨头」避免重复告警。新增 输卵管/精囊/锁骨/肋骨 等成对器官扩大覆盖。
ORGAN_SIDE_LIST = [o for o in SIDE_CHECK_ORGANS if o not in R2_COVERED]
# —— 段内跨句比对(R15)用此表：在 ORGAN_SIDE_LIST 基础上补 R2 已覆盖的「肾/股骨头」
#    （R15 仅查描述段内部，不与 R2 跨段检查重叠，故可并存）
ORGAN_SIDE_LIST_INTERNAL = ORGAN_SIDE_LIST + list(R2_COVERED)



class LesionRulesMixin:
    # R5 描述-结论矛盾（按器官族核对：描述段某器官族出现阳性征，印象段未就该器官族给出对应结论）
    def _r5_consistency(self, text, ents) -> List[Finding]:
        out = []
        secs = self._secs(text)
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
            seg = f_txt[max(0, (e.start - f0) - 20): (e.end - f0) + 20]
            if any(k in seg for k in POSITIVE_MARKERS):
                fam_mk.setdefault(fam, e.text)
        for fam, name in fam_mk.items():
            # 印象段是否就该器官族给出结论（任一含该器官族同义词的实体 + 结论词）
            fam_organs = {w for w, c in ANATOMY_SYNONYMS.items() if _r5_fam(c) == fam}
            mentioned = any(o in i_txt for o in fam_organs)
            concluded = any(k in i_txt for k in
                            ["占位", "结节", "癌", "瘤", "恶性", "病变", "异常",
                             "增大", "扩张", "囊肿", "结石", "水肿", "出血",
                             # 阴性/概括性结论：印象段已对该器官族给出结论（未见异常/正常/良性等），
                             # 视为"已结论"，避免『描述有结节 + 印象称未见异常』被 R5 误报
                             # （此类矛盾交由 R17 逐部位精确比对处理）
                             "未见异常", "未见明显异常", "正常", "未见占位", "良性",
                             "未见明确异常", "未见确切异常"])
            if mentioned and concluded:
                continue
            out.append(Finding("R5-CONSISTENCY", "描述-结论矛盾", "medium",
                f"影像描述段器官「{name}」（族={fam}）提示阳性征，但诊断印象段未就该器官给出对应结论",
                name, (-1, -1)))
        return out


    # R7 描述内部矛盾（同一描述段内出现男女专属器官混用 —— 真实自相矛盾）
    def _r7_internal(self, text, ents) -> List[Finding]:
        out = []
        findings_ents = [e for e in ents if e.section == "findings"]
        g = set()
        for e in findings_ents:
            if e.label == "gender_organ":
                g.add(self.kg.expected_gender_for_organ(e.text))
        if len(g) > 1:
            out.append(Finding("R7-INTERNAL", "描述内部矛盾", "medium",
                f"影像描述段内出现男女专属器官混用{g}（同一患者不可能同时存在）", "", (-1, -1)))
        return out

    # R8 同音/近音错别字（多由语音录入产生：词典由 rules_config.json 维护，可在 GUI 增删）

    # R9 用户自定义互斥冲突（由 rules_config.json 维护：词A 与 词B 不应在同一范围内共存）
    def _r9_conflict(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        conflicts = self.rules_config.get("conflicts", []) or []
        for rule in conflicts:
            a = (rule.get("a") or "").strip()
            b = (rule.get("b") or "").strip()
            if not a or not b or a == b:
                continue   # 自反矛盾对(A==B)或空值无效，跳过防误报
            scope = rule.get("scope", "正文")
            sev = rule.get("severity", "medium")
            # 范围扩展：
            #   正文    → 整篇报告
            #   描述段  → 仅 检查所见/影像描述 段
            #   结论段  → 仅 诊断印象/影像诊断/结论 段
            #   同一句  → 同一句内 A 与 B 同时出现才算冲突（"同一行前后错误"）
            #   描述vs结论 → A 出现在描述段 且 B 出现在结论段（跨段上下文错误）
            secs = self._secs(text)
            f_txt, i_txt = secs["findings"], secs["impression"]
            note = rule.get("note", "")
            hit = False
            target = text   # 默认正文；各分支按需覆盖，供豁免检查使用
            if scope == "描述段":
                target = f_txt
                hit = bool(target) and a in target and b in target
            elif scope == "结论段":
                target = i_txt
                hit = bool(target) and a in target and b in target
            elif scope == "同一句":
                # 同一句内 A、B 同现（按句切分，逐句判断）
                for sent in _split_sentences(f_txt + "\n" + i_txt):
                    if a in sent and b in sent:
                        target = sent
                        hit = True
                        break
            elif scope == "描述vs结论":
                # 跨段：描述含 A 且 结论含 B（或反向）——"描述/诊断错误"
                hit = (a in f_txt and b in i_txt) or (b in f_txt and a in i_txt)
            else:   # 正文（默认）
                hit = a in text and b in text
            if hit:
                # 豁免1：否定前缀——若 a/b 中任一词被否定修饰（如『未见占位』中『占位』被『未见』
                # 否定），则该词不算真正出现，互斥不成立（正常阴性描述，不报）。
                if not _word_effectively_present(target, a) or not _word_effectively_present(target, b):
                    continue
                # 豁免2：鉴别/软化语境——『良性 vs 恶性』『未见 vs 占位』等若处于鉴别诊断表达
                # （良恶性待定/不除外恶性/需除外…/鉴别…），同现属正常鉴别，不报。
                if any(w in target for w in ("待定", "不除外", "鉴别", "除外")):
                    continue
                msg = (f"检出互斥冲突：『{a}』与『{b}』在[{scope}]内同时出现，应互斥"
                       + (f"（{note}）" if note else ""))
                out.append(Finding("R9-CONFLICT", "自定义互斥冲突", sev, msg, a, (-1, -1)))
        return out

    # R10 结构化报告模板合规（必填段 + 随访建议；要点由 rules_config.json 的 template 维护）

    # R12 同一句话逻辑错误（句级自相矛盾）
    def _r12_sentence(self, text, ents) -> List[Finding]:
        out = []
        secs = self._secs(text)
        f_txt = secs["findings"]
        if not f_txt:
            return out
        for sent in _split_sentences(f_txt):
            # 1) 同一句内男女专属器官混用（如『子宫…前列腺…』）
            s_genders = {g for organ, g in GENDER_ORGANS.items() if organ in sent}
            if len(s_genders) > 1:
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "high",
                    f"同一句话内同时出现男女专属器官（自相矛盾）：『{sent[:30]}…』",
                    sent[:30], (-1, -1)))
                continue
            # 2) 同一句内既称某部位正常又描述该部位阳性征（真正的自相矛盾）。
            #    仅当『正常』与阳性征指向同一部位时才判，避免『右肺见结节，左肺正常』
            #    这类不同部位的对称描述被误报（2026-08-05 已加固『余两肺未见异常』）。
            if _REGION_NORMAL_RE.search(sent) and _has_positive(sent):
                if not _r12_same_region(sent):
                    continue
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "high",
                    f"同一句话内既称『未见异常』又描述阳性征（自相矛盾）：『{sent[:30]}…』",
                    sent[:30], (-1, -1)))
        return out

    # R14 前后文逻辑错误（描述段 ↔ 结论段 一致性）

    # R14 前后文逻辑错误（描述段 ↔ 结论段 一致性）
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
        # R14-3 病灶数量前后不一致
        cf = _extract_lesion_count(f_txt)
        ci = _extract_lesion_count(i_txt)
        if cf is not None and ci is not None and cf != ci:
            out.append(Finding("R14-COUNT", "前后文逻辑错误-数量不一致", "medium",
                f"影像描述段提及病灶约 {cf} 枚/个，影像结论段提及约 {ci} 枚/个，数量前后不一致",
                "", (-1, -1)))
        # R14-4 同器官左右跨段矛盾（超越 R2 的有限规范器官族，覆盖文本级左右写法）
        for o in ORGAN_SIDE_LIST:
            fs = _organ_sides_in_text(f_txt, o)
            isd = _organ_sides_in_text(i_txt, o)
            if len(fs) == 1 and len(isd) == 1 and fs != isd:
                out.append(Finding("R14-SIDE", "前后文逻辑错误-左右矛盾", "high",
                    f"同一器官「{o}」在影像描述为『{'左' if 'left' in fs else '右'}』侧、"
                    f"影像结论为『{'左' if 'left' in isd else '右'}』侧，方位前后矛盾",
                    "", (-1, -1)))
                break
        # R14-4b 英文报告（MIMIC-CXR / IU-Xray 风格）左右跨段矛盾
        if not any(f.rule_id == "R14-SIDE" for f in out) and re.search(r"[A-Za-z]", f_txt + i_txt):
            for key, aliases in EN_SIDE_ORGANS.items():
                fs = _organ_sides_en(f_txt, aliases)
                isd = _organ_sides_en(i_txt, aliases)
                if len(fs) == 1 and len(isd) == 1 and fs != isd:
                    out.append(Finding("R14-SIDE", "前后文逻辑错误-左右矛盾", "high",
                        f"同一器官『{key}』在影像描述为『left』、影像结论为『right』，方位前后矛盾（英文报告）",
                        "", (-1, -1)))
                    break
        return out

    # R15 上下文逻辑错误（同一描述段内跨句一致性）

    # R15 上下文逻辑错误（同一描述段内跨句一致性）
    def _r15_internal(self, text) -> List[Finding]:
        out = []
        secs = self._secs(text)
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
        # R15-2 同器官在描述段内前后左右矛盾
        for o in ORGAN_SIDE_LIST_INTERNAL:
            per = [(sent, _organ_sides_in_text(sent, o)) for sent in sents
                   if len(_organ_sides_in_text(sent, o)) == 1]
            flagged = False
            for i in range(len(per)):
                for j in range(i + 1, len(per)):
                    if per[i][1] != per[j][1]:
                        out.append(Finding("R15-SIDE", "上下文逻辑错误-左右自相矛盾", "high",
                            f"影像描述段内同一器官「{o}」前后方位不一致："
                            f"『{per[i][0][:18]}…』与『{per[j][0][:18]}…』",
                            "", (-1, -1)))
                        flagged = True
                        break
                if flagged:
                    break
        # R15-3 同一病灶先见后无（描述段内跨句）
        for lw in LESION_WORDS:
            pres = absn = None
            for idx, sent in enumerate(sents):
                has_pres = (lw in sent
                            and any(re.search(re.escape(v), sent) for v in _PRESENCE_VERBS)
                            and not any(re.search(re.escape(v), sent) for v in _ABSENCE_VERBS))
                has_abs = lw in sent and any(re.search(re.escape(v), sent) for v in _ABSENCE_VERBS)
                if has_pres:
                    pres = idx
                if has_abs:
                    absn = idx
            if pres is not None and absn is not None and absn > pres:
                out.append(Finding("R15-PRESENCE", "上下文逻辑错误-先见后无", "medium",
                    f"影像描述段内对同一「{lw}」先描述存在、后又称未见/消失，前后矛盾",
                    "", (-1, -1)))
                break
        return out

    # R17 逐部位精确比对（描述段 ↔ 结论段，按 器官 + 侧别 精确到同一部位）

    # R22 病灶尺寸-术语一致性：称『结节』但测量值 >3cm（应称肿块），或
    # 称『肿块』但测量值 <1cm（应称结节）。临床上结节≤3cm、肿块>3cm 是
    # 放射科基本口径，术语与测量值明显不匹配提示描述或测量有误。
    def _r22_lesion_size(self, text, secs) -> List[Finding]:
        out = []
        combined = secs["findings"] + "\n" + secs["impression"]
        if not combined:
            return out
        # 提取所有带尺寸的结节/肿块表述：术语 + 紧随的测量值（cm）
        # 匹配如：『结节，直径约 3.5cm』『结节大小约 4.2×3.1cm』『肿块，大小约 0.8cm』
        pat = re.compile(
            r"(结节|肿块|占位|肿物)[，,、:：\s]*"
            r"(?:大小|直径|径线|体积|最长径)?\s*约?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:×|x|\*)\s*(\d+(?:\.\d+)?)?\s*(cm|毫米|mm)"
            r"|(结节|肿块|占位|肿物)[，,、:：\s]*"
            r"(?:大小|直径|径线|体积|最长径)?\s*约?\s*"
            r"(\d+(?:\.\d+)?)\s*(cm|毫米|mm)",
            re.I)
        for m in pat.finditer(combined):
            # 两分支：分支1(双径线 结节…4.2×3.1cm) 组1/2/3/4；
            #        分支2(单径线 结节…3.5cm) 组5/6/7
            term = m.group(1) or m.group(5)
            if not term:
                continue
            if m.group(2) is not None:          # 双径线分支
                v1 = float(m.group(2))
                v2 = float(m.group(3)) if m.group(3) else None
                unit = (m.group(4) or "").lower()
            else:                                # 单径线分支
                v1 = float(m.group(6))
                v2 = None
                unit = (m.group(7) or "").lower()
            cm = v1 if unit == "cm" else v1 / 10.0
            # 双径线取最大径
            if v2 is not None:
                v2cm = v2 if unit == "cm" else v2 / 10.0
                cm = max(cm, v2cm)
            if term == "结节" and cm > 3.0:
                out.append(Finding("R22-SIZE", "病灶尺寸-术语矛盾", "medium",
                    f"称「结节」但测量最大径约 {cm:.1f}cm（>3cm），按放射科口径应称「肿块」，"
                    f"请核对描述或测量是否一致（结节≤3cm）",
                    m.group(0), (-1, -1)))
            elif term == "肿块" and cm < 1.0:
                out.append(Finding("R22-SIZE", "病灶尺寸-术语矛盾", "medium",
                    f"称「肿块」但测量最大径约 {cm:.1f}cm（<1cm），按放射科口径应称「结节」，"
                    f"请核对描述或测量是否一致",
                    m.group(0), (-1, -1)))
            # 数字/单位错字（2026-08-16 增强）：换算后最大径明显超出人体合理范围
            # （如结节写 30cm，多为 mm 误写为 cm），提示单位可能误写。
            if cm > 10.0:
                out.append(Finding("R22-UNIT", "尺寸单位疑似误写", "low",
                    f"测量最大径约 {cm:.1f}cm，超出常见病灶量级，疑为长度单位误写"
                    f"（mm 误写为 cm）或数值录入有误，请核对",
                    m.group(0), (-1, -1)))
        return out

    # R20 模板完整性校验：按检查类型（从登记部位/检查方式推断）校验必查要素缺项。
    # 与 R18 互补：R18 查『区域是否有任意器官』，R20 查『该检查类型必查要素是否齐』。
    # 防误报策略：报告头/描述段整体"未见异常"声明不豁免（与 R18 不同）——
    # 胸部 CT 即使全部正常也应点名肺纹理/纵隔/胸膜等，故缺项仍提示（medium 级）。
