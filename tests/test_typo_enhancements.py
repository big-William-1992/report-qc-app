# -*- coding: utf-8 -*-
"""错别字识别增强测试：P0 上下文消歧 / P1 形近字 / P2 词表扩展 / P4 敏感度。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine

eng = RuleEngine()


def _r19(report):
    return [f for f in eng.run(report, {}) if f.rule_id == "R19-HOMOPHONE"]


def _r8(report):
    return [f for f in eng.run(report, {}) if f.rule_id == "R8-TYPO"]


# ---------- P0 上下文消歧：白名单词组内部不误报 ----------
def test_p0_context_no_false_positive():
    for r in [
        "影像描述：未见明显异常。\n影像诊断：胸部未见明显异常。",
        "影像描述：双肺纹理清晰，肺实质未见异常密度影。\n影像诊断：未见明显异常。",
        "影像描述：肝脏大小形态正常，未见占位性病变。\n影像诊断：未见占位。",
    ]:
        assert not _r19(r), f"标准报告不应 R19 误报: {r[:20]}"


# ---------- P1 形近字（词表外，高灵敏度才报） ----------
def test_p1_shape_similar_high():
    eng.rules_config["r19_sensitivity"] = "high"
    hits = _r19("影像描述：见王动脉增宽。\n影像诊断：王动脉增宽。")
    assert hits and "形近" in hits[0].message and "主动脉" in hits[0].message


def test_p1_shape_similar_medium_silent():
    eng.rules_config["r19_sensitivity"] = "medium"
    assert not _r19("影像描述：见王动脉增宽。\n影像诊断：王动脉增宽。")


# ---------- P2 词表扩展（R8 直接命中形近/输入法错） ----------
def test_p2_ime_errors():
    hits = _r8("影像描述：双废纹理清晰。\n影像诊断：未见异常。")
    assert hits and "双肺纹理" in hits[0].message


def test_p2_shape_errors_r8():
    hits = _r8("影像描述：肝区末见异常。\n影像诊断：末见异常。")
    assert hits and "未见异常" in hits[0].message


def test_p2_again_r8():
    hits = _r8("影像描述：腺体曾生。\n影像诊断：增生。")
    assert hits and "增生" in hits[0].message


# ---------- 真错字回归（增强不导致漏检） ----------
def test_regression_homophone():
    hits = _r19("影像描述：右肺上叶见磨玻离影。\n影像诊断：磨玻离结节。")
    assert hits and "磨玻璃" in hits[0].message


def test_regression_r8():
    hits = _r8("影像描述：建议定期随防。\n影像诊断：随防。")
    assert hits and "随访" in hits[0].message


# ---------- 敏感度档位默认 medium ----------
def test_sensitivity_default_medium():
    eng.rules_config["r19_sensitivity"] = "medium"
    # medium 应检出近音（非形近）
    hits = _r19("影像描述：右肺上叶见磨玻离影。\n影像诊断：磨玻离结节。")
    assert hits
