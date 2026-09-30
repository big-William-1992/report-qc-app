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


# ── 「本机免令牌」便利通道的可信判定（2026-09-30 安全加固）──────────────
# 背景（真实绕过路径）：应用绑定 127.0.0.1、反向代理与它同机部署时，
# **所有内网用户的 request.client.host 都是 127.0.0.1** —— 于是任何人只要带
# `X-Emp-Id: <任意工号>` 就被当成可信本机，可免令牌冒充他人；审计日志里
# 记录的来源 IP 也全是 127.0.0.1，事后无法追溯。
# 加固：本机通道额外要求「请求不带任何代理转发头」——
#   · 真实的本机桌面端/本机脚本直连不会带 X-Forwarded-For / X-Real-IP / Forwarded；
#   · 经反向代理来的请求会带（deploy/nginx-report-qc.conf 就是这么配的）。
# 该判定是单调保守的：代理头只会**关闭**本机通道，永远不会开启它，
# 因此内网客户端伪造 XFF 也无法借此提权（它的 host 本来就不是回环）。
# 需要彻底关闭该通道（例如多机部署、或本机也存在不受信用户）时设
# `QC_LOCAL_TRUST=0`。
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")


def _proxy_headers_present(request: Request) -> bool:
    return any(request.headers.get(h) for h in _PROXY_HEADERS)


def _is_loopback(request: Request) -> bool:
    return bool(request.client and request.client.host in ("127.0.0.1", "::1", "localhost"))


def local_trust_allowed(request: Request) -> bool:
    """是否允许走"本机免令牌"通道（须同时满足：非禁用 + 回环 + 无代理头）。"""
    if (os.environ.get("QC_LOCAL_TRUST", "") or "").strip() == "0":
        return False
    return _is_loopback(request) and not _proxy_headers_present(request)


def require_emp(request: Request,
                authorization: Optional[str] = Header(None),
                x_emp_id: Optional[str] = Header(None)) -> str:
    import accounts
    emp = _emp_from_auth(authorization)
    if not emp and local_trust_allowed(request):
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
    if local_trust_allowed(request):
        emp = (x_emp_id or "").strip()
        if emp and accounts.account_exists(emp):
            return emp
        if not emp and accounts.count_accounts() == 0:
            return "local"
        if emp:
            raise HTTPException(401, f"账号 '{emp}' 不存在或已注销")
        raise HTTPException(
            401, "缺少鉴权：请通过 X-Emp-Id 头指定有效账号，或使用 Bearer token"
                 "（经反向代理访问时不接受 X-Emp-Id；如确需本机通道请设 QC_LOCAL_TRUST=1 "
                 "并确保只有本机可直连）")
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


# ── 授权门（2026-09-30 从 main.py 下沉）──────────────────────────────────────
# 为什么放这里：它是**写接口的授权依赖**，与 require_emp_local / require_admin 同类；
# 留在 main.py 会让拆出去的路由模块只能反向 import main（循环依赖）。
# 端点数量多、语义为"拒绝写操作"，故归入鉴权/授权层。
def require_license_active():
    """授权门服务端强制（2026-08-18 接入）：试用期结束且未激活时拒绝写操作。

    与前端 gate 同源（license_web.check_trial，读 appdata/license.json）；
    开发/内测试用期内（trial）放行，过期未激活返回 403。
    仅用于产生/修改数据的写接口（读接口不拦，登录用户仍可查看历史数据）。
    2026-08-24 安全加固：license 读取异常时拒绝而非放行（fail-closed）。
    """
    try:
        import license_web
        from server.core import _appdata_dir
        state, _days = license_web.check_trial(_appdata_dir())
    except Exception:
        raise HTTPException(500, "授权验证异常，请检查 license.json 是否完整")
    if state == "expired":
        raise HTTPException(403, "试用期已结束，请输入激活码激活后继续使用")
    return True
