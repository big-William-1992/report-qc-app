# -*- coding: utf-8 -*-
"""OCR 优化三件套本地验证：
1. 变化检测：静止帧跳过 / 内容变化能触发
2. 置信度过滤：正常清晰文本不应被 0.7 阈值误杀（回归）
3. headless opencv 下 RapidOCR 完整推理可用
运行：python test/test_ocr_optimize.py
"""
import os
import sys


from PIL import Image, ImageDraw, ImageFont
import ocr_provider
import engine

import pytest


# CI（ubuntu-latest）默认**不装中文字体**：原先的字体查找全部落空 → font=None →
# PIL 退回内置位图字体，中文渲染成乱码，OCR 读出 "8888888548 / B888888CT"，
# 于是 `姓名解析失败` 假失败（2026-10-01 Build Windows #172 实测复现）。
# 本测试**验证的是 OCR 链路**，不是字体环境；无 CJK 字体时应 skip 而非 fail。
_CJK_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    # Linux（CI 装了 fonts-noto-cjk / fonts-wqy 时可用）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


def _load_cjk_font(size: int = 26):
    """返回可渲染中文的字体对象；找不到返回 None。

    先按已知路径找（快），找不到再**扫描字体目录**（稳）—— fonts-noto-cjk 在
    不同发行版/版本下的落盘路径并不一致（/usr/share/fonts/opentype/noto/ 或
    /usr/share/fonts/noto-cjk/ 等），硬编码单一路径容易再次出现
    "本地能跑、CI skip" 的情况。
    """
    for fp in _CJK_FONT_CANDIDATES:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    # 兜底：扫常见字体根目录下的 CJK 字体文件
    import glob
    patterns = (
        "/usr/share/fonts/**/NotoSansCJK*",
        "/usr/share/fonts/**/NotoSerifCJK*",
        "/usr/share/fonts/**/wqy*.tt[cf]",
        "/usr/share/fonts/**/*ming*.tt[cf]",
        "/usr/share/fonts/**/*CJK*.tt[cf]",
        "/usr/local/share/fonts/**/*CJK*.tt[cf]",
    )
    for pat in patterns:
        for fp in sorted(glob.glob(pat, recursive=True)):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return None


def _render(lines, size=(420, 160)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    font = _load_cjk_font(26)
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
    if _load_cjk_font() is None:
        pytest.skip(
            "环境无中文字体（CI ubuntu-latest 默认不装），无法渲染中文样本 —— "
            "本用例验证的是 OCR 链路而非字体环境，跳过而非误报失败。"
            "如需在 CI 跑：apt-get install -y fonts-noto-cjk"
        )
    ok, reason = ocr_provider.availability()
    assert ok, f"OCR 不可用：{reason}"
    img = _render(["姓名：张伟", "性别：男 年龄：54岁", "检查部位：胸部CT"])
    text = ocr_provider.ocr_image(img)          # 默认 0.7 阈值
    print("  识别文本：", text.replace("\n", " / "))
    meta = engine.extract_meta(text)
    # 明确区分「OCR 没读出来」与「解析失败」：前者说明渲染/字体仍有问题，
    # 避免把乱码当"部分识别"而让断言给出误导性的失败原因。
    assert "张伟" in text, (
        f"OCR 未能识别出中文样本（很可能字体渲染异常，而非阈值过滤）：{text!r}"
    )
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
