"""server/security.py — 鉴权、令牌与启动期安全策略。

从 server/main.py 迁移而来，保持 API 契约不变。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException, Request

from .core import _appdata_dir, _restrict_file_access


def _audit_log(*args):
    try:
        from log_utils import get_logger
        lg = get_logger()
        if lg is not None:
            lg.info(" ".join(str(a) for a in args))
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def _load_or_create_secret() -> str:
    path = os.path.join(_appdata_dir(), "qc_secret.key")
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                v = fh.read().strip()
            if v:
                return v
    except Exception:
        _audit_log("qc_secret.key read failed")
    v = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(v)
        _restrict_file_access(path)
    except Exception:
        _audit_log("qc_secret.key write failed")
    return v


QC_API_SECRET = os.environ.get("QC_API_SECRET", "").strip()
SECRET = QC_API_SECRET or _load_or_create_secret()
TOKEN_TTL = int(os.environ.get("QC_API_TTL", "86400"))


def _is_network_host(host: str) -> bool:
    return (host or "").strip().lower() not in ("127.0.0.1", "::1", "localhost", "localhost:port")


def _require_secret_for_network_host(host: str) -> None:
    if QC_API_SECRET or not _is_network_host(host):
        return
    raise SystemExit(
        "[SECURITY] 非本机监听（host=%s）必须设置 QC_API_SECRET。\n"
        "单机/桌面端默认绑定 127.0.0.1 可自动生成随机密钥；\n"
        "内网/公网多用户部署请先执行：export QC_API_SECRET=<强随机串>，并保持各节点一致。"
        % host
    )


def make_token(emp_id: str, ttl: int = TOKEN_TTL) -> str:
    exp = int(time.time()) + ttl
    payload = f"{emp_id}.{exp}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def verify_token(tok: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        emp_id, exp = payload.rsplit(".", 1)
        if int(exp) < time.time():
            return None
        expect = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expect, sig):
            return emp_id
    except Exception:
        return None
    return None


def _emp_from_auth(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    tok = authorization
    if tok.lower().startswith("bearer "):
        tok = tok[7:]
    return verify_token(tok)


def require_emp(request: Request,
                authorization: Optional[str] = Header(None),
                x_emp_id: Optional[str] = Header(None)) -> str:
    import accounts
    emp = _emp_from_auth(authorization)
    if not emp and request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        emp = (x_emp_id or "").strip()
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token>（远程访问不接受 X-Emp-Id 头）")
    if not accounts.account_exists(emp):
        raise HTTPException(401, "账号不存在或已注销")
    return emp


def require_emp_local(request: Request,
                      authorization: Optional[str] = Header(None),
                      x_emp_id: Optional[str] = Header(None)) -> str:
    import accounts
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        emp = (x_emp_id or "").strip()
        if emp and accounts.account_exists(emp):
            return emp
        if not emp and accounts.count_accounts() == 0:
            return "local"
        if emp:
            raise HTTPException(401, f"账号 '{emp}' 不存在或已注销")
        raise HTTPException(401, "缺少鉴权：请通过 X-Emp-Id 头指定有效账号，或使用 Bearer token")
    emp = _emp_from_auth(authorization)
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token>（远程访问不接受 X-Emp-Id 头）")
    if not accounts.account_exists(emp):
        raise HTTPException(401, "账号不存在或已注销")
    return emp


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    import accounts
    emp = _emp_from_auth(authorization)
    if not emp:
        raise HTTPException(401, "管理员操作需登录（Bearer token）")
    if not accounts.account_exists(emp):
        raise HTTPException(401, "账号不存在或已注销")
    if accounts.get_role(emp) != "admin":
        raise HTTPException(403, "需要管理员权限")
    return emp
