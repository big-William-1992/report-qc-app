# -*- coding: utf-8 -*-
"""OCR 优化三件套本地验证：
1. 变化检测：静止帧跳过 / 内容变化能触发
2. 置信度过滤：正常清晰文本不应被 0.7 阈值误杀（回归）
3. headless opencv 下 RapidOCR 完整推理可用
运行：python test/test_ocr_optimize.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PIL import Image, ImageDraw, ImageFont
import ocr_provider
import engine


def _render(lines, size=(420, 160)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    font = None
    for fp in ("/System/Library/Fonts/STHeiti Light.ttc",
               "/System/Library/Fonts/Hiragino Sans GB.ttc",
               "C:/Windows/Fonts/msyh.ttc"):
        if os.path.isfile(fp):
            try:
                font = ImageFont.truetype(fp, 26)
                break
            except Exception:
                pass
    y = 12
    for ln in lines:
        d.text((14, y), ln, fill="black", font=font)
        y += 34
    return img


def test_change_detection():
    a = _render(["姓名：张伟", "性别：男 年龄：54岁", "检查部位：胸部CT"])
    a2 = a.copy()
    b = _render(["姓名：李芳", "性别：女 年龄：33岁", "检查部位：头颅MR"])
    sa, sa2, sb = (ocr_provider.image_signature(x) for x in (a, a2, b))
    assert not ocr_provider.signature_changed(sa, sa2), "完全相同帧不应判定为变化"
    assert ocr_provider.signature_changed(sa, sb), "换病人帧必须判定为变化"
    assert ocr_provider.signature_changed(None, sa), "首帧(None)必须判定为变化"
    print("[PASS] 变化检测：静止跳过 / 换人触发 / 首帧触发")


def test_ocr_with_confidence():
    ok, reason = ocr_provider.availability()
    assert ok, f"OCR 不可用：{reason}"
    img = _render(["姓名：张伟", "性别：男 年龄：54岁", "检查部位：胸部CT"])
    text = ocr_provider.ocr_image(img)          # 默认 0.7 阈值
    print("  识别文本：", text.replace("\n", " / "))
    meta = engine.extract_meta(text)
    assert meta.get("patient") == "张伟", f"姓名解析失败: {meta}"
    assert meta.get("gender") == "男", f"性别解析失败: {meta}"
    assert "54" in (meta.get("age") or ""), f"年龄解析失败: {meta}"
    print("[PASS] 置信度过滤下清晰文本完整识别（张伟/男/54/胸部）")


def test_headless_cv2():
    import cv2
    # headless 版没有 GUI 模块，但 OCR 用到的核心 API 必须在
    for fn in ("resize", "cvtColor", "copyMakeBorder"):
        assert hasattr(cv2, fn), f"cv2.{fn} 缺失"
    print(f"[PASS] cv2 {cv2.__version__} 核心 API 可用（headless 兼容）")


if __name__ == "__main__":
    test_change_detection()
    test_headless_cv2()
    test_ocr_with_confidence()
    print("\n全部通过 ✅")
