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

class TemplateRulesMixin:
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
    def _r18_region_coverage(self, text, meta) -> List[Finding]:
        out = []
        applied = meta.get("applied_site", "")
        if not applied:
            return out
        regions = _extract_regions(applied)
        if not regions:
            return out
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        i_txt = secs["impression"]
        combined = f_txt + "\n" + i_txt
        # 检查类型推断（原 R20 逻辑下沉）：同区域下优先以更具体的「必查要素」口径报告漏写
        matched_type = self._infer_exam_type(text, meta)
        req = REPORT_TYPE_REQUIREMENTS.get(matched_type) if matched_type else None
        # R18 原防误报：描述段整体为正常声明（含『未见异常』且不带阳性征）→ 视为各区域已声明
        # 未见异常，不判区域器官漏写（避免『上腹部CT未见异常』因未点名器官被误报）。
        region_normal = _claims_normal(f_txt) and not _has_positive(f_txt)
        if not region_normal:
            for region in regions:
                orgs = REGION_TO_ORGANS.get(region, [])
                if not orgs:
                    continue
                # 描述段或结论段任一覆盖该区域器官即视为已描述（两段互补避免漏报；两段都无才判漏写）
                hit = any(org in f_txt for org in orgs) or any(org in i_txt for org in orgs)
                # 区域器官级漏写：仅当无法推断检查类型时回退（原 R18 口径），
                # 能推断类型则统一由下方「必查要素」口径输出，避免同一漏写双报。
                if not hit and req is None:
                    sample = "、".join(orgs[:4])
                    out.append(Finding("R18-COVERAGE", "检查部位器官漏写", "medium",
                        f"检查部位含「{region}」，但影像描述与影像诊断中均未描述{region}"
                        f"相关器官（如{sample}等），疑似漏写或检查部位登记有误",
                        region, (-1, -1)))
        # 检查类型必查要素漏写（原 R20）：独立于区域器官命中，能推断类型即校验要素；
        # 不豁免正常声明（正常报告也应点名结构，见 R20 原设计），故不套用 region_normal 守卫。
        if req is not None:
            elems = req["要素"]
            missing = [e for e in elems if e not in combined]
            if missing:
                # 必查要素告警口径（2026-08-20 修正）：
                # - 普查型检查（胸部CT/头颅CT/腰椎/腹部CT/颈椎/膝关节等）要素清单为强制项，
                #   只要任一结构要素缺失且报告未整体声明正常，即报漏写（沿用 R20 原设计，
                #   确保『只报一个结节却漏评肺纹理/纵隔/胸膜』等不完整报告被抓出）。
                # - 聚焦型检查（盆腔/乳腺）只要描述了该类型主器官（如盆腔报告提到子宫/卵巢/
                #   前列腺），即视为报告已覆盖，不再因未逐字点名「膀胱/直肠/盆壁」等结构
                #   而误报聚焦性合法报告（子宫肌瘤/卵巢囊肿等）。
                _relaxed = FOCUSED_TYPE_PRIMARY.get(matched_type)
                _covered = bool(_relaxed) and any(o in combined for o in _relaxed)
                if not _covered and not _claims_normal(combined):
                    sample = "、".join(missing[:6])
                    out.append(Finding("R18-COVERAGE", "报告必查要素漏写", "medium",
                        f"「{matched_type}」报告缺少必查要素：{sample}。{req['提示']}",
                        matched_type, (-1, -1)))
        return out

    # R22 病灶尺寸-术语一致性：称『结节』但测量值 >3cm（应称肿块），或
    # 称『肿块』但测量值 <1cm（应称结节）。临床上结节≤3cm、肿块>3cm 是
    # 放射科基本口径，术语与测量值明显不匹配提示描述或测量有误。
    def _r16_followup_timeframe(self, text) -> List[Finding]:
        out = []
        if not _ZH_NLP_OK:
            return out
        r = _zh_extract_followup(text)
        if r["has_followup"] and r["timeframe_months"] is None:
            out.append(Finding("R16-FOLLOWUP", "随访时限缺失", "low",
                "报告给出随访/复查建议但未明确时限（如『3个月后』），建议补充具体随访间隔",
                "", (-1, -1)))
        return out

    # R24 建议强度矛盾（定性良性 vs 强处置建议，2026-08-23 新增）
    def _r24_advice_conflict(self, text: str) -> List[Finding]:
        out = []
        if not text:
            return out
        # 条件①：明确良性定性词。印象段优先 → 描述段次之；均无段落结构时
        # _split_for_r5 的 findings 即为全文，天然退回全文扫描。
        secs = self._split_for_r5(text)
        i_txt = secs["impression"]
        f_txt = secs["findings"]
        benign_word = ""
        for seg in filter(None, (i_txt, f_txt)):
            m = R24_BENIGN_CONFIRM_RE.search(seg or "")
            while m and R24_BENIGN_NEG_PRE_RE.search(seg[max(0, m.start() - 4):m.start()]):
                m = R24_BENIGN_CONFIRM_RE.search(seg, m.start() + 1)
            if m:
                benign_word = m.group()
                break
        if not benign_word:
            return out
        # 条件②：建议/印象部分的强处置词，须同句内有建议类引导词先行锚定；
        # 无段落结构时退回全文（此时病史陈述误触风险略升，故追加术后/已行豁免）。
        adv_seg = i_txt or text
        base = len(text) - len(adv_seg)
        for m in re.finditer("|".join(map(re.escape, R24_STRONG_INTERVENTION)), adv_seg):
            s0 = max([adv_seg.rfind(d, 0, m.start()) for d in "。！？!?；;\n"] + [-1])
            pre = adv_seg[s0 + 1:m.start()]
            post = adv_seg[m.end():m.end() + 2]
            if "术后" in post or "术后" in adv_seg[max(0, m.start() - 2):m.end()]:
                continue  # 切除术后/术后化疗等病史陈述
            if re.search(r"(?:已行|曾行|既往)[^。；\n]{0,4}$", pre):
                continue  # 已行切除/曾行化疗等手术史回顾
            if not R24_ADVICE_LEAD_RE.search(pre):
                continue  # 处置词未被建议类引导词锚定（含『穿刺细胞学』弱表述）
            snippet = adv_seg[max(0, m.start() - 15):min(len(adv_seg), m.end() + 15)]
            out.append(Finding(
                "R24-ADVICE", "建议强度矛盾", "high",
                f"报告将病灶定性为良性（{benign_word}），但建议部分又出现强处置表述「{m.group()}」，"
                f"定性与处置强度矛盾，建议核实",
                snippet, (base + m.start(), base + m.end())))
            break  # 一份报告只报一条，避免同类告警刷屏
        return out
