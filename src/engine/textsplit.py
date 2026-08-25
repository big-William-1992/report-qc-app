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

_ZH_ENT_LABEL_MAP = {"征象": "sign", "随访": "followup", "程度": "degree"}

# 段落标题共享常量（2026-08-18 H9 修复）：NER SECTION_MAP 与 _split_for_r5 必须同源，
# 避免「影像所见/超声所见/CT所见/MRI所见/MR所见」等最常见描述段标题只在一处生效，
# 导致 R5/R2/R12 在部分报告上静默漏检。修改标题集合只改这里。
_FINDINGS_HEADERS = (
    r"影像所见|影像描述|检查所见|超声所见|CT所见|MRI所见|MR所见|"
    r"表现|imaging findings|findings|radiographic findings"
)
_IMPRESSION_HEADERS = (
    r"影像诊断|诊断印象|印象|诊断意见|诊断结论|影像结论|结论|"
    r"impression|diagnosis"
)

# 段落切分单一数据源（2026-08-21 架构收敛）：NER._split_sections 与 R5._split_for_r5
# 共用同一 (正则, section) 列表，杜绝「标题集合各处各写一遍」导致 R2/R5/R17 跨段比对
# 静默破坏。修改段落标题只改 _FINDINGS_HEADERS/_IMPRESSION_HEADERS 两处即可。
_SECTION_SPANS_SRC = [
    (re.compile(_FINDINGS_HEADERS, re.I), "findings"),
    (re.compile(_IMPRESSION_HEADERS, re.I), "impression"),
    (re.compile(r"患者信息|患者|性别|年龄|检查部位|申请"), "meta"),
]

def _sectionize(text: str) -> list:
    """按段落标题把 text 切成 (section, start, end) 区间列表（单一实现）。
    供 NER 实体标注与 R5 描述/印象切分共用；REF：NER._split_sections 的语义。"""
    spans = []
    for pat, sec in _SECTION_SPANS_SRC:
        for m in pat.finditer(text):
            spans.append((m.start(), sec))
    spans.sort()
    sections = []
    for i, (start, sec) in enumerate(spans):
        end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        sections.append((sec, start, end))
    if not sections:
        sections = [("findings", 0, len(text))]
    return sections




# ---- 整屏 OCR 文本流的「行级」三段切分（2026-08-23 自 server/main.py 收敛至此）----
# 与上方 _SECTION_SPANS_SRC 的分工：_SECTION_SPANS_SRC 是对连续正文做字符级正则
# 切分（服务 NER/R5 跨段比对），标题词均为长且明确的词组；此处面向 OCR 整屏文本
# 流按行匹配短标题词（如「描述」「诊断」单独出现），若并入字符级数据源会把正文
# 中的高频词误判为段起点、破坏 R5/R17 区间，故保持独立表、同文件单一实现。
# 消费方：server /api/v1/qc/ocr-dynamic（动态模式整屏 OCR → basic/findings/impression）。
_DYNAMIC_FINDINGS_TITLES = ("检查所见", "影像所见", "影像描述", "所见", "检查描述", "描述")
_DYNAMIC_IMPRESSION_TITLES = ("诊断印象", "影像诊断", "诊断意见", "诊断结论", "印象", "结论", "诊断")
_DYNAMIC_BASIC_TITLES = ("患者", "病人", "姓名", "检查号", "影像号", "登记")


def _strip_dynamic_title(line: str, pats) -> str:
    """剥离行首的段落标题词，保留正文。如『检查所见：双肺纹理增多』→『双肺纹理增多』。
    标题可能后接中文冒号/空格/顿点；也可能标题在行中（罕见），统一只剥行首。"""
    s = line.strip()
    for p in sorted(pats, key=len, reverse=True):
        if s.startswith(p):
            rest = s[len(p):].lstrip("：:：: .、\t")
            return rest.strip()
        # 兼容『所见：』『描述 :』等带空格的标题写法
        if s.startswith(p + " ") or s.startswith(p + "："):
            rest = s[len(p):].lstrip(" ：: .、\t")
            return rest.strip()
    return s


