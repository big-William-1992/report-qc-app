"""
report_qc_app/src/ocr_provider.py
「屏幕区域监控」的 OCR 提供器 —— 全部本地离线，不联网，符合医疗数据不出域合规要求。

数据流：
    框选屏幕区域 (x, y, w, h) → 截图 → RapidOCR 识别中文 → 文本
    → engine.extract_meta(text) 解析 姓名/性别/年龄/检查部位/侧别
    → 回填元信息输入框 → 触发 engine.run → 自动驱动 R1/R3/R6

引擎选型：RapidOCR（onnxruntime 本地推理，中文效果好，~16MB 模型）。
模型放置优先级：
    1) assets/ocr_models（随 exe/源码分发内置，完全离线）
    2) RapidOCR 包内置默认模型（首次运行会从官网自动下载，需联网一次）
若两种都不可用（未安装 rapidocr-onnxruntime），availability() 返回 False 并给出 pip 安装提示。
"""

import os
import sys

_PIP_HINT = "请先安装 OCR 依赖：pip install rapidocr-onnxruntime Pillow"


def _assets_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def _model_dir() -> str:
    return os.path.join(_assets_dir(), "ocr_models")


_engine = None          # 已初始化的 RapidOCR 实例
_engine_err = None      # 初始化失败原因（缓存，避免重复尝试）


def availability() -> (bool, str):
    """返回 (是否可用, 说明)。可用时说明为空串。"""
    try:
        eng = _get_engine()
    except Exception as e:  # 不应发生，_get_engine 内部已吞异常
        return False, str(e)
    if eng is None:
        return False, _engine_err or _PIP_HINT
    return True, ""


def _get_engine():
    """懒加载 RapidOCR 实例；失败只记录一次原因并返回 None（不抛异常）。"""
    global _engine, _engine_err
    if _engine is not None:
        return _engine
    if _engine_err is not None:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        _engine_err = f"未安装 RapidOCR：{e}。{_PIP_HINT}"
        return None
    try:
        mdir = _model_dir()
        det = os.path.join(mdir, "ch_PP-OCRv3_det_infer.onnx")
        rec = os.path.join(mdir, "ch_PP-OCRv3_rec_infer.onnx")
        cls = os.path.join(mdir, "ch_ppocr_mobile_v2.0_cls_infer.onnx")
        if os.path.isfile(det) and os.path.isfile(rec) and os.path.isfile(cls):
            # 离线内置模型：新版 RapidOCR 字符字典已内嵌在 rec.onnx，无需外部 keys.txt
            kw = dict(model_path=det, rec_model_path=rec, cls_model_path=cls)
            keys = os.path.join(mdir, "ppocr_keys_v1.txt")
            if os.path.isfile(keys):
                kw["rec_char_dict_path"] = keys
            _engine = RapidOCR(**kw)
        else:
            # 回退到 RapidOCR 自带模型（首次会从官网下载，需联网一次；
            # 若已下载则走本地缓存 ~/.cache/rapidocr_onnxruntime）
            _engine = RapidOCR()
    except Exception as e:
        _engine_err = f"RapidOCR 初始化失败：{e}"
        return None
    return _engine


def _grab_region(bbox) -> object:
    """截取屏幕区域 → PIL.Image。bbox = (x, y, w, h) 逻辑像素坐标。

    平台说明：
    - macOS：PIL ImageGrab 自动按 Retina 缩放，传入逻辑坐标即可（需『屏幕录制』权限）。
    - Windows：逻辑坐标即像素，直接可用。
    权限不足时 ImageGrab 会抛异常或返回空白图，由调用方捕获提示。
    """
    from PIL import ImageGrab
    x, y, w, h = [int(v) for v in bbox]
    if w <= 0 or h <= 0:
        raise ValueError("区域宽高必须为正数")
    return ImageGrab.grab(bbox=(x, y, x + w, y + h))


def ocr_image(img) -> str:
    """对 PIL.Image 做 OCR，返回识别文本（按行拼接）。"""
    eng = _get_engine()
    if eng is None:
        raise RuntimeError(_engine_err or _PIP_HINT)
    # RapidOCR 接受 numpy.ndarray / 路径 / bytes，不直接接受 PIL.Image
    if hasattr(img, "mode"):  # 疑似 PIL.Image
        import numpy as np
        img = np.asarray(img)
    try:
        result, _ = eng(img)
    except Exception as e:
        raise RuntimeError(f"OCR 推理失败：{e}")
    if not result:
        return ""
    lines = []
    for item in result:
        # RapidOCR 单条：[box, (text, score)] 或 [box, text, score]
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        payload = item[1]
        if isinstance(payload, (list, tuple)):
            txt = payload[0] if payload else ""
        else:
            txt = payload
        if txt:
            lines.append(txt)
    return "\n".join(lines).strip()


def region_to_text(bbox) -> str:
    """截图指定屏幕区域并识别文本（区域监控主入口）。"""
    img = _grab_region(bbox)
    return ocr_image(img)


def capture_region(bbox):
    """仅截图，返回 PIL.Image（供调试/预览）。"""
    return _grab_region(bbox)
