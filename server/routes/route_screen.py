"""
route_screen.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from typing import Dict, Any
from server.schemas import ScreenOCRReq
from server.deps import _OCR_LOCK, _OCR_CACHE, _OCR_CACHE_MAX, _SHOT, _SHOT_MAX_W, _envelope, require_emp_local, _grab_fullscreen, _ocr_config_path, _split_dynamic
import io, base64, time, json, engine

router = APIRouter(tags=['screen'])

@router.post("/api/v1/screen/capture")
def screen_capture(emp: str = Depends(require_emp_local)):
    """抓取整屏，返回缩略图 base64（原图缓存于服务端供后续高精度裁剪）。"""
    try:
        with _OCR_LOCK:
            img = _grab_fullscreen()
            _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = img, img.width, img.height, time.time()
    except Exception as exc:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "SCREEN_UNAVAILABLE", None, str(exc)))
    thumb = img
    if img.width > _SHOT_MAX_W:
        ratio = _SHOT_MAX_W / float(img.width)
        thumb = img.resize((_SHOT_MAX_W, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    thumb.convert("RGB").save(buf, format="PNG")
    return _envelope(True, "OK", {
        "image_base64": base64.b64encode(buf.getvalue()).decode(),
        "width": img.width, "height": img.height,
        "thumb_width": thumb.width, "thumb_height": thumb.height,
        "ts": _SHOT["ts"],
    })


@router.post("/api/v1/screen/ocr")
def screen_ocr(req: ScreenOCRReq, emp: str = Depends(require_emp_local)):
    """按比例框在缓存的整屏原图上裁剪并 OCR，返回三区文本 + 结构化 meta。"""
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    img = _SHOT.get("img")
    with _OCR_LOCK:
        if req.refresh or img is None:
            try:
                img = _grab_fullscreen()
                _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = \
                    img, img.width, img.height, time.time()
            except Exception as exc:
                return JSONResponse(status_code=503,
                                    content=_envelope(False, "SCREEN_UNAVAILABLE", None, str(exc)))
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
                texts, errors = _split_dynamic(full)
            except Exception as exc:
                errors["_dynamic"] = str(exc)
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
                cached = _OCR_CACHE.get(role)
                if cached and cached.get("sig") == sig:
                    texts[role] = cached.get("text") or ""
                    continue
                try:
                    txt = ocr_provider.ocr_image(crop) or ""
                    texts[role] = txt
                    _OCR_CACHE[role] = {"sig": sig, "text": txt}
                    if len(_OCR_CACHE) > _OCR_CACHE_MAX:
                        _OCR_CACHE.pop(next(iter(_OCR_CACHE)))
                except Exception as exc:
                    texts[role] = ""
                    errors[role] = str(exc)
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
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    return _envelope(True, "OK", {"web_regions": cfg.get("web_regions") or {}})


@router.put("/api/v1/screen/regions")
def screen_regions_put(regions: Dict[str, Any], emp: str = Depends(require_emp_local)):
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    cfg["web_regions"] = regions
    with open(_ocr_config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return _envelope(True, "OK", {"web_regions": regions}, "框选区域已保存")
