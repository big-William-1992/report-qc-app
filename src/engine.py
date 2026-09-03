"""
report_qc_app/src/engine.py
第一代医学影像报告质控引擎（纯标准库，零依赖）
管线：报告文本 → 分段 → NER → 知识图谱约束 → 规则引擎 → 错误 + 多维评分

P2 重构（2026-09-02）：巨型单文件已按业务域拆分，本文件为兼容门面：
- 数据模型   engine_types.py    Entity / Finding
- 共享 helper engine_helpers.py  _KG / 性别 / 侧别 / 声明判定 / 计数
- 配置层     engine_config.py   TYPO_MAP_DEFAULT / rules_config 读写 / learn_typo
- NER        engine_ner.py      ChineseRadiologyNER
- 元信息抽取 engine_meta.py     extract_meta / extract_meta_full / format_patient_ident
- 规则分组   rules_meta.py      R1/R2/R3/R4/R6/R11/R21
             rules_typo.py      R8/R19 / scan_reports_for_typos
             rules_template.py  R10/R16/R20
             rules_region.py    R17/R18
             rules_lesion.py    R5/R7/R9/R12/R14/R15/R22
RuleEngine 由各规则 Mixin 组合；对外 API 与拆分前完全一致
（`from engine import RuleEngine/Entity/Finding/...` 均可）。
"""
import re
from typing import List, Optional, Dict

# 词表常量模块（渐进式抽取）：engine.py 通过 `from _lexicons import *` 引入，
# 并显式 re-export 保持 `from engine import XXX` 对外兼容（2026-08-16）。
from _lexicons import *  # noqa: F401,F403,E402
from _utils import *     # noqa: F401,F403,E402

# 解剖部位知识图谱（RadLex 器官族 + PadChest 104 部位→UMLS）：驱动左右侧比对表
from anatomy_lexicon import SIDE_CHECK_ORGANS, R2_COVERED, EN_SIDE_ORGANS  # noqa: F401

# 数据模型 / 共享 helper / NER / 元信息抽取 / 配置层
from engine_types import Entity, Finding  # noqa: F401
from engine_helpers import (_KG, _zh, _norm_gender, _parse_gender_from_text,
                         _norm_laterality, _detect_side_in_text, _project_side,
                         _claims_normal, _has_positive, _word_effectively_present,
                         _split_sentences, _cn_to_int, _extract_lesion_count,
                         _has_marker_unnegated, _organ_sides_in_text, _organ_sides_en)
from engine_ner import ChineseRadiologyNER
from engine_meta import (extract_meta, extract_meta_full, format_patient_ident,
                         _extract_gender_cn, _extract_age, _extract_modality,
                         _extract_site, _extract_laterality, _extract_patient,
                         _extract_exam_no, _lab_re, _site_from_text,
                         _site_from_fulltext, _meta_header_region, _ocr_fix_num)
from engine_config import (TYPO_MAP_DEFAULT, DEFAULT_TEMPLATE, RULES_CONFIG_PATH,  # noqa: F401
                           default_rules_config, load_rules_config,
                           save_rules_config, learn_typo)

# 规则分组 Mixin 与词表（re-export 保持 from engine import XXX 兼容）
from rules_meta import MetaRulesMixin
from rules_typo import TypoRulesMixin, scan_reports_for_typos  # noqa: F401
from rules_template import TemplateRulesMixin, REPORT_TYPE_REQUIREMENTS  # noqa: F401
from rules_region import (RegionRulesMixin, REGION_LEXICON, REGION_TO_ORGANS,  # noqa: F401
                          _extract_regions)
from rules_lesion import (LesionRulesMixin, ORGAN_SIDE_LIST,  # noqa: F401
                         ORGAN_SIDE_LIST_INTERNAL, MALIGNANT_MARKERS, BENIGN_MARKERS,
                         LESION_WORDS)


