"""
route_ocr.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, UploadFile, File, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
from server.schemas import OCRB64, OCRMetaReq
from server.deps import _OCR_MAX_BYTES, _envelope, require_emp_local
import io, base64 as _b64, engine

router = APIRouter(tags=['ocr'])

@router.post("/api/v1/ocr")
async def ocr_upload(file: UploadFile = File(...), emp: str = Depends(require_emp_local)):
    from PIL import Image
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    data = await file.read(_OCR_MAX_BYTES + 1)
    if len(data) > _OCR_MAX_BYTES:
        raise HTTPException(413, f"图片过大（上限 {_OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


@router.post("/api/v1/ocr/base64")
def ocr_base64(req: OCRB64, emp: str = Depends(require_emp_local)):
    from PIL import Image
    import ocr_provider
    import base64 as _b64
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    if len(req.image_base64) > _OCR_MAX_BYTES * 4 // 3:
        raise HTTPException(413, f"图片过大（上限 {_OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        raw = _b64.b64decode(req.image_base64)
        img = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})
@router.post("/api/v1/ocr/meta")
def ocr_meta(req: OCRMetaReq, emp: str = Depends(require_emp_local)):
    """对三段 OCR 文本做结构化抽取（姓名/性别/年龄/部位/侧别/检查类型）。

    与 ``/screen/ocr`` 共用 ``engine.extract_meta_full``，保证「屏幕模式」与「图片模式」
    姓名回填行为一致、都走后端最稳健的跨区补抽逻辑。前端在图片模式下拿到本结果后
    优先用于回填，避免只依赖前端解析在『独立姓名行』等边缘布局下漏抽。
    """
    try:
        meta = engine.extract_meta_full(req.basic or "", req.findings or "", req.impression or "")
    except Exception as exc:
        return _envelope(False, "META_ERR", None, str(exc))
    return _envelope(True, "OK", {"meta": meta})
