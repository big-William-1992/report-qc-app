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

ORGAN_SIDE_LIST = [o for o in SIDE_CHECK_ORGANS if o not in R2_COVERED]
# ORGAN_SIDE_LIST_INTERNAL 已删除（2026-08-18）：仅供已删除的 R15-SIDE 段内比对使用，
# 左右矛盾统一由 R2（跨段）/ R17（逐部位）负责。

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


# 否定前缀与阳性词之间的判定正则：否定词后允许少量修饰间隔（如『实质性』『明显』），
# 但不允许跨逗号/句号/分号/顿号/换行（避免把前一分句的『无』错配到后一分句的阳性词）。
# 修复点（2026-08-18）：旧逻辑用 pre.endswith(neg) 只看紧邻结尾，『未见实质性病变』中
# 『病变』前 5 字为『未见实质』无法 endswith『未见』→ 误判阳性 → 纯正常报告误报 R17。
_NEG_BEFORE_POS_RE = re.compile(
    r"(?:" + "|".join(re.escape(n) for n in sorted(_NEG_PREFIXES, key=len, reverse=True))
    + r")[^，。；;、\n]{0,8}$"
)


def _is_negative_claim(text: str) -> bool:
    """文本是否含『否定前缀 + 阳性征词』的阴性声明（如『未见实质性病变』『无占位』『未见明显异常信号』）。

    与 _has_positive 互补：前者找"未被否定的阳性词"（真阳性），本函数找"被否定掉的阳性词"
    （作者明确声明无该征象）。用于段级『全局正常』判定与 R17 段级兜底，使
    『描述整段阴性声明 + 结论阳性诊断』能正确报出『描述正常结论异常』矛盾。"""
    if not text:
        return False
    for k in POSITIVE_STRONG:
        idx = text.find(k)
        while idx != -1:
            pre = text[max(0, idx - 12): idx]
            if _NEG_BEFORE_POS_RE.search(pre):
                return True
            idx = text.find(k, idx + 1)
    return False


def _has_positive(text: str) -> bool:
    """文本是否包含强阳性征（异常表现）。
    注：中文无词边界，采用子串匹配；POSITIVE_STRONG 均为强特异性词（占位/结节/癌…），
    误命中风险低。『未见/未见明显/无/不伴…』等否定前缀后的阳性征词不算异常，
    避免『未见明显炎症』『未见实质性病变』『无增生』被误判（参考 _NEG_PREFIXES）。"""
    if not text:
        return False
    for k in POSITIVE_STRONG:
        idx = text.find(k)
        while idx != -1:
            pre = text[max(0, idx - 12): idx]
            if not _NEG_BEFORE_POS_RE.search(pre):
                return True
            idx = text.find(k, idx + 1)
    return False


def _word_effectively_present(target: str, word: str) -> bool:
    """word 在 target 中是否有『未被否定前缀修饰』的出现（即真正积极出现）。

    用于 R9 矛盾检测豁免：『未见占位』中『占位』被『未见』否定，不算真正出现，
    故『未见 vs 占位』矛盾对不触发（正常阴性描述）。与 _has_positive 的否定语义一致
    （否定前缀 + 允许间隔修饰词 + 不跨标点，见 _NEG_BEFORE_POS_RE）。"""
    if not word:
        return False
    idx = target.find(word)
    while idx != -1:
        pre = target[max(0, idx - 12): idx]
        if not _NEG_BEFORE_POS_RE.search(pre):
            return True
        idx = target.find(word, idx + 1)
    return False


# 2026-08-24 性能优化：预编译 _organ_asserted 使用的否定后缀正则
_NEG_AFTER_COMPILED = re.compile(
    r"[^，。；;、\n]{0,4}("
    + "|".join(re.escape(n) for n in sorted(_NEG_PREFIXES, key=len, reverse=True))
    + r")")


def _organ_asserted(text: str, organ: str) -> bool:
    """organ 在 text 中是否有『未被否定修饰』的真实出现（与 R9/R17 否定口径统一）。

    处理两种语序：① 前否定『未见前列腺』（否定前缀在前）；② 后否定『前列腺未见异常』
    （否定前缀紧接器官之后 ≤4 字内）。用于 R1/R12 跨性别器官告警豁免——
    女性盆腔报告写『前列腺区未见异常』属合法否定表述，不应升为 high 级矛盾。"""
    if not text or not organ:
        return False
    # 2026-08-24 性能优化：将正则编译提升到模块级别（避免每次调用重编译）
    _neg_after = _NEG_AFTER_COMPILED
    idx = text.find(organ)
    while idx != -1:
        pre = text[max(0, idx - 12):idx]
        post = text[idx + len(organ): idx + len(organ) + 12]
        neg = bool(_NEG_BEFORE_POS_RE.search(pre)) or bool(_neg_after.search(post))
        if not neg:
            return True
        idx = text.find(organ, idx + 1)
    return False


