"""
ocr_runtime.py — OCR/截屏的共享运行时状态（2026-09-30 路由拆分 S3 抽出）

为什么单独成模块（而不是散在某个路由文件里）：
`/api/v1/screen/*` 与 `/api/v1/ocr*` 共用同一把推理锁与截屏缓存。历史上它们都在
main.py 里、靠模块级全局变量共享；一旦把端点拆到不同文件，若不把状态一并抽出来，
就会出现"两个模块各持一份锁/缓存"的隐性错误（锁不互斥 → 并发推理把内存打爆；
缓存各存一份 → 命中率归零）。故本模块是这两个路由**唯一**的状态来源。

包含：
  · `OCR_LOCK`        —— 推理串行锁（**必须共用**）
  · `SHOT`            —— 最近一次整屏截图（供按比例裁剪）
  · `OCR_CACHE`       —— 区域识别结果缓存（按裁剪图指纹）
  · `OCR_MAX_BYTES`   —— 上传图片大小上限
  · `grab_fullscreen()` / `ocr_config_path()`

## 为什么 OCR 必须串行（不要"优化"掉这把锁）

RapidOCR 单次推理峰值内存约 610MB。医院很多是 8GB 内存的低配桌面，
屏幕热键连按 + SPA 同时触发时若并发推理，会在同一实例上内存叠加导致 OOM
（2026-08-18 加固）。因此所有推理入口都必须 `with OCR_LOCK:`。
"""
import base64
import io
import json
import os
import threading
import time
from typing import Any

# 上传图片大小上限（超出则 413，避免大图把低配机器打爆）
OCR_MAX_BYTES = int(os.environ.get("QC_OCR_MAX_BYTES", str(20 * 1024 * 1024)))

# 最近一次整屏截图（原图，供 /screen/ocr 按比例框裁剪）。
# 「在原图上裁剪而非缩略图」可避免下采样导致小字识别率骤降。
SHOT: dict = {"img": None, "w": 0, "h": 0, "ts": 0.0}
SHOT_MAX_W = 1600          # 传给前端的缩略图最大宽度（省带宽，不影响识别精度）

# OCR 结果缓存：按「区域 key + 裁剪图指纹」缓存识别文本，画面未变时跳过推理。
OCR_CACHE: dict = {}       # region_key -> {"sig": tuple, "text": str}
OCR_CACHE_MAX = 12

# 截屏 + 推理共享锁：防「一个请求重抓屏覆盖另一个正在裁剪的原图」「缓存读写非原子」。
OCR_LOCK = threading.Lock()


def grab_fullscreen():
    """抓取整屏。macOS 未授权「屏幕录制」时会拿到纯黑图（不抛异常）→ 主动报错。"""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    if img is None:
        raise RuntimeError("截屏返回空图")
    try:
        ext = img.convert("L").getextrema()
        if ext == (0, 0):
            raise RuntimeError(
                "截屏结果全黑：macOS 需在『系统设置 → 隐私与安全性 → 屏幕录制』"
                "中勾选本应用（终端/星衍质控），授权后需重启应用。")
    except RuntimeError:
        raise
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return img


def remember_shot(img) -> None:
    """把整屏原图记入 SHOT（调用方应已持有 OCR_LOCK）。"""
    SHOT["img"], SHOT["w"], SHOT["h"], SHOT["ts"] = img, img.width, img.height, time.time()


def thumb_png(img, max_w: int = None):
    """生成给前端的缩略图 PNG bytes（等比缩小到 max_w 以内）。"""
    max_w = max_w or SHOT_MAX_W
    thumb = img
    if img.width > max_w:
        ratio = max_w / float(img.width)
        thumb = img.resize((max_w, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    thumb.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), thumb


def ocr_config_path() -> str:
    """OCR 区域配置路径（**单一来源**：src/paths.ocr_config_path）。

    ⚠️ 2026-09-30 修复：main.py 里曾另有一份实现，用 `%APPDATA%/MedicalReportQC`
    或 `~/.config/MedicalReportQC`；而 `paths.ocr_config_path()` 已统一到
    `user_data_dir()`。两者**指向不同文件**，但那份实现的 docstring 却写着
    "实现桌面/Web 区域配置互通" —— 即 Web 保存的区域框与桌面读取的配置**根本不是同一个
    文件**（正是本仓反复出现的"两套路径解析"问题）。现统一委托 paths。
    """
    import paths
    return paths.ocr_config_path()


def load_ocr_config() -> dict:
    try:
        with open(ocr_config_path(), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except FileNotFoundError:   # silent-except-ok: 首次运行没有该配置文件属正常分支
        return {}
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
        return {}


def save_ocr_config(cfg: dict) -> None:
    path = ocr_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    os.replace(tmp, path)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):   # silent-except-ok: 纯类型转换兜底，无副作用可掩盖
        return default
