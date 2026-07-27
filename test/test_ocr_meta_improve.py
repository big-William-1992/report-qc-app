# -*- coding: utf-8 -*-
"""OCR 基础信息识别精度提升 + 一次性触发 的回归测试（v2.3）。

覆盖三处改动：
1) 标签空格容错（PACS OCR 常把『姓名』识别成『姓 名』）→ 仍能抽到姓名/影像号；
2) 影像号抽取 + OCR 数字混淆归一（O/o→0、I/l→1）；
3) 矮条小字区域（高 < SMALL_REGION_H）预处理 2x 放大，抬升字高提升检出；
4) 『影像号/姓名』组合显示值 format_patient_ident 的纯函数约定。

本文件使用**真实** cv2/numpy（不 mock），因为 preprocess_for_ocr 的放大逻辑
必须跑真实 resize。请单独进程运行，避免被其它测试把 cv2 替换成 MagicMock。
运行：python test/test_ocr_meta_improve.py
"""
import os
import sys
import unittest

REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, REPO_SRC)

import numpy as np  # noqa: E402  (真实 numpy，必须早于任何 mock 环境)
import ocr_provider as O  # noqa: E402
import engine  # noqa: E402


class TestLabelSpaceTolerance(unittest.TestCase):
    """OCR 常在标签字符间混入空格/制表符，抽取必须容忍。"""

    def test_patient_spaced_label(self):
        self.assertEqual(engine._extract_patient("姓 名：张三"), "张三")
        self.assertEqual(engine._extract_patient("姓\t名:李四"), "李四")

    def test_patient_plain_label(self):
        self.assertEqual(engine._extract_patient("患者姓名：王五"), "王五")
        self.assertEqual(engine._extract_patient("姓名：赵六"), "赵六")

    def test_patient_absent(self):
        self.assertEqual(engine._extract_patient("无任何标识字段文本"), "")
        self.assertEqual(engine._extract_patient(""), "")

    def test_exam_no_spaced_label(self):
        self.assertEqual(engine._extract_exam_no("影 像 号：CT20240715001"),
                         "CT20240715001")
        self.assertEqual(engine._extract_exam_no("检 查 号: PACS0001"), "PACS0001")

    def test_lab_re_tolerates_gaps(self):
        pat = engine._lab_re("姓名")
        import re
        self.assertIsNotNone(re.search(pat + r"[:：\s]*([\u4e00-\u9fa5]{1,4})",
                                        "姓 名：张三"))


class TestExamNoExtraction(unittest.TestCase):
    """影像号/检查号 抽取 + 数字混淆归一。"""

    def test_strong_labels(self):
        for lab in ("影像号", "检查号", "图像号", "放射号", "RIS号", "PACS号",
                    "门诊号", "住院号", "编号"):
            with self.subTest(lab=lab):
                self.assertEqual(engine._extract_exam_no(f"{lab}：12345678"),
                                 "12345678")

    def test_digit_confusion_normalized(self):
        # O/o→0, I/l→1：输入 "O0I1"（2 位真数字，过半 → 触发归一）
        self.assertEqual(engine._extract_exam_no("检查号：O0I1"), "0011")
        # "1l0O"：数字 1+0=2/4 过半 → 归一 l→1, O→0 → "1100"
        self.assertEqual(engine._extract_exam_no("影像号：1l0O"), "1100")

    def test_no_confusion_for_alpha_heavy(self):
        # 纯字母号（数字不过半）→ 不做混淆替换，保留原样
        self.assertEqual(engine._extract_exam_no("影像号：ABCDEF"), "ABCDEF")

    def test_absent(self):
        self.assertEqual(engine._extract_exam_no("无编号信息"), "")
        self.assertEqual(engine._extract_exam_no(""), "")

    def test_extract_meta_returns_exam_no(self):
        meta = engine.extract_meta("影像号：CT123 姓名：张三 性别：男 年龄：45")
        self.assertEqual(meta["exam_no"], "CT123")
        self.assertEqual(meta["patient"], "张三")


class TestFormatPatientIdent(unittest.TestCase):
    """『影像号/姓名』输入框组合显示值约定。"""

    def test_both(self):
        self.assertEqual(engine.format_patient_ident("CT123", "张三"), "CT123/张三")

    def test_only_exam_no(self):
        self.assertEqual(engine.format_patient_ident("CT123", ""), "CT123")

    def test_only_name(self):
        self.assertEqual(engine.format_patient_ident("", "张三"), "张三")

    def test_neither(self):
        self.assertEqual(engine.format_patient_ident("", ""), "")
        self.assertEqual(engine.format_patient_ident(None, None), "")

    def test_pipeline(self):
        text = "影像号：CT2024 姓名：张三 性别：男 年龄：45"
        meta = engine.extract_meta(text)
        self.assertEqual(engine.format_patient_ident(meta["exam_no"], meta["patient"]),
                         "CT2024/张三")


class TestPreprocessSmallRegionUpscale(unittest.TestCase):
    """矮条小字区域（高 < SMALL_REGION_H）预处理应 2x 放大抬升字高。"""

    def test_small_region_upscaled(self):
        h, w = 40, 120  # 低于 SMALL_REGION_H(96)
        img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        out = O.preprocess_for_ocr(img)
        self.assertEqual(out.shape[0], h * 2)
        self.assertEqual(out.shape[1], w * 2)
        self.assertEqual(out.shape[2], 3)

    def test_boundary_not_upscaled(self):
        h, w = 100, 120  # 大于阈值，不放大
        img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        out = O.preprocess_for_ocr(img)
        self.assertEqual(out.shape[0], h)
        self.assertEqual(out.shape[1], w)

    def test_pil_input_small_region(self):
        from PIL import Image
        img = Image.new("RGB", (120, 40), (200, 200, 200))
        out = O.preprocess_for_ocr(img)
        self.assertEqual(out.shape[0], 80)  # 40 * 2


if __name__ == "__main__":
    unittest.main(verbosity=2)
