"""
route_accounts.py — 账号 / 登录 / 科室（2026-09-30 路由拆分 S6a）

从 `server/main.py` 迁出，端点实现**逐字搬迁**（7 个）：

  POST /api/v1/accounts                    创建账号（首个账号免鉴权引导）
  POST /api/v1/accounts/login              登录（含失败锁定/限流）
  GET  /api/v1/accounts/me                 当前账号信息
  GET  /api/v1/accounts                    账号列表（管理员）
  POST /api/v1/accounts/{emp_id}/role      改角色（管理员）
  POST /api/v1/accounts/{emp_id}/password  改密码
  POST /api/v1/accounts/{emp_id}/dept      改科室（管理员）

依赖全部来自共享层：`server.security`（令牌/鉴权）、`server.deps`（登录限流/审计）、
`src/accounts.py`（数据层）。**不要在本文件另建令牌或限流实现**。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from server.core import _envelope
from server.deps import (_clear_login_failures, _login_locked,
                         _record_login_failure, log_audit)
from server.security import (_emp_from_auth, make_token, require_admin,
                             require_emp, require_license_active)  # noqa: F401
from server.schemas import (AccountCreate, DeptReq, LoginReq, PwdReq,
                            RoleReq)

import accounts

router = APIRouter(tags=["accounts"])

@router.post("/api/v1/accounts")
def account_create(req: AccountCreate, request: Request,
                   authorization: Optional[str] = Header(None),
                   x_emp_id: Optional[str] = Header(None)):
    # 首个账号免鉴权引导（boot）；已存在账号则必须登录后才能创建（防滥用）。
    # X-Emp-Id 头仅限本机（127.0.0.1）兜底：内网 --host 0.0.0.0 部署时，
    # 任意客户端伪造 X-Emp-Id 即可批量创建账号（2026-08-18 修复）。
    emp = None
    if accounts.count_accounts() > 0:
        emp = _emp_from_auth(authorization)
        if not emp:
            _local = request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
            if _local:
                emp = (x_emp_id or "").strip()
        if not emp:
            raise HTTPException(401, "创建账号需登录：Authorization: Bearer <token>")
        if accounts.get_role(emp) != "admin":
            raise HTTPException(403, "仅管理员可创建账号")
    _client_ip = request.client.host if request.client else ""
    _operator = emp or "boot"
    ok, msg = accounts.create_account(req.emp_id, req.password, req.name)
    if not ok:
        return _envelope(False, "ERR", {}, msg)
    log_audit(_operator, "account_created",
              {"target": req.emp_id}, _client_ip)
    # 首账号自动 admin 已在 create_account 的 INSERT 事务内原子判定（BEGIN IMMEDIATE），
    # 无需在此二次 count+set_role（旧实现有并发竞态，两个并发首账号可都成 admin）
    token = make_token(req.emp_id)   # 首个账号创建即登录，免去二次登录
    return _envelope(True, "OK",
                     {"token": token, "emp_id": req.emp_id, "name": req.name,
                      "role": accounts.get_role(req.emp_id)}, msg)


# 登录失败限速：统一走 server.deps 的 emp_id 内存限流（测试可直接重置 main._LOGIN_FAIL）
from server import deps as _deps_auth  # noqa: E402

_LOGIN_FAIL = _deps_auth._LOGIN_FAIL
LOGIN_LOCK_SECONDS = _deps_auth._LOGIN_LOCK_DURATION


@router.post("/api/v1/accounts/login")
def account_login(req: LoginReq, request: Request):
    emp_id = (req.emp_id or "").strip()
    _client_ip = request.client.host if request.client else ""
    if _login_locked(emp_id):
        log_audit(emp_id, "login_locked", {}, _client_ip)
        return _envelope(False, "ERR", {}, "登录失败次数过多，请稍后再试")
    if not accounts.verify_account(emp_id, req.password):
        _record_login_failure(emp_id)
        rec = _deps_auth._LOGIN_FAIL.get(emp_id, [0, 0, None])
        log_audit(emp_id, "login_failed", {"attempts": rec[0]}, _client_ip)
        return _envelope(False, "ERR", {}, "工号或密码错误")
    _clear_login_failures(emp_id)
    token = make_token(emp_id)
    log_audit(emp_id, "login_success", {}, _client_ip)
    return _envelope(True, "OK",
                      {"token": token, "emp_id": emp_id,
                       "name": accounts.get_name(emp_id),
                       "role": accounts.get_role(emp_id)})


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
    _ip = request.client.host if request.client else ""
    if req.role not in ("admin", "doctor"):
        return _envelope(False, "ERR", {}, "角色只能是 admin 或 doctor")
    if not accounts.set_role(emp_id, req.role):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "role_changed",
              {"target": emp_id, "new_role": req.role}, _ip)
    return _envelope(True, "OK", {}, "角色已更新")




@router.post("/api/v1/accounts/{emp_id}/password")
def account_reset_password(request: Request, emp_id: str, req: PwdReq,
                           admin: str = Depends(require_admin)):
    _ip = request.client.host if request.client else ""
    if len(req.password or "") < 6:
        return _envelope(False, "ERR", {}, "密码至少 6 位")
    if not accounts.reset_password(emp_id, req.password):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "password_reset", {"target": emp_id}, _ip)
    return _envelope(True, "OK", {}, "密码已重置")




@router.post("/api/v1/accounts/{emp_id}/dept")
def account_set_dept(request: Request, emp_id: str, req: DeptReq, admin: str = Depends(require_admin)):
    _ip = request.client.host if request.client else ""
    if not accounts.set_dept(emp_id, req.dept_id):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "dept_changed", {"target": emp_id, "dept_id": req.dept_id}, _ip)
    return _envelope(True, "OK", {}, "科室已更新")


