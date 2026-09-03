"""放射科高频正确词组库（白名单锚定）+ 读音相似错字推导
================================================================================
设计目标（对应业务需求：从放射科常用字、词组出发确定高频词组，其他相似读音的
字/词作为「可能错误」标记）：

1. **高频正确词组库（白名单）**：收录放射科报告最常用的标准词组——解剖结构、
   方位、征象、病变、程度、随访句式。这些是「确定正确」的锚点。
2. **读音相似推导**：对文本中出现的每个「未知/可疑」片段，用 pypinyin 转拼音后
   与高频词组库比对；读音相同（同音）或高度相似（近音，编辑距离=1 的声母/韵母
   替换）的，即标记为「疑似错字」，并给出最可能的正确词。

设计原则
--------
1. 纯数据 + 惰性拼音缓存，pypinyin 为运行时可选依赖：未安装时自动降级为仅用
   手工 TYPO 词典，绝不影响既有 R8。
2. 与 assets/rules_config.json 解耦：rules_config 存用户可改的手工错别字表；
   本文件存「标准词组白名单」，供读音推导做锚定。两者互补。
3. 词组库按「词长降序」匹配，避免『磨玻璃密度影』先于『磨玻璃影』被切分。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 1. 高频标准词组库（白名单锚定）
# ---------------------------------------------------------------------------
# 高频标准词组库（白名单锚定）：数据外部化到 assets/lexicons/highfreq_words.json
# （医师可自行编辑词表，无需改代码；缺失/损坏时回退最小内置词表并告警）。
_HIGHFREQ_WORDS: List[Tuple[str, str]] = []
try:
    import json as _json
    import paths as _paths
    with open(_paths.assets_dir() + "/lexicons/highfreq_words.json", encoding="utf-8") as _f:
        _HIGHFREQ_WORDS = [(w, c) for w, c in _json.load(_f) if w and c]
except Exception as _e:
    _HIGHFREQ_WORDS = [("结节", "征象"), ("肿块", "征象"), ("占位", "征象"),
                       ("肺", "结构"), ("肝", "结构"), ("肾", "结构"),
                       ("正常", "正常"), ("未见异常", "正常"), ("建议", "随访")]
    print(f"[highfreq_lexicon] 高频词库 JSON 加载失败（{_e}），使用最小内置词表")


# 词组 → 拼音（构建时惰性填充）
_WORD_PINYIN: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# 2. 读音相似推导
# ---------------------------------------------------------------------------
def _load_pinyin() -> Optional[object]:
    """惰性加载 pypinyin；失败返回 None（降级：仅手工词典）。"""
    try:
        import pypinyin
        return pypinyin
    except Exception:
        return None


_PY = _load_pinyin()


# 放射语境多音字优先读音（2026-08-16 增强）：lazy_pinyin 对多音字取默认读音，
# 部分放射术语中的读音与其默认读音不同，这里在 _word_pinyin 中按字覆盖。
_RAD_POLYPHONE: Dict[str, str] = {
    "咽": "yan",        # 咽部（yān）
    "骨": "gu",         # 骨质 / 骨（gǔ）
    "脏": "zang",       # 内脏（zàng）
    "血": "xue",        # 血液（xuè）
    "椎": "zhui",       # 椎体（zhuī）
    "隔": "ge",         # 纵隔（gé）
    "横": "heng",       # 横隔（héng）
    "结": "jie",        # 结节（jié）
    "冠": "guan",       # 冠脉（guān）
    "尿": "niao",       # 尿（niào）
    "窦": "dou",        # 上颌窦（dòu）
    "给": "gei",        # 供血（gěi）
    "颈": "jing",       # 颈部（jǐng）
    "膀": "pang",       # 膀胱（páng）
}


def _word_pinyin(word: str) -> str:
    """把词组转成『声母-韵母拼接』的紧凑拼音串，供比对。
    多音字优先按放射语境读音覆盖（_RAD_POLYPHONE）。"""
    if _PY is None:
        return ""
    cached = _WORD_PINYIN.get(word)
    if cached is not None:
        return cached
    try:
        # 逐字取读音（errors="default" 非汉字原样返回，保证一字一音对齐），
        # 再对放射多音字做语境覆盖。
        from pypinyin import pinyin, Style
        parts = pinyin(word, style=Style.NORMAL, errors="default")
        py_parts = []
        for ch, plist in zip(word, parts):
            base = plist[0].lower() if plist else ""
            py_parts.append(_RAD_POLYPHONE.get(ch, base))
        py = "".join(py_parts)
    except Exception:
        py = ""
    _WORD_PINYIN[word] = py
    return py


def _pinyin_build_index() -> Dict[int, Dict[str, List[str]]]:
    """按长度分桶建立『拼音 → 词组』索引，供读音相似查询。
    返回 {词长: {拼音: [词组...]}}。"""
    buckets: Dict[int, Dict[str, List[str]]] = {}
    for word, _cat in _HIGHFREQ_WORDS:
        n = len(word)
        if n < 2:
            continue
        py = _word_pinyin(word)
        if not py:
            continue
        buckets.setdefault(n, {}).setdefault(py, []).append(word)
    return buckets


# 长度 → {拼音: [词组]}
_INDEX = _pinyin_build_index()

# 上下文敏感词排除表：这些串虽然读音与某高频词相同/相似，但在真实报告中是
# 「核心词 + 动词/虚词」的合法句法组合（如『右肺见磨玻璃影』里『肺见』=肺+见），
# 属滑窗切词的固有误判。逐一人工复核后列入，避免 R19 误报。
_HF_IGNORE: Set[str] = set()
try:
    import json as _json
    import paths as _paths
    with open(_paths.assets_dir() + "/lexicons/hf_ignore.json", encoding="utf-8") as _f:
        _HF_IGNORE = set(_json.load(_f))
except Exception:
    _HF_IGNORE = set()



def _edit_distance(a: str, b: str) -> int:
    """经典编辑距离（用于拼音串近似比对）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _pinyin_similar(py_a: str, py_b: str) -> bool:
    """判定两个拼音串是否『读音相似』（同音 或 编辑距离≤1）。"""
    if py_a == py_b:
        return True
    if abs(len(py_a) - len(py_b)) > 1:
        return False
    return _edit_distance(py_a, py_b) <= 1


