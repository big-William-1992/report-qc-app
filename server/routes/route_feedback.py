"""
route_feedback.py — 反馈闭环审核 API 路由
=========================================
把 R19 误报/漏报反馈闭环从 CLI 审核升级为网页审核：
- GET  /api/v1/feedback/pending  待审核列表（分页）
- GET  /api/v1/feedback/stats    统计（总量/待审/已审/真错字/误报/跳过）
- POST /api/v1/feedback/review   逐条审核（y=真错字 / n=误报 / s=跳过）
- POST /api/v1/feedback/apply    把已确认词应用到白名单/错字表

审核语义与 feedback_collector 完全一致：
- y（真错字）→ 直接 engine.learn_typo 学入 R8 错字表（rules_config typos）
- n（误报）→ 记入 whitelist_delta，apply 时写入 user_whitelist.json
- s（跳过）→ 仅记录
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from server.deps import _envelope, require_emp_local
import feedback_collector as fb

router = APIRouter(tags=['feedback'])


@router.get("/api/v1/feedback/pending")
def feedback_pending(limit: int = 0, emp: str = Depends(require_emp_local)):
    """待审核列表（按时间倒序，limit=0 返回全部）。"""
    try:
        items = fb.get_pending(limit=limit)
        return _envelope(True, "OK", items)
    except Exception as exc:
        raise HTTPException(500, f"读取待审核列表失败: {exc}")


@router.get("/api/v1/feedback/stats")
def feedback_stats(emp: str = Depends(require_emp_local)):
    """反馈统计。"""
    try:
        return _envelope(True, "OK", fb.get_stats())
    except Exception as exc:
        raise HTTPException(500, f"读取统计失败: {exc}")


@router.post("/api/v1/feedback/review")
def feedback_review(item_id: str, verdict: str, note: str = "",
                    emp: str = Depends(require_emp_local)):
    """审核单条告警。

    verdict: y=真错字（学入错字表）/ n=误报（进白名单增量）/ s=跳过
    """
    if verdict not in ("y", "n", "s"):
        raise HTTPException(400, "verdict 必须是 y / n / s")
    if not item_id.strip():
        raise HTTPException(400, "item_id 不能为空")
    try:
        ok = fb.review_one(item_id.strip(), verdict, note or "")
    except Exception as exc:
        raise HTTPException(500, f"审核失败: {exc}")
    if not ok:
        raise HTTPException(404, f"未找到待审核条目: {item_id}")
    return _envelope(True, "OK", {"id": item_id, "verdict": verdict})


@router.post("/api/v1/feedback/apply")
def feedback_apply(dry_run: bool = True, emp: str = Depends(require_emp_local)):
    """把 whitelist_delta 应用到白名单（dry_run=True 仅预览）。"""
    try:
        added, removed = fb.apply_delta(dry_run=dry_run)
        return _envelope(True, "OK", {"added": added, "removed": removed})
    except Exception as exc:
        raise HTTPException(500, f"应用白名单增量失败: {exc}")
