"""
rules_typo.py — 错别字规则（R8/R19）与词频学习（P2 拆分自 engine.py）
====================================================================
- _HF_OK / _MW_OK：highfreq_lexicon / medical_whitelist 的导入降级开关
  （导入失败仅告警，R19 同音层跳过，不影响 R8 与 R19-WHITELIST 层）
- TypoRulesMixin：_r8_typo（词典错别字）、_r19_homophone（白名单域外 + 读音相似双层）
- scan_reports_for_typos：历史报告词频学习候选（懒加载 samplelib + highfreq_lexicon）

依赖：_lexicons、engine_config、engine_types、highfreq_lexicon、medical_whitelist。
"""
import re
import logging as _engine_logging
from typing import List

from _lexicons import *  # noqa: F401,F403

from engine_config import load_rules_config, RULES_CONFIG_PATH
from engine_types import Finding

_engine_logger = _engine_logging.getLogger(__name__)

# 高频正确词组库 + 读音相似推导（R19）：白名单锚定 → 读音相似标记疑似错字。
# 依赖 pypinyin（运行时可选），未安装时 R19 自动降级为空（不影响既有 R8 词典）。
try:
    from highfreq_lexicon import (
        segment_candidates as _hf_segment_candidates,
        highfreq_words as _hf_highfreq_words,
        is_pinyin_available as _hf_pinyin_available,
    )
    _HF_OK = True
except Exception as _e:  # pragma: no cover - 仅防御性降级
    _HF_OK = False
    _engine_logger.warning(
        f"highfreq_lexicon 导入失败（{_e}）。"
        "R19 同音/形近错字检测层将完全跳过，不影响 R8 词典和 R19-WHITELIST 层。"
    )

# 医学词组白名单（所有词表合并）：用于「不在白名单中的中文片段 = 疑似错别字」检测
try:
    from medical_whitelist import (
        covered_spans as _mw_covered_spans,
        find_suspicious_segments as _mw_find_suspicious,
        whitelist_size as _mw_size,
    )
    _MW_OK = True
except Exception:  # pragma: no cover - 仅防御性降级
    _MW_OK = False



def scan_reports_for_typos(path: str = RULES_CONFIG_PATH, limit: int = 200) -> list:
    """历史报告词频学习：扫描样本库 report_text，自动发现「低频写法 → 高频标准写法」候选。
    返回候选列表 [{wrong, correct, count, category, similarity, reason}]，供前端一键采纳。

    算法（纯本地、无模型）：
    1. 从样本库读取最近 limit 份报告正文；
    2. 滑窗 2-4 字切词 + 高频词库锚定，统计每词出现频次；
    3. 对每个「不在白名单、出现次数少」的片段，用读音相似度与高频正确词比对；
    4. 相似度高（同音/近音）且明显低于锚点词频的，列为候选错字；
    5. 已存在于 typos / ignores 的自动排除。
    """
    try:
        import samplelib
        # SQL 层直接限制最近 limit 份（避免全量载入长文本报告）
        samples = samplelib.list_samples_full(limit=limit)
    except Exception:
        return []
    if not samples:
        return []
    # 词频统计（单报告处理长度设上限，防止极端超长文本拖垮接口）
    from collections import Counter
    _MAX_CHARS = 4000
    freq: Counter = Counter()
    for s in samples:
        text = (s.get("report_text") or "") if isinstance(s, dict) else getattr(s, "report_text", "") or ""
        if not text:
            continue
        text = text[:_MAX_CHARS]
        # 标点/空白替换为空格作为切词边界，避免跨标点把不相关的字拼成「伪词」
        # （如「结节。复查」被拼成「结节复查」误导统计）
        text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", text)
        # 只对连续中文片段滑窗，英文/数字单词整体计一次
        for seg in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
            n = len(seg)
            for i in range(n):
                for L in (4, 3, 2):
                    if i + L <= n:
                        freq[seg[i:i + L]] += 1
    if not freq:
        return []
    # 读取现有规则避免重复推荐
    cfg = load_rules_config(path)
    typos = set(cfg.get("typos", {}))
    ignores = set(cfg.get("ignores", []))
    # 高频锚点：取词库中出现次数 ≥ 2 的词作为「标准写法」
    anchors = {w for w, c in freq.items() if c >= 2 and len(w) >= 2}
    # 低频候选 + 读音比对
    candidates = []
    seen = set()
    try:
        from highfreq_lexicon import find_homophone_suggestions, highfreq_words
        hf = {w for w, _ in highfreq_words()}
    except Exception:
        hf = set()
    for w, c in sorted(freq.items(), key=lambda kv: -kv[1]):
        if c >= 3 or len(w) < 2:
            continue
        if w in typos or w in ignores or w in hf or w in seen:
            continue
        if w in anchors:
            continue
        cand = find_homophone_suggestions(w)
        if not cand:
            continue
        best, cat, sim = cand[0]
        if sim < 0.97:
            continue
        seen.add(w)
        candidates.append({
            "wrong": w, "correct": best, "count": c,
            "category": cat, "similarity": round(sim, 3),
            "reason": f"历史报告出现 {c} 次，读音与「{best}」相似，疑似错字",
        })
        if len(candidates) >= 20:
            break
    return candidates




