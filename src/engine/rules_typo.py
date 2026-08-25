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

class TypoRulesMixin:
    def _traditional_hits(self, text: str) -> list:
        """检测繁体/异体字（2026-08-18）：简体词典对繁体输入会产出碎片实体误报，
        检出常见繁体字时提示用户，避免静默误判。"""
        if not text:
            return []
        hits = []
        for ch in _TRADITIONAL_CHARS:
            if ch in text:
                hits.append(ch)
                if len(hits) >= 6:
                    break
        return hits

    def _r8_typo(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        typo_map = self.rules_config.get("typos", {}) or {}
        # 停用单条错字：disabled_typos 中列出的错词跳过（词库可视化维护的「停用」动作）
        disabled = set(self.rules_config.get("disabled_typos") or [])
        seen = set()
        reported = set()  # 每个错词仅报一次（2026-08-20）：同一错字在描述段+结论段各出现
                          # 一次时不再重复告警，降低 low 级噪声
        # 按错词长度降序匹配，优先命中更长的错写（如"淋巴结解"先于"结解"），避免重复告警
        for wrong in sorted(typo_map.keys(), key=len, reverse=True):
            if wrong in disabled:
                continue  # 用户已停用该词条
            if wrong in reported:
                continue  # 该错词已报过，不再重复
            if wrong == typo_map.get(wrong):
                continue  # 自映射无意义项
            correct = typo_map[wrong]
            m = re.search(re.escape(wrong), text)  # 仅取首个出现位置
            if not m:
                continue
            s, e = m.start(), m.end()
            # 上下文安全闸（2026-08-22）：错写本身是常见字/词的条目，
            # 仅当出现在放射语境附近才判为错别字，避免无关组合误报。
            if wrong in TYPO_CONTEXT_REQUIRED and not _typo_in_medical_context(text, s, e):
                continue
            # 跳过已被更长错词覆盖的区间
            if any(ms <= s < me or ms < e <= me for ms, me in seen):
                continue
            seen.add((s, e))
            reported.add(wrong)
            out.append(Finding("R8-TYPO", "同音错别字", "medium",
                f"检出疑似错别字「{wrong}」，疑为「{correct}」（常见语音录入误写）",
                wrong, (s, e), correct))
        return out

    # R19 读音相似错字（高频词组锚定 + pypinyin 自动推导）
    # 思路：放射科高频正确词组作为「白名单锚定」。对文本中每个中文词组片段，
    # 若其读音与某高频正确词完全相同（同音异字，语音录入最典型）或高度相似，
    # 且该片段本身不是已知正确词，则标记为「可能错误」，给出最可能的正确词。
    # 与 R8 的区别：R8 靠人工维护的错词表；R19 靠「高频词库 + 读音」自动推导，
    # 能发现词表外的、读音相近但写法错误的词组。pypinyin 不可用时本规则静默关闭。
    def _r19_homophone(self, text) -> List[Finding]:
        out = []
        if not _HF_OK or not _hf_pinyin_available():
            return out
        if not text:
            return out
        # 空格/标点容忍（2026-08-16 增强）：移除中文之间的空格/标点，使
        # 『磨 玻 璃』『磨．玻．璃』等 OCR/录入带空格的词能被识别为『磨玻璃』。
        # 仅用于 R19 读音比对（R19 为 low 级提示，位置偏移对使用影响极小）。
        norm_text = _r19_norm_text(text)
        # R8 已标记区间：R19 不重复报（同音异字由 R19 补漏）
        r8_spans = set()
        typo_map = self.rules_config.get("typos", {}) or {}
        for wrong in typo_map:
            for m in re.finditer(re.escape(wrong), norm_text):
                r8_spans.add((m.start(), m.end()))
        seen_spans: Set[Tuple[int, int]] = set(r8_spans)
        # 切词：先按高频白名单最长匹配切出已知正确词（跳过不查），
        # 剩余中文串用 2~4 字滑窗做读音比对。
        hf = sorted(_hf_highfreq_words(), key=lambda t: len(t[0]), reverse=True)
        # 标记白名单覆盖区间
        covered = []
        for w, _c in hf:
            for m in re.finditer(re.escape(w), norm_text):
                covered.append((m.start(), m.end()))
        covered.sort()
        # P4 敏感度：设置页可调（low=仅同音 / medium=近音 / high=含形近）
        sensitivity = str(self.rules_config.get("r19_sensitivity", "medium")).lower()
        if sensitivity not in ("low", "medium", "high"):
            sensitivity = "medium"
        # 安全词集合（内置 R19_SAFE_WORDS ∪ 用户配置 r19_safe_words），exact 同音命中时放行。
        _r19_safe_words = set(R19_SAFE_WORDS)
        _r19_safe_words.update(self.rules_config.get("r19_safe_words") or [])
        def _in_covered(s, e):
            return any(ms <= s and e <= me for ms, me in covered)
        def _in_seen(s, e):
            return any(ms <= s < me or ms < e <= me for ms, me in seen_spans)
        # P0 上下文消歧：疑似错字片段若被某个更长的白名单词组完全包含
        # （如「未见明显异常」覆盖切出的「见明」），视为合法组合，豁免。
        def _inside_covered(s, e, cov):
            if not cov:
                return False
            import bisect
            idx = bisect.bisect_right([c[0] for c in cov], s) - 1
            if idx < 0:
                return False
            ms, me = cov[idx]
            return ms <= s and e <= me
        cjk = re.compile(r"[\u4e00-\u9fff]+")
        for m in cjk.finditer(norm_text):
            s0 = m.start()
            run = m.group()
            # 对 run 内每个可能起点做滑窗
            for i in range(len(run)):
                # 滑窗 4→3→2：命中 exact（同音）立即采用；near/shape 先记下继续找更短窗的 exact。
                # 2026-08-18：此前任一命中即 break——『膜玻璃样』4字窗近音命中『磨玻璃影』
                # 会阻断 3 字窗『膜玻璃』的同音『磨玻璃』，导致自动修正改错词。
                best_hit = None  # (seg, s, e, best, cat, kind)
                for ln in (4, 3, 2):
                    if i + ln > len(run):
                        continue
                    s, e = s0 + i, s0 + i + ln
                    if _in_seen(s, e):
                        continue
                    seg = run[i:i + ln]
                    hit, cand = _hf_segment_candidates(seg, sensitivity)
                    if hit and cand:
                        best, cat, sim, _kind = cand[0]
                        if _kind == "exact":
                            best_hit = (seg, s, e, best, cat, "exact")
                            break
                        if best_hit is None:
                            best_hit = (seg, s, e, best, cat, _kind)
                if best_hit:
                    seg, s, e, best, cat, _kind = best_hit
                    if not _in_covered(s, e):
                        # P0 上下文消歧：命中疑似错字但位于更长白名单词组
                        # 内部（如「未见明显异常」切出「见明」）→ 豁免，
                        # 消除『切词切出伪词』的误报。
                        if _inside_covered(s, e, covered):
                            continue
                        # 安全词抑制（2026-08-22）：exact（同音）命中且片段本身是常见合法词
                        # （如『印象』『姐姐』等与医学词同音的常见中文词），判定为正常用词而非
                        # 错字，直接跳过——这是 R19 同音误报的主要来源，显著降噪。
                        # （shape/near 是字形或近音差异，更可能是真错字，不在此抑制范围内。）
                        if _kind == "exact" and seg in _r19_safe_words:
                            continue
                        # 判定错字类型：exact=同音 / near=近音 / shape=形近
                        if _kind == "shape":
                            reason = "形近"
                        elif _kind == "exact":
                            reason = "同音"
                        else:
                            reason = "近音"
                        out.append(Finding(
                            "R19-HOMOPHONE", "读音/形近错字", "low",
                            f"「{seg}」{reason}与高频词「{best}」（{cat}）相近，"
                            f"疑为语音或输入法录入误写，请核对",
                            seg, (s, e), best))
                        seen_spans.add((s, e))
        return out

    # R9 用户自定义互斥冲突（由 rules_config.json 维护：词A 与 词B 不应在同一范围内共存）