def _same_chars_reordered(seg: str, candidate: str) -> bool:
    """检查两个文本是否由完全相同字符组成（仅顺序不同）。

    用于捕捉「诊断→断诊」这类同字换序错字：pypinyin 按字输出拼音，
    "诊断"=zhenduan，"断诊"=duanzhen，拼音串不同但各字读音相同。

    收窄条件（P1 修复）：
    1. 仅匹配长度 ≤ 3 的片段——长度 ≥ 4 时同字重排的概率极低（如"正常信号"
       vs "信号正常"），且误报风险高。
    2. seg 本身不能是高频正确词——若 seg 本身就是正确词，说明它合法，
       不应被识别为候选词的同字重排（避免 FP）。
    """
    if len(seg) != len(candidate):
        return False
    if len(seg) > 3:
        return False
    if sorted(seg) != sorted(candidate):
        return False
    # seg 本身不在高频词表中——避免"正常信号"被识别为"信号正常"的同字重排
    for w, _c in _HIGHFREQ_WORDS:
        if w == seg:
            return False
    return True


def find_homophone_suggestions(segment: str,
                               top_k: int = 3) -> List[Tuple[str, str, float]]:
    """给定一个可疑片段，返回读音相似的高频正确词候选。

    返回 [(正确词, 类别, 相似度)]，按相似度降序；未安装 pypinyin 时返回空。
    """
    if _PY is None or not segment:
        return []
    seg_py = _word_pinyin(segment)
    if not seg_py:
        return []
    n = len(segment)
    cand: List[Tuple[str, str, float]] = []
    # 同长优先；也允许 ±1 长（少一字/多一字）
    for ln in (n, n - 1, n + 1):
        if ln < 2 or ln > 6:
            continue
        bucket = _INDEX.get(ln, {})
        for py, words in bucket.items():
            if not _pinyin_similar(seg_py, py):
                continue
            dist = _edit_distance(seg_py, py)
            sim = 1.0 - dist / max(len(seg_py), len(py), 1)
            for w in words:
                cat = next((c for (x, c) in _HIGHFREQ_WORDS if x == w), "")
                cand.append((w, cat, round(sim, 3)))
    # P1 同字换序补充：字符顺序不同但读音相同（如"断诊"duanzhen vs "诊断"zhenduan），
    # pypinyin 按字输出导致拼音串不同，需额外检查字符重排。
    # 收窄：仅长度 ≤ 3 的片段才触发同字重排（避免"正常信号"被识别为"信号正常"）
    if n <= 3:
        seg_sorted = sorted(segment)
        for ln in (n,):
            bucket = _INDEX.get(ln, {})
            for py, words in bucket.items():
                for w in words:
                    if w in [x for x, _, _ in cand]:
                        continue
                    if sorted(w) == seg_sorted:
                        # 片段本身不能是高频正确词（合法词不应被标记）
                        if segment in [x for x, _c in _HIGHFREQ_WORDS]:
                            continue
                        cat = next((c for (x, c) in _HIGHFREQ_WORDS if x == w), "")
                        cand.append((w, cat, 0.99))

    # P1 形近字补充：读音不相似但形近（五笔/形码/OCR 误识）的候选也纳入，
    # 相似度记 0.9（低于同音 1.0 / 近音 0.98，但高于 R19 触发阈值时仍可检出）
    for ln in (n,):
        bucket = _INDEX.get(ln, {})
        for py, words in bucket.items():
            for w in words:
                if _shape_similar_word(segment, w):
                    if w not in [x for x, _, _ in cand]:
                        cat = next((c for (x, c) in _HIGHFREQ_WORDS if x == w), "")
                        cand.append((w, cat, 0.9))
    cand.sort(key=lambda t: -t[2])
    # 去重（同词多类别只留一个）
    seen: Set[str] = set()
    dedup: List[Tuple[str, str, float]] = []
    for w, c, s in cand:
        if w in seen:
            continue
        seen.add(w)
        dedup.append((w, c, s))
        if len(dedup) >= top_k:
            break
    return dedup


