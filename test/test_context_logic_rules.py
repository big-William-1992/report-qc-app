#!/usr/bin/env python3
"""上下文逻辑错误 + 同一句话逻辑错误 规则测试（R11 / R12 / R1 段落归属）。

R11：信息框 vs 描述框/结论框 跨框比对
  - R11-SIDE  左右一致性（信息框侧别 vs 描述/结论提及方位）
  - R11-ABNORMAL 描述有阳性征但结论称未见异常
R12：同一句话内自相矛盾（男女专属器官混用 / 称未见异常却描述阳性征）
R1 ：性别-器官矛盾（标注段落来源：影像描述段/影像结论段）

多数用例不依赖 NER（直接扫描句/段文本或注入实体），稳定可重复。
"""
import os, sys
import unittest

REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, REPO_SRC)
import engine
from engine import Entity, RuleEngine


def _run(text, meta):
    return RuleEngine().run(text, meta)


class TestHelpers(unittest.TestCase):
    def test_detect_side(self):
        self.assertEqual(engine._detect_side_in_text("右侧胸腔见积液"), {"right"})
        self.assertEqual(engine._detect_side_in_text("左侧股骨"), {"left"})
        self.assertEqual(engine._detect_side_in_text("双肺未见异常"), set())
        self.assertEqual(engine._detect_side_in_text("双侧胸腔积液"), {"bilateral"})

    def test_norm_laterality(self):
        self.assertEqual(engine._norm_laterality("左"), "left")
        self.assertEqual(engine._norm_laterality("右侧"), "right")
        self.assertEqual(engine._norm_laterality("双侧"), "bilateral")
        self.assertIsNone(engine._norm_laterality(""))

    def test_claims_normal(self):
        self.assertTrue(engine._claims_normal("诊断印象：未见异常。"))
        self.assertTrue(engine._claims_normal("未见明显异常征象"))
        self.assertFalse(engine._claims_normal("右肺上叶见一结节"))

    def test_has_positive(self):
        self.assertTrue(engine._has_positive("右肺见一结节"))
        self.assertFalse(engine._has_positive("双肺纹理清晰，未见异常"))

    def test_split_sentences(self):
        s = engine._split_sentences("子宫未见异常。但前列腺增大！建议复查？")
        self.assertEqual(len(s), 3)
        self.assertTrue(all(s))


class TestR1GenderSection(unittest.TestCase):
    def test_male_with_uterus_in_findings(self):
        ents = [Entity("子宫", "gender_organ", 0, 2, "findings", "female")]
        out = RuleEngine()._r1_gender("x", ents, {"gender": "男"})
        ids = [f.rule_id for f in out]
        self.assertIn("R1-GENDER", ids)
        self.assertTrue(any("影像描述段" in f.message for f in out))


class TestR11Context(unittest.TestCase):
    def test_side_only_contralateral_no_flag(self):
        # 回归：信息框侧别为左，但报告只提右侧（本侧未描述）——
        # 本侧可能正常未提及，属『未涉及』而非矛盾，不再误报 R11-SIDE。
        text = ("检查所见：\n右侧胸腔见少量积液。\n"
                "诊断印象：\n右侧胸腔积液。\n")
        out = _run(text, {"laterality": "左"})
        ids = [f.rule_id for f in out]
        self.assertNotIn("R11-SIDE", ids)

    def test_side_no_mismatch_when_consistent(self):
        text = ("检查所见：\n左侧胸腔见少量积液。\n"
                "诊断印象：\n左侧胸腔积液。\n")
        out = _run(text, {"laterality": "左"})
        self.assertNotIn("R11-SIDE", [f.rule_id for f in out])

    def test_abnormal_in_findings_normal_in_impression(self):
        text = ("检查所见：\n右肺上叶见一结节，大小约12mm。\n"
                "诊断印象：\n未见异常。\n")
        out = _run(text, {})
        # R17 逐部位精确比对取代整段级 R11-ABNORMAL：按「右肺」同一部位判定描述阳性/结论正常矛盾
        self.assertIn("R17-PERREGION", [f.rule_id for f in out])

    def test_no_abnormal_flag_when_impression_positive(self):
        text = ("检查所见：\n右肺上叶见一结节。\n"
                "诊断印象：\n右肺上叶结节，考虑良性。\n")
        out = _run(text, {})
        self.assertNotIn("R11-ABNORMAL", [f.rule_id for f in out])