# 跨规则去重（2026-08-19）：同一事实被多条规则从不同角度重复告警时，
# 仅保留「主规则」产出的一条，抑制同组其他冗余来源，避免同一矛盾在 UI 刷屏。
# 分组依据为语义同根，而非简单按 rule_id 去重：
#   consistency —— R5(描述-结论器官一致性) 与 R17(逐部位描述↔结论) 同述描述-结论矛盾，
#                  设计上 R17 已统一接管描述-结论一致性产出，故主规则为 R17-PERREGION。
#   site        —— R6(登记部位错配) 与 R18(区域器官漏写) 同述「申请部位与正文不符」，
#                  主规则取更高严重度、语义更直接的 R6-SITE。
#   nature      —— R9(用户自定义互斥) 与 R14(良恶性定性矛盾) 同述良恶性冲突，
#                  主规则取内置专用的 R14-NATURE。
# 仅当组内主规则存在时才抑制同组其他来源；主规则单独出现（无冗余）时不误删。
_DEDUP_GROUPS = {
    "consistency": {"R5-CONSISTENCY", "R17-PERREGION"},
    "site": {"R6-SITE", "R18-COVERAGE"},
    "nature": {"R9-CONFLICT", "R14-NATURE"},
}
_DEDUP_PRIMARY = {
    "consistency": "R17-PERREGION",
    "site": "R6-SITE",
    "nature": "R14-NATURE",
}


def _dedup_findings(findings: list) -> list:
    """按语义同根分组抑制冗余告警；返回去重后的 findings（保持原序）。"""
    by_group = {}
    for f in findings:
        g = next((k for k, v in _DEDUP_GROUPS.items() if f.rule_id in v), None)
        if g is None:
            continue
        by_group.setdefault(g, []).append(f)
    drop = set()
    for g, fs in by_group.items():
        primary = _DEDUP_PRIMARY[g]
        if not any(f.rule_id == primary for f in fs):
            continue  # 仅冗余来源单独出现，不抑制
        for f in fs:
            if f.rule_id != primary:
                drop.add(id(f))
    return [f for f in findings if id(f) not in drop]


def _r12_same_region(sent: str) -> bool:
    """R12 句级矛盾辅助：判断句中『部位+正常』与阳性征是否指向同一部位。

    - 相同部位（如『左肺见结节，左肺正常』）→ 真自相矛盾，返回 True。
    - 不同部位（如『右肺见结节，左肺正常』的对称描述）→ 不判矛盾，返回 False。
    对每个阳性征，取其之前最近的部位提及与『部位+正常』的提及比对。
    """
    m = _REGION_NORMAL_RE.search(sent)
    if not m:
        return False
    norm_s, norm_e = m.start(), m.end()          # 『部位正常』区间
    region_spans = _region_spans_in_text(sent)   # [(key, side, start, end)]
    # 正常声明所对应的部位提及
    normal_spans = [sp for sp in region_spans if sp[2] >= norm_s and sp[3] <= norm_e]
    # 收集未被否定的阳性征位置（统一采用全引擎的否定判定 _NEG_BEFORE_POS_RE，
    # 与 _has_positive / _is_negative_claim / _word_effectively_present 保持一致；
    # 旧逻辑用 pre.endswith(neg) 仅看紧邻结尾 5 字，无法识别『未见实质性病变』式
    # 带修饰间隔的否定，会导致异侧/带修饰词的阳性征误判或漏判）
    pos_idx = []
    for k in POSITIVE_STRONG:
        i = sent.find(k)
        while i != -1:
            pre = sent[max(0, i - 12): i]
            if not _NEG_BEFORE_POS_RE.search(pre):
                pos_idx.append(i)
            i = sent.find(k, i + 1)
    if not pos_idx:
        return False
    for pi in pos_idx:
        before = [sp for sp in region_spans if sp[3] <= pi]
        if not before:
            # 无部位限定词：若句内确有部位+正常声明，保守视为同部位（维持判矛盾）
            if normal_spans:
                return True
            continue
        nearest = max(before, key=lambda sp: sp[3])   # 阳性征前最近的部位
        if any(sp[0] == nearest[0] and sp[1] == nearest[1] for sp in normal_spans):
            return True
    return False


_ORGAN_COMPOUND = {
    "肾": ["肾上腺", "肾盂", "肾盏", "肾窦", "肾门"],
    "肝": ["肝胆"],
    "肺": ["肺门", "肺野", "肺尖", "肺实质", "肺底", "肺间质"],  # 2026-08-18：肺门等复合词此前未剔除 → "右肺门"被当"肺"侧别误报 R2
}


