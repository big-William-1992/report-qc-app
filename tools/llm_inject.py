"""LLM 错误注入（ReXErr 方法论）+ 回检闭环。

与现有「检测模式」(run_llm_qc) 互补：这里让 LLM **往干净报告里注入**可控的临床错误，
产出 (错误报告, 结构化标签) 训练对；再用确定性引擎回检，只保留「错误真能被检出、标签一致」的样本，
过滤噪标。模型无关：通过 src/llm_client 的 ollama/vllm/cloud provider 接入。

合规：真实报告仅经院内/本地模型注入；公有云 API 只用于脱敏/合成样本（见 docs/...调研与中文迁移方案.md）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from engine import RuleEngine, extract_meta  # noqa: E402
from llm_client import LLMClient, LLMResponse  # noqa: E402
from dataset_adapters import INSTRUCTION  # noqa: E402

# 注入错误类型 → 本项目中可由规则引擎回检的规则前缀（其余类型需 LLM 回检）
RULE_PREFIX = {
    "R8-TYPO": "R8", "R2-LATERALITY": "R2", "R1-GENDER": "R1",
    "R5-CONSISTENCY": "R5", "R12-SENTENCE": "R12", "R15-PRESENCE": "R15",
}

INJECTION_SYSTEM = (
    "你是资深放射科质控专家。给定一份【干净、无错】的放射诊断报告，"
    "请按下方错误类型清单，注入恰好 N 处『临床合理、可被质控检出』的错误"
    "（不要改动无关内容，保持其余表述自然一致）。\n"
    "仅输出严格 JSON：{\"injected_report\": <注入错误后的完整报告>, "
    "\"errors\": [{\"error_type\": <下列之一>, \"location\": <出错原文片段>, "
    "\"severity\": \"low|medium|high\", \"rationale\": <一句说明>}]}\n"
    "error_type 可选：R2-LATERALITY(左右混淆), R1-GENDER(性别矛盾), "
    "R8-TYPO(错别字), R5-CONSISTENCY(否定/矛盾), R12-SENTENCE(多余句), "
    "R15-PRESENCE(遗漏), R22-QUAL(定性-尺寸矛盾)。"
)


def build_messages(text: str, n: int = 1):
    return INJECTION_SYSTEM, f"请对以下干净报告注入 {n} 处错误：\n\n{text}"


def inject_errors(client: LLMClient, text: str, n: int = 1,
                  temperature: float = 0.3) -> tuple:
    """调用 LLM 注入错误。返回 (injected_report, errors_list) 或 None。"""
    system, user = build_messages(text, n)
    resp = client.chat(system, user, json_mode=True, temperature=temperature)
    if resp.error or not resp.parsed:
        return None
    parsed = resp.parsed if isinstance(resp.parsed, dict) else {}
    inj = parsed.get("injected_report") or parsed.get("report") or ""
    errs = parsed.get("errors") or []
    if not inj or not isinstance(errs, list):
        return None
    return inj, errs


def verify_injection(injected_text: str, errors: list, eng: RuleEngine,
                     llm_detect=None) -> tuple:
    """回检：错误是否落在文本中、且可被质控检出。返回 (kept, detected_set, missing)。"""
    detected = {f.rule_id for f in eng.run(injected_text, extract_meta(injected_text))}
    missing = []
    for e in errors:
        et = e.get("error_type", "")
        loc = e.get("location", "")
        if loc and loc not in injected_text:
            missing.append(f"{et}:location-missing")
            continue
        prefix = RULE_PREFIX.get(et)
        if prefix:
            if not any(d.startswith(prefix) for d in detected):
                missing.append(f"{et}:not-by-rule")
        elif llm_detect:
            if et not in llm_detect(injected_text):
                missing.append(f"{et}:not-by-llm")
    return (len(missing) == 0), detected, missing


def to_record(injected_text: str, errors: list) -> dict:
    return {"instruction": INSTRUCTION, "input": injected_text,
            "output": json.dumps(errors, ensure_ascii=False)}


class FakeInjectionClient(LLMClient):
    """离线自测用：模拟 LLM 返回的注入结果（结构化 JSON）。"""

    def __init__(self, payload: dict):
        self.payload = payload

    def chat(self, system, user, **kw):
        return LLMResponse(text=json.dumps(self.payload, ensure_ascii=False),
                           parsed=self.payload, model="fake")
