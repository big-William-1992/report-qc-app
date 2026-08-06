"""
report_qc_app/src/accounts.py
多用户账号管理（科室自托管形态）：基于 SQLAlchemy 的 users 表。
- 密码以 PBKDF2 + 随机盐哈希存储，不以明文。
- 新增 role（admin / doctor）与 dept_id（所属科室）字段，支撑多用户与科室数据归属。
- 会话（当前登录工号）仍写 session.json，便于重启后预填登录框（仍需重新输密码落实责任归属）。
- 抽象层由 server/db.py 提供：默认 SQLite，生产可切 PostgreSQL 不改业务代码。
"""
import os
import sys
import json
import hashlib
import secrets
import datetime

from server import db, models
SessionLocal = db.SessionLocal
User = models.User
Department = models.Department


# ---------------- 会话（当前登录工号，文件持久化，前端预填用） ----------------
def _assets_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "assets")
    os.makedirs(d, exist_ok=True)
    return d


def _session_path() -> str:
    return os.path.join(_assets_dir(), "session.json")


def init_db_safe() -> None:
    """兼容旧调用点：确保表存在（幂等）。"""
    try:
        init_db()
    except Exception:
        pass


# 旧代码直接调用 accounts.init_db()，这里转发到 SQLAlchemy 建表
def init_db() -> None:  # noqa: F811
    from server import db as _srv
    _init = _srv.init_db
    _init()


def _hash_password(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()


def create_account(emp_id: str, password: str, name: str = "",
                   role: str = "doctor", dept_id=None) -> tuple:
    """创建账号。返回 (ok, msg)。工号为登录名，唯一。role 默认 doctor。"""
    emp_id = (emp_id or "").strip()
    password = password or ""
    name = (name or "").strip()
    role = (role or "doctor").strip()
    if not emp_id:
        return False, "工号不能为空"
    if len(password) < 6:
        return False, "密码至少 6 位"
    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    init_db()
    with SessionLocal() as s:
        if s.query(User).filter(User.emp_id == emp_id).first():
            return False, f"工号「{emp_id}」已存在"
        u = User(
            emp_id=emp_id, name=name, pwd_hash=pwd_hash, salt=salt,
            role=role, dept_id=dept_id, created_at=datetime.datetime.now(),
        )
        s.add(u)
        s.commit()
    return True, "创建成功"


def verify_account(emp_id: str, password: str) -> bool:
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return False
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        if not u:
            return False
        return _hash_password(password or "", u.salt) == u.pwd_hash


def account_exists(emp_id: str) -> bool:
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return False
    init_db()
    with SessionLocal() as s:
        return s.query(User).filter(User.emp_id == emp_id).first() is not None


def count_accounts() -> int:
    init_db()
    with SessionLocal() as s:
        return s.query(User).count()


def get_name(emp_id: str) -> str:
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return ""
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        return u.name if u else ""


def get_role(emp_id: str) -> str:
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return ""
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        return u.role if u else ""


def set_role(emp_id: str, role: str) -> bool:
    emp_id = (emp_id or "").strip()
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        if not u:
            return False
        u.role = (role or "doctor").strip()
        s.commit()
    return True


def reset_password(emp_id: str, new_pw: str) -> bool:
    """管理员重置某账号密码（或本人修改）。"""
    emp_id = (emp_id or "").strip()
    if not emp_id or not new_pw:
        return False
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        if not u:
            return False
        salt = secrets.token_hex(16)
        u.salt = salt
        u.pwd_hash = _hash_password(new_pw, salt)
        s.commit()
    return True


def get_dept_id(emp_id: str):
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return None
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        return u.dept_id if u else None


def set_dept(emp_id: str, dept_id) -> bool:
    emp_id = (emp_id or "").strip()
    init_db()
    with SessionLocal() as s:
        u = s.query(User).filter(User.emp_id == emp_id).first()
        if not u:
            return False
        u.dept_id = dept_id
        s.commit()
    return True


def list_accounts() -> list:
    """返回 [(emp_id, name), ...]，兼容旧调用。"""
    init_db()
    with SessionLocal() as s:
        return [(u.emp_id, u.name or "") for u in
                s.query(User).order_by(User.emp_id).all()]


def list_accounts_full() -> list:
    """返回含角色/科室的账号列表，供管理页。"""
    init_db()
    with SessionLocal() as s:
        rows = []
        for u in s.query(User).order_by(User.emp_id).all():
            dept = s.query(Department).filter(Department.id == u.dept_id).first()
            rows.append({
                "emp_id": u.emp_id,
                "name": u.name or "",
                "role": u.role,
                "dept_id": u.dept_id,
                "dept_name": dept.name if dept else "",
                "created_at": u.created_at.isoformat() if u.created_at else "",
            })
        return rows


def create_department(name: str):
    """创建科室（唯一）。返回 (ok, dept_id|msg)。"""
    name = (name or "").strip()
    if not name:
        return False, "科室名不能为空"
    init_db()
    with SessionLocal() as s:
        d = s.query(Department).filter(Department.name == name).first()
        if d:
            return False, d.id
        d = Department(name=name, created_at=datetime.datetime.now())
        s.add(d)
        s.commit()
        return True, d.id


def list_departments() -> list:
    init_db()
    with SessionLocal() as s:
        return [{"id": d.id, "name": d.name} for d in
                s.query(Department).order_by(Department.name).all()]


# ---------------- 会话（当前登录工号） ----------------
def set_session(emp_id: str) -> None:
    try:
        with open(_session_path(), "w", encoding="utf-8") as fh:
            json.dump({"emp_id": emp_id or ""}, fh)
    except Exception:
        pass


def get_session() -> str:
    try:
        with open(_session_path(), encoding="utf-8") as fh:
            return json.load(fh).get("emp_id", "") or ""
    except Exception:
        return ""


def clear_session() -> None:
    try:
        os.remove(_session_path())
    except Exception:
        pass
