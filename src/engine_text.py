"""src/engine_text.py — 引擎文本工具层（2026-08-18 从 engine.py 拆分）

承载不依赖 RuleEngine 类的自包含文本处理函数（句子切分、中文数字、
侧别识别、病灶计数、否定感知的标记检测）。engine.py `from engine_text import *` 复用。
"""
import re

from _lexicons import LATERALITY, _R19_NORM_RE

__all__ = [
    "_CN_NUM", "_norm_laterality", "_detect_side_in_text", "_project_side",
    "_split_sentences", "_cn_to_int", "_extract_lesion_count", "_has_marker_unnegated",
    "_r19_norm_text", "_map_norm_span_to_orig",
]


def _r19_norm_text(text: str) -> str:
    """R19 规范化：循环删除中文字符间的空格/点/逗号等（与 _R19_NORM_RE 一致）。

    用于错字检测的匹配基准；auto_fix 需用 _map_norm_span_to_orig 把
    norm 坐标还原回原文，避免在含空格/标点的报告上切片错位（2026-08-18 修复）。
    """
    if not text:
        return text
    norm = text
    while True:
        _nxt = _R19_NORM_RE.sub(lambda m: m.group(1) + m.group(2), norm)
        if _nxt == norm:
            return norm
        norm = _nxt


def _map_norm_span_to_orig(text: str, norm_text: str, s: int, e: int):
    """把 norm_text 上的字符区间 [s,e) 映射回原文本 text 的区间。

    norm_text 是 text 删除若干字符后的子序列，双指针逐字符对齐。
    返回 (orig_start, orig_end)；无法对齐时返回 (-1, -1)。
    """
    if not text or s < 0 or e > len(norm_text) or s >= e:
        return (-1, -1)
    j = 0
    start = end = -1
    for i, ch in enumerate(text):
        if j >= len(norm_text):
            break
        if ch == norm_text[j]:
            if j == s:
                start = i
            j += 1
            if j == e:
                end = i + 1
                break
    return (start, end)


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "双": 2, "单": 1}


def _norm_laterality(s):
    """把侧别写法归一化为 'left'/'right'/'bilateral'；无法识别返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    if s in LATERALITY:
        return LATERALITY[s]
    if "双" in s or "两" in s or "左右" in s:
        return "bilateral"
    if "左" in s and "右" not in s:
        return "left"
    if "右" in s and "左" not in s:
        return "right"
    return None


def _detect_side_in_text(text: str) -> set:
    """扫描文本中提及的方位集合（left/right/bilateral）。兼容中英文报告。"""
    if not text:
        return set()
    sides = set()
    if re.search(r"左\s*(侧|肺|肾|肝|乳|肾上腺|卵巢|睾丸|附件|股骨|肱骨|膝|髋|肩|肘|腕|踝|叶|上|下|腹|盆|位)", text):
        sides.add("left")
    if re.search(r"右\s*(侧|肺|肾|肝|乳|肾上腺|卵巢|睾丸|附件|股骨|肱骨|膝|髋|肩|肘|腕|踝|叶|上|下|腹|盆|位)", text):
        sides.add("right")
    if re.search(r"双侧|两侧|左右|两边", text):
        sides.add("bilateral")
    low = text.lower()
    if re.search(r"\bleft\b", low):
        sides.add("left")
    if re.search(r"\bright\b", low):
        sides.add("right")
    if re.search(r"\bbilateral\b", low):
        sides.add("bilateral")
    return sides


def _project_side(report: str, meta: dict):
    """派生『项目 / 检查部位』侧别（left/right/bilateral/None）。

    优先级：① 显式 meta.laterality > ② 报告内显式标签字段 > ③ meta.applied_site。
    """
    s = _norm_laterality(meta.get("laterality"))
    if s:
        return s
    for label in ("检查项目", "检查部位", "检查名称", "申请部位", "扫描部位", "检查范围"):
        m = re.search(label + r"[:：]?\s*([^\n，,。；;：]+)", report or "")
        if m:
            ss = _norm_laterality(m.group(1))
            if ss:
                return ss
    ss = _norm_laterality(meta.get("applied_site"))
    if ss:
        return ss
    return None


def _split_sentences(text: str) -> list:
    """按中英文句末标点 + 换行切分为句子（保留标点）。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p and p.strip()]


def _cn_to_int(tok):
    """中文数字/阿拉伯数字 → int；无法解析返回 None。"""
    if tok is None:
        return None
    tok = str(tok).strip()
    if tok.isdigit():
        return int(tok)
    return _CN_NUM.get(tok)


def _extract_lesion_count(text: str):
    """抽取文本中明确的病灶计数（枚/个），无法判定返回 None。
    2026-08-18：① 优先『共/合计/共见 N 枚』总数；多个独立计数（无总数）返回 None（保守不报）。
    ② 新增『多发/数个/多个』→ 2（≥2）与『单发/单个』→ 1——此前『双肺见多发结节』
    + 结论『共 2 枚』最常见的数量矛盾表达漏检。
    2026-08-24：修复返回类型不一致问题，"multi" 改为返回 int 2。"""
    if not text:
        return None
    if re.search(r"多发|数个|多个|数枚", text):
        return 2  # multi → 最小值 2，保持 int 类型一致
    if re.search(r"单发|单个", text):
        return 1
    m = re.search(r"(?:共|合计|共见|总计|总见|总)\s*([一二三四五六七八九十两双单\d])\s*枚", text)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"(?:共|合计|共见|总计|总见|总)\s*([一二三四五六七八九十两双单\d])\s*个\s*(?:结节|占位|肿块|病灶|囊肿|结石|骨折|积液)", text)
    if m:
        return _cn_to_int(m.group(1))
    if len(re.findall(r"[一二三四五六七八九十两双单\d]\s*枚", text)) > 1:
        return None
    m = re.search(r"([一二三四五六七八九十两双单\d])\s*枚", text)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"([一二三四五六七八九十两双单\d])\s*个\s*(?:结节|占位|肿块|病灶|囊肿|结石|骨折|积液)", text)
    if m:
        return _cn_to_int(m.group(1))
    return None


def _has_marker_unnegated(text: str, markers) -> bool:
    """文本中是否存在未受否定修饰的标记词（用于良恶性判定，避免『未见恶性』误判为恶性）。"""
    if not text:
        return False
    for sent in _split_sentences(text):
        s = sent.lower()
        if not any(k.lower() in s for k in markers):
            continue
        # 2026-08-18 修复：『不除外/不考虑/待排』是阳性倾向措辞（临床强证据），
        # 不能当否定跳过——否则『右肾占位，不除外恶性』+结论『考虑良性』的真矛盾漏检。
        # 仅真正的阴性（未见/未示/除外=排除）才算否定。
        # 2026-08-24 修复：用负向前瞻排除"不除外"中的"除外"子串
        if re.search(r"不除外|不考虑|待排", sent):
            return True  # 阳性倾向，直接判定为存在标记
        if re.search(r"未见|未示|未见明显|未见可疑|未发现|不伴|除外|未见\s*明确", sent):
            continue
        return True
    return False
