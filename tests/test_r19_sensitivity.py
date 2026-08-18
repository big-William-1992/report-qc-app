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


class TestMediumTierNearPhonetic:
    """R19 档位语义回归（2026-08-18 修复）：
    medium 档 3+ 字片段可检近音（此前近音相似度达不到 0.98 阈值，medium 实际=同音）；
    2 字片段保守仅同音（防『双肺→上肺』类高频词近音误报）。"""

    def test_medium_3plus_near_phonetic(self):
        # 4 字近音（拼音编辑距离 1）：medium 档应命中
        from highfreq_lexicon import segment_candidates
        hit, cand = segment_candidates("膜玻璃样", "medium")
        assert hit, "medium 档 3+ 字近音应命中"
        assert any(k == "near" for _, _, _, k in cand), [c[:3] for c in cand]

    def test_medium_2char_exact_only(self):
        # 2 字片段：仅同音（『双肺→上肺』近音不得报，防高频词误报）
        from highfreq_lexicon import segment_candidates
        hit, cand = segment_candidates("双肺", "medium")
        assert not hit, "2 字近音应被 medium 过滤"
        # 2 字同音错字仍报（『占为→占位』）
        hit2, cand2 = segment_candidates("占为", "medium")
        assert hit2 and any(k == "exact" for _, _, _, k in cand2)

    def test_autofix_exact_preferred_over_near(self):
        # 滑窗 exact 优先（2026-08-18）：『膜玻璃样密度影』自动修正应改『磨玻璃』（3字窗同音）
        # 而非 4 字窗近音『磨玻璃影』
        t = "检查所见：双肺 见膜玻璃样密度影，考虑间质性改变。\n诊断印象：间质性改变。"
        f = RuleEngine().run(t, {})
        r19 = [x for x in f if x.rule_id == "R19-HOMOPHONE" and "磨玻璃" in x.suggestion]
        assert r19, [x.message for x in f]
        fixed, n_fix, manual, fixes = RuleEngine().auto_fix(t, f)
        assert n_fix == 1, (n_fix, fixes)
        for fx in fixes:
            assert fx["correct"] == "磨玻璃", fx
