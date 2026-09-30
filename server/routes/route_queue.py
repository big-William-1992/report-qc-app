"""
route_queue.py — 待质控队列（2026-09-30 路由拆分第一片）

从 `server/main.py` 拆出，端点实现**逐字搬迁**（不做行为改动），
仅把共享助手改为从 `server.core` / `server.security` 引入：

  GET    /api/v1/queue        列出队列（非 admin 仅见自己提交 + RIS 公共复核项）
  POST   /api/v1/queue        入队（正文 MD5 去重，落 qc.db QueueItem 表）
  DELETE /api/v1/queue        清空（仅 admin）
  DELETE /api/v1/queue/{qid}  移出单条（非 admin 仅可移出自己的/RIS 公共项）

拆分约定（详见 docs/ROUTES_SPLIT_PLAN.md）：
  1. 每个路由模块都必须在 main.py `include_router`（tests/test_single_implementation.py 守着）；
  2. 拆出后**必须删掉 main.py 里的原实现**，否则同一路径两份处理器，后者永不生效；
  3. 鉴权一律来自 `server.security`，不要在本文件另建一套。
"""
from fastapi import APIRouter, Depends, HTTPException

from server.core import (
    _envelope,
    _load_queue,
    _queue_orm_add_dedup,
    _queue_orm_clear,
    _queue_orm_remove,
)
from server.security import require_emp_local, require_license_active
from server.schemas import QueueItemReq

import accounts

router = APIRouter(tags=["queue"])


@router.get("/api/v1/queue")
def queue_list(emp: str = Depends(require_emp_local)):
    items = _load_queue()
    # 归属过滤（2026-08-18）：非 admin 仅看自己提交的队列项 + RIS 公共复核项（_emp=ris-poll）
    if accounts.get_role(emp) != "admin":
        items = [it for it in items
                 if (it.get("meta") or {}).get("_emp") in (emp, "ris-poll")]
    return _envelope(True, "OK", {"items": items, "count": len(items)})


@router.post("/api/v1/queue")
def queue_add(req: QueueItemReq, emp: str = Depends(require_emp_local),
              _lic: bool = Depends(require_license_active)):
    """加入待质控队列；按正文 MD5 去重（2026-08-18 收敛：落 qc.db QueueItem 表，
    数据库层唯一索引原子去重，杜绝并发重复入队）。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    meta = dict(req.meta or {})
    meta.setdefault("patient", (req.patient or "").strip())
    meta.setdefault("applied_site", (req.site or "").strip())
    meta.setdefault("source", req.source or "手动")
    meta.setdefault("_emp", emp)  # 记录提交人工号，供 queue_list 归属过滤（2026-08-18）
    new_id, duplicated = _queue_orm_add_dedup(text, meta)
    if duplicated:
        return _envelope(True, "OK", {"id": str(new_id), "duplicated": True}, "该报告已在队列中")
    items = _load_queue()
    return _envelope(True, "OK", {"id": str(new_id), "duplicated": False,
                                  "count": len(items) + 1})


@router.delete("/api/v1/queue")
def queue_clear(emp: str = Depends(require_emp_local),
                _lic: bool = Depends(require_license_active)):
    # 归属校验（2026-08-18）：清空是破坏性操作，仅 admin 可整表清空；
    # 普通医生只能清掉自己的条目（走逐条删除）。
    if accounts.get_role(emp) != "admin":
        raise HTTPException(403, "仅管理员可清空队列，可逐条移出自己提交的条目")
    _queue_orm_clear()
    return _envelope(True, "OK", {"count": 0}, "队列已清空")


@router.delete("/api/v1/queue/{qid}")
def queue_remove(qid: str, emp: str = Depends(require_emp_local),
                 _lic: bool = Depends(require_license_active)):
    try:
        qid_int = int(qid)
    except (TypeError, ValueError):
        raise HTTPException(404, "队列条目不存在")
    items = _load_queue()
    it = next((x for x in items if str(x.get("id")) == qid), None)
    if not it:
        raise HTTPException(404, "队列条目不存在")
    # 归属校验：非 admin 仅可移出自己提交（meta._emp == 工号）或 RIS 公共复核项
    if accounts.get_role(emp) != "admin":
        owner = (it.get("meta") or {}).get("_emp")
        if owner not in (emp, "ris-poll"):
            raise HTTPException(403, "只能移出自己提交的条目")
    if not _queue_orm_remove(qid_int):
        raise HTTPException(404, "队列条目不存在")
    return _envelope(True, "OK", {"count": len(_load_queue())}, "已移出队列")
