#!/usr/bin/env python3
"""上下文逻辑错误 + 同一句话逻辑错误 规则测试（R17 / R12 / R1 段落归属）。

R11 已删除（2026-08-18）：性别维度并入 R1-GENDER，侧别/描述-结论矛盾并入
R17-PERREGION；R15-SIDE（段内左右矛盾）因与 R2 重复也已删除（2026-08-18），
左右矛盾统一由 R2（跨段）/ R17（逐部位）负责。本节用例按当前规则族验证
「不再误报侧别/矛盾类」的防回归语义。
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
        # 2026-08-18 修复：否定词与阳性词之间隔修饰语（实质性/明显/占位性）也应判负，
        # 避免『未见实质性病变』等阴性声明被误判为阳性 → 纯正常报告误报 R17-PERREGION。
        self.assertFalse(engine._has_positive("未见实质性病变"))
        self.assertFalse(engine._has_positive("未见明显实质性病变"))
        self.assertFalse(engine._has_positive("未见占位性病变"))
        self.assertFalse(engine._has_positive("未见明显异常信号"))
        # 跨逗号不应吞前一分句的否定词：『无明确占位，左肾见小结节』中『小结节』为真阳性
        self.assertTrue(engine._has_positive("无明确占位，左肾见小结节"))

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


class TestContextNoSideFalsePositive(unittest.TestCase):
    """R11/R15-SIDE 删除后的防回归（2026-08-18）：信息框侧别 vs 报告侧别不再有
    独立侧别规则；左右矛盾交由 R2-LATERALITY（跨段）/ R17-PERREGION（逐部位）。"""

    def test_side_only_contralateral_no_flag(self):
        # 信息框侧别为左，但报告只提右侧（本侧未描述）——
        # 本侧可能正常未提及，属『未涉及』而非矛盾，不得误报侧别/逐部位矛盾。
        text = ("检查所见：\n右侧胸腔见少量积液。\n"
                "诊断印象：\n右侧胸腔积液。\n")
        out = _run(text, {"laterality": "左"})
        ids = [f.rule_id for f in out]
        self.assertNotIn("R17-PERREGION", ids)

    def test_side_no_mismatch_when_consistent(self):
        text = ("检查所见：\n左侧胸腔见少量积液。\n"
                "诊断印象：\n左侧胸腔积液。\n")
        out = _run(text, {"laterality": "左"})
        ids = [f.rule_id for f in out]
        self.assertNotIn("R17-PERREGION", ids)

    def test_abnormal_in_findings_normal_in_impression(self):
        # R17 逐部位精确比对取代整段级 R11-ABNORMAL：按「右肺」同一部位判定描述阳性/结论正常矛盾
        text = ("检查所见：\n右肺上叶见一结节，大小约12mm。\n"
                "诊断印象：\n未见异常。\n")
        out = _run(text, {})
        self.assertIn("R17-PERREGION", [f.rule_id for f in out])


class TestR17NegationFix(unittest.TestCase):
    """2026-08-18 修复回归：_has_positive 对『否定词+间隔修饰语+阳性词』误判阳性
    （如『未见实质性病变』），导致纯正常报告误报 R17-PERREGION；同时阴性声明未被识别
    导致『描述正常、结论异常』跨段矛盾漏检。"""

    def test_normal_report_no_false_positive(self):
        # 纯正常报告：描述『未见实质性病变』（阴性声明）+ 结论正常 → 不得报 R17-PERREGION
        text = "影像描述：双肺纹理清晰，未见实质性病变。\n影像结论：胸部未见明显异常。"
        out = _run(text, {})
        self.assertNotIn("R17-PERREGION", [f.rule_id for f in out])

    def test_findings_negative_claim_impression_positive(self):
        # 描述整段阴性声明 + 结论阳性诊断 → 应报『描述正常结论异常』（此前漏检）
        text = "影像描述：双肺纹理清晰，未见实质性病变。\n影像结论：左肺上叶见结节，考虑恶性，建议复查。"
        out = _run(text, {})
        self.assertIn("R17-PERREGION", [f.rule_id for f in out])

    def test_findings_positive_impression_normal(self):
        # 反向：描述阳性 + 结论正常声明 → 仍应报（回归，确保不误伤既有能力）
        text = "影像描述：右肺上叶见结节，边界清。\n影像结论：胸部未见明显异常。"
        out = _run(text, {})
        self.assertIn("R17-PERREGION", [f.rule_id for f in out])

    def test_no_abnormal_flag_when_impression_positive(self):
        # 描述阳性 + 结论同样阳性（考虑良性）→ 无矛盾，不得报 R17-PERREGION
        text = ("检查所见：\n右肺上叶见一结节。\n"
                "诊断印象：\n右肺上叶结节，考虑良性。\n")
        out = _run(text, {})
        self.assertNotIn("R17-PERREGION", [f.rule_id for f in out])


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


class TestReviewFixes20260818(unittest.TestCase):
    """2026-08-18 系统性代码审查的引擎误报/漏检修复回归（R5/R6/R8/R14/R15/R17/R3/R10）。"""

    def _ids(self, text, meta=None):
        return [f.rule_id for f in _run(text, meta or {})]

    def test_r5_bilateral_wording_no_false_positive(self):
        # 印象段『两肺/双肾未见异常』属阴性一致结论，不应报 R5-CONSISTENCY
        self.assertNotIn("R5-CONSISTENCY",
                         self._ids("影像描述：右肺上叶见一结节。\n影像结论：两肺未见明显异常。"))
        self.assertNotIn("R5-CONSISTENCY",
                         self._ids("影像描述：右肾见囊肿。\n影像结论：双肾未见明显异常。"))

    def test_r6_single_char_site_context(self):
        # 申请胸部 vs 左肾结石 → 应报（单字键+侧别语境仍计入）
        self.assertIn("R6-SITE", self._ids("检查所见：左肾见结石。\n诊断印象：左肾结石。",
                                           {"applied_site": "胸部"}))
        # 申请上腹部 + 正文偶发『未见脑转移』→ 不误报
        self.assertNotIn("R6-SITE",
                         self._ids("影像描述：肝左叶见占位，未见脑转移。\n影像结论：肝脏占位。",
                                   {"applied_site": "上腹部"}))

    def test_r8_yijian_improving_no_false_positive(self):
        # 『病灶较前已见好转』是正确表达，不报 R8-TYPO（也不应进 auto_fix）
        self.assertNotIn("R8-TYPO",
                         self._ids("影像描述：病灶较前已见好转。\n影像结论：较前好转。"))

    def test_r14_multi_site_count_conservative(self):
        # 多部位分列计数（左肺3枚、右肺2枚）无总数 → R14-COUNT 保守不报
        self.assertNotIn("R14-COUNT",
                         self._ids("影像描述：左肺见3枚结节，右肺见2枚结节。\n影像结论：双肺多发结节。"))

    def test_r15_contralateral_negative_control(self):
        # 『病灶侧 + 对侧阴性对照』是标准写法，不误报矛盾（R15-SIDE 已删，防回归）
        self.assertNotIn("R17-PERREGION",
                         self._ids("影像描述：左肺见结节；右肺未见明显结节。\n影像结论：左肺结节。"))

    def test_r17_comma_boundary(self):
        # 『右肺结节，余肺未见异常』逗号后声明不贴给右肺 → 与结论正常构成真矛盾仍应报
        self.assertIn("R17-PERREGION",
                      self._ids("影像描述：右肺上叶见一结节，余肺未见异常。\n影像结论：胸部未见明显异常。"))

    def test_r3_pelvis_no_birads_misconfig(self):
        # 盆腔检查不再被误要求 PI-RADS
        self.assertNotIn("R3-SCORE",
                         self._ids("影像描述：子宫大小形态正常。\n影像结论：未见异常。",
                                   {"applied_site": "盆腔"}))

    def test_r10_clinical_conclusion_not_impression(self):
        # 『临床初步结论』是正文词，不应被当作结论段标题 → 缺结论段仍应报模板缺失
        self.assertIn("R10-TEMPLATE",
                      self._ids("影像描述：右肺见结节。\n临床初步结论：结节待查。"))


if __name__ == "__main__":
    unittest.main()


class TestSidePhrasingNER(unittest.TestCase):
    """NER 侧别/复合词修复回归（2026-08-18 第六轮 P0）：
    『左侧X』措辞可识别（此前漏检）；复合词（右肺门/左肾上腺）不被短别名误切（此前 R5/R2 误报）。"""

    def test_left_side_word_phrasing(self):
        # 『左侧肾上腺』措辞：跨段左右矛盾应报 R2（此前『左+器官』遇"侧"字整体漏检）
        out = _run("检查所见：左侧肾上腺见占位。\n诊断印象：右侧肾上腺见占位。", {})
        self.assertIn("R2-LATERALITY", [f.rule_id for f in out])

    def test_hilum_not_mistaken_for_lung(self):
        # 『右肺门淋巴结增大』不得被 NER 误切为『右肺』→ R5 不误报（结论段肺门可被识别）
        out = _run("检查所见：右肺门淋巴结增大。\n诊断印象：肺门淋巴结增大，建议随访。", {})
        self.assertNotIn("R5-CONSISTENCY", [f.rule_id for f in out])
        self.assertNotIn("R2-LATERALITY", [f.rule_id for f in out])

    def test_adrenal_not_mistaken_for_kidney(self):
        # 『左肾上腺/右肾上腺』按 adrenal 族报矛盾（此前误归肾族）
        out = _run("检查所见：左肾上腺见占位。\n诊断印象：右肾上腺见占位。", {})
        ids = [f.rule_id for f in out]
        self.assertIn("R2-LATERALITY", ids)
        # 不得同时按肾族重复报（描述段肾实体不应出现）
        self.assertNotIn("R5-CONSISTENCY", ids)

    def test_hilum_real_mismatch_still_reported(self):
        # 真矛盾不因修复漏检：描述右肺门阳性 + 结论整体正常 → R17 仍报
        out = _run("检查所见：右肺门淋巴结增大。\n诊断印象：胸部未见明显异常。", {})
        self.assertIn("R17-PERREGION", [f.rule_id for f in out])
