"""
engine_helpers.py — 跨规则共享的辅助函数与知识图谱薄封装（P2 拆分自 engine.py）
=============================================================================
本模块是规则分组的公共底层：只依赖 _lexicons 词表，**禁止**反向依赖
rules_* / engine_* 模块，保证依赖单向无环。

内容：
- _KG：GENDER_ORGANS / MODALITY_SCORE / SITE_NORM 的薄封装（RuleEngine.kg）
- 侧别系列：_norm_laterality / _detect_side_in_text / _project_side（R11）
- 正常/阳性声明：_claims_normal / _has_positive / _word_effectively_present
  （R9/R12/R15/R17/R18/R20）
- 句子切分与计数：_split_sentences / _cn_to_int / _extract_lesion_count /
  _has_marker_unnegated（R9/R12/R14/R15）
- 器官左右侧：_ORGAN_COMPOUND / _organ_sides_in_text / _organ_sides_en（R14/R15）
- 性别归一：_zh / _norm_gender / _parse_gender_from_text（R1/R11/R21 + 元信息抽取）
"""
import re
from typing import Optional

from _lexicons import *  # noqa: F401,F403

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "双": 2, "单": 1}


def _norm_laterality(s):
    """把侧别写法归一化为 'left'/'right'/'bilateral'；无法识别返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    if s in LATERALITY:
        return LATERALITY[s]
    # 兼容口语/书面写法：左侧/右侧/双侧/两边/左右
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
    # 英文报告（MIMIC-CXR / IU-Xray 风格）：left/right/bilateral 关键词
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

    优先级：① 显式 meta.laterality（SPA 侧别下拉）>
    ② 报告内显式标签字段（如『检查项目：右膝』『检查部位：左肺』）>
    ③ meta.applied_site（检查部位自由文本，如『右膝关节』）。

    之前 R11-SIDE 只取 ①，用户常把侧别写在 项目 自由文本里而不填独立下拉框，
    导致 meta.laterality 为空、规则整条跳过，无法核对 项目 vs 描述/诊断 左右。
    ② ③ 兜底让规则在常见录入方式下都能正确启动。
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


def _organ_sides_en(text: str, aliases) -> set:
    """英文器官左右检测：返回文本中某器官的方位集合（left/right）。
    语序兼容 'left lung' 与 'lung left'，并允许侧别词与器官间有少量修饰词
    （如 'right lower lobe'）。仅在含英文的报告中使用，不影响中文。"""
    if not text:
        return set()
    sides = set()
    low = text.lower()
    for a in aliases:
        if (re.search(r"\bleft\b.{0,30}?\b" + re.escape(a), low)
                or re.search(r"\b" + re.escape(a) + r"\b.{0,30}?\bleft\b", low)):
            sides.add("left")
        if (re.search(r"\bright\b.{0,30}?\b" + re.escape(a), low)
                or re.search(r"\b" + re.escape(a) + r"\b.{0,30}?\bright\b", low)):
            sides.add("right")
    return sides


def _claims_normal(text: str) -> bool:
    """文本是否明确声明『未见异常/正常』（用于描述-结论矛盾）。
    注：中文无词边界，采用子串匹配；NORMAL_CLAIM 均为强特异性表述，误命中风险低。
    2026-08-05 起补充『部位+正常』精准识别：仅当『正常』紧跟解剖部位/区域词时
    才判为正常声明，避免『形态正常』『结构正常』『密度正常』『信号正常』等误报
    （这正是此前『小脑正常→结论小脑软化灶』漏检的根因）。"""
    if not text:
        return False
    if any(k in text for k in NORMAL_CLAIM):
        return True
    return bool(_REGION_NORMAL_RE.search(text))


def _has_positive(text: str) -> bool:
    """文本是否包含强阳性征（异常表现）。
    注：中文无词边界，采用子串匹配；POSITIVE_STRONG 均为强特异性词（占位/结节/癌…），
    误命中风险低。『未见/未见明显/无/不伴…』等否定前缀后的阳性征词不算异常，
    避免『未见明显炎症』『无增生』被误判（参考 _NEG_PREFIXES）。如需更严谨可改为句级判定。"""
    if not text:
        return False
    for k in POSITIVE_STRONG:
        idx = text.find(k)
        while idx != -1:
            pre = text[max(0, idx - 5): idx]
            if not any(pre.endswith(neg) for neg in _NEG_PREFIXES):
                return True
            idx = text.find(k, idx + 1)
    return False


def _word_effectively_present(target: str, word: str) -> bool:
    """word 在 target 中是否有『未被否定前缀修饰』的出现（即真正积极出现）。

    用于 R9 矛盾检测豁免：『未见占位』中『占位』被『未见』否定，不算真正出现，
    故『未见 vs 占位』矛盾对不触发（正常阴性描述）。与 _has_positive 的否定语义一致。
    """
    if not word:
        return False
    idx = target.find(word)
    while idx != -1:
        pre = target[max(0, idx - 5): idx]
        if not any(pre.endswith(neg) for neg in _NEG_PREFIXES):
            return True
        idx = target.find(word, idx + 1)
    return False


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
    """抽取文本中明确的病灶计数（枚/个），无法判定（如『多发/数枚』）返回 None。"""
    if not text:
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
        if re.search(r"未见|未示|未见明显|不除外|不考虑|除外|待排|未见\s*明确", sent):
            continue
        return True
    return False


# 复合器官（含短名），避免短名被复合词误命中（如「肾上腺」误判为「肾」的左右）。
# 注意：肝左叶/肝右叶 是真实的左右叶矛盾来源，必须保留比对，故不入此排除列表。
_ORGAN_COMPOUND = {
    "肾": ["肾上腺", "肾盂", "肾盏", "肾窦", "肾门"],
    "肝": ["肝胆"],
}


def _organ_sides_in_text(text: str, organ: str) -> set:
    """返回文本中提及某器官的方位集合（left/right）。兼容『左肺』与『肝左叶』两种语序。
    先剔除复合器官名（如肾上腺/肝胆），避免短名（肾/肝）被复合词误命中而产生假阳性左右矛盾。"""
    sides = set()
    tmp = text
    for comp in _ORGAN_COMPOUND.get(organ, []):
        tmp = tmp.replace(comp, "")
    if (re.search(r"左\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*左", tmp)):
        sides.add("left")
    if (re.search(r"右\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*右", tmp)):
        sides.add("right")
    return sides


def _zh(g: str) -> str:
    return {"male": "男", "female": "女"}.get(g, g)


def _norm_gender(g) -> Optional[str]:
    """把各种性别写法归一化为 'male'/'female'；无法识别返回 None。"""
    if not g:
        return None
    s = str(g).strip().lower()
    if s in ("male", "男", "男性", "m", "boy"):
        return "male"
    if s in ("female", "女", "女性", "f", "girl"):
        return "female"
    return None


def _parse_gender_from_text(text: str) -> Optional[str]:
    """从报告正文推断性别。策略：
      1) 优先匹配显式字段：性别：男 / 性别 女 / 男性 / 女性
      2) 其次匹配常见病史写法：男，45岁 / 女 32Y / M,45 / F 32
    仅在证据明确时返回，避免误判。OCR 形近字（另≈男、文/久≈女）一并归一。
    """
    if not text:
        return None
    _gn = {"男": "male", "另": "male", "女": "female", "文": "female", "久": "female"}
    # 显式字段（容忍全角空格）
    m = re.search(r"(?:性\s*别|患\s*者|受\s*检\s*者)[:：\s　]*([男女另文久])", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    m = re.search(r"([男女另文久])性", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    # 病史写法：性别 + 紧邻年龄（岁/Y/y/歲或逗号后数字），兼容『男性，45岁』
    m = re.search(r"[，,、\s：:　]([男女另文久])\s*性?\s*[，,、]?\s*\d{1,3}\s*[岁YyＹ歲]", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    m = re.search(r"\b([MFmf])\s*[,，]?\s*\d{1,3}\b", text)
    if m:
        return "male" if m.group(1).lower() == "m" else "female"
    # 英文整词：Male / Female（无紧邻数字的病史写法，如『Patient：ZHANG SAN  Male 45Y』）
    m = re.search(r"\b(male|female)\b", text, re.IGNORECASE)
    if m:
        return "male" if m.group(1).lower() == "male" else "female"
    return None


class _KG:
    """知识图谱薄封装：GENDER_ORGANS / MODALITY_SCORE / SITE_NORM 的查询接口。
    被 R1（性别矛盾）、R3（评分缺失）、R6（登记部位）、R7（描述内部矛盾）使用。"""

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