def split_dynamic(full: str):
    """动态模式：整屏 OCR 文本流 → 按标题切分三区。
    顺序遍历所有行，命中标题关键词的行作为段起点：
      basic=患者信息 / findings=检查所见·影像描述 / impression=诊断印象·影像诊断·结论
    若找不到某标题，则该段并入相邻段或留空，由 extract_meta_full / 前端兜底。
    返回 (texts, errors)，texts 键为 basic/findings/impression。"""
    texts = {"basic": "", "findings": "", "impression": ""}
    errors = {}
    lines = [ln for ln in (full or "").splitlines() if ln.strip()]
    if not lines:
        return texts, errors
    # 找各段标题行下标（首个命中）
    def _first_idx(pats):
        for i, ln in enumerate(lines):
            for p in pats:
                if p in ln:
                    return i
        return -1
    f_idx = _first_idx(_DYNAMIC_FINDINGS_TITLES)
    i_idx = _first_idx(_DYNAMIC_IMPRESSION_TITLES)
    b_idx = _first_idx(_DYNAMIC_BASIC_TITLES)
    # 修正：诊断标题若出现在描述标题之前（PACS 常把「诊断」列在患者信息区），
    # 以描述标题为基准重排——取描述之后首个诊断标题。
    if f_idx >= 0 and i_idx >= 0 and i_idx < f_idx:
        for j in range(f_idx, len(lines)):
            if any(p in lines[j] for p in _DYNAMIC_IMPRESSION_TITLES):
                i_idx = j
                break
    # basic：起始（或患者标题）→ 描述标题（或诊断标题）
    # 注意：若「患者」标题出现在描述/诊断之后（部分 PACS 布局），b_start > end，
    # 直接取起始段即可（lines[0:end]），避免 basic 被截成空串。
    # 2026-08-24 修复：b_start 应在 basic 区域内（不超 f_idx），防止混入 findings 内容
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
    elif i_idx >= 0:
        end = i_idx
    else:
        end = len(lines)
    b_start = b_idx if 0 <= b_idx < min(end, f_idx if f_idx >= 0 else end) else 0
    texts["basic"] = "\n".join(lines[b_start:end]).strip()
    # findings：从描述标题行开始（含该行正文，标题词被剥掉）→ 诊断标题行前
    # 注意跳过中间的患者标题行（部分 PACS 布局把患者信息插在描述段里），
    # 避免「患者：张三」等 basic 内容混入描述正文。
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
        head = _strip_dynamic_title(lines[f_idx], _DYNAMIC_FINDINGS_TITLES)
        body = [head]
        for ln in lines[f_idx + 1:end]:
            if b_idx >= 0 and b_idx != f_idx and any(p in ln for p in _DYNAMIC_BASIC_TITLES):
                continue
            body.append(ln)
        texts["findings"] = "\n".join(body).strip()
    # impression：从诊断标题行开始（含该行正文，标题词被剥掉）→ 末尾
    if i_idx >= 0:
        head = _strip_dynamic_title(lines[i_idx], _DYNAMIC_IMPRESSION_TITLES)
        body = [head] + [ln for ln in lines[i_idx + 1:]]
        texts["impression"] = "\n".join(body).strip()
    return texts, errors


def _is_common_word(w: str) -> bool:
    """错词是否为高频合法词（2026-08-18 H6）：R8 词条 wrong 命中高频白名单
    （如「有肺/直接/结界」这类本身是常用表述的词）视为疑似误录，
    拒绝 learn_typo 入库、auto_fix 自动替换——防一键采纳把报告语义改写。
    依赖 highfreq_lexicon；词库缺失时保守返回 False（不阻断既有流程）。"""
    if not w or len(w) < 2 or len(w) > 8:
        return False
    try:
        from highfreq_lexicon import highfreq_words as _hf
        return w in {x for x, _ in _hf()}
    except Exception:
        return False


# ----------------------------- 数据模型 -----------------------------