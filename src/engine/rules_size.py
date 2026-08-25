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

class SizeRulesMixin:
    def _r4_unit(self, ents) -> List[Finding]:
        out = []
        for e in [x for x in ents if x.label == "bad_unit"]:
            out.append(Finding("R4-UNIT", "计量单位错误", "low",
                f"检出非常规单位表示「{e.text}」（单位={e.canonical}）", e.text, (e.start, e.end)))
        return out

    # R5 描述-结论矛盾（按器官族核对：描述段某器官族出现阳性征，印象段未就该器官族给出对应结论）
    def _r22_lesion_size(self, text, secs) -> List[Finding]:
        out = []
        combined = secs["findings"] + "\n" + secs["impression"]
        if not combined:
            return out
        # 提取所有带尺寸的结节/肿块表述：术语 + 紧随的测量值（cm）
        # 匹配如：『结节，直径约 3.5cm』『结节大小约 4.2×3.1cm』『肿块，大小约 0.8cm』
        pat = re.compile(
            # 2026-08-18：术语后容忍 0-14 个非数字字符（『结节较前增大，现约3.5cm』
            # 此前因中间夹修饰短语而漏检）；匹配后校验间隔串不含否定词。
            r"(结节|肿块|占位|肿物)[^0-9]{0,14}?"
            r"(?:大小|直径|径线|体积|最长径)?\s*约?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:×|x|\*)\s*(\d+(?:\.\d+)?)?\s*(cm|毫米|mm)"
            r"|(结节|肿块|占位|肿物)[^0-9]{0,14}?"
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
        # 可配置：要求阳性病灶必须报告尺寸（默认关，避免常规报告过度告警）。
        # 仅对描述段明确阳性（非否定、附近无测量值）却无尺寸的病灶提示（2026-08-21）。
        if self.rules_config.get("require_lesion_size", False):
            _fbody = secs.get("findings", "")
            _size_re = re.compile(
                r"\d+(?:\.\d+)?\s*(?:×|x|\*)\s*\d*(?:\.\d+)?\s*(?:cm|毫米|mm)"
                r"|\d+(?:\.\d+)?\s*(?:cm|毫米|mm)")
            for term in ("结节", "肿块", "占位", "肿物"):
                _hit = False
                for m in re.finditer(term, _fbody):
                    pre = _fbody[max(0, m.start() - 12): m.start()]
                    if any(re.search(re.escape(v), pre) for v in _NEG_PREFIXES):
                        continue
                    post = _fbody[m.end(): m.end() + 25]
                    if _size_re.search(post):
                        continue
                    out.append(Finding("R22-SIZE-MISSING", "病灶缺尺寸", "low",
                        f"描述『{term}』但未给出测量尺寸，建议补充长径/大小等信息",
                        term, (-1, -1)))
                    _hit = True
                    break
                if _hit:
                    break
        # R22 定性-尺寸矛盾（2026-08-22，术语召回）：病灶定性形容词与实测尺寸自相矛盾。
        # 如『巨大/较大/明显 结节/肿块』却仅测 <1cm，或『微小/小 结节』却测 >3cm——
        # 这种内部不一致是放射术语质控的高频漏报点，且判定明确、误报极低。
        _QUAL_LARGE = ("巨大", "较大", "明显", "大")
        _QUAL_SMALL = ("微小", "小", "细小")
        _qual_pat = re.compile(
            r"(巨大|较大|明显|大|微小|小|细小)\s*"
            r"(结节|肿块|占位|肿物)[^0-9]{0,14}?"
            r"(?:大小|直径|径线|体积|最长径)?\s*约?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:×|x|\*)\s*(\d+(?:\.\d+)?)?\s*(cm|毫米|mm)"
            r"|(巨大|较大|明显|大|微小|小|细小)\s*"
            r"(结节|肿块|占位|肿物)[^0-9]{0,14}?"
            r"(?:大小|直径|径线|体积|最长径)?\s*约?\s*"
            r"(\d+(?:\.\d+)?)\s*(cm|毫米|mm)",
            re.I)
        for m in _qual_pat.finditer(combined):
            if m.group(2) is not None:           # 双径线分支
                qual, term = m.group(1), m.group(2)
                v1 = float(m.group(3))
                v2 = float(m.group(4)) if m.group(4) else None
                unit = (m.group(5) or "").lower()
            else:                                 # 单径线分支
                qual, term = m.group(6), m.group(7)
                v1 = float(m.group(8))
                v2 = None
                unit = (m.group(9) or "").lower()
            cm = v1 if unit == "cm" else v1 / 10.0
            if v2 is not None:
                v2cm = v2 if unit == "cm" else v2 / 10.0
                cm = max(cm, v2cm)
            if qual in _QUAL_LARGE and cm < 1.0:
                out.append(Finding("R22-QUAL", "定性-尺寸矛盾", "medium",
                    f"称「{qual}{term}」但测量最大径仅约 {cm:.1f}cm（<1cm），"
                    f"定性描述与测量尺寸矛盾，请核对描述或测量",
                    m.group(0), (-1, -1)))
            elif qual in _QUAL_SMALL and cm > 3.0:
                out.append(Finding("R22-QUAL", "定性-尺寸矛盾", "medium",
                    f"称「{qual}{term}」但测量最大径达约 {cm:.1f}cm（>3cm），"
                    f"定性描述与测量尺寸矛盾，请核对描述或测量",
                    m.group(0), (-1, -1)))
        return out

    # R20 模板完整性校验已并入 R18-COVERAGE（见 _r18_region_coverage：原 R20 的「必查要素」
    # 下沉为该方法的类型级分支，原 R18 区域器官口径仅在无法推断检查类型时回退）；
    # R20-TEMPLATE 不再单独产出。

    # R21 性别-部位联动（检查类型级）：男性检查乳腺/子宫/卵巢，女性检查前列腺/睾丸。
    # 与 R1(正文出现异性别器官) 互补：R1 抓正文描述，R21 抓『检查部位登记』层面——
    # 登记部位/检查方式与性别不匹配（如男性做钼靶、女性做前列腺 MR）。
