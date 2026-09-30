"""
route_ocr.py — 图片 OCR（2026-09-30 路由拆分 S3）

从 `server/main.py` 迁出，端点实现**逐字搬迁**：

  POST /api/v1/ocr         上传图片文件 → OCR 文本
  POST /api/v1/ocr/base64  base64 图片 → OCR 文本
  POST /api/v1/ocr/meta    三段文本 → 结构化抽取（姓名/性别/年龄/部位/侧别/检查类型）

推理走 `server/ocr_runtime.OCR_LOCK` 串行（RapidOCR 单次峰值约 610MB，
并发推理会让低配桌面 OOM；见该模块说明，不要绕开这把锁）。
"""
import base64 as _b64
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from server.core import _envelope
from server.ocr_runtime import OCR_LOCK, OCR_MAX_BYTES
from server.security import require_emp_local, require_license_active
from server.schemas import OCRB64, OCRMetaReq

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


router = APIRouter(tags=["ocr"])


@router.post("/api/v1/ocr")
async def ocr_upload(file: UploadFile = File(...), emp: str = Depends(require_emp_local),
                     _lic: bool = Depends(require_license_active)):
    from PIL import Image
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    data = await file.read(OCR_MAX_BYTES + 1)
    if len(data) > OCR_MAX_BYTES:
        raise HTTPException(413, f"图片过大（上限 {OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    # 推理串行（2026-08-18）：RapidOCR 单次峰值约 610MB，与 /screen/ocr 共用
    # OCR_LOCK，避免并发推理在同一实例上内存叠加导致医院低配桌面 OOM。
    with OCR_LOCK:
        text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


@router.post("/api/v1/ocr/base64")
def ocr_base64(req: OCRB64, emp: str = Depends(require_emp_local),
               _lic: bool = Depends(require_license_active)):
    from PIL import Image
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    if len(req.image_base64) > OCR_MAX_BYTES * 4 // 3:
        raise HTTPException(413, f"图片过大（上限 {OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        raw = _b64.b64decode(req.image_base64)
        img = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    with OCR_LOCK:
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
        meta = engine.extract_meta_full(req.basic or "", req.findings or "",
                                        req.impression or "")
    except Exception as exc:
        _lg(__name__)          # 结构化抽取失败留痕（前端只看到 META_ERR 代码）
        return _envelope(False, "META_ERR", None, type(exc).__name__)
    return _envelope(True, "OK", {"meta": meta})