# ----------------------------- 规则引擎 -----------------------------
class RuleEngine(MetaRulesMixin, TypoRulesMixin, TemplateRulesMixin,
                 RegionRulesMixin, LesionRulesMixin):
    def __init__(self):
        self.kg = _KG()
        self.rules_config = load_rules_config()

    def reload_rules(self):
        """重新从 assets/rules_config.json 读取规则（用户维护后即时生效）。"""
        self.rules_config = load_rules_config()

    def run(self, text: str, meta: dict) -> List[Finding]:
        # 实际启用规则：R1–R12、R14、R15、R17；R16（随访时限缺失）为可选规则，
        # 由 rules_config.enable_r16 控制（默认关闭，避免对常规『定期复查』过度告警）。
        # R13 为预留编号（当前无对应规则），故不调用 _r13_*。
        # R17 为逐部位精确比对（描述段↔结论段按 器官+侧别 精确到同一部位，承接原 R11-2/R14-1 段级逻辑）。
        # R18 为检查部位器官漏写（登记区域声明 → 描述段应含该区域器官，承接"胸部、上腹部"漏写上腹部器官等场景）。
        ner = ChineseRadiologyNER()
        ents = ner.extract(text)
        secs = self._split_for_r5(text)
        return (self._r1_gender(text, ents, meta)
                + self._r2_laterality(text, ents)
                + self._r3_score(text, meta)
                + self._r4_unit(ents)
                + self._r5_consistency(text, ents)
                + self._r6_site(text, meta)
                + self._r7_internal(text, ents)
                + self._r8_typo(text)
                + self._r9_conflict(text)
                + self._r10_template(text)
                + self._r11_context(text, meta, secs)
                + self._r12_sentence(text, ents)
                + self._r14_cross(text, secs)
                + self._r15_internal(text)
                + self._r17_cross_region(text, secs)
                + self._r18_region_coverage(text, meta)
                + self._r20_template_completeness(text, meta)
                + self._r21_gender_site(text, meta)
                + self._r22_lesion_size(text, secs)
                + (self._r19_homophone(text)
                   if self.rules_config.get("enable_r19", True) else [])
                + (self._r16_followup_timeframe(text)
                   if self.rules_config.get("enable_r16") else []))

    def run_with_feedback(self, text: str, meta: Optional[dict] = None) -> List[Finding]:
        """运行质控规则，并自动收集 R19 告警到反馈队列。

        与 run() 的区别：额外调用 feedback_collector.collect() 把 R19 告警
        写入 data/feedback/pending.json，供后续人工审核。

        Args:
            text: 报告文本
            meta: 元数据（患者、检查类型等）

        Returns:
            Finding 列表（与 run() 相同）
        """
        if meta is None:
            meta = {}
        findings = self.run(text, meta)

        # 自动收集 R19 告警
        try:
            from feedback_collector import collect as _fb_collect
            _fb_collect(findings, text, context={
                "modality": meta.get("modality", ""),
                "applied_site": meta.get("applied_site", ""),
                "gender": meta.get("gender", ""),
            })
        except Exception:
            pass  # 反馈收集失败不影响主流程

        return findings

    def auto_fix(self, text: str, findings: List[Finding]):
        """自动修正：确定性错别字（R8 词典 / R19 读音推导）可安全替换；
        矛盾/规范/缺失类错误无法判定正确值，不改文本（返回建议修正文本供人工参考）。
        返回 (修正后文本, 已修正错别字数, 需人工确认的问题数, 改动明细列表)。
        改动明细每项：{start, end, wrong, correct, snippet, message} —— 供前端预览逐条确认。"""
        fixes = []
        manual = 0
        for fd in findings:
            # 确定性错别字：R8 词典命中 / R19 读音推导（两者都带 suggestion=正确词）
            if fd.rule_id in ("R8-TYPO", "R19-HOMOPHONE"):
                s, e = fd.span
                sug = getattr(fd, "suggestion", "")
                if s >= 0 and e > s and sug:
                    wrong = text[s:e]
                    snippet = text[max(0, s - 12): min(len(text), e + 12)]
                    fixes.append({"start": s, "end": e, "wrong": wrong,
                                  "correct": sug, "snippet": snippet, "message": fd.message})
            else:
                # 非错别字类（性别矛盾/左右混淆/描述-结论矛盾/部位不符等）需人工判定，不自动改
                if fd.severity in ("high", "medium", "low"):
                    manual += 1
        # 区间已由引擎去重，无重叠；从右往左替换以规避位置偏移
        fixes.sort(key=lambda x: x["start"], reverse=True)
        fixed = text
        for fx in fixes:
            s, e, correct = fx["start"], fx["end"], fx["correct"]
            fixed = fixed[:s] + correct + fixed[e:]
        return fixed, len(fixes), manual, fixes

    def _secs(self, text: str) -> dict:
        """分段结果惰性缓存：run() 与各规则共享同一份 secs，避免每规则重复计算。

        缓存按文本对象身份（is）失效——run() 全程传同一 text 对象；
        外部直接调用单个规则方法时自动重算，无陈旧风险。
        """
        if getattr(self, "_secs_text", None) is not text:
            self._secs_text = text
            self._secs_val = self._split_for_r5(text)
        return self._secs_val

    @staticmethod
    def _split_for_r5(text: str) -> dict:
        """按段落标题切分描述/印象原文（与 NER 段落划分一致）。
        返回 dict 额外含 findings_start：findings 段在原始 text 中的起始偏移，
        供 R5 用相对偏移取实体附近窗口（避免用绝对偏移索引子串导致错位）。"""
        spans = []
        for pat, sec in [("(?i)检查所见|影像描述|表现|imaging findings|findings", "findings"),
                         ("(?i)影像诊断|诊断印象|印象|诊断意见|诊断结论|影像结论|结论|impression|diagnosis", "impression")]:
            for m in re.finditer(pat, text):
                spans.append((m.start(), sec))
        spans.sort()
        res = {"findings": text, "impression": "", "findings_start": 0}
        if spans:
            # 从第一个标题起往后切：findings 取首个 findings 起 ~ 下一个 impression；impression 取首个 impression 起
            f0 = next((s for s in spans if s[1] == "findings"), None)
            i0 = next((s for s in spans if s[1] == "impression"), None)
            if f0 and i0 and i0[0] > f0[0]:
                res["findings"] = text[f0[0]:i0[0]]
                res["impression"] = text[i0[0]:]
                res["findings_start"] = f0[0]
            elif i0:
                res["impression"] = text[i0[0]:]
                res["findings"] = text[:i0[0]]
                # findings 取全文前半，起点为 0
        return res



