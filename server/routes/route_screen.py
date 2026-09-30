"""
route_screen.py — 屏幕采集与框选 OCR（2026-09-30 路由拆分 S3）

从 `server/main.py` 迁出，端点实现**逐字搬迁**（除共享状态改由 ocr_runtime 提供）：

  POST /api/v1/screen/capture   抓整屏 → 返回缩略图（原图缓存供后续精确裁剪）
  POST /api/v1/screen/ocr       按比例框/动态模式在缓存原图上裁剪并 OCR
  GET  /api/v1/screen/regions   读取 SPA 保存的比例框（web_regions）
  PUT  /api/v1/screen/regions   保存比例框（含坐标校验）

共享状态（截图缓存 / OCR 结果缓存 / 推理锁）来自 `server/ocr_runtime.py` ——
**不要再在本文件里另建一份**：锁不共用会导致并发推理把低配机器内存打爆
（RapidOCR 单次峰值约 610MB），缓存各存一份则命中率归零。
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from server.core import _envelope
from server.ocr_runtime import (
    OCR_CACHE,
    OCR_CACHE_MAX,
    OCR_LOCK,
    SHOT,
    grab_fullscreen as _grab_fullscreen,   # 保持与旧实现同名，便于对照
    load_ocr_config,
    remember_shot,
    save_ocr_config,
    thumb_png,
)
from server.security import require_emp_local, require_license_active
from server.schemas import ScreenOCRReq

import engine
import ocr_provider

def _lg(where: str) -> None:
    """降级留痕（局部导入，零模块级依赖）。"""
    try:
        try:
            from ..log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(where)
    except Exception:           # silent-except-ok: 观测层自身失败绝不能抛
        pass


router = APIRouter(tags=["screen"])


@router.post("/api/v1/screen/capture")
def screen_capture(emp: str = Depends(require_emp_local),
                   _lic: bool = Depends(require_license_active)):
    """抓取整屏，返回缩略图 base64（原图缓存于服务端供后续高精度裁剪）。"""
    try:
        with OCR_LOCK:
            img = _grab_fullscreen()
            remember_shot(img)
    except Exception as exc:
        # 2026-09-30：抓屏失败必须**服务端留痕**——医生只会说"点了没反应/识别不了"，
        # 没有日志就无从排查（macOS 屏幕录制权限、远程桌面黑屏等都在此抛错）。
        _lg(__name__)
        return JSONResponse(status_code=503,
                            content=_envelope(False, "SCREEN_UNAVAILABLE", None,
                                              type(exc).__name__))
    image_b64, thumb = thumb_png(img)
    return _envelope(True, "OK", {
        "image_base64": image_b64,
        "width": img.width, "height": img.height,
        "thumb_width": thumb.width, "thumb_height": thumb.height,
        "ts": SHOT["ts"],
    })


@router.post("/api/v1/screen/ocr")
def screen_ocr(req: ScreenOCRReq, emp: str = Depends(require_emp_local),
               _lic: bool = Depends(require_license_active)):
    """按比例框在缓存的整屏原图上裁剪并 OCR，返回三区文本 + 结构化 meta。"""
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    img = SHOT.get("img")
    with OCR_LOCK:
        if req.refresh or img is None:
            try:
                img = _grab_fullscreen()
                remember_shot(img)
            except Exception as exc:
                _lg(__name__)      # 同上：抓屏失败要留痕
                return JSONResponse(status_code=503,
                                    content=_envelope(False, "SCREEN_UNAVAILABLE", None,
                                                      type(exc).__name__))
        W, H = img.width, img.height
        texts: Dict[str, str] = {}
        errors: Dict[str, str] = {}

        if req.dynamic:
            # ---- 动态语义识别：先按三区外接矩形裁剪（限定报告区），
            #      再对裁剪结果 OCR 一次，按标题在文本流中切分 ----
            # 固定像素框在 PACS 内容上下/左右滚动后会错位；本模式不依赖精确坐标，
            # 只依赖「检查所见/影像描述 → 描述段、诊断印象/结论 → 诊断段」的文本顺序，
            # 滚动改变的是屏幕上的像素位置，不改变文本流顺序，故怎么滚都能识别对。
            # 外接矩形只需粗略覆盖报告区即可：排除 PACS 报告区外的工具栏/图像区/
            # 其他窗口文字，避免整屏 OCR 把无关内容切进描述/诊断段。
            ocr_img = img
            if req.dynamic_region:
                dr = req.dynamic_region
                x0 = max(0, min(W - 1, int(dr.x * W)))
                y0 = max(0, min(H - 1, int(dr.y * H)))
                x1 = max(x0 + 1, min(W, int((dr.x + dr.w) * W)))
                y1 = max(y0 + 1, min(H, int((dr.y + dr.h) * H)))
                ocr_img = img.crop((x0, y0, x1, y1))
            try:
                full = ocr_provider.ocr_image(ocr_img) or ""
                texts, errors = engine.split_dynamic(full)
            except Exception as exc:
                errors["_dynamic"] = type(exc).__name__
        else:
            # ---- 固定框位模式（原逻辑，供精确框选场景）----
            for role, r in (req.regions or {}).items():
                x0 = max(0, min(W - 1, int(r.x * W)))
                y0 = max(0, min(H - 1, int(r.y * H)))
                x1 = max(x0 + 1, min(W, int((r.x + r.w) * W)))
                y1 = max(y0 + 1, min(H, int((r.y + r.h) * H)))
                crop = img.crop((x0, y0, x1, y1))
                # 画面未变则直接复用上次识别结果，跳过 CPU 推理（解决重复识别卡顿）
                try:
                    sig = ocr_provider.image_signature(crop)
                except Exception:
                    sig = None
                cached = OCR_CACHE.get(role)
                if cached and cached.get("sig") == sig:
                    texts[role] = cached.get("text") or ""
                    continue
                try:
                    txt = ocr_provider.ocr_image(crop) or ""
                    texts[role] = txt
                    OCR_CACHE[role] = {"sig": sig, "text": txt}
                    if len(OCR_CACHE) > OCR_CACHE_MAX:
                        OCR_CACHE.pop(next(iter(OCR_CACHE)))
                except Exception as exc:
                    texts[role] = ""
                    errors[role] = type(exc).__name__
    meta = {}
    try:
        meta = engine.extract_meta_full(texts.get("basic", ""),
                                        texts.get("findings", ""),
                                        texts.get("impression", ""))
    except Exception:
        try:
            meta = engine.extract_meta(texts.get("basic", ""))
        except Exception:
            meta = {}
    return _envelope(True, "OK", {"texts": texts, "meta": meta, "errors": errors})


@router.get("/api/v1/screen/regions")
def screen_regions_get(emp: str = Depends(require_emp_local)):
    """读取 SPA 侧保存的比例框（web_regions）。"""
    cfg = load_ocr_config()
    return _envelope(True, "OK", {"web_regions": cfg.get("web_regions") or {}})


@router.put("/api/v1/screen/regions")
def screen_regions_put(regions: Dict[str, Any], emp: str = Depends(require_emp_local)):
    # 坐标校验（2026-08-18）：0<=x,y<=1 且 0<w,h<=1 且非 NaN——此前原样持久化坏值，
    # 越界/NaN 会在 /screen/ocr 的 int(r.x*W) 抛 ValueError 500。
    import math
    for _k, r in (regions or {}).items():
        if not isinstance(r, dict):
            raise HTTPException(400, "区域格式应为 {key: {x,y,w,h}}")
        for _f in ("x", "y", "w", "h"):
            v = r.get(_f)
            if not isinstance(v, (int, float)) or math.isnan(v):
                raise HTTPException(400, f"区域坐标 {_f} 非法")
        if not (0 <= r["x"] <= 1 and 0 <= r["y"] <= 1
                and 0 < r["w"] <= 1 and 0 < r["h"] <= 1):
            raise HTTPException(400, "区域坐标越界（x/y 0~1，w/h 0~1 且 >0）")
    cfg = load_ocr_config()
    cfg["web_regions"] = regions
    try:
        save_ocr_config(cfg)
    except Exception as exc:
        # 2026-09-30：配置目录不可写时必须给出**明确错误**而不是 500 堆栈
        # （此前该路径的写入未包裹，只读目录下会抛未处理异常 → FastAPI 500 内部错误）。
        raise HTTPException(500, f"区域配置写入失败（配置目录不可写？）：{type(exc).__name__}")
    return _envelope(True, "OK", {"web_regions": regions}, "框选区域已保存")
