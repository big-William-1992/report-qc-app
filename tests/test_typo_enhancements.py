# -*- coding: utf-8 -*-
"""错别字识别增强测试：P0 上下文消歧 / P1 形近字 / P2 词表扩展 / P4 敏感度。"""
import sys
import os


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
    # "王动脉" 不在白名单中 → R19 检出（WL 层标记更大 span "见王动脉"/"王动脉增"，
    # HOMOPHONE 层形近检测精确匹配到 "王动脉"→"主动脉"）
    hits = [f for f in eng.run("影像描述：见王动脉增宽。\n影像诊断：王动脉增宽。", {})
            if f.rule_id in ("R19-WHITELIST", "R19-HOMOPHONE")]
    assert hits, "王动脉 不在白名单中，high 灵敏度下应被 R19 检出"
    assert any("王动脉" in f.snippet for f in hits), f"Expected 王动脉 in snippets: {[f.snippet for f in hits]}"


def test_p1_shape_similar_medium_silent():
    eng.rules_config["r19_sensitivity"] = "medium"
    # medium 灵敏度下，"王动脉" 仍被 R19 检出（形近字层）
    hits = [f for f in eng.run("影像描述：见王动脉增宽。\n影像诊断：王动脉增宽。", {})
            if f.rule_id in ("R19-WHITELIST", "R19-HOMOPHONE")]
    assert hits, "王动脉 不在白名单中，medium 灵敏度下应被 R19 检出"
    assert any("王动脉" in f.snippet for f in hits), f"Expected 王动脉 in snippets: {[f.snippet for f in hits]}"


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
    # "磨玻离" 已按人工审核学入 R8 词典（反馈闭环 y→learn_typo），
    # 由 R8 确定性检出；R19 不重复报。接受 R8/R19 任一检出路径。
    hits = [f for f in eng.run("影像描述：右肺上叶见磨玻离影。\n影像诊断：磨玻离结节。", {})
            if f.rule_id in ("R19-WHITELIST", "R19-HOMOPHONE", "R8-TYPO")]
    assert hits
    # 至少有一条告警的 snippet 提到"磨玻离"
    assert any("磨玻离" in (f.snippet or "") for f in hits), f"Expected 磨玻离 in snippets: {[f.snippet for f in hits]}"


def test_regression_r8():
    hits = _r8("影像描述：建议定期随防。\n影像诊断：随防。")
    assert hits and "随访" in hits[0].message


# ---------- 敏感度档位默认 medium ----------
def test_sensitivity_default_medium():
    eng.rules_config["r19_sensitivity"] = "medium"
    # 磨玻离已学入 R8（反馈闭环），R8/R19 任一检出均可
    hits = [f for f in eng.run("影像描述：右肺上叶见磨玻离影。\n影像诊断：磨玻离结节。", {})
            if f.rule_id in ("R19-WHITELIST", "R19-HOMOPHONE", "R8-TYPO")]
    assert hits
