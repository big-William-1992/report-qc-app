"""中文放射征象同义词归一表（项目自建，放射科医师维护）
================================================================================
填补 catalog 中「全英文术语 + RadLex 少量 zh 别名」之不足：把中文报告里口语化 /
语音录入 / 地区习惯写法归一为规范 token，供质控规则（R6 部位、R14 侧别、R11
上下文、以及中文临床 NER）更稳地匹配。

设计原则
--------
1. **纯数据 + 纯函数，零第三方依赖**，便于单测与 PyInstaller 打包（与
   anatomy_lexicon.py 同风格）。
2. **只做「口语 → 规范词」替换，绝不删除信息**：normalize_text() 把
   『磨玻璃密度影』替成『磨玻璃影』，原方位/器官 token 保持不动，因此叠加在
   现有规则上不会破坏既有匹配（纯增益预通道）。
3. 词典与 assets/rules_config.json 解耦：rules_config 存用户可改的错别字
   （TYPO_MAP），本文件存「同义规范」映射（增殖灶→增殖灶、斑片影→斑片影…），
   两者职责不同。

数据来源
--------
- 放射科常见中文征象命名习惯（医师经验）；
- RadLex 少量 zh 别名 + 中文诊断学教材命名；
- 与 src/zh_ner.py 联动：NER 抽取出的征象实体先经本表归一，再喂给规则。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 1. 征象同义词：口语/异写 → 规范 token
# ---------------------------------------------------------------------------
# 键为「可能是异写/口语」的词，值为规范 token。匹配时按键长降序，避免
# 『磨玻璃』误命中『磨玻璃密度影』（后者更长优先）。
SIGN_SYNONYMS: Dict[str, str] = {
    # —— 磨玻璃类 ——
    "磨玻璃密度影": "磨玻璃影",
    "磨玻璃影": "磨玻璃影",
    "磨玻璃结节": "磨玻璃影",
    "磨玻璃": "磨玻璃影",
    "ggo": "磨玻璃影",
    "ggn": "磨玻璃影",
    # —— 斑片/实变类 ——
    "斑片状影": "斑片影",
    "斑片状": "斑片影",
    "斑片影": "斑片影",
    "斑片": "斑片影",
    "片状影": "斑片影",
    "片状密度增高影": "斑片影",
    # —— 索条/纤维类 ——
    "索条状影": "索条影",
    "索条状": "索条影",
    "条索影": "索条影",
    "条索状": "索条影",
    "索条影": "索条影",
    "索条": "索条影",
    "纤维条索影": "索条影",
    "纤维灶": "纤维灶",
    "纤维化灶": "纤维灶",
    "肺纤维化": "纤维化",
    "纤维化": "纤维化",
    # —— 结节/占位/肿块 ——
    "小结节": "结节",
    "微小结节": "结节",
    "结节影": "结节",
    "结节": "结节",
    "占位灶": "占位",
    "占位病变": "占位",
    "占位": "占位",
    "肿块影": "肿块",
    "肿块": "肿块",
    "肿物": "肿块",
    "团块": "肿块",
    # —— 钙化/空洞/渗出 ——
    "钙化灶": "钙化",
    "钙化": "钙化",
    "空洞形成": "空洞",
    "空洞": "空洞",
    "空腔": "空洞",
    "渗出影": "渗出",
    "渗出性病变": "渗出",
    "渗出": "渗出",
    "浸润影": "浸润",
    "浸润": "浸润",
    # —— 积液/气胸/不张 ——
    "胸腔积血": "胸腔积液",
    "胸腔积液": "胸腔积液",
    "胸水": "胸腔积液",
    "心包积液": "心包积液",
    "气胸": "气胸",
    "肺不张": "肺不张",
    "段不张": "肺不张",
    "亚段不张": "肺不张",
    # —— 增殖/炎性/其他 ——
    "增殖灶": "增殖灶",
    "增殖性病变": "增殖灶",
    "炎性病变": "炎性病变",
    "慢性炎症": "炎性病变",
    "慢性炎症灶": "炎性病变",
    "粟粒样结节": "结节",
    "网格影": "网格影",
    "蜂窝影": "蜂窝影",
    "支气管扩张": "支气管扩张",
    "支扩": "支气管扩张",
    "纵隔影增宽": "纵隔增宽",
    "纵隔增宽": "纵隔增宽",
    "心影增大": "心影增大",
    "心影饱满": "心影增大",
    "主动脉迂曲": "主动脉迂曲",
    "主动脉硬化": "主动脉硬化",
    "主动脉增宽": "主动脉增宽",
}

# 规范 token 全集（用于快速判断某词是否已是规范征象）
SIGN_CANONICAL: Set[str] = set(SIGN_SYNONYMS.values())

# ---------------------------------------------------------------------------
# 2. 程度词：少量/中度/大量 → 规范程度
# ---------------------------------------------------------------------------
DEGREE_SYNONYMS: Dict[str, str] = {
    "少许": "轻度",
    "少量": "轻度",
    "轻度": "轻度",
    "散在": "轻度",
    "局限性": "轻度",
    "中等量": "中度",
    "中度": "中度",
    "多发": "多发",
    "大量": "重度",
    "重度": "重度",
    "显著": "重度",
    "广泛": "重度",
}

# ---------------------------------------------------------------------------
# 3. 随访 / 复查 同义词与时限归一
# ---------------------------------------------------------------------------
FOLLOWUP_SYNONYMS: Dict[str, str] = {
    "定期复查": "随访",
    "定期随诊": "随访",
    "随诊": "随访",
    "随访": "随访",
    "建议复查": "复查",
    "复查": "复查",
    "复诊": "复查",
    "动态观察": "随访",
    "密切随访": "随访",
}

# 随访时限 → 归一月数（用于 R10 细化：有随访建议但无明确时限）
# 只覆盖明确数字写法；『定期/长期』等模糊时限返回 None（由规则另行处理）。
_FOLLOWUP_TIMEFRAME_PATTERNS: List[Tuple[str, Optional[int]]] = [
    (r"(\d+)\s*个?\s*月", None),          # 月份占位，需动态求值
    (r"半年|6\s*个?\s*月", 6),
    (r"三个月|3\s*个?\s*月|三月", 3),
    (r"一年|1\s*年|12\s*个?\s*月|一年后", 12),
    (r"两年|2\s*年|24\s*个?\s*月", 24),
]


# ---------------------------------------------------------------------------
# 归一函数
# ---------------------------------------------------------------------------
def _longest_first(d: Dict[str, str]) -> List[str]:
    """返回按键长降序的键列表（避免短词误命中长词前缀）。"""
    return sorted(d.keys(), key=len, reverse=True)


_SIGN_KEYS_DESC = _longest_first(SIGN_SYNONYMS)
_SIGN_RE = re.compile("|".join(re.escape(k) for k in _SIGN_KEYS_DESC))
_DEGREE_KEYS_DESC = _longest_first(DEGREE_SYNONYMS)
_DEGREE_RE = re.compile("|".join(re.escape(k) for k in _DEGREE_KEYS_DESC))
_FOLLOWUP_KEYS_DESC = _longest_first(FOLLOWUP_SYNONYMS)
_FOLLOWUP_RE = re.compile("|".join(re.escape(k) for k in _FOLLOWUP_KEYS_DESC))


def normalize_signs(text: str) -> Set[str]:
    """返回文本中出现的「规范征象 token」集合（已归一）。无则返回空集合。"""
    if not text:
        return set()
    out: Set[str] = set()
    for m in _SIGN_RE.finditer(text):
        out.add(SIGN_SYNONYMS[m.group(0)])
    return out


def normalize_text(text: str) -> str:
    """把文本中的口语/异写征象、程度、随访词归一为规范 token。

    纯替换、不删信息：原方位/器官/数字 token 保持不动，因此可安全作为质控规则
    的预通道（pre-pass）叠加在现有匹配之上。返回新字符串。
    """
    if not text:
        return ""
    # 顺序：征象 → 程度 → 随访（随访词可能含『复查』等，与征象无重叠）
    t = _SIGN_RE.sub(lambda m: SIGN_SYNONYMS[m.group(0)], text)
    t = _DEGREE_RE.sub(lambda m: DEGREE_SYNONYMS[m.group(0)], t)
    t = _FOLLOWUP_RE.sub(lambda m: FOLLOWUP_SYNONYMS[m.group(0)], t)
    return t


def extract_followup(text: str) -> Dict[str, object]:
    """抽取随访/复查意图与时限。

    返回 dict：
      - has_followup: bool（文本含『随访/复查/随诊』等）
      - timeframe_months: Optional[int]（明确数字时限；模糊/缺失为 None）
      - raw_timeframe: Optional[str]（命中的原始时限表述）
    """
    if not text:
        return {"has_followup": False, "timeframe_months": None, "raw_timeframe": None}
    has = bool(_FOLLOWUP_RE.search(text))
    tf_months: Optional[int] = None
    raw: Optional[str] = None
    # 动态求值的月份模式（允许结尾带『后/内』，如『3个月后』）
    m = re.search(r"(\d+)\s*个?\s*月\s*[后内]?", text)
    if m:
        try:
            tf_months = int(m.group(1))
            raw = m.group(0)
        except ValueError:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
    if tf_months is None:
        for pat, val in _FOLLOWUP_TIMEFRAME_PATTERNS:
            mm = re.search(pat, text)
            if mm:
                tf_months = val
                raw = mm.group(0)
                break
    return {
        "has_followup": has,
        "timeframe_months": tf_months,
        "raw_timeframe": raw,
    }


def canonical_sign(slang: str) -> Optional[str]:
    """单个词 → 规范 token；非已知征象返回 None。"""
    return SIGN_SYNONYMS.get(slang.strip())