def _organ_sides_in_text(text: str, organ: str) -> set:
    """返回文本中提及某器官的方位集合（left/right）。兼容『左肺』『左侧肺』与『肝左叶』两种语序。
    先剔除复合器官名（如肾上腺/肝胆/肺门），避免短名（肾/肝/肺）被复合词误命中而产生假阳性左右矛盾。
    2026-08-18 修复：容忍『左侧X』措辞（放射科最常见写法），此前『左+器官』正则遇"侧"字即阻断，
    『左侧肾上腺见占位/右侧肾上腺见占位』的跨段左右矛盾整体漏检。"""
    sides = set()
    tmp = text
    for comp in _ORGAN_COMPOUND.get(organ, []):
        tmp = tmp.replace(comp, "")
    if (re.search(r"左\s*(?:侧)?\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*(?:侧)?\s*左", tmp)):
        sides.add("left")
    if (re.search(r"右\s*(?:侧)?\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*(?:侧)?\s*右", tmp)):
        sides.add("right")
    return sides

# 放射报告常见同音/近音错别字兜底词典已外置至 typo_lexicon.py（单一数据源，2026-08-23
# 去重 47 个重复键后迁出）。此处 import 并保留同名 re-export，维持
# `from engine import TYPO_MAP_DEFAULT`（如 tools/cn_error_synth.py）对外兼容。
from typo_lexicon import TYPO_MAP_DEFAULT  # noqa: F401

# R8 上下文安全闸（2026-08-22）：下列错词的「错写」本身也是常见合法字/词
# （如『坐/又/费/纵/前/子/卵』等），在无关语境下易误命中。仅当错词周围 ±16 字内
# 出现任一放射语境词（解剖/方位/征象/检查/测量）时才放行，避免把『坐火车』『又肺部』
# 这类偶发组合误报为错别字。其余『错写本身几乎不可能作为正确词出现』的条目
# （姐节/战位/结解/病造…）不进此表，保持高检出零误报。
TYPO_CONTEXT_REQUIRED = {
    "坐肺", "又肺", "由肺", "费炎", "费部", "废部", "纵哥", "纵膈", "纵阁",
    "前裂腺", "前例腺", "前腺", "子官", "字官", "字宫", "卵曹", "卵槽",
    "申状腺", "申壮腺", "腮线", "腮泉", "骨拆", "食官", "食到", "食通",
}

# 放射语境锚定词（供 TYPO_CONTEXT_REQUIRED 判定）；取自解剖/方位/征象/检查/测量高频词。
_TYPO_CTX_TOKENS = (
    list(ANATOMY_SYNONYMS.keys()) + list(GENDER_ORGANS.keys())
    + ["左", "右", "双侧", "上叶", "下叶", "上肺", "下肺", "见", "示", "可见",
       "占位", "结节", "肿块", "肿物", "囊肿", "钙化", "增生", "强化", "增强",
       "密度", "信号", "cm", "mm", "CT", "MR", "超声", "检查", "影像", "描述",
       "诊断", "报告", "正常", "异常", "纹理", "征象", "模糊", "清晰", "毛糙"]
)

def _typo_in_medical_context(text: str, s: int, e: int) -> bool:
    """错词 (s,e) 周围 ±16 字是否含放射语境锚定词。"""
    win = text[max(0, s - 16): e + 16]
    return any(tok in win for tok in _TYPO_CTX_TOKENS)


# R19 安全词表（2026-08-22，降误报）：下列常见中文词与某医学高频词「完全同音」
# （如『印象/影响』↔影像、『姐姐』↔结节、『造型』↔造影、『现象/想象』↔显像、
# 『两性』↔良性、『事变』↔实变、『解释』↔结石、『边远』↔边缘、『鲜味』↔纤维…）。
# 中文同音字极多，R19 的 exact（同音）命中会把这些合法用词误判为错字。本表
# 列出确为常见合法词、且与医学词同音的串，exact 命中时直接放行（不报）。
# 注意：本表绝不收录 TYPO_MAP_DEFAULT 中的错写（那些确是错字，须照常报）。
# 用户可在 rules_config.json 的 r19_safe_words 追加机构特有安全词。
R19_SAFE_WORDS = {
    "印象", "影响", "姐姐", "转义", "转椅", "造型", "现象", "想象",
    "两性", "量性", "事变", "边远", "鲜味", "解释", "揭示", "接受",
    "结束", "已经", "一线", "自供", "乱抄", "古哲", "魔狐", "揭示",
    "器官",  # 常见词，与「气管」(qìguǎn) 完全同音，极易误报
} - set(TYPO_MAP_DEFAULT.keys())  # 确保不与错别字表冲突（错别字优先）

# 规则配置文件路径（与 samples.db 同目录：assets/rules_config.json）
# 兼容 PyInstaller 打包：资源根统一走 app_paths.frozen_resource_dir
# （PyInstaller 6 onedir 下 datas 在 _MEIPASS/<app>/_internal，不在 exe 目录）。
try:
    import app_paths
except ImportError:  # 兼容 from src import engine 的包式导入
    from . import app_paths  # type: ignore

