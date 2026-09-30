"""
route_admin.py — 管理端（审计/授权/订单/错误报告/备份）（2026-09-30 路由拆分 S6b）

从 `server/main.py` 迁出，端点实现**逐字搬迁**（共 15 个）：

审计日志   GET  /api/v1/admin/audit-logs · /audit-logs/export
授权管理   GET  /api/v1/admin/license/status
           POST /api/v1/admin/license/deactivate · /license/extend
订单管理   GET  /api/v1/admin/orders · /orders/export
           POST /api/v1/admin/orders/create · /confirm · /cancel
错误报告   GET  /api/v1/admin/errors · /errors/export
备份恢复   GET  /api/v1/admin/backup/status
           POST /api/v1/admin/backup/run · /backup/restore

全部走 `require_admin`（除个别读接口用 `require_emp_local`）。
依赖均为共享模块：`server.models.Order`（订单状态机）、`src/backup`、
`src/error_reporter`、`src/license_utils` —— **本文件无自有模块级状态**。

## 2026-09-30 修复：订单域 5 个端点此前 100% 不可用

`orders` 的 list/create/confirm/cancel/export 都调用 `db.get_session()`，
而 `server/db.py` **没有这个函数**（只有 `SessionLocal` 与 `get_db` 生成器）→
每个请求都以 `AttributeError` 失败。因为该域**没有任何测试覆盖**，
这个缺陷一直存在（连同 main.py 里的 `/api/v1/export/data`，同一写法）。
现统一改为 `SessionLocal()`（本仓其余代码的写法）。
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from server.core import SessionLocal, _envelope
from server.security import require_admin


router = APIRouter(tags=["admin"])

@router.get("/api/v1/admin/audit-logs")
def audit_log_list(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                   action: str = "", emp_id: str = "",
                   start: str = "", end: str = "",
                   admin: str = Depends(require_admin)):
    """管理员查看操作审计日志（按时间倒序，支持按操作类型/工号/时间范围筛选）。"""
    from datetime import datetime as _dt
    from sqlalchemy import desc
    from server.models import AuditLog

    def _parse_dt(value: str):
        if not value:
            return None
        try:
            return _dt.fromisoformat(value)
        except Exception:
            raise HTTPException(400, "时间格式无效")

    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)

    sess = SessionLocal()
    try:
        q = sess.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action == action)
        if emp_id:
            q = q.filter(AuditLog.emp_id == emp_id)
        if start_dt:
            q = q.filter(AuditLog.ts >= start_dt)
        if end_dt:
            q = q.filter(AuditLog.ts <= end_dt)
        q = q.order_by(desc(AuditLog.ts), desc(AuditLog.id))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        items = [{"id": r.id, "ts": r.ts.isoformat() if r.ts else "",
                  "emp_id": r.emp_id, "action": r.action,
                  "detail": r.detail or "", "ip": r.ip or ""}
                 for r in rows]
        return _envelope(True, "OK", {
            "total": total, "items": items,
            "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        })
    finally:
        sess.close()




@router.get("/api/v1/admin/audit-logs/export")
def audit_log_export(format: str = "json", action: str = "",
                     emp_id: str = "", start: str = "", end: str = "",
                     admin: str = Depends(require_admin)):
    """批量导出审计日志（JSON/CSV），用于多台部署集中归档（P2 改造，2026-09-12）。

    参数：
      format: "json"（默认）或 "csv"
      action / emp_id / start / end: 同 audit-logs 端点
    返回：
      JSON: {"ok": true, "items": [...], "count": N}
      CSV:  text/csv 文件（Content-Disposition 触发下载）
    """
    import csv
    import io
    from datetime import datetime as _dt
    from sqlalchemy import desc
    from server.models import AuditLog
    from fastapi.responses import Response

    def _parse_dt(value: str):
        if not value:
            return None
        try:
            return _dt.fromisoformat(value)
        except Exception:
            raise HTTPException(400, "时间格式无效")

    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)

    sess = SessionLocal()
    try:
        q = sess.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action == action)
        if emp_id:
            q = q.filter(AuditLog.emp_id == emp_id)
        if start_dt:
            q = q.filter(AuditLog.ts >= start_dt)
        if end_dt:
            q = q.filter(AuditLog.ts <= end_dt)
        q = q.order_by(desc(AuditLog.ts), desc(AuditLog.id))
        rows = q.all()

        items = [{"id": r.id, "ts": r.ts.isoformat() if r.ts else "",
                  "emp_id": r.emp_id, "action": r.action,
                  "detail": r.detail or "", "ip": r.ip or ""}
                 for r in rows]

        if format.lower() == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "ts", "emp_id", "action", "detail", "ip"])
            for it in items:
                w.writerow([it["id"], it["ts"], it["emp_id"], it["action"],
                            it["detail"], it["ip"]])
            csv_content = buf.getvalue()
            return Response(
                content=csv_content,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=audit_log_{_dt.now().strftime('%Y%m%d')}.csv"})

        return _envelope(True, "OK", {"items": items, "count": len(items)})
    finally:
        sess.close()


# ── 授权管理 API（2026-09-12 商业化）──────────────────────────────────


@router.get("/api/v1/admin/license/status")
def license_status(admin: str = Depends(require_admin)):
    """查看授权状态：单机/浮动、座位数、试用期、剩余天数等。"""
    import license_utils as _lu
    return _envelope(True, "OK", _lu.get_license_info())


@router.post("/api/v1/admin/license/deactivate")
def license_deactivate(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """吊销授权。单机模式清除本机激活；浮动模式移除指定机器心跳。"""
    import license_utils as _lu
    machine_id_str = (req.get("machine_id") or "").strip()
    result = _lu.deactivate(machine_id_str)
    if not result.get("ok"):
        raise HTTPException(500, result.get("message", "吊销失败"))
    return _envelope(True, "OK", result, "吊销成功")


@router.post("/api/v1/admin/license/extend")
def license_extend(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """延长试用/授权天数。"""
    import license_utils as _lu
    days = int(req.get("days") or 0)
    reason = (req.get("reason") or "").strip()
    if days <= 0:
        raise HTTPException(400, "天数必须大于 0")
    result = _lu.extend_license(days, reason)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "延长失败"))
    return _envelope(True, "OK", result, "延长成功")


# ── 订单管理 API（2026-09-12 商业化）──────────────────────────────────


@router.get("/api/v1/admin/orders")
def orders_list(status: str = "", limit: int = Query(100, ge=1, le=500),
                admin: str = Depends(require_admin)):
    """查询订单列表。"""
    from sqlalchemy import desc
    from server.models import Order
    db_s = SessionLocal()   # 2026-09-30 修复：db.get_session() 并不存在（见文件头说明）
    try:
        q = db_s.query(Order).order_by(desc(Order.created_at)).limit(limit)
        if status:
            q = q.filter(Order.status == status)
        rows = q.all()
        return _envelope(True, "OK", [
            {"id": r.id, "order_no": r.order_no, "product": r.product,
             "amount": r.amount, "customer_name": r.customer_name,
             "customer_contact": r.customer_contact, "department_id": r.department_id,
             "status": r.status, "paid_at": r.paid_at.isoformat() if r.paid_at else None,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "notes": r.notes or ""} for r in rows
        ])
    finally:
        db_s.close()


@router.post("/api/v1/admin/orders/create")
def orders_create(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """创建订单（手动收款时记录）。"""
    from datetime import datetime
    from server.models import Order
    import uuid
    customer_name = (req.get("customer_name") or "").strip()
    if not customer_name:
        raise HTTPException(400, "缺少客户名称")
    amount = int(req.get("amount") or 59)
    order_no = f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    db_s = SessionLocal()   # 2026-09-30 修复：db.get_session() 并不存在（见文件头说明）
    try:
        o = Order(
            order_no=order_no,
            product=req.get("product", "年费授权"),
            amount=amount,
            customer_name=customer_name,
            customer_contact=req.get("customer_contact", ""),
            department_id=req.get("department_id", ""),
            notes=req.get("notes", ""),
        )
        db_s.add(o)
        db_s.commit()
        db_s.refresh(o)
        return _envelope(True, "OK", {
            "id": o.id, "order_no": o.order_no, "amount": o.amount,
            "customer_name": o.customer_name, "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }, "订单已创建")
    finally:
        db_s.close()


@router.post("/api/v1/admin/orders/confirm")
def orders_confirm(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """确认收款（手动核销）。"""
    from datetime import datetime
    from server.models import Order
    order_no = (req.get("order_no") or "").strip()
    if not order_no:
        raise HTTPException(400, "缺少订单号")
    db_s = SessionLocal()   # 2026-09-30 修复：db.get_session() 并不存在（见文件头说明）
    try:
        o = db_s.query(Order).filter(Order.order_no == order_no).first()
        if not o:
            raise HTTPException(404, f"订单 {order_no} 不存在")
        if o.status != "pending":
            raise HTTPException(400, f"订单状态已是 {o.status}，不能重复确认")
        o.status = "paid"
        o.paid_at = datetime.now()
        if req.get("notes"):
            o.notes = (o.notes or "") + " | " + req["notes"]
        db_s.commit()
        return _envelope(True, "OK", {"order_no": o.order_no, "status": o.status},
                         "收款已确认")
    finally:
        db_s.close()


@router.post("/api/v1/admin/orders/cancel")
def orders_cancel(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """取消订单或退款。"""
    from server.models import Order
    order_no = (req.get("order_no") or "").strip()
    if not order_no:
        raise HTTPException(400, "缺少订单号")
    new_status = (req.get("new_status") or "cancelled").strip()
    if new_status not in ("cancelled", "refunded"):
        raise HTTPException(400, "状态必须是 cancelled 或 refunded")
    db_s = SessionLocal()   # 2026-09-30 修复：db.get_session() 并不存在（见文件头说明）
    try:
        o = db_s.query(Order).filter(Order.order_no == order_no).first()
        if not o:
            raise HTTPException(404, f"订单 {order_no} 不存在")
        o.status = new_status
        db_s.commit()
        return _envelope(True, "OK", {"order_no": o.order_no, "status": o.status},
                         f"订单已{new_status}")
    finally:
        db_s.close()




@router.get("/api/v1/admin/orders/export")
def orders_export(format: str = "csv", admin: str = Depends(require_admin)):
    """导出订单记录为 CSV。"""
    import csv, io
    from datetime import datetime
    from sqlalchemy import desc
    from server.models import Order
    from fastapi.responses import Response
    db_s = SessionLocal()   # 2026-09-30 修复：db.get_session() 并不存在（见文件头说明）
    try:
        rows = db_s.query(Order).order_by(desc(Order.created_at)).all()
    finally:
        db_s.close()
    if format == "json":
        return _envelope(True, "OK", [
            {"order_no": r.order_no, "product": r.product, "amount": r.amount,
             "customer_name": r.customer_name, "customer_contact": r.customer_contact,
             "department_id": r.department_id, "status": r.status,
             "paid_at": r.paid_at.isoformat() if r.paid_at else None,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "notes": r.notes or ""} for r in rows
        ])
    # CSV
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["订单号", "产品", "金额(元)", "客户名称", "联系方式", "科室", "状态", "收款时间", "创建时间", "备注"])
    for r in rows:
        w.writerow([r.order_no, r.product, r.amount, r.customer_name,
                    r.customer_contact, r.department_id, r.status,
                    r.paid_at.isoformat() if r.paid_at else "",
                    r.created_at.isoformat() if r.created_at else "", r.notes or ""])
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=orders_{datetime.now().strftime('%Y%m%d')}.csv"})


# ── 用户反馈提交 API（2026-09-12 商业化）──────────────────────────────


@router.get("/api/v1/admin/errors")
def errors_list(limit: int = Query(50, ge=1, le=200),
                admin: str = Depends(require_admin)):
    """查看最近的错误报告。"""
    import error_reporter as _er
    return _envelope(True, "OK", {"reports": _er.get_recent_reports(limit), "stats": _er.get_stats()})


@router.get("/api/v1/admin/errors/export")
def errors_export(admin: str = Depends(require_admin)):
    """导出错误报告为 JSON。"""
    import error_reporter as _er
    return _envelope(True, "OK", _er.get_recent_reports(limit=500))


# ── 数据导出 API（2026-09-12 商业化：数据可移植性）──────────────────────────


@router.get("/api/v1/admin/backup/status")
def backup_status(admin: str = Depends(require_admin)):
    """查看备份状态：最近备份时间、保留策略、备份文件列表。"""
    import backup as _bk
    return _envelope(True, "OK", _bk.get_backup_status())


@router.post("/api/v1/admin/backup/run")
def backup_run_now(admin: str = Depends(require_admin)):
    """手动触发一次备份。"""
    import backup as _bk
    result = _bk.run_backup()
    return _envelope(True, "OK", result, "备份完成")


@router.post("/api/v1/admin/backup/restore")
def backup_restore(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """从指定备份文件恢复数据。"""
    import backup as _bk
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "缺少备份文件名")
    result = _bk.restore_backup(name)
    if not result.get("ok"):
        raise HTTPException(500, f"恢复失败: {'; '.join(result.get('errors', []))}")
    return _envelope(True, "OK", result, "恢复完成")

