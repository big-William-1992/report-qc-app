"""
route_account.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
from server.schemas import AccountCreate, LoginReq, RoleReq, PwdReq, DeptReq, DeptCreateReq
from server.deps import SECRET, _envelope, _emp_from_auth, make_token, require_emp, require_admin
import accounts

router = APIRouter(tags=['accounts'])

@router.post("/api/v1/accounts")
def account_create(req: AccountCreate,
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
    return _envelope(True, "OK",
                     {"token": token, "emp_id": req.emp_id, "name": req.name,
                      "role": accounts.get_role(req.emp_id)}, msg)


@router.post("/api/v1/accounts/login")
def account_login(req: LoginReq):
    if not accounts.verify_account(req.emp_id, req.password):
        return _envelope(False, "ERR", {}, "工号或密码错误")
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
def account_set_role(emp_id: str, req: RoleReq, admin: str = Depends(require_admin)):
    if req.role not in ("admin", "doctor"):
        return _envelope(False, "ERR", {}, "角色只能是 admin 或 doctor")
    if not accounts.set_role(emp_id, req.role):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "角色已更新")




@router.post("/api/v1/accounts/{emp_id}/password")
def account_reset_password(emp_id: str, req: PwdReq, admin: str = Depends(require_admin)):
    if len(req.password or "") < 6:
        return _envelope(False, "ERR", {}, "密码至少 6 位")
    if not accounts.reset_password(emp_id, req.password):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "密码已重置")




@router.post("/api/v1/accounts/{emp_id}/dept")
def account_set_dept(emp_id: str, req: DeptReq, admin: str = Depends(require_admin)):
    if not accounts.set_dept(emp_id, req.dept_id):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "科室已更新")


@router.get("/api/v1/departments")
def department_list(admin: str = Depends(require_admin)):
    return _envelope(True, "OK", accounts.list_departments())




@router.post("/api/v1/departments")
def department_create(req: DeptCreateReq, admin: str = Depends(require_admin)):
    ok, msg = accounts.create_department(req.name)
    if not ok:
        return _envelope(False, "ERR", {}, str(msg))
    return _envelope(True, "OK", {}, "科室已创建")

