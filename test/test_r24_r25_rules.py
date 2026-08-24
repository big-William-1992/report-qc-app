#!/usr/bin/env python3
"""R24-ADVICE 建议强度矛盾 / R25-TEMPORAL 时序方向矛盾 规则测试（2026-08-23 新增）。

R24：报告定性为良性却建议强处置（穿刺活检/切除/抗肿瘤/化疗/放疗）→ high。
     窄模式：良性定性词须未被否定；强处置词须被建议类引导词同句锚定，
     『切除术后』『穿刺细胞学』等病史/弱表述不触发。
R25：同一报告内对同一病灶的时序方向描述自相矛盾（较前增大 vs 较前缩小、
     较前增多 vs 较前减少）→ medium。窄模式：两侧均须提取到同一主体键
     （病灶关键词+侧别），主体缺失或不同不报，不涉及跨报告数值对比。
"""
import os, sys
import unittest

REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, REPO_SRC)
from engine import RuleEngine


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _ids(text, meta=None):
    return [f.rule_id for f in _run(text, meta)]


class TestR24Advice(unittest.TestCase):
    """R24-ADVICE 建议强度矛盾。"""

    # ---------- 正例（触发） ----------
    def test_benign_impression_with_biopsy_advice(self):
        # 印象段定性良性 + 建议穿刺活检 → 触发
        text = ("检查所见：右乳见一肿块，边界清。\n"
                "诊断印象：考虑良性病变，建议穿刺活检。")
        self.assertIn("R24-ADVICE", _ids(text))

    def test_unstructured_benign_with_resection_advice(self):
        # 无段落结构全文回退：考虑良性 + 建议手术切除 → 触发
        text = "肝右叶占位，考虑良性可能。建议行手术切除治疗。"
        self.assertIn("R24-ADVICE", _ids(text))

    def test_no_malignant_sign_with_chemo_advice(self):
        # 『未见恶性』也是明确良性判定 + 建议化疗 → 触发
        text = ("影像描述：胸腔积液较前吸收。\n"
                "诊断印象：未见恶性征象，建议行化疗。")
        out = _run(text)
        r24 = next(f for f in out if f.rule_id == "R24-ADVICE")
        self.assertEqual(r24.severity, "high")
        self.assertIn("化疗", r24.snippet)  # snippet 取处置词所在片段

    # ---------- 反例（不触发） ----------
    def test_malignant_with_biopsy_no_flag(self):
        # 易混淆：考虑恶性建议穿刺——定性非良性，条件①不成立
        text = ("检查所见：右肺上叶见一结节，边缘毛刺。\n"
                "诊断印象：考虑恶性，建议穿刺活检。")
        self.assertNotIn("R24-ADVICE", _ids(text))

    def test_benign_with_followup_only_no_flag(self):
        # 定性良性但无强处置词（仅随访复查），条件②不成立
        text = ("检查所见：右肾见囊肿。\n"
                "诊断印象：考虑良性病变，建议定期复查随访。")
        self.assertNotIn("R24-ADVICE", _ids(text))

    def test_biopsy_cytology_weak_wording_no_flag(self):
        # 弱表述：『穿刺细胞学』不含『穿刺活检』，不得触发
        text = "经皮穿刺细胞学检查示良性病变。建议随访观察。"
        self.assertNotIn("R24-ADVICE", _ids(text))

    def test_post_resection_history_no_flag(self):
        # 病史陈述『切除术后』+ 未见恶性征象，不是处置建议
        text = ("影像描述：左肺上叶切除术后改变，余肺未见恶性征象。\n"
                "诊断印象：术后改变，建议定期复查。")
        self.assertNotIn("R24-ADVICE", _ids(text))

    def test_negated_benign_marker_no_flag(self):
        # 良性定性词被否定（不考虑良性）→ 条件①不成立
        text = "诊断印象：不考虑良性病变，建议穿刺活检明确诊断。"
        self.assertNotIn("R24-ADVICE", _ids(text))


class TestR25Temporal(unittest.TestCase):
    """R25-TEMPORAL 时序方向矛盾。"""

    # ---------- 正例（触发） ----------
    def test_growth_vs_shrink_cross_section(self):
        # 描述段称增大、结论段称缩小（同一右肺结节）→ 触发
        text = ("检查所见：右肺结节较前增大，现约12mm。\n"
                "诊断印象：右肺结节较前缩小。")
        out = _run(text)
        r25 = next(f for f in out if f.rule_id == "R25-TEMPORAL")
        self.assertEqual(r25.severity, "medium")

    def test_increase_vs_decrease_same_subject(self):
        # 增多 vs 减少（同一左侧积液主体）→ 触发
        text = ("影像描述：左胸腔积液较前增多。\n"
                "诊断印象：左侧胸腔积液较前减少，建议核实。")
        self.assertIn("R25-TEMPORAL", _ids(text))

    def test_modifier_gap_still_detected(self):
        # 『较前明显增大/缩小』夹修饰语也应命中（gap ≤4 字）
        text = "肝内病灶较前明显增多；肝内病灶较前明显减少。"
        self.assertIn("R25-TEMPORAL", _ids(text))

    # ---------- 反例（不触发） ----------
    def test_single_direction_no_flag(self):
        # 易混淆：仅单侧出现『较前增大伴坏死』，无反向描述
        text = "右肺结节较前增大伴坏死，现约3.5cm。建议复查。"
        self.assertNotIn("R25-TEMPORAL", _ids(text))

    def test_different_side_lesions_no_flag(self):
        # 左右不同病灶差异化转归是正常临床情形，不得误报
        text = "右肺结节较前增大，左肺结节较前缩小。"
        self.assertNotIn("R25-TEMPORAL", _ids(text))

    def test_partial_trend_split_no_flag(self):
        # 『部分…部分…』指同组病灶内分化转归，主体不明，保守不报
        text = "双肺多发结节，部分较前增大，部分较前缩小。"
        self.assertNotIn("R25-TEMPORAL", _ids(text))

    def test_different_organs_no_flag(self):
        # 不同器官各自与之前对比（积液增多/病灶减少），非同一主体
        text = "右侧胸腔积液较前增多，肝内病灶较前减少。"
        self.assertNotIn("R25-TEMPORAL", _ids(text))

    def test_no_subject_key_no_flag(self):
        # 两个方向均无病灶主体词（窄模式：宁可漏报）
        text = "对比前片，较前增大。复查所见：较前缩小。"
        self.assertNotIn("R25-TEMPORAL", _ids(text))


if __name__ == "__main__":
    unittest.main()
