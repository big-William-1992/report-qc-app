"""融合仲裁层回归测试（无需 GPU / 模型）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_fusion import fuse, rule_finding_to_dict, llm_finding_to_dict  # noqa: E402


class FakeFinding:
    def __init__(self, rule_id, error_type, severity, message, snippet="", span=(-1, -1)):
        self.rule_id = rule_id
        self.error_type = error_type
        self.severity = severity
        self.message = message
        self.snippet = snippet
        self.span = span


def test_rule_status_and_conf():
    d = rule_finding_to_dict(FakeFinding("R8-TYPO", "错别字", "medium", "错", "坐肺"))
    assert d["source"] == "rule" and d["status"] == "rule" and d["confidence"] == 1.0


def test_llm_conf_gating():
    hi = llm_finding_to_dict({"error_type": "rec", "location": "x",
                              "severity": "high", "confidence": 0.9})
    mid = llm_finding_to_dict({"error_type": "rec", "location": "x",
                               "severity": "medium", "confidence": 0.6})
    lo = llm_finding_to_dict({"error_type": "rec", "location": "x",
                              "severity": "low", "confidence": 0.3})
    assert hi["status"] == "suggestion"
    assert mid["status"] == "suggestion_pending"
    assert lo["status"] == "needs_review"


def test_dual_confirm():
    rules = [FakeFinding("R8-TYPO", "错别字", "medium", "错", "坐肺上叶")]
    llms = [{"error_type": "错别字", "location": "坐肺上叶",
             "severity": "medium", "confidence": 0.9, "rationale": "同音错"}]
    out = fuse(rules, llms)
    assert out["counts"]["dual_confirmed"] == 1
    assert out["llm_findings"][0]["dual_confirmed"] is True
    assert out["rule_findings"][0]["dual_confirmed"] is True


def test_fused_order_rule_first():
    rules = [FakeFinding("R8-TYPO", "错别字", "low", "错", "坐肺")]
    llms = [{"error_type": "x", "location": "y", "severity": "high", "confidence": 0.9}]
    out = fuse(rules, llms)
    assert out["fused"][0]["source"] == "rule"