# _R19_NORM_RE 的单趟等价实现 + 原始下标映射：
# 返回 (norm_text, raw_idx)，raw_idx[k] = norm_text[k] 在原始文本中的下标。
# 语义与 while 循环 _R19_NORM_RE.sub 完全一致：CJK + 分隔符串 + CJK → 保留两端 CJK。
# 用单趟扫描便于同时记录「归一化字符 ← 原始下标」，供 r8_spans 坐标映射。
def _norm_text_with_map(text: str):
    _sep = set("·．.,，、；;")  # 与 _R19_NORM_RE 中括号内容一致
    norm_chars: List[str] = []
    raw_idx: List[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if "\u4e00" <= ch <= "\u9fff":
            norm_chars.append(ch)
            raw_idx.append(i)
            i += 1
            # 折叠后续 分隔符+CJK 链（等价于多轮正则替换）
            while i < n:
                j = i
                while j < n and (text[j].isspace() or text[j] in _sep):
                    j += 1
                if j < n and "\u4e00" <= text[j] <= "\u9fff" and j > i:
                    norm_chars.append(text[j])
                    raw_idx.append(j)
                    i = j + 1
                else:
                    break
            continue
        norm_chars.append(ch)
        raw_idx.append(i)
        i += 1
    return "".join(norm_chars), raw_idx


class TypoRulesMixin:
    # R8 同音/近音错别字（多由语音录入产生：词典由 rules_config.json 维护，可在 GUI 增删）
    def _r8_typo(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        typo_map = self.rules_config.get("typos", {}) or {}
        # 停用单条错字：disabled_typos 中列出的错词跳过（词库可视化维护的「停用」动作）
        disabled = set(self.rules_config.get("disabled_typos") or [])
        seen = set()
        # 按错词长度降序匹配，优先命中更长的错写（如"淋巴结解"先于"结解"），避免重复告警
        for wrong in sorted(typo_map.keys(), key=len, reverse=True):
            if wrong in disabled:
                continue  # 用户已停用该词条
            if wrong == typo_map.get(wrong):
                continue  # 自映射无意义项
            correct = typo_map[wrong]
            # 防御性跳过：错词是正确词的前缀/子串（如「未见明」⊂「未见明显」），
            # 匹配正确文本会产生「未见明显显」式重复，且无法区分正确/错误上下文。
            if len(wrong) < len(correct) and wrong in correct:
                continue
            for m in re.finditer(re.escape(wrong), text):
                s, e = m.start(), m.end()
                # 跳过已被更长错词覆盖的区间
                if any(ms <= s < me or ms < e <= me for ms, me in seen):
                    continue
                seen.add((s, e))
                out.append(Finding("R8-TYPO", "同音错别字", "medium",
                    f"检出疑似错别字「{wrong}」，疑为「{correct}」（常见语音录入误写）",
                    wrong, (s, e), correct))
        return out

    # R19 读音/白名单双重错字检测
    # 第一层：医学白名单域外检测 — 不在任何医学词表中的中文片段直接标记
    # 第二层：pypinyin 读音相似检测 — 白名单内的词用读音比对补漏
    #
    # 设计思路：医学文书有强烈的逻辑性和规范性，绝大多数词组都在词表中。
    # 「不明显」在白名单中 → 正确；「部明显」不在任何医学词表中 → 大概率错别字。

    def _r19_homophone(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        # 空格/标点容忍：移除中文之间的空格/标点（单趟扫描 + 原始下标映射，
        # 与 _R19_NORM_RE 多轮替换等价——见模块级 _norm_text_with_map）
        norm_text, _raw_idx = _norm_text_with_map(text)
        import bisect as _bisect

        # R8 已标记区间：R19 不重复报。
        # 关键：必须在**原始文本**上重建（R8 在原始文本匹配），再映射回归一化坐标——
        # 带空格/标点的错字（如「磨 玻 离」）R8 匹配不到，若按归一化文本重建
        # r8_spans 会把它误判为「R8 已覆盖」而跳过，导致漏检。
        r8_spans = set()
        typo_map = self.rules_config.get("typos", {}) or {}
        for wrong in typo_map:
            for m in re.finditer(re.escape(wrong), text):
                _s = _bisect.bisect_left(_raw_idx, m.start())
                _e = _bisect.bisect_left(_raw_idx, m.end())
                r8_spans.add((_s, _e))

        # 白名单层和同音层各自收集，最后合并去重
        # （白名单层覆盖的 span 不再重复报同音层，但保留白名单层结果）
        wl_findings: List[Finding] = []
        hp_findings: List[Finding] = []

        # ---- 第一层：医学白名单域外检测 ----
        if _MW_OK:
            suspicious = _mw_find_suspicious(norm_text, min_len=2, max_len=4)
            for s, e, seg in suspicious:
                if any(ms <= s < me or ms < e <= me for ms, me in r8_spans):
                    continue
                if not re.fullmatch(r"[一-鿿]+", seg):
                    continue
                wl_findings.append(Finding(
                    "R19-WHITELIST", "非标准医学词组", "low",
                    f'「{seg}」不在医学词组白名单中，疑为录入错误，请核对（如「部明显」应为「不明显」）',
                    seg, (s, e), ""))

        # ---- 第二层：pypinyin 读音相似检测（原有逻辑，pypinyin 不可用时跳过）----
        if _HF_OK and _hf_pinyin_available():

            # 用医学白名单的多字词覆盖区间替代原有高频词白名单
            # 只用长度>=2的词做覆盖：单字白名单只用于 R19-WHITELIST 层，不同时屏蔽同音层；
            # 否则"断诊"（诊断→断诊换序）两字均在单字白名单中，导致同音层被跳过、漏检。
            if _MW_OK:
                covered = [(s, e) for s, e in _mw_covered_spans(norm_text) if e - s >= 2]
            else:
                hf = sorted(_hf_highfreq_words(), key=lambda t: len(t[0]), reverse=True)
                covered = []
                for w, _c in hf:
                    for m in re.finditer(re.escape(w), norm_text):
                        covered.append((m.start(), m.end()))
            covered.sort()

            sensitivity = str(self.rules_config.get("r19_sensitivity", "medium")).lower()
            if sensitivity not in ("low", "medium", "high"):
                sensitivity = "medium"

            def _in_covered(s, e):
                return any(ms <= s and e <= me for ms, me in covered)
            def _inside_covered(s, e, cov):
                if not cov:
                    return False
                import bisect
                idx = bisect.bisect_right([c[0] for c in cov], s) - 1
                if idx < 0:
                    return False
                ms, me = cov[idx]
                return ms <= s and e <= me

            # 收集同音层发现，最后与白名单层合并（同 span 优先保留同音层，因带建议值）
            wl_spans = [(f.span[0], f.span[1]) for f in wl_findings]
            cjk = re.compile(r"[一-鿿]+")
            for m in cjk.finditer(norm_text):
                s0 = m.start()
                run = m.group()
                for i in range(len(run)):
                    for ln in (4, 3, 2):
                        if i + ln > len(run):
                            continue
                        s, e = s0 + i, s0 + i + ln
                        if any(ms <= s < me or ms < e <= me for ms, me in r8_spans):
                            continue
                        # 若白名单层已标记同 span 或完全包含此段，同音层不再重复。
                        # 仅跳过"精确匹配"（相同起止位置），不过度跳过"子串"——
                        # 若白名单层只标记了更大的可疑段（如"建议随珍"），同音层
                        # 仍需报告子串级别的具体错字建议（如"随珍"→"随诊"）。
                        # 否则"建议随珍"被 wl 层整段标记后，"随珍"→"随诊"的修正建议就丢失了。
                        if any(ws == s and we == e for ws, we in wl_spans):
                            continue
                        seg = run[i:i + ln]
                        # 跳过纯重复字符（如"骨骨""腺腺"），极大概率是合法文本
                        if len(set(seg)) == 1:
                            continue
                        hit, cand = _hf_segment_candidates(seg, sensitivity)
                        if hit and cand:
                            best, cat, sim = cand[0]
                            # 跨词滑窗碎片过滤：窗口内每个字符都被某个 ≥2 字白名单词
                            # 覆盖（允许跨词，如「左肾」「占位」之间的「肾占」）→ 合法
                            # 组合边界，跳过。与白名单层 all_covered_mc 语义一致；
                            # 真错字（「磨玻离」的「磨」不被任何词覆盖）不受影响。
                            _all_chars_covered = all(
                                any(cs <= pos < ce for cs, ce in covered)
                                for pos in range(s, e))
                            if not _in_covered(s, e):
                                if _inside_covered(s, e, covered) or _all_chars_covered:
                                    continue
                                if sim < 0.98:
                                    reason = "形近"
                                elif sim == 1.0:
                                    reason = "同音"
                                else:
                                    reason = "近音"
                                hp_findings.append(Finding(
                                    "R19-HOMOPHONE", "读音/形近错字", "low",
                                    f"「{seg}」{reason}与高频词「{best}」（{cat}）相近，"
                                    f"疑为语音或输入法录入误写，请核对",
                                    seg, (s, e), best))
                                break

        # 合并：白名单层 + 同音层（去重：同 span 或与同音层重叠的保留同音层，因带建议值）
        for f in wl_findings:
            if any(f.span[0] < h.span[1] and h.span[0] < f.span[1] for h in hp_findings):
                continue
            out.append(f)
        out.extend(hp_findings)
        return out
