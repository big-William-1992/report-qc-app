"""
engine_types.py — 引擎数据模型（从 engine.py 拆分，P2 巨型单文件拆分）
====================================================================
Entity：NER 实体（text/label/start/end/section/canonical）
Finding：质控发现（rule_id/error_type/severity/message/snippet/span/suggestion）

RuleEngine.run() 与全部 R1-R22 规则统一返回 List[Finding]；
auto_fix 依赖 rule_id ∈ ("R8-TYPO", "R19-HOMOPHONE") + suggestion 的命名契约。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int
    section: str = ""
    canonical: Optional[str] = None


@dataclass
class Finding:
    rule_id: str
    error_type: str
    severity: str      # high / medium / low
    message: str
    snippet: str = ""
    span: tuple = (-1, -1)
    suggestion: str = ""   # 可自动修正的建议值（仅 R8 错别字填充）
