"""LLM 质控提示词工程。

以既有 R1–R23 错误分类法做锚定（few-shot 对齐 taxonomy），要求模型只报告
确定性规则可能漏掉的「语义/语境类」错误，并输出结构化 JSON。
"""
from __future__ import annotations

import json
from typing import Optional, List

RULE_TAXONOMY = """既有确定性规则引擎已覆盖的硬错误类型（LLM 无需重复，仅作分类对齐参考）：
- 患者基本信息：性别矛盾(R1-GENDER)、性别-部位联动(R21-GENDER-SITE)
- 部位/方位：左右侧混淆(R2-LATERALITY)、登记部位不符(R6-SITE)、部位器官漏写(R18-COVERAGE)
- 评分系统：TI-RADS/LU-RADS 等缺失/错误(R3-SCORE)
- 单位与尺寸：计量单位错误(R4-UNIT)、尺寸单位规范(R22-UNIT)、病灶缺尺寸(R22-SIZE-MISSING)、定性-尺寸矛盾(R22-SIZE/R22-QUAL)
- 描述-结论一致性：描述-结论矛盾(R5-CONSISTENCY)、逐部位矛盾(R17-PERREGION)、良恶性定性矛盾(R14-NATURE)、数量矛盾(R14-COUNT)、段首正常段内阳性(R15-NORMAL)、先见后无(R15-PRESENCE)
- 句内逻辑：句内自相矛盾含男女器官混用(R12-SENTENCE，原 R7 已并入)、自定义互斥冲突(R9-CONFLICT)
- 术语/错别字：同音错别字(R8-TYPO)、形近/读音错字(R19-HOMOPHONE)、繁体字提示(R23-TRADITIONAL)
- 模板与随访：模板合规/要素漏写(R10-TEMPLATE/R18-COVERAGE)、随访建议时限缺失或不匹配(R16-FOLLOWUP)
LLM 重点补充：跨句长程逻辑矛盾、推荐处置与征象不匹配、叙事质量与规范、罕见/新类型错误。
"""

SYSTEM_PROMPT = """你是一名资深放射科质控专家，负责审核放射诊断报告。

任务：找出确定性规则引擎「可能漏掉」的语义级 / 语境级错误。
错误类型必须使用下方【错误分类法】中的标准代码（error_type），格式为「Rxx-名称」；
若确属全新类型、不在列表中，使用 "L1-OTHER" 并在 rationale 说明。
若报告无明显语义问题，返回空数组。

输出要求：
1. 仅输出一个 JSON 数组，不要包含任何解释性文字或 markdown 代码块标记。
2. 数组每个元素是一个对象，字段如下：
   - error_type: 必须来自【错误分类法】的标准代码，如 "R2-LATERALITY"、"R5-CONSISTENCY"、"R1-GENDER"、"R8-TYPO"、"R22-QUAL"、"L1-OTHER"
   - location: 问题所在的报告片段原文（引号截取，便于定位）
   - severity: "high" | "medium" | "low"
   - confidence: 0~1 之间的浮点数，表示你对该问题的把握
   - rationale: 一句话说明为什么是问题、应怎样修改
3. 不要编造问题；对不确定的内容宁可返回空数组，也不要低质量猜测。
4. 不同性质的错误必须使用不同的 error_type 代码，按实际错误类型从分类法中选取，禁止将所有问题归为同一种类型。
"""

SCHEMA_HINT = """输出格式示例【示例仅演示 JSON 结构；error_type 必须按报告中的实际错误类型从分类法中选取对应代码，不同类型的错误用不同代码，勿照搬示例】：
[
  {"error_type": "R2-LATERALITY", "location": "诊断印象：左肺上叶磨玻璃结节", "severity": "high", "confidence": 0.9,
   "rationale": "检查所见为右肺上叶，结论写成左肺上叶，左右侧混淆。"},
  {"error_type": "R8-TYPO", "location": "摩玻璃结节", "severity": "medium", "confidence": 0.95,
   "rationale": "'磨玻璃'误写为'摩玻璃'，应为磨玻璃结节。"},
  {"error_type": "R1-GENDER", "location": "该病变多见于女性", "severity": "high", "confidence": 0.85,
   "rationale": "患者为男性，结论却称多见于女性，性别矛盾。"},
  {"error_type": "R5-CONSISTENCY", "location": "诊断印象：未见占位", "severity": "high", "confidence": 0.9,
   "rationale": "描述段明确见实质性占位，印象却说未见占位，前后否定矛盾。"}
]
若无问题，直接输出：[]
"""


def build_qc_prompt(report_text: str, meta: Optional[dict] = None,
                    rag_contexts: Optional[List[str]] = None,
                    taxonomy: str = RULE_TAXONOMY) -> tuple:
    system = SYSTEM_PROMPT
    user_parts: List[str] = []
    user_parts.append("【错误分类法（error_type 必须从中取标准代码）】\n" + taxonomy)
    if meta:
        user_parts.append("【患者/登记信息】\n" + json.dumps(meta, ensure_ascii=False, indent=2))
    user_parts.append("【待质控报告】\n" + report_text)
    if rag_contexts:
        user_parts.append("【参考规范 / 知识】\n" + "\n".join(f"- {c}" for c in rag_contexts))
    user_parts.append(SCHEMA_HINT)
    user = "\n\n".join(user_parts)
    return system, user
