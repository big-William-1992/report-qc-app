import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from llm_inject import (inject_errors, verify_injection, to_record,  # noqa: E402
                        FakeInjectionClient, RULE_PREFIX)  # noqa: E402
from engine import RuleEngine  # noqa: E402


def _payload(text):
    # 模拟 LLM 注入：错别字（右肺→又肺，TYPO_MAP 错写，规则可回检）
    inj = text.replace("右肺", "又肺", 1)
    errs = [
        {"error_type": "R8-TYPO", "location": "又肺", "severity": "medium",
         "rationale": "错别字：'右肺' 误写为 '又肺'"},
    ]
    return inj, errs


def test_inject_errors_parses_fake_client():
    text = "患者男，58岁。检查所见：右肺上叶见结节。"
    inj_exp, errs_exp = _payload(text)
    client = FakeInjectionClient({"injected_report": inj_exp, "errors": errs_exp})
    res = inject_errors(client, text)
    assert res is not None
    inj, errs = res
    assert inj == inj_exp and errs == errs_exp


def test_verify_keeps_detectable_errors():
    text = "患者男，58岁。检查所见：右肺上叶见结节。"
    inj, errs = _payload(text)
    eng = RuleEngine()
    ok, detected, missing = verify_injection(inj, errs, eng)
    assert ok is True, missing
    assert any(d.startswith("R8") for d in detected)


def test_verify_rejects_undetectable():
    # 注入一个规则无法回检、且 location 不在文本里的错误
    text = "患者男，58岁。检查所见：右肺上叶见结节。"
    inj = text
    errs = [{"error_type": "R8-TYPO", "location": "错别字片段", "severity": "low",
             "rationale": "假错误"}]
    ok, _, missing = verify_injection(inj, errs, RuleEngine())
    assert ok is False and missing


def test_to_record_schema():
    text = "患者男，58岁。检查所见：右肺上叶见结节。"
    inj, errs = _payload(text)
    rec = to_record(inj, errs)
    assert set(rec) == {"instruction", "input", "output"}
    assert json.loads(rec["output"]) == errs
