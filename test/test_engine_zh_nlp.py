"""中文同义词 / 中文 NER 接入引擎的集成测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine, ChineseRadiologyNER


def test_ner_extracts_sign_entity():
    # 增强后的引擎 NER 应产出 sign 实体（复用 zh_ner + zh_radiology_synonyms）
    ents = ChineseRadiologyNER().extract("右肺上叶见磨玻璃密度影，伴斑片影")
    signs = [e for e in ents if e.label == "sign"]
    canons = {e.canonical for e in signs}
    assert "磨玻璃影" in canons
    assert "斑片影" in canons


def test_ner_extracts_followup_entity():
    ents = ChineseRadiologyNER().extract("建议3个月后复查")
    fups = [e for e in ents if e.label == "followup"]
    assert fups


def test_r16_off_by_default():
    # 默认关闭：含『建议复查』但不开启 enable_r16 时不报 R16
    eng = RuleEngine()
    eng.rules_config = {}
    fs = eng.run(
        "影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节，建议复查。", {})
    assert not any(f.rule_id == "R16-FOLLOWUP" for f in fs)


def test_r16_on_detects_missing_timeframe():
    eng = RuleEngine()
    eng.rules_config = {"enable_r16": True}
    fs = eng.run(
        "影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节，建议复查。", {})
    assert any(f.rule_id == "R16-FOLLOWUP" for f in fs)


def test_r16_on_ignores_with_timeframe():
    # 有具体时限（3个月后）则不报缺失
    eng = RuleEngine()
    eng.rules_config = {"enable_r16": True}
    fs = eng.run(
        "影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节，建议3个月后复查。", {})
    assert not any(f.rule_id == "R16-FOLLOWUP" for f in fs)


def test_r16_no_false_positive_without_followup():
    eng = RuleEngine()
    eng.rules_config = {"enable_r16": True}
    fs = eng.run("影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节。", {})
    assert not any(f.rule_id == "R16-FOLLOWUP" for f in fs)
