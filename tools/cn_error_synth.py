"""本地 teacher 中文错例合成器（确定性，可验证）。

在没有 GPU / 微调模型时，作为「本地 teacher」的替身：对一份**干净**中文报告注入
可控的临床错误，产出 (错误报告, 期望发现) 训练对，格式与 llm_qc 输出完全一致。
后续接入真实 Ollama/vLLM teacher 时，只需在 build_cn_dataset.py 用 --teacher llm 替换本模块。

注入类型（与本项目 R1–R23 对齐）：
  - laterality : 左右混淆（左肺↔右肺 等）→ R2-LATERALITY
  - typo       : 错别字注入（正确词→错词，取自 TYPO_MAP_DEFAULT）→ R8-TYPO
  - gender     : 性别矛盾（患者男→患者女）→ R1-GENDER
  - negation   : 否定词翻转（未见→可见）→ R5-CONSISTENCY
"""
import re
import sys
from engine import TYPO_MAP_DEFAULT  # wrong->correct

_CORRECT_TO_WRONG = {v: k for k, v in TYPO_MAP_DEFAULT.items()
                     if isinstance(v, str) and isinstance(k, str) and v and k}

_LATERALITY = [("左肺", "右肺"), ("右肺", "左肺"), ("左侧", "右侧"),
               ("右侧", "左侧"), ("左叶", "右叶"), ("右叶", "左叶"),
               ("左肾", "右肾"), ("右肾", "左肾")]


def _finding(et, loc, sev, rat):
    return {"error_type": et, "location": loc, "severity": sev,
            "confidence": 1.0, "rationale": rat}


def _inject_laterality(text):
    for a, b in _LATERALITY:
        if a in text:
            return text.replace(a, b, 1), a, b
    return None


def _inject_typo(text):
    for correct, wrong in _CORRECT_TO_WRONG.items():
        if correct in text:
            return text.replace(correct, wrong, 1), correct, wrong
    return None


def _inject_gender(text):
    if "患者男" in text:
        return text.replace("患者男", "患者女", 1), "患者男", "患者女"
    if "男，" in text:
        return text.replace("男，", "女，", 1), "男", "女"
    if "女性" in text:
        return text.replace("女性", "男性", 1), "女性", "男性"
    return None


def _inject_negation(text):
    for a, b in (("未见", "可见"), ("无", "有"), ("不", "可")):
        if a in text:
            return text.replace(a, b, 1), a, b
    return None


def synthesize(text: str, kinds=("laterality", "typo", "gender", "negation")):
    """对干净报告注入错误，返回 (错误报告文本, 期望发现列表)。"""
    findings = []
    t = text
    if "laterality" in kinds:
        r = _inject_laterality(t)
        if r:
            t, a, b = r
            findings.append(_finding("R2-LATERALITY", b, "high", f"左右混淆：将 '{a}' 误写为 '{b}'"))
    if "typo" in kinds and not findings:
        r = _inject_typo(t)
        if r:
            t, a, b = r
            findings.append(_finding("R8-TYPO", b, "medium", f"错别字：'{a}' 误写为 '{b}'"))
    if "gender" in kinds:
        r = _inject_gender(t)
        if r:
            t, a, b = r
            findings.append(_finding("R1-GENDER", b, "high", f"性别矛盾：'{a}' 误写为 '{b}'"))
    if "negation" in kinds:
        r = _inject_negation(t)
        if r:
            t, a, b = r
            findings.append(_finding("R5-CONSISTENCY", b, "high", f"否定词翻转：'{a}' 改为 '{b}'"))
    return t, findings


def to_record(injected_text: str, findings: list, instruction: str = "") -> dict:
    from dataset_adapters import INSTRUCTION  # 复用统一指令
    return {"instruction": instruction or INSTRUCTION,
            "input": injected_text,
            "output": __import__("json").dumps(findings, ensure_ascii=False)}
