# -*- coding: utf-8 -*-
"""OCR 病人基础信息识别容错加固（v2）回归测试。

覆盖通用方案一版修复（针对用户反馈「影像号/检查号错、姓名/性别错、部位/侧别缺失」）：
1) 标签/分隔符容忍**全角空格 U+3000**（中文 OCR 最常见噪声之一）；
2) 性别形近字归一：另≈男、文/久≈女；兼容『男性，45岁』写法；
3) 检查号 OCR 字符混淆扩展：O/o→0、I/i/l→1、B→8、Z→2、S→5、G→6（仅数字为主时）；
4) 姓名字符类放宽：允许间隔号·、上限 4→6 字（复姓带点/外籍姓名）；
5) extract_meta_full：部位/侧别/检查类型除患者栏(basic)外，还会从所见/结论区补抽。

无需 cv2/numpy（纯解析逻辑），可与 test_ocr_meta_improve.py 同进程运行。
"""
import os
import sys
import unittest

REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, REPO_SRC)

import engine  # noqa: E402


class TestFullWidthSpace(unittest.TestCase):
    """标签被全角空格（U+3000）拆开时仍能抽取。"""

    def test_patient_fullwidth_spaced(self):
        # 姓　名：张三 （全角空格插在标签中间）
        self.assertEqual(engine._extract_patient("姓　名：张三"), "张三")

    def test_exam_no_fullwidth_spaced(self):
        # 检查号　：CT123 （标签内含全角空格 + 冒号前全角空格）
        self.assertEqual(engine._extract_exam_no("检查号　：CT123"), "CT123")

    def test_gender_fullwidth_after_label(self):
        # 性别　：男 （标签与冒号间全角空格）
        self.assertEqual(engine._extract_gender_cn("性别　：男"), "男")


class TestGenderHomoglyph(unittest.TestCase):
    """OCR 把 男/女 识别成形近字 另/文/久 时归一。"""

    def test_nan_homoglyph(self):
        self.assertEqual(engine._extract_gender_cn("性别：另"), "男")
        self.assertEqual(engine._parse_gender_from_text("另性，45岁"), "male")

    def test_nv_homoglyph(self):
        self.assertEqual(engine._extract_gender_cn("性别：文"), "女")
        self.assertEqual(engine._extract_gender_cn("患者 文"), "女")

    def test_jiu_homoglyph(self):
        self.assertEqual(engine._parse_gender_from_text("患者，久，52岁"), "female")


class TestAgeMaleWriting(unittest.TestCase):
    """兼容『男性，45岁』写法（性别后带『性』字）。"""

    def test_male_xing(self):
        self.assertEqual(engine._extract_age("男性，45岁"), "45")

    def test_female_xing(self):
        self.assertEqual(engine._extract_age("女性，32岁"), "32")

    def test_plain_comma(self):
        self.assertEqual(engine._extract_age("男，48岁"), "48")


class TestExamNoExtendedConfusion(unittest.TestCase):
    """检查号 OCR 字符混淆扩展归一。"""

    def test_o_i_b_z(self):
        # O0I1B2：数字 0,0,1,2 共4/6 过半 → 归一 O→0,I→1,B→8 → 001182
        self.assertEqual(engine._extract_exam_no("检查号：O0I1B2"), "001182")

    def test_bzsg_flanked_by_digits(self):
        # B/Z/S/G 仅在被数字夹住时归一（避免误伤 PACS 等真实字母）
        self.assertEqual(engine._extract_exam_no("检查号：A1B23"), "A1823")
        self.assertEqual(engine._extract_exam_no("影像号：9Z07"), "9207")
        self.assertEqual(engine._extract_exam_no("门诊号：3S8"), "358")
        self.assertEqual(engine._extract_exam_no("住院号：4G6"), "466")

    def test_real_letter_preserved(self):
        # PACS/CT/MR 等含真实字母的编号不被误伤
        self.assertEqual(engine._extract_exam_no("影像号：PACS0001"), "PACS0001")
        self.assertEqual(engine._extract_exam_no("检查号：CT2026"), "CT2026")

    def test_alpha_heavy_untouched(self):
        # 纯字母号不触发归一
        self.assertEqual(engine._extract_exam_no("影像号：ABCDEF"), "ABCDEF")

    def test_long_number_ok(self):
        # 长度上限放宽到 40
        self.assertEqual(engine._extract_exam_no("影像号：12345678901234567890"),
                         "12345678901234567890")


class TestNameMiddleDot(unittest.TestCase):
    """姓名含间隔号·或较长时仍能抽取。"""

    def test_middle_dot(self):
        self.assertEqual(engine._extract_patient("姓名：阿米娜·古丽"), "阿米娜·古丽")

    def test_four_char(self):
        self.assertEqual(engine._extract_patient("姓名：欧阳娜娜"), "欧阳娜娜")


class TestExtractMetaFull(unittest.TestCase):
    """extract_meta_full 从所见/结论区补抽部位/侧别。"""

    def test_site_laterality_from_findings(self):
        basic = "影像号：CT1 姓名：张三 性别：男 年龄：45"  # 患者栏无部位侧别
        findings = "左肺上叶见结节，边缘毛刺"
        impression = "考虑腺癌"
        meta = engine.extract_meta_full(basic, findings, impression)
        self.assertEqual(meta["exam_no"], "CT1")
        self.assertEqual(meta["patient"], "张三")
        self.assertEqual(meta["laterality"], "左")
        self.assertEqual(meta["applied_site"], "胸部")

    def test_basic_site_not_overwritten(self):
        # basic 已含部位时，不被 findings 覆盖
        basic = "检查部位：腹部 影像号：X 姓名：Y"
        findings = "左肺上叶占位"
        meta = engine.extract_meta_full(basic, findings, "")
        self.assertEqual(meta["applied_site"], "腹部")

    def test_empty_findings_no_crash(self):
        meta = engine.extract_meta_full("姓名：张三")
        self.assertEqual(meta["patient"], "张三")
        self.assertEqual(meta["laterality"], "")
        self.assertEqual(meta["applied_site"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
