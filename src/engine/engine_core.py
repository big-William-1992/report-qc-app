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
# 显式静态导入全部子模块 (2026-08-25 审计修复):
# 这些模块同时经 _pull_symbols 运行时注入, 但 PyInstaller modulegraph 只认静态
# import —— 缺此行则 exe 打包后运行期 ModuleNotFoundError(CI 测不出, 见 smoke_test 局限)。
from . import models as _em, textsplit as _et, lexicon_region as _el, \
    claims as _ec, config_store as _ecs, ner as _en, scoring as _esc, \
    meta_extract as _eme  # noqa: F401

from .rules_meta import MetaRulesMixin
from .rules_typo import TypoRulesMixin
from .rules_consistency import ConsistencyRulesMixin
from .rules_size import SizeRulesMixin
from .rules_sentence import SentenceRulesMixin
from .rules_template import TemplateRulesMixin

class _KG:
    def expected_gender_for_organ(self, organ: str) -> Optional[str]:
        return GENDER_ORGANS.get(organ)

    def required_score_for_modality(self, modality: str) -> Optional[str]:
        return MODALITY_SCORE.get(modality)

    def norm_site(self, site: str) -> Optional[str]:
        """登记部位 → 部位族。先整串精确匹配；匹配不到则按子串最长匹配，
        以兼容『全腹部CT』『上腹部平扫』『胸部正侧位』等带检查类型后缀的登记写法。"""
        site = site.strip()
        if not site:
            return None
        exact = SITE_NORM.get(site)
        if exact:
            return exact
        # 按词长降序找包含的子串（长词优先，避免『肺』抢『双肺』等）
        for k in sorted(SITE_NORM, key=len, reverse=True):
            if k in site:
                return SITE_NORM[k]
        return None


# ----------------------------- 评分与统计 -----------------------------

class RuleEngine(MetaRulesMixin, TypoRulesMixin, ConsistencyRulesMixin,
                 SizeRulesMixin, SentenceRulesMixin, TemplateRulesMixin):
    def __init__(self):
        self.kg = _KG()
        self.rules_config = load_rules_config()

    def reload_rules(self):
        """重新从 assets/rules_config.json 读取规则（用户维护后即时生效）。"""
        self.rules_config = load_rules_config()

    def run(self, text: str, meta: dict) -> List[Finding]:
        # 实际启用规则：R1、R2、R3、R4、R5、R6、R8、R9、R10、R12、R14、R15、R17、R18、R21、R22、R24、R25；
        #   R7（描述段男女专属器官混用）已并入 R12-SENTENCE；R20（必查要素漏写）已并入 R18-COVERAGE
        #   （见 _r18_region_coverage 类型级分支），二者均不再单独启用。
        # R11（信息框-正文跨框矛盾）已全部并入 R1-GENDER / R2-LATERALITY / R17-PERREGION，
        #   引擎无 _r11_* 实现，不再单独产出。
        # R16（随访时限缺失）、R19（读音/形近错字）为可选规则，分别由 rules_config.enable_r16 / enable_r19 控制（默认关闭/开启）。
        # R13 为预留编号（当前无对应规则），故不调用 _r13_*。
        # R17 为逐部位精确比对（描述段↔结论段按 器官+侧别 精确到同一部位，承接原 R11-2/R14-1 段级逻辑）；
        #   其段级兜底也已接管原 R11-ABNORMAL / R14-NORMAL（描述-结论一致性矛盾统一由 R17-PERREGION 产出）。
        # 左右侧跨段矛盾原由 R2（NER 器官族）与 R14-SIDE（文本级兜底）分工，现统一合并至 R2-LATERALITY
        #   （见 _r2_laterality：NER 分支 + 中英文文本分支 + 器官族去重）；R14 现仅保留 R14-NATURE / R14-COUNT。
        # R18 为检查部位器官漏写（登记区域声明 → 描述段应含该区域器官）。
        ner = ChineseRadiologyNER()
        ents = ner.extract(text)
        secs = self._split_for_r5(text)
        _finds = (self._r1_gender(text, ents, meta)
                  + self._r2_laterality(text, ents)
                  + self._r3_score(text, meta)
                  + self._r4_unit(ents)
                  + self._r5_consistency(text, ents)
                  + self._r6_site(text, meta)
                  + self._r8_typo(text)
                  + self._r9_conflict(text)
                  + self._r10_template(text)
                  + self._r12_sentence(text, ents)
                  + self._r14_cross(text, secs)
                  + self._r15_internal(text)
                  + self._r17_cross_region(text, secs)
                  + self._r18_region_coverage(text, meta)
                  + self._r21_gender_site(text, meta)
                  + self._r22_lesion_size(text, secs)  # E1 2026-08-18：复用上方 secs，避免重复切分
                  + self._r24_advice_conflict(text)   # R24 建议强度矛盾（2026-08-23 新增）
                  + self._r25_temporal_direction(text)  # R25 时序方向矛盾（2026-08-23 新增）
                  + (self._r19_homophone(text)
                     if self.rules_config.get("enable_r19", True) else [])
                  + (self._r16_followup_timeframe(text)
                     if self.rules_config.get("enable_r16") else []))
        # 繁体/异体字提示（2026-08-18）：避免简体词典对繁体输入静默误判
        _trad = self._traditional_hits(text)
        if _trad:
            _finds.append(Finding(
                "R23-TRADITIONAL", "繁体字提示", "low",
                f"检测到繁体/异体字（{'、'.join(_trad)}…），质控词典为简体，"
                f"相关规则识别可能不完整，建议转简体后重试",
                "", (-1, -1)))
        # 跨规则去重：同一事实的多规则冗余告警仅保留主规则一条（见 _DEDUP_GROUPS）
        _finds = _dedup_findings(_finds)
        return _finds

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
                if fd.rule_id == "R19-HOMOPHONE":
                    # R19 的 span 基于去空格/标点的 norm_text，直接切原文会错位
                    # （如『双肺 磨玻璃样密度影』）。用双指针映射还原到原文坐标。
                    s, e = _map_norm_span_to_orig(text, _r19_norm_text(text), s, e)
                sug = getattr(fd, "suggestion", "")
                if s >= 0 and e > s and sug:
                    wrong = text[s:e]
                    # 常用词保护（2026-08-18 H6）：R8 词典词条 wrong 命中高频白名单
                    # （用户手工录入的 直接→直径 等）只提示不自动替换，防一键采纳
                    # 把报告里的合法表述静默改写。R19 为读音推导，不受此限。
                    if fd.rule_id == "R8-TYPO" and _is_common_word(wrong):
                        manual += 1
                        continue
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

    # R1 性别矛盾
