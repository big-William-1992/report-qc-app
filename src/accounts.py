"""
report_qc_app/src/accounts.py
本地账号管理：用户名即「工号」，密码以 PBKDF2 + 随机盐哈希存储。
完全离线，账号数据仅存于本机 SQLite，不上传任何网络。

设计要点：
- 无服务端、无网络依赖，适合单机/科室内部署的合规场景。
- 密码不以明文存储，使用 hashlib.pbkdf2_hmac（sha256，10 万次迭代），
  每个账号独立的随机盐，防止彩虹表/相同密码碰撞。
- 会话（当前登录工号）写入同目录 session.json，便于重启后预填登录框，
  但仍需重新输入密码以落实「谁做的质控」责任归属。
"""

import os
import sys
import sqlite3
import json
import hashlib
import secrets
import datetime

# 测试用：允许覆盖数据库路径，避免污染真实应用数据
_DB_OVERRIDE = None


def _default_db_path() -> str:
    if getattr(sys, "frozen", False):
        # 打包后放在用户可写目录（与样本库同目录）
        return os.path.join(os.path.expandvars("%APPDATA%"),
                            "MedicalReportQC", "accounts.db")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "accounts.db")


def _db_path() -> str:
    return _DB_OVERRIDE or _default_db_path()


def _session_path() -> str:
    return os.path.join(os.path.dirname(_db_path()), "session.json")


def init_db(path: str = None) -> None:
    path = path or _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                emp_id     TEXT PRIMARY KEY,
                name       TEXT,
                pwd_hash   TEXT NOT NULL,
                salt       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def _hash_password(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()


def create_account(emp_id: str, password: str, name: str = "") -> tuple:
    """创建账号。返回 (ok: bool, msg: str)。工号为登录用户名，唯一。"""
    emp_id = (emp_id or "").strip()
    password = password or ""
    name = (name or "").strip()
    if not emp_id:
        return False, "工号不能为空"
    if len(password) < 6:
        return False, "密码至少 6 位"
    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    created = datetime.datetime.now().isoformat(timespec="seconds")
    init_db()
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO accounts(emp_id, name, pwd_hash, salt, created_at) "
                "VALUES(?,?,?,?,?)",
                (emp_id, name, pwd_hash, salt, created))
    except sqlite3.IntegrityError:
        return False, f"工号「{emp_id}」已存在"
    return True, "创建成功"


def verify_account(emp_id: str, password: str) -> bool:
    """校验工号 + 密码，返回是否匹配。"""
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return False
    init_db()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT pwd_hash, salt FROM accounts WHERE emp_id=?", (emp_id,)
        ).fetchone()
    if not r:
        return False
    return _hash_password(password or "", r["salt"]) == r["pwd_hash"]


def account_exists(emp_id: str) -> bool:
    emp_id = (emp_id or "").strip()
    init_db()
    with sqlite3.connect(_db_path()) as conn:
        r = conn.execute(
            "SELECT 1 FROM accounts WHERE emp_id=?", (emp_id,)
        ).fetchone()
    return r is not None


def count_accounts() -> int:
    init_db()
    with sqlite3.connect(_db_path()) as conn:
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def get_name(emp_id: str) -> str:
    """返回工号对应的显示姓名（无则空串）。"""
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return ""
    init_db()
    with sqlite3.connect(_db_path()) as conn:
        r = conn.execute(
            "SELECT name FROM accounts WHERE emp_id=?", (emp_id,)
        ).fetchone()
    return (r[0] or "") if r else ""


def list_accounts() -> list:
    """返回 [(emp_id, name), ...]，按工号排序，供管理/统计扩展。"""
    init_db()
    with sqlite3.connect(_db_path()) as conn:
        rows = conn.execute(
            "SELECT emp_id, name FROM accounts ORDER BY emp_id"
        ).fetchall()
    return [(r[0], r[1] or "") for r in rows]


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
