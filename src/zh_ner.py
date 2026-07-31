"""中文临床 NER（命名实体识别）—— 词典/规则式实现
================================================================================
从中文放射报告中抽取实体：部位 / 征象 / 程度 / 随访。

为什么是「词典式」而不是「模型」
--------------------------------
本产品定位**完全离线 + 单人开发**，而真正用 CBLUE / CCKS 训练、或加载 CMedKG
图谱推理需要：授权数据集下载 + 标注 + GPU 训练/推理，当前环境不具备。
因此第一版采用**词典/规则式** NER：复用 src/anatomy_lexicon.py（RadLex 器官族
中文别名）与 src/zh_radiology_synonyms.py（中文征象同义词）做最长匹配抽取。

可插拔设计（模型就绪后零改动切换）
----------------------------------
- ``NERBackend`` 抽象基类定义 ``extract(text) -> List[Entity]``；
- ``LexiconBackend`` 为当前默认实现；
- 将来用 CBLUE/CCKS 训练出中文医疗 NER 模型（或接 CMedKG 图谱推理）后，只需实现
  ``ModelBackend.extract`` 并经 ``set_backend()`` 切换，引擎其余代码不变。

实体类型
--------
- 部位 (organ)    ：来自 anatomy_lexicon 的 RadLex 器官族中文别名
- 征象 (sign)     ：来自 zh_radiology_synonyms 的中文征象同义词（已归一）
- 程度 (degree)   ：少量/中度/大量 等
- 随访 (followup) ：随访/复查/时限

零第三方依赖，便于单测与 PyInstaller 打包。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from anatomy_lexicon import ANATOMY
from zh_radiology_synonyms import (
    SIGN_SYNONYMS, DEGREE_SYNONYMS, FOLLOWUP_SYNONYMS,
)


@dataclass
class Entity:
    """一个抽取出的中文临床实体。"""
    text: str                 # 原文片段
    label: str                # 部位 / 征象 / 程度 / 随访
    start: int                # 在原文中的起始偏移
    end: int                  # 结束偏移（不含）
    canonical: Optional[str] = None   # 归一后的规范 token（部位=器官 key）
    side: Optional[str] = None        # 方位 left/right/None（仅部位）


# ----------------------- 构建词典索引（导入时一次） -----------------------
def _build_organ_index() -> Dict[str, str]:
    """器官中文别名 → 器官 key（RadLex 器官族）。长别名优先。"""
    idx: Dict[str, str] = {}
    # 先按别名长度降序处理，使较长别名后写入、不被短别名覆盖
    items = []
    for key, meta in ANATOMY.items():
        for zh in meta.get("zh", []):
            items.append((zh, key))
    for zh, key in sorted(items, key=lambda x: len(x[0]), reverse=True):
        idx[zh] = key
    return idx


_ORGAN_INDEX = _build_organ_index()
_ORGAN_KEYS_DESC = sorted(_ORGAN_INDEX.keys(), key=len, reverse=True)
_ORGAN_RE = re.compile("|".join(re.escape(k) for k in _ORGAN_KEYS_DESC))

_SIGN_KEYS_DESC = sorted(SIGN_SYNONYMS.keys(), key=len, reverse=True)
_SIGN_RE = re.compile("|".join(re.escape(k) for k in _SIGN_KEYS_DESC))

_DEGREE_KEYS_DESC = sorted(DEGREE_SYNONYMS.keys(), key=len, reverse=True)
_DEGREE_RE = re.compile("|".join(re.escape(k) for k in _DEGREE_KEYS_DESC))

_FOLLOWUP_KEYS_DESC = sorted(FOLLOWUP_SYNONYMS.keys(), key=len, reverse=True)
_FOLLOWUP_RE = re.compile("|".join(re.escape(k) for k in _FOLLOWUP_KEYS_DESC))

_SIDE_RE = re.compile(r"(左|右)\s*$")


def _organ_side(raw: str, text: str, start: int) -> Optional[str]:
    """探测器官实体的方位 left/right/None。

    方位可能出现在两处：
    1) 别名内部（如『左肺』『肝左叶』中的左/右）；
    2) 别名前的 1~2 字窗口（如『于左 肺上叶』）。
    两者都考虑，优先别名内部。
    """
    if "左" in raw:
        return "left"
    if "右" in raw:
        return "right"
    window = text[max(0, start - 2):start]
    m = _SIDE_RE.search(window)
    if m:
        return "left" if m.group(1) == "左" else "right"
    return None


def _scan(text: str, pattern, label: str, canonical_fn) -> List[Entity]:
    """通用扫描：对任一类别做最长匹配并生成实体。"""
    out: List[Entity] = []
    for m in pattern.finditer(text):
        s, e = m.start(), m.end()
        raw = m.group(0)
        canon = canonical_fn(raw)
        side = _organ_side(raw, text, s) if label == "部位" else None
        out.append(Entity(raw, label, s, e, canon, side))
    return out


def _non_max_suppress(ents: List[Entity]) -> List[Entity]:
    """去除被更长/更前实体完全覆盖的重叠实体（中文无词边界，避免短词嵌套告警）。"""
    order = sorted(ents, key=lambda x: (x.start, -(x.end - x.start)))
    kept: List[Entity] = []
    for e in order:
        if any(k.start <= e.start and e.end <= k.end and k is not e for k in kept):
            continue
        kept.append(e)
    return sorted(kept, key=lambda x: x.start)


class NERBackend(ABC):
    """NER 后端抽象接口（词典实现 / 模型实现共用）。"""

    @abstractmethod
    def extract(self, text: str) -> List[Entity]:
        """从文本抽取实体列表。"""
        raise NotImplementedError


class LexiconBackend(NERBackend):
    """词典/规则式实现（默认）。"""

    def extract(self, text: str) -> List[Entity]:
        if not text:
            return []
        ents: List[Entity] = []
        ents += _scan(text, _ORGAN_RE, "部位", lambda w: _ORGAN_INDEX.get(w))
        ents += _scan(text, _SIGN_RE, "征象", lambda w: SIGN_SYNONYMS.get(w))
        ents += _scan(text, _DEGREE_RE, "程度", lambda w: DEGREE_SYNONYMS.get(w))
        ents += _scan(text, _FOLLOWUP_RE, "随访", lambda w: FOLLOWUP_SYNONYMS.get(w))
        return _non_max_suppress(ents)


class ModelBackend(NERBackend):
    """模型实现占位（未启用）。

    将来用 CBLUE / CCKS 训练出的中文医疗 NER 模型（或接 CMedKG 图谱推理）时，
    在此实现 extract()：加载本地模型权重 → 推理 → 将结果转成 Entity 列表。
    切换方式：``set_backend(ModelBackend(...))``。引擎其余代码无需改动。
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path

    def extract(self, text: str) -> List[Entity]:
        raise NotImplementedError(
            "ModelBackend 尚未启用：当前环境无法训练/加载中文医疗 NER 模型。"
            "请先用 LexiconBackend（默认），或提供经 CBLUE/CCKS 训练、CMedKG "
            "蒸馏的本地模型权重后在此实现 extract()。"
        )


# 模块级当前后端（默认词典式）
_backend: NERBackend = LexiconBackend()


def set_backend(backend: NERBackend) -> None:
    """切换 NER 后端（如切换到 ModelBackend）。"""
    global _backend
    _backend = backend


def get_backend() -> NERBackend:
    """返回当前 NER 后端。"""
    return _backend


def extract_entities(text: str) -> List[Entity]:
    """用当前后端抽取实体（便捷函数）。"""
    return _backend.extract(text)


def entities_by_label(text: str, label: str) -> List[Entity]:
    """抽取并过滤指定类型的实体。"""
    return [e for e in extract_entities(text) if e.label == label]