# ----------------------------- 评分与统计 -----------------------------
SEVERITY_WEIGHT = {"high": 30, "medium": 15, "low": 5}


def score(findings: List[Finding]) -> Dict[str, dict]:
    """返回每维度评分明细：{维度: {"score": int, "deductions": [{"rule","delta","reason"}]}}。
    旧消费方如需简单 {维度:int} 请用 score_summary()。"""
    total_penalty = sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in findings)
    acc_ded = [{"rule": f.rule_id, "delta": -SEVERITY_WEIGHT.get(f.severity, 0),
                "reason": f.error_type} for f in findings]
    completeness, comp_ded = 100, []
    if any(f.rule_id == "R3-SCORE" for f in findings):
        completeness, comp_ded = 80, [{"rule": "R3-SCORE", "delta": -20, "reason": "评分标准缺失"}]
    norm = 90 if findings else 100
    norm_ded = [{"rule": "综合", "delta": -10, "reason": "存在质控问题"}] if findings else []
    return {
        "准确性": {"score": max(0, 100 - total_penalty), "deductions": acc_ded},
        "完整性": {"score": completeness, "deductions": comp_ded},
        "规范性": {"score": norm, "deductions": norm_ded},
        "及时性": {"score": 100, "deductions": []},
    }


def score_summary(scores: Dict[str, dict]) -> Dict[str, int]:
    """从 score() 的新结构提取 {维度: 分数(int)}，兼容旧消费方（驾驶舱/导出）。"""
    return {dim: (v.get("score", 100) if isinstance(v, dict) else v)
            for dim, v in scores.items()}


def error_type_counts(findings: List[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.error_type] = counts.get(f.error_type, 0) + 1
    return counts


