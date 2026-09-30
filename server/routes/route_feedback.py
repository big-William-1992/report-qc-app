"""
route_feedback.py — 医生反馈（badcase 回流）（2026-09-30 路由拆分 S6a）

从 `server/main.py` 迁出，端点实现**逐字搬迁**：

  POST /api/v1/feedback         医生提交误报/漏报（增量精调的唯一数据源）
  GET  /api/v1/feedback/stats   反馈统计
  GET  /api/v1/feedback/export  导出 JSONL（供 tools/export_badcase_training.py 消费）

存储为**独立的 feedback.db**（见 src/badcase_store.py），刻意不与 qc.db 同库：
便于诊断包单独导出与精调管线消费，不要"顺手并库"。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from server.core import _envelope
from server.security import require_emp_local, require_license_active
from server.schemas import FeedbackReq

router = APIRouter(tags=["feedback"])

@router.post("/api/v1/feedback")
def submit_feedback(req: FeedbackReq,
                    emp: str = Depends(require_emp_local),
                    _lic: bool = Depends(require_license_active)):
    """医生反馈回流: 误报👎/漏报➕ → feedback.db (P1-4 badcase 闭环入口)。
    存储失败不影响质控主流程(降级返回 ok=False 而非 500)。"""
    try:
        from badcase_store import record
        data = req.model_dump()
        data["user_id"] = emp
        fid = record(data)
        return _envelope(True, "已记录，感谢反馈", {"id": fid})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        from log_utils import log_quiet; log_quiet(__name__)
        return _envelope(False, "反馈暂存失败（不影响质控结果）", {})


@router.get("/api/v1/feedback/stats")
def feedback_stats(emp: str = Depends(require_emp_local)):
    """反馈计数(驾驶舱展示用)。"""
    try:
        from badcase_store import stats
        return _envelope(True, "OK", stats())
    except Exception:
        from log_utils import log_quiet; log_quiet(__name__)
        return _envelope(True, "OK", {"total": 0, "by_type": {}, "last_7d": 0})


@router.get("/api/v1/feedback/export")
def feedback_export(limit: int = 1000,
                    emp: str = Depends(require_emp_local)):
    """导出最近反馈(JSONL), 供 tools/export_badcase_training.py 精调管线消费。"""
    from badcase_store import list_recent
    import json as _json
    rows = list_recent(limit=limit)
    body = "\n".join(_json.dumps(r, ensure_ascii=False) for r in rows)
    return JSONResponse(content=body or "", media_type="application/x-ndjson")
