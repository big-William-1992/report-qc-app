"""
route_queue.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from server.schemas import QueueItemReq
from server.deps import _envelope, require_emp_local, _load_queue, _save_queue
import hashlib, time, uuid as _uuid

router = APIRouter(tags=['queue'])

@router.get("/api/v1/queue")
def queue_list(emp: str = Depends(require_emp_local)):
    items = _load_queue()
    return _envelope(True, "OK", {"items": items, "count": len(items)})


@router.post("/api/v1/queue")
def queue_add(req: QueueItemReq, emp: str = Depends(require_emp_local)):
    """加入待质控队列；按正文 MD5 去重（与桌面版同一去重口径）。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    norm = "".join(text.split())
    h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
    items = _load_queue()
    for it in items:
        if it.get("hash") == h:
            return _envelope(True, "OK", {"id": it["id"], "duplicated": True}, "该报告已在队列中")
    item = {
        "id": _uuid.uuid4().hex[:8],
        "hash": h,
        "patient": (req.patient or req.meta.get("patient", "")).strip(),
        "site": (req.site or req.meta.get("applied_site", "")).strip(),
        "text": text,
        "source": req.source or "手动",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "meta": req.meta or {},
    }
    items.append(item)
    _save_queue(items)
    return _envelope(True, "OK", {"id": item["id"], "duplicated": False, "count": len(items)})


@router.delete("/api/v1/queue")
def queue_clear(emp: str = Depends(require_emp_local)):
    _save_queue([])
    return _envelope(True, "OK", {"count": 0}, "队列已清空")


@router.delete("/api/v1/queue/{qid}")
def queue_remove(qid: str, emp: str = Depends(require_emp_local)):
    items = _load_queue()
    kept = [it for it in items if it.get("id") != qid]
    if len(kept) == len(items):
        raise HTTPException(404, "队列条目不存在")
    _save_queue(kept)
    return _envelope(True, "OK", {"count": len(kept)}, "已移出队列")