# ---------------------------------------------------------------------------
# 2.5 形近字识别（P1）：读音检测对「形近但不同音」的字无能为力
# （五笔/形码输入法、OCR 误识别多为形近字），这里建立常用形近字组。
# ---------------------------------------------------------------------------
# 形近字组：同组内两字互视为形近（左右/上下结构、偏旁相似、仅一笔之差）。
_SHAPE_GROUPS: List[str] = []
try:
    import json as _json
    import paths as _paths
    with open(_paths.assets_dir() + "/lexicons/shape_groups.json", encoding="utf-8") as _f:
        _SHAPE_GROUPS = list(_json.load(_f))
except Exception:
    _SHAPE_GROUPS = ["未末", "日目", "干千", "人入", "土士"]


# 展开为 字 → 形近字集合（双向）
_SHAPE_MAP: Dict[str, Set[str]] = {}
for _g in _SHAPE_GROUPS:
    for _ch in _g:
        _SHAPE_MAP.setdefault(_ch, set()).update(_g)


def _shape_similar_word(word_a: str, word_b: str) -> bool:
    """两词『形近』判定：长度相同且逐字比对，形近字对数 ≥ 1 且其余字相同。
    形近字主要特征：单个错字时（如 未梢→末梢）非常有效。"""
    if len(word_a) != len(word_b):
        return False
    diff = 0
    for ca, cb in zip(word_a, word_b):
        if ca == cb:
            continue
        diff += 1
        if diff > 1:
            return False
        if cb not in _SHAPE_MAP.get(ca, set()):
            return False
    return diff == 1


# ---------------------------------------------------------------------------
# 3. 暴露给引擎的接口
# ---------------------------------------------------------------------------
def highfreq_words() -> List[Tuple[str, str]]:
    """返回完整高频词组库（只读拷贝），供前端展示/配置。"""
    return list(_HIGHFREQ_WORDS)


def is_pinyin_available() -> bool:
    return _PY is not None


# P4 敏感度档位 → 最低相似度阈值（高敏感度抓更多，容忍略多误报）
# 形近候选相似度记 0.9，故高敏感度阈值须 ≤0.9 才能纳入形近字
SENSITIVITY_MIN_SIM = {"low": 1.0, "medium": 0.98, "high": 0.9}


def segment_candidates(seg: str,
                       sensitivity: str = "medium") -> Tuple[bool, List[Tuple[str, str, float]]]:
    """判断一个片段是否『疑似错字』并给出读音相似候选。

    返回 (命中与否, 候选列表)。命中条件：片段本身不在白名单里，
    但与某高频正确词读音相同/相似（相似度 ≥ 当前敏感度档位阈值）。
    sensitivity ∈ {low, medium, high}：低=仅同音、中=近音(默认)、高=含形近。
    """
    seg = seg.strip()
    if not seg:
        return False, []
    # 上下文敏感排除表：读音虽相似但是「核心词+动词/虚词」的合法组合，不报
    if seg in _HF_IGNORE:
        return False, []
    # 本身就是正确词，不报
    for w, _c in _HIGHFREQ_WORDS:
        if w == seg:
            return False, []
    min_sim = SENSITIVITY_MIN_SIM.get(sensitivity, 0.98)
    cand = [c for c in find_homophone_suggestions(seg) if c[2] >= min_sim]
    # 形近候选（相似度记 0.9）来自人工精选形近字组（膈/隔、肋/胸、末/未…），
    # 误报风险低；medium/high 敏感度下均纳入（仅 low 只报同音，避免误报）。
    if sensitivity != "low":
        _shape_only = [c for c in find_homophone_suggestions(seg)
                       if c[2] == 0.9 and c not in cand]
        if _shape_only:
            cand.extend(_shape_only)
            cand.sort(key=lambda t: -t[2])
    if cand:
        return True, cand
    return False, []