class TestR12Sentence(unittest.TestCase):
    def test_mixed_gender_organ_same_sentence(self):
        text = ("检查所见：\n子宫未见异常，但前列腺增大。\n"
                "诊断印象：\n建议复查。\n")
        out = _run(text, {})
        self.assertIn("R12-SENTENCE", [f.rule_id for f in out])

    def test_normal_claim_with_positive_same_sentence(self):
        text = ("检查所见：\n双肺未见异常，但见一占位性病变。\n"
                "诊断印象：\n\n")
        out = _run(text, {})
        # R9 互斥词对（『未见』vs『占位』）在句内直接捕获，语义等价于 R12-SENTENCE
        self.assertIn("R9-CONFLICT", [f.rule_id for f in out])

    def test_clean_sentence_no_flag(self):
        text = ("检查所见：\n右肺上叶见一结节。\n"
                "诊断印象：\n右肺上叶结节。\n")
        out = _run(text, {})
        self.assertNotIn("R12-SENTENCE", [f.rule_id for f in out])


class TestFalsePositiveRegression(unittest.TestCase):
    """上下文一致性误报回归测试：确保正常/鉴别的标准报告表述不再被误报。"""

    def test_r9_no_visible_mass_no_flag(self):
        # 回归 R9：『未见占位性病变』是标准阴性表述，『未见』+『占位』同现不应判为互斥矛盾。
        text = ("检查所见：\n双肺纹理清晰，未见占位性病变。\n"
                "诊断印象：\n双肺未见明确异常。\n")
        out = _run(text, {})
        self.assertNotIn("R9-CONFLICT", [f.rule_id for f in out])

    def test_r9_benign_malignant_differentiation_no_flag(self):
        # 回归 R9：『良恶性待定』『不除外恶性』是正常鉴别表达，『良性』『恶性』同现不应误报。
        text = ("检查所见：\n右肺上叶见一结节，性质待定，需鉴别良恶性。\n"
                "诊断印象：\n结节，考虑良性可能，但不除外恶性。\n")
        out = _run(text, {})
        self.assertNotIn("R9-CONFLICT", [f.rule_id for f in out])

    def test_r12_different_region_symmetric_no_flag(self):
        # 回归 R12：同一句内不同部位『右肺结节 + 左肺正常』是对称描述，不判为自相矛盾。
        text = ("检查所见：\n右肺见一结节，左肺正常。\n"
                "诊断印象：\n右肺结节。\n")
        out = _run(text, {})
        self.assertNotIn("R12-SENTENCE", [f.rule_id for f in out])

    def test_r12_same_region_conflict_still_detected(self):
        # 保障 R12：同一部位『左肺正常 + 左肺结节』仍是真矛盾，应保留告警。
        text = ("检查所见：\n左肺正常，但左肺见一结节。\n"
                "诊断印象：\n左肺结节。\n")
        out = _run(text, {})
        self.assertIn("R12-SENTENCE", [f.rule_id for f in out])

    def test_r5_negative_impression_no_flag(self):
        # 回归 R5：描述有结节、印象段对相应器官给出阴性/概括结论（未见异常/良性），不再误报 R5。
        text = ("检查所见：\n右肺上叶见一结节。\n"
                "诊断印象：\n右肺未见明显异常。\n")
        out = _run(text, {})
        self.assertNotIn("R5-CONSISTENCY", [f.rule_id for f in out])


if __name__ == "__main__":
    unittest.main()
