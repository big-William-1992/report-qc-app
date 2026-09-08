"""
route_account.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from typing import Optional
from server.schemas import (AccountCreate, RegisterReq, ChangePwdReq, LoginReq,
                            RoleReq, PwdReq, DeptReq, DeptCreateReq)
from server.deps import (SECRET, _envelope, _emp_from_auth, make_token,
                          require_emp, require_admin, log_audit,
                          _check_login_rate, _record_login_failure, _clear_login_failures)
import accounts

router = APIRouter(tags=['accounts'])

@router.post("/api/v1/accounts")
def account_create(request: Request, req: AccountCreate,
                   authorization: Optional[str] = Header(None),
                   x_emp_id: Optional[str] = Header(None)):
    # 首个账号免鉴权引导（boot）；已存在账号则必须登录后才能创建（防滥用）
    if accounts.count_accounts() > 0:
        emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
        if not emp:
            raise HTTPException(401, "创建账号需登录：Authorization: Bearer <token> 或 X-Emp-Id 头")
    ok, msg = accounts.create_account(req.emp_id, req.password, req.name)
    if not ok:
        return _envelope(False, "ERR", {}, msg)
    # 首账号自动 admin 已在 create_account 的 INSERT 事务内原子判定（BEGIN IMMEDIATE），
    # 无需在此二次 count+set_role（旧实现有并发竞态，两个并发首账号可都成 admin）
    token = make_token(req.emp_id)   # 首个账号创建即登录，免去二次登录
    log_audit(req.emp_id, "account_created", {"emp_id": req.emp_id, "name": req.name},
              request.client.host if request.client else "")
    return _envelope(True, "OK",
                     {"token": token, "emp_id": req.emp_id, "name": req.name,
                      "role": accounts.get_role(req.emp_id)}, msg)


@router.post("/api/v1/accounts/register")
def account_register(req: RegisterReq, request: Request):
    """登录页自助注册：始终创建 doctor 角色（绝不授予 admin，防滥用）。
    与 /api/v1/accounts 的差异：后者创建账号需登录（管理员/首账号引导）。"""
    _check_login_rate(request)
    # 空库必须走「创建首个账号」引导（自动 admin）；自助注册拒绝，防止抢先注册提权
    if accounts.count_accounts() == 0:
        return _envelope(False, "ERR", {}, "系统尚未初始化，请先创建首个账号")
    ok, msg = accounts.create_account(req.emp_id, req.password, req.name, role="doctor")
    if not ok:
        return _envelope(False, "ERR", {}, msg)
    log_audit(req.emp_id, "account_registered", {"name": req.name},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "注册成功，请使用新账号登录")


@router.post("/api/v1/accounts/change-password")
def account_change_password(req: ChangePwdReq, request: Request):
    """登录页自助修改密码：校验旧密码后可重置为任意 >=6 位新密码。"""
    _check_login_rate(request)
    if len(req.new_password or "") < 6:
        return _envelope(False, "ERR", {}, "新密码至少 6 位")
    if not accounts.verify_account(req.emp_id, req.old_password):
        _record_login_failure(request)
        log_audit(req.emp_id, "password_change_failed", {"reason": "旧密码错误"},
                  request.client.host if request.client else "")
        return _envelope(False, "ERR", {}, "旧密码不正确")
    if not accounts.reset_password(req.emp_id, req.new_password):
        return _envelope(False, "ERR", {}, "账号不存在")
    _clear_login_failures(request)
    log_audit(req.emp_id, "password_changed", {"via": "login_screen"},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "密码已修改，请用新密码登录")


@router.post("/api/v1/accounts/login")
def account_login(req: LoginReq, request: Request):
    _check_login_rate(request)
    if not accounts.verify_account(req.emp_id, req.password):
        _record_login_failure(request)
        ip = request.client.host if request.client else ""
        log_audit(req.emp_id, "login_failed", {"reason": "密码错误"}, ip)
        return _envelope(False, "ERR", {}, "工号或密码错误")
    _clear_login_failures(request)
    ip = request.client.host if request.client else ""
    log_audit(req.emp_id, "login_success", None, ip)
    token = make_token(req.emp_id)
    return _envelope(True, "OK",
                      {"token": token, "emp_id": req.emp_id,
                       "name": accounts.get_name(req.emp_id),
                       "role": accounts.get_role(req.emp_id)})




@router.get("/api/v1/accounts/me")
def account_me(emp: str = Depends(require_emp)):
    return _envelope(True, "OK",
                     {"emp_id": emp, "name": accounts.get_name(emp), "role": accounts.get_role(emp)})


@router.get("/api/v1/accounts")
def account_list(emp: str = Depends(require_emp)):
    # 管理员看全部（含角色/科室），普通用户只看自己
    if accounts.get_role(emp) == "admin":
        return _envelope(True, "OK", accounts.list_accounts_full())
    return _envelope(True, "OK",
                     [{"emp_id": emp, "name": accounts.get_name(emp), "role": accounts.get_role(emp)}])




@router.post("/api/v1/accounts/{emp_id}/role")
def account_set_role(request: Request, emp_id: str, req: RoleReq,
                     admin: str = Depends(require_admin)):
    if req.role not in ("admin", "doctor"):
        return _envelope(False, "ERR", {}, "角色只能是 admin 或 doctor")
    if not accounts.set_role(emp_id, req.role):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "role_changed", {"target": emp_id, "role": req.role},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "角色已更新")




@router.post("/api/v1/accounts/{emp_id}/password")
def account_reset_password(request: Request, emp_id: str, req: PwdReq,
                           admin: str = Depends(require_admin)):
    if len(req.password or "") < 6:
        return _envelope(False, "ERR", {}, "密码至少 6 位")
    if not accounts.reset_password(emp_id, req.password):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "password_reset", {"target": emp_id},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "密码已重置")




@router.post("/api/v1/accounts/{emp_id}/dept")
def account_set_dept(request: Request, emp_id: str, req: DeptReq,
                     admin: str = Depends(require_admin)):
    if not accounts.set_dept(emp_id, req.dept_id):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "dept_changed", {"target": emp_id, "dept_id": req.dept_id},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "科室已更新")


@router.get("/api/v1/departments")
def department_list(admin: str = Depends(require_admin)):
    return _envelope(True, "OK", accounts.list_departments())




@router.post("/api/v1/departments")
def department_create(request: Request, req: DeptCreateReq,
                      admin: str = Depends(require_admin)):
    ok, msg = accounts.create_department(req.name)
    if not ok:
        return _envelope(False, "ERR", {}, str(msg))
    log_audit(admin, "department_created", {"name": req.name},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {}, "科室已创建")


@router.get("/api/v1/admin/audit-logs")
def audit_log_list(page: int = 1, page_size: int = 50,
                   action: str = "", emp_id: str = "",
                   admin: str = Depends(require_admin)):
    """管理员查看操作审计日志（按时间倒序，支持按操作类型/工号筛选）。"""
    from sqlalchemy import desc
    from server.db import SessionLocal
    from server.models import AuditLog
    sess = SessionLocal()
    try:
        q = sess.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action == action)
        if emp_id:
            q = q.filter(AuditLog.emp_id == emp_id)
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
