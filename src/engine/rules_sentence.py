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

class SentenceRulesMixin:
    def _r9_conflict(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        conflicts = self.rules_config.get("conflicts", []) or []
        # 2026-08-24 性能优化：将 _split_for_r5 提到循环外，避免每条规则重复拆分
        secs = self._split_for_r5(text)
        f_txt, i_txt = secs["findings"], secs["impression"]
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
    def _r10_template(self, text) -> List[Finding]:
        out = []
        cfg = (self.rules_config.get("template") or dict(DEFAULT_TEMPLATE))
        required = cfg.get("required_sections", ["findings", "impression"])
        sev = cfg.get("severity", "low")
        has_findings = bool(re.search(r"检查所见|影像描述|影像所见|表现", text))
        # 结论段判定限定『行首标题 + 冒号』（2026-08-18 修复）：此前子串"结论"会误匹配
        # 『临床初步结论』『结论尚待』等正文词，导致真缺结论段时漏检模板缺失。
        has_impression = bool(re.search(r"(?m)^\s*(?:诊断印象|印象|诊断意见|影像结论|影像诊断|结论)\s*[:：]", text))
        if "findings" in required and not has_findings:
            out.append(Finding("R10-TEMPLATE", "模板缺失-描述段", sev,
                "报告缺少『检查所见/影像描述/影像所见』段，不符合结构化报告规范", "", (-1, -1)))
        if "impression" in required and not has_impression:
            out.append(Finding("R10-TEMPLATE", "模板缺失-结论段", sev,
                "报告缺少『诊断印象/结论』段，不符合结构化报告规范", "", (-1, -1)))
        if cfg.get("require_followup") and not re.search(r"随访|建议|复查|随诊", text):
            out.append(Finding("R10-TEMPLATE", "模板缺失-随访建议", sev,
                "报告未给出随访/复查建议，建议补充", "", (-1, -1)))
        return out

    # R11 上下文逻辑错误（信息框 vs 描述框/结论框 跨框比对）
    # （R11 信息框-正文矛盾已全部并入 R1-GENDER / R2-LATERALITY / R17-PERREGION，
    #   原 _r11_context 恒返回 []，2026-08-18 清理删除。）

    # R12 同一句话逻辑错误（句级自相矛盾）
    def _r12_sentence(self, text, ents) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        if not f_txt:
            return out
        within_found = False
        for sent in _split_sentences(f_txt):
            # 1) 同一句内男女专属器官混用（如『子宫…前列腺…』）
            #    矛盾成立的判据：句内出现一个『被真实断言（非否定）』的男性专属器官，
            #    且同时提及任一女性专属器官（提及即可，因『子宫未见异常』仍意味着患者具子宫）。
            #    『前列腺区未见异常』等被否定修饰的男性器官不计入，避免女性盆腔报告的
            #    合法否定表述被升为 high 级矛盾（与 R9/R17 否定口径统一，2026-08-20）。
            s_has_male = any(organ in sent and GENDER_ORGANS.get(organ) == "male"
                             and _organ_asserted(sent, organ)
                             for organ in GENDER_ORGANS)
            # 2026-08-24 修复：女性器官仅需提及即可（如"子宫未见异常"仍意味着患者具子宫），
            # 不要求 _organ_asserted（否定的女性器官提及仍表明患者性别）
            s_has_female = any(organ in sent and GENDER_ORGANS.get(organ) == "female"
                               for organ in GENDER_ORGANS)
            if s_has_male and s_has_female:
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "high",
                    f"同一句话内同时出现男女专属器官（自相矛盾）：『{sent[:30]}…』",
                    sent[:30], (-1, -1)))
                within_found = True
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
        # 3) 跨句（整段）男女专属器官混用：原 R7-INTERNAL 的段级逻辑，并入 R12 以避免双规则重复。
        #    若句内已捕获男女器官混用（within_found），不再重复报整段级；仅当矛盾分散在不同句子时补充。
        if not within_found:
            sec_genders = {g for organ, g in GENDER_ORGANS.items()
                           if organ in f_txt and _organ_asserted(f_txt, organ)}
            # 并入 NER 识别的 gender_organ 实体（覆盖文本级子串未命中、但 NER 已侧别的器官）；
            # 同样豁免被否定修饰的器官（如『前列腺区未见异常』，2026-08-20）。
            for e in ents:
                if e.label == "gender_organ" and e.section == "findings":
                    g = self.kg.expected_gender_for_organ(e.text)
                    if g and _organ_asserted(f_txt, e.text):
                        sec_genders.add(g)
            if len(sec_genders) > 1:
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "medium",
                    "影像描述段内出现男女专属器官混用（同一患者不可能同时存在），自相矛盾",
                    "", (-1, -1)))
        return out

    # R14 前后文逻辑错误（描述段 ↔ 结论段 一致性）
