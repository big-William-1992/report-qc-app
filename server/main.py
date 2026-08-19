"""
report_qc_app/server/main.py
星衍放射质控软件 — HTTP/REST 服务（对应《接口文档_HTTP_REST.md》规范，v3.0）

设计要点（与桌面端一致）：
- 完全离线优先：/qc/* 与 /samples/* 仅依赖标准库引擎（engine/accounts/samplelib）；
  /ocr 为可选能力，懒加载，缺 RapidOCR 依赖时返回 503（不影响 /qc 主流程）。
- 责任到人：所有写入类接口必须携带操作员工号——内网用 `X-Emp-Id` 头，
  公网用 `Authorization: Bearer <token>`（由 /accounts/login 签发，HMAC 签名，零额外依赖）。
- OCR 与质检解耦：/qc/check 纯 CPU 标准库，可多副本水平扩展；/ocr 仅在具备
  显示/模型环境的节点启用。

启动：
    cd report_qc_app
    pip install -r server/requirements.txt
    uvicorn server.main:app --host 0.0.0.0 --port 8000
"""
import os
import sys
import io
import re
import time
import hmac
import hashlib
import base64
import json
import secrets
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any

def _bundle_root() -> str:
    """资源（server/web/assets/src）在磁盘上的根目录。

    - 普通源码运行：本文件位于 <root>/server/main.py，root = 其上级目录。
    - PyInstaller 冻结（单目录/单文件）：资源随 exe 平铺在 exe 所在目录（单目录）
      或解压到 sys._MEIPASS（单文件），而非从 __file__ 回溯（冻结后 __file__ 指向
      PYZ 归档内的合成路径，回溯会得到不存在的目录，导致静态资源挂载失败）。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return str(Path(__file__).resolve().parent.parent)


# 把 src 加入 sys.path，便于 import engine / accounts / samplelib
_APP_ROOT = _bundle_root()
_SERVER = os.path.join(_APP_ROOT, "server") if getattr(sys, "frozen", False) \
    else str(Path(__file__).resolve().parent)
_SRC = os.path.join(_APP_ROOT, "src") if getattr(sys, "frozen", False) \
    else str(Path(__file__).resolve().parent.parent / "src")
# 项目根（server 的父目录 / 冻结后的 exe 所在目录）：用于 `from server import db`
# 这类「包内绝对导入」在源码与冻结两种模式下都能无歧义解析。
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 把 server 自身目录加入 path（双保险，便于裸 import 兜底）
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

# E2E 测试隔离（2026-08-18 修复）：QC_DB_OVERRIDE 必须在 import server.db 之前
# 转成 DATABASE_URL，否则 db.engine 已按默认路径创建，accounts._DB_OVERRIDE 的
# 二次切换因 SQLAlchemy engine/SessionLocal 绑定引用而失效（此前实测不隔离、
# 测试数据会写入真实 qc.db）。
# 2026-08-18 增强：accounts.py 顶部 `from server import db` 可能在 main 之前
# 触发 server.db 初始化（如 pytest 先收集 test_accounts.py），env 转换对已加载的
# db 模块不生效 → 此处显式 set_database_override 强制切换（幂等，不依赖 import 顺序）。
_qc_db_override = os.environ.get("QC_DB_OVERRIDE", "").strip()
if _qc_db_override:
    # 2026-08-18：Windows 路径含反斜杠，SQLAlchemy URL 需正斜杠（C:/...）
    _qc_db_url = "sqlite:///" + os.path.abspath(_qc_db_override).replace("\\", "/")
    os.environ["DATABASE_URL"] = _qc_db_url
    try:
        import server.db as _sdb
        _sdb.set_database_override(_qc_db_url)
    except Exception:
        pass

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import engine
import ris
import accounts
import samplelib
from server import license_web
import ocr_provider
from version import APP_VERSION
from server import db  # SQLAlchemy 统一数据层（users/departments/queue/settings）
from server.db import SessionLocal  # 会话工厂（队列/设置 ORM 读写，2026-08-18 task 229 收敛）
from server.core import (  # 共享层（2026-08-18 拆分）：日志/响应封装/数据目录/队列与设置数据层
    _log, _envelope, _eng_scores, _appdata_dir, _atomic_json_write, _JSON_IO_LOCK,
    _queue_orm_all, _queue_orm_add, _queue_orm_add_dedup, _queue_orm_remove,
    _queue_orm_clear, _load_queue,
    _migrate_queue_to_db, _settings_orm_all, _settings_orm_save, _migrate_settings_to_db,
)

# ----------------------------- 鉴权（stdlib HMAC 签名 token） -----------------------------
def _load_or_create_secret() -> str:
    """QC_API_SECRET 未设置时：从用户数据目录读持久化随机密钥；无则生成并 0600 保存。

    2026-08-18 H1b 修复：此前缺失时回退硬编码 'change-me-in-prod'，该默认值随
    软件分发即任何拿到代码者都能离线伪造任意工号 token。改为首启生成随机密钥
    （多 worker/多进程共享同一文件，token 验签一致），不拒绝启动（保本地双击即用）。
    """
    path = os.path.join(_appdata_dir(), "qc_secret.key")
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                v = fh.read().strip()
            if v:
                return v
    except Exception:
        pass
    v = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(v)
        os.chmod(path, 0o600)
    except Exception:
        pass
    return v


QC_API_SECRET = os.environ.get("QC_API_SECRET", "").strip()
if QC_API_SECRET:
    SECRET = QC_API_SECRET
else:
    # 本地演示/桌面单机默认值；公网/内网多用户部署必须设置强随机密钥。
    SECRET = _load_or_create_secret()
    print("\n[SECURITY] 警告: 未设置 QC_API_SECRET，已生成本机随机密钥并持久化到用户数据目录。"
          "公网/内网多用户部署请 export QC_API_SECRET=<强随机串> 保持各节点一致。\n", file=sys.stderr)
TOKEN_TTL = int(os.environ.get("QC_API_TTL", "86400"))  # 默认 24h


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
    """写操作鉴权：优先 Bearer token；X-Emp-Id 头仅限本机（127.0.0.1）调用时兜底。
    远程请求一律拒绝 X-Emp-Id——该头无任何凭证，可被任意伪造冒充他人（2026-08-18 收紧）。"""
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
    """写操作鉴权（本地优先）：

    - 来自 127.0.0.1/::1 的调用（桌面端 WebView、浏览器同源 localhost）自动放行，
      避免 SPA 必须携带鉴权头，保持本地"双击即用"体验；
    - 公网/远程部署强制 Bearer token（不再接受 X-Emp-Id 头，防止伪造冒充）。
    """
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        return (x_emp_id or "local").strip() or "local"
    emp = _emp_from_auth(authorization)
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token>（远程访问不接受 X-Emp-Id 头）")
    if not accounts.account_exists(emp):
        raise HTTPException(401, "账号不存在或已注销")
    return emp


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    """管理员操作依赖：强制 Bearer token（不享受 localhost 放行，防止伪造 X-Emp-Id 提权）。
    定义在认证区块（规则写接口之前），2026-08-18 由 731 行前移，避免默认参数求值顺序问题。"""
    emp = _emp_from_auth(authorization)
    if not emp:
        raise HTTPException(401, "管理员操作需登录（Bearer token）")
    if not accounts.account_exists(emp):
        raise HTTPException(401, "账号不存在或已注销")
    if accounts.get_role(emp) != "admin":
        raise HTTPException(403, "需要管理员权限")
    return emp


def _scope_user_id(emp: str) -> Optional[str]:
    """多用户数据隔离（2026-08-18）：admin/本机返回 None（看全部样本与统计）；
    普通医生返回本人工号，样本读取/导出/统计仅限本人数据。"""
    if not emp or emp == "local":
        return None
    try:
        if accounts.get_role(emp) == "admin":
            return None
    except Exception:
        pass
    return emp


def require_license_active():
    """授权门服务端强制（2026-08-18 接入）：试用期结束且未激活时拒绝写操作。

    与前端 gate 同源（license_web.check_trial，读 appdata/license.json）；
    开发/内测试用期内（trial）放行，过期未激活返回 403。
    仅用于产生/修改数据的写接口（读接口不拦，登录用户仍可查看历史数据）。
    """
    try:
        state, _days = license_web.check_trial(_appdata_dir())
    except Exception:
        return True  # license 读取异常时放行，避免误锁
    if state == "expired":
        raise HTTPException(403, "试用期已结束，请输入激活码激活后继续使用")
    return True


# ----------------------------- 应用 -----------------------------
app = FastAPI(title="星衍放射质控 API", version=APP_VERSION)

# 安全响应头（2026-08-18 H3 服务端修复）：CSP / nosniff / 防 iframe 嵌入。
# script-src 含 'unsafe-inline' 是因 app.js 动态渲染的 10 处内联 onclick 事件处理器；
# 存储型 XSS 已由前端 escapeHtml 全量转义封堵，此头为纵深防御。
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp

# CORS 白名单：默认只放行桌面端 WebView 的本地源（127.0.0.1 / localhost，任意端口）。
# 禁止通配 "*"：否则任何网页（含恶意站点）都能向本机特权端点发请求（CSRF 面，见
# require_emp_local 的本地放行）。内网/远程部署需要放行其它源时，用环境变量追加：
#   QC_CORS_ORIGINS=http://192.168.1.10:8000,http://qc.example.com
_cors_origins = [o.strip() for o in os.environ.get("QC_CORS_ORIGINS", "").split(",") if o.strip()]
# 仅放行本机实际服务端口（默认 8000，QC_PORT 可配置），避免任意 localhost 端口页面无凭据跨域
_QC_PORT = os.environ.get("QC_PORT", "8000").strip()
_LOCAL_ORIGIN_RE = rf"^https?://(127\.0\.0\.1|localhost|\[::1\]):{re.escape(_QC_PORT)}$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_LOCAL_ORIGIN_RE,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------- 请求模型 -----------------------------
class FindingOut(BaseModel):
    rule_id: str
    error_type: str
    severity: str
    message: str
    snippet: str = ""
    span: list = [-1, -1]
    suggestion: str = ""


class CheckReq(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class BatchItem(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class BatchReq(BaseModel):
    items: List[BatchItem]


class AccountCreate(BaseModel):
    emp_id: str
    password: str
    name: str = ""


class LoginReq(BaseModel):
    emp_id: str
    password: str


class SampleCreate(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    findings: List[dict] = []
    score: Dict[str, Any] = {}
    anonymize: bool = False
    user_id: Optional[str] = None


class OCRB64(BaseModel):
    image_base64: str


# ----------------------------- 请求模型（Phase1 补充） -----------------------------
class RisConfigReq(BaseModel):
    db_type: str = "sqlserver"
    host: str = ""
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    query_sql: str = ""


class SampleExportReq(BaseModel):
    path: str = ""
    fmt: str = "csv"
    anonymize: bool = False  # 导出脱敏（2026-08-18）：剥离患者姓名/性别/年龄


class SampleImportReq(BaseModel):
    path: str = ""


# ----------------------------- 辅助 -----------------------------
# 引擎 Finding.severity 用 high/medium/low；看板 by_severity 用 critical/warning/info。
_SEV_MAP = {"high": "critical", "medium": "warning", "low": "info"}
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def _worst_sev(findings: list) -> str:
    """从 findings 推导最严重级别（high>medium>low）；无 findings 视为 low→info。"""
    worst = "low"
    for f in (findings or []):
        sv = f.get("severity", "low") if isinstance(f, dict) else getattr(f, "severity", "low")
        if _SEV_RANK.get(sv, 0) > _SEV_RANK.get(worst, 0):
            worst = sv
    return _SEV_MAP.get(worst, "info")


# 进程级引擎单例（2026-08-18 E2 修复）：此前每次 /qc/check 新建 RuleEngine 并重读
# rules_config.json（批量 50 条即 50 次磁盘读）。规则变更后 _reload_engine_rules()
# 刷新；run() 只读 self.rules_config 引用，并发安全。
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = engine.RuleEngine()
    return _ENGINE


def _reload_engine_rules():
    try:
        _get_engine().reload_rules()
    except Exception:
        pass


def _run_qc(report: str, meta: dict, auto_fix: bool) -> dict:
    eng = _get_engine()
    findings = eng.run(report, meta)
    score = engine.score_summary(engine.score(findings))
    data = {
        "findings": [f.__dict__ for f in findings],
        "score": score,
        "error_counts": engine.error_type_counts(findings),
        "fixed": None,
    }
    if auto_fix:
        fixed_text, n_fixed, n_manual, details = eng.auto_fix(report, findings)
        data["fixed"] = {"fixed_text": fixed_text, "n_fixed": n_fixed,
                         "n_manual": n_manual, "details": details}
    return data


# ----------------------------- 质控计算（无状态） -----------------------------
# 内存态简单限流（2026-08-18 H1c）：质控是 CPU 密集计算，远程部署时防止
# 无凭证/恶意调用打满 CPU（DoS）。每 IP 每分钟默认 60 次，可 QC_RATE_PER_MIN 调整。
_QC_RATE_MAX = int(os.environ.get("QC_RATE_PER_MIN", "60"))
_QC_RATE: Dict[str, list] = {}
_QC_RATE_LOCK = threading.Lock()


def _qc_rate_ok(ip: str) -> bool:
    now = time.time()
    with _QC_RATE_LOCK:
        ts = [t for t in _QC_RATE.get(ip, []) if now - t < 60]
        if len(ts) >= _QC_RATE_MAX:
            _QC_RATE[ip] = ts
            return False
        ts.append(now)
        _QC_RATE[ip] = ts
        return True


@app.post("/api/v1/qc/check")
def qc_check(req: CheckReq, request: Request,
             emp: str = Depends(require_emp_local),
             _lic: bool = Depends(require_license_active)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    if len(req.report) > 20000:
        raise HTTPException(413, "报告文本过长（上限 20000 字符）")
    ip = request.client.host if request.client else ""
    if not _qc_rate_ok(ip):
        raise HTTPException(429, "质控请求过于频繁，请稍后重试")
    return _envelope(True, "OK", _run_qc(req.report, req.meta, req.auto_fix))


@app.post("/api/v1/qc/batch")
def qc_batch(req: BatchReq, request: Request,
             emp: str = Depends(require_emp_local),
             _lic: bool = Depends(require_license_active)):
    if len(req.items) > 50:
        raise HTTPException(400, "单次最多 50 条")
    ip = request.client.host if request.client else ""
    if not _qc_rate_ok(ip):
        raise HTTPException(429, "质控请求过于频繁，请稍后重试")
    results = []
    for it in req.items:
        if not it.report.strip():
            results.append({"ok": False, "error": "report 不能为空"})
            continue
        if len(it.report) > 20000:
            results.append({"ok": False, "error": "report 过长（上限 20000 字符）"})
            continue
        results.append({"ok": True, **_run_qc(it.report, it.meta, it.auto_fix)})
    return _envelope(True, "OK", {"results": results})


@app.get("/api/v1/qc/rules")
def qc_rules_get():
    """返回规则元信息列表（供前端规则维护页展示；更新配置走 PUT）。
    2026-08-18 同步：清单改为规则合并后的实际产出 rule_id，severity 与引擎口径
    （high/medium/low）一致；R7/R11/R13/R20 已合并或预留，不再单列。"""
    rule_meta = [
        {"rule_id": "R1-GENDER",      "name": "性别矛盾",         "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R2-LATERALITY",  "name": "左右侧混淆",       "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R3-SCORE",       "name": "评分缺失",         "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R4-UNIT",        "name": "计量单位错误",     "category": "准确性", "severity": "low",     "enabled": True},
        {"rule_id": "R5-CONSISTENCY", "name": "描述-结论矛盾",    "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R6-SITE",        "name": "登记部位不符",     "category": "完整性", "severity": "high",    "enabled": True},
        {"rule_id": "R8-TYPO",        "name": "同音错别字",       "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R9-CONFLICT",    "name": "自定义互斥冲突",   "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R10-TEMPLATE",   "name": "模板合规",         "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R12-SENTENCE",   "name": "句内自相矛盾",     "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R14-NATURE",     "name": "良恶性定性矛盾",   "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R14-COUNT",      "name": "病灶数量矛盾",     "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R15-NORMAL",     "name": "段首正常段内阳性", "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R15-PRESENCE",   "name": "先见后无",         "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R16-FOLLOWUP",   "name": "随访时限缺失",     "category": "及时性", "severity": "low",     "enabled": False},
        {"rule_id": "R17-PERREGION",  "name": "逐部位描述-结论矛盾", "category": "准确性", "severity": "high", "enabled": True},
        {"rule_id": "R18-COVERAGE",   "name": "部位器官漏写",     "category": "完整性", "severity": "medium",  "enabled": True},
        {"rule_id": "R19-HOMOPHONE",  "name": "形近错字",         "category": "准确性", "severity": "low",     "enabled": True},
        {"rule_id": "R21-GENDER-SITE","name": "性别-部位联动",    "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R22-SIZE",       "name": "尺寸-术语一致性",  "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R22-UNIT",       "name": "尺寸单位规范",     "category": "规范性", "severity": "low",     "enabled": True},
    ]
    return _envelope(True, "OK", rule_meta)


@app.put("/api/v1/qc/rules")
def qc_rules_put(cfg: Dict[str, Any], emp: str = Depends(require_admin)):
    # 读→合并→写回（2026-08-18 M3 修复）：只覆盖客户端提交的已知键，不再整表覆盖
    # （此前部分 PUT 会静默丢弃 enable_r19/r19_sensitivity/disabled_typos）。
    # 注意：typos 必须**增量合并**（dict.update）而非整体替换——客户端通常只传
    # 1~2 条增改，整体替换会把规则库其余几百条用户词条清空。
    cur = engine.load_rules_config()
    for k in ("conflicts", "ignores", "template"):
        if k in cfg:
            cur[k] = cfg[k]
    if isinstance(cfg.get("typos"), dict):
        merged = dict(cur.get("typos") or {})
        merged.update(cfg["typos"])   # 新增/修改；不删除既有词条
        cur["typos"] = merged
    engine.save_rules_config(cur)
    _reload_engine_rules()
    return _envelope(True, "OK", {k: cur.get(k) for k in ("typos", "conflicts", "ignores", "template")},
                    "规则已更新，下次请求自动生效")


# ----------------------------- OCR（可选） -----------------------------
# OCR 是计算密集型且无速率限制：限制上传大小，并要求本地放行/远程凭证，
# 避免内网暴露时被无凭证调用消耗内存与 OCR 算力（拒绝服务面）。
_OCR_MAX_BYTES = int(os.environ.get("QC_OCR_MAX_BYTES", str(20 * 1024 * 1024)))


@app.post("/api/v1/ocr")
async def ocr_upload(file: UploadFile = File(...), emp: str = Depends(require_emp_local),
                  _lic: bool = Depends(require_license_active)):
    from PIL import Image
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    data = await file.read(_OCR_MAX_BYTES + 1)
    if len(data) > _OCR_MAX_BYTES:
        raise HTTPException(413, f"图片过大（上限 {_OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    # 推理串行（2026-08-18）：RapidOCR 单次峰值约 610MB，与 /screen/ocr 共用
    # _OCR_LOCK，避免并发推理在同一实例上内存叠加导致医院低配桌面 OOM。
    with _OCR_LOCK:
        text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


@app.post("/api/v1/ocr/base64")
def ocr_base64(req: OCRB64, emp: str = Depends(require_emp_local),
               _lic: bool = Depends(require_license_active)):
    from PIL import Image
    import ocr_provider
    import base64 as _b64
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    if len(req.image_base64) > _OCR_MAX_BYTES * 4 // 3:
        raise HTTPException(413, f"图片过大（上限 {_OCR_MAX_BYTES // (1024*1024)}MB）")
    try:
        raw = _b64.b64decode(req.image_base64)
        img = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    with _OCR_LOCK:
        text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


# ----------------------------- RIS / PACS 直连（迁移自 web/api/ris.py） -----------------------------
@app.get("/api/v1/ris/config")
def ris_config_get(emp: str = Depends(require_emp_local)):
    """读取已保存的 RIS 连接配置（password 脱敏不回传）。
    2026-08-18 新增：此前 save_config 仅被 Tkinter 版调用，SPA 配置后轮询线程读不到。"""
    cfg = dict(ris.load_config())
    if cfg.get("password"):
        cfg["password"] = "******"
    return _envelope(True, "OK", cfg)


@app.put("/api/v1/ris/config")
def ris_config_put(req: RisConfigReq, emp: str = Depends(require_admin)):
    """保存 RIS 连接配置（轮询/拉取复用；管理权限）。"""
    cfg = ris.load_config()
    cfg.update({
        "db_type": req.db_type or "sqlserver",
        "host": req.host or "",
        "port": req.port or "",
        "database": req.database or "",
        "user": req.user or "",
        "password": req.password or cfg.get("password", ""),  # 未填则保留原值
        "query": req.query_sql or cfg.get("query", ""),
    })
    ris.save_config(cfg)
    return _envelope(True, "OK", {}, "RIS 连接配置已保存")


@app.get("/api/v1/ris/drivers")
def ris_drivers(emp: str = Depends(require_emp_local)):
    """返回支持的数据库驱动列表及可用性。"""
    drivers = []
    for dtype in ("sqlserver", "oracle", "mysql", "postgresql"):
        ok, mod, msg = ris.driver_available(dtype)
        drivers.append({"type": dtype, "available": ok, "module": mod or "", "message": msg})
    return _envelope(True, "OK", drivers)


@app.post("/api/v1/ris/test-connection")
def ris_test_connection(req: RisConfigReq, emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query": req.query_sql,
    }
    ok, msg = ris.test_connection(config)
    return _envelope(True, "OK", {"ok": ok, "message": msg})


@app.post("/api/v1/ris/fetch-reports")
def ris_fetch_reports(req: RisConfigReq, limit: int = Query(50, ge=1, le=200),
                      emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query": req.query_sql,
    }
    try:
        reports = ris.fetch_reports(config, limit=limit)
    except Exception as exc:
        # 统一错误封装（与 poll-now 失败路径对齐），避免 FastAPI 默认 500 破坏前端 data.ok 判定
        return _envelope(False, "RIS_ERR", {}, f"RIS 拉取失败：{type(exc).__name__}：{exc}")
    items = [{
        "report_text": (r.get("report_text", "") or "")[:500],
        "patient": r.get("patient", ""),
        "gender": r.get("gender", ""),
        "age": r.get("age", ""),
        "modality": r.get("modality", ""),
        "applied_site": r.get("applied_site", ""),
        "ts": r.get("ts", ""),
    } for r in (reports or [])]
    return _envelope(True, "OK", {"items": items, "count": len(items)})


# ----------------------------- RIS 主动轮询质检（P0：发现即质控闭环） -----------------------------
# 后台守护线程按 interval_min 周期性拉取 RIS 新报告 → 自动质控 → 结果入库样本库 + 进待质控队列。
# 配置与「已处理去重指纹」持久化在 appdata/ris_poll.json，重启不丢失、不重复处理。
_POLL_PATH = None


def _poll_path() -> str:
    global _POLL_PATH
    if _POLL_PATH is None:
        _POLL_PATH = os.path.join(_appdata_dir(), "ris_poll.json")
    return _POLL_PATH


_POLL_DEFAULT = {
    "enabled": False,          # 轮询总开关
    "interval_min": 30,        # 拉取间隔（分钟）
    "limit": 50,               # 每次最多拉取条数
    "auto_qc": True,           # 拉取后自动质控入库
    "auto_enqueue": True,      # 同时进待质控队列（医师复核）
    "last_run": "",            # 上次成功运行时间（ISO）
    "last_count": 0,           # 上次新增数量
    "last_error": "",          # 最近一次错误信息
    "seen": [],                # 已处理报告正文 MD5 指纹（去重）
}


def _poll_config() -> dict:
    cfg = dict(_POLL_DEFAULT)
    cfg["seen"] = list(cfg["seen"])
    try:
        with _JSON_IO_LOCK:
            with open(_poll_path(), encoding="utf-8") as fh:
                data = json.load(fh) or {}
        for k in _POLL_DEFAULT:
            if k in data:
                cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def _save_poll_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_poll_path()), exist_ok=True)
        with _JSON_IO_LOCK:
            _atomic_json_write(_poll_path(), cfg)
    except Exception:
        pass


class RisPollConfigReq(BaseModel):
    enabled: Optional[bool] = None
    interval_min: Optional[int] = None
    limit: Optional[int] = None
    auto_qc: Optional[bool] = None
    auto_enqueue: Optional[bool] = None


@app.get("/api/v1/ris/poll-status")
def ris_poll_status(emp: str = Depends(require_emp_local)):
    cfg = _poll_config()
    return _envelope(True, "OK", {
        "enabled": cfg.get("enabled", False),
        "interval_min": cfg.get("interval_min", 30),
        "limit": cfg.get("limit", 50),
        "auto_qc": cfg.get("auto_qc", True),
        "auto_enqueue": cfg.get("auto_enqueue", True),
        "last_run": cfg.get("last_run", ""),
        "last_count": cfg.get("last_count", 0),
        "last_error": cfg.get("last_error", ""),
        "seen_count": len(cfg.get("seen") or []),
    })


@app.put("/api/v1/ris/poll-config")
def ris_poll_config_put(req: RisPollConfigReq, emp: str = Depends(require_emp_local)):
    cfg = _poll_config()
    if req.enabled is not None:
        cfg["enabled"] = bool(req.enabled)
    if req.interval_min is not None:
        cfg["interval_min"] = max(5, min(int(req.interval_min), 1440))
    if req.limit is not None:
        cfg["limit"] = max(5, min(int(req.limit), 200))
    if req.auto_qc is not None:
        cfg["auto_qc"] = bool(req.auto_qc)
    if req.auto_enqueue is not None:
        cfg["auto_enqueue"] = bool(req.auto_enqueue)
    _save_poll_config(cfg)
    return _envelope(True, "OK", ris_poll_status(emp), "轮询配置已保存")


@app.post("/api/v1/ris/poll-now")
def ris_poll_now(emp: str = Depends(require_emp_local)):
    """手动立即触发一次轮询（不依赖 enabled 开关，便于配置后首跑验证）。"""
    try:
        result = _ris_poll_once(manual=True)
        return _envelope(True, "OK", result)
    except Exception as exc:
        return _envelope(False, "POLL_ERR", {"error": type(exc).__name__}, f"轮询失败：{exc}")


def _ris_poll_once(manual: bool = False) -> dict:
    """执行一次轮询：拉取 RIS → 质控 → 入库 + 入队。返回统计。

    互斥：非阻塞获取全局锁；若另一路（后台线程/手动）正在轮询则直接返回
    {"skipped": True}，避免并发拉取同一批报告重复入库/入队。
    """
    if not _RIS_POLL_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "已有轮询在进行中"}
    try:
        return _ris_poll_once_locked(manual)
    finally:
        _RIS_POLL_LOCK.release()


def _ris_poll_once_locked(manual: bool = False) -> dict:
    cfg = _poll_config()
    config = ris.load_config()
    if not config.get("host"):
        raise RuntimeError("RIS 连接未配置，请在 RIS 直连页填写并测试连接")
    reports = ris.fetch_reports(config, limit=int(cfg.get("limit", 50)))
    new_reports = []
    if not reports:
        new_count = 0
    else:
        seen = set(cfg.get("seen") or [])
        new_reports = []
        for r in reports:
            norm = "".join((r.get("report_text") or "").split())
            if not norm:
                continue
            h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            new_reports.append(r)
        cfg["seen"] = list(seen)[-5000:]   # 仅保留最近 5000 指纹，防无限膨胀
        new_count = len(new_reports)
        emp_id = "ris-poll"
        if cfg.get("auto_qc"):
            for r in new_reports:
                try:
                    qc = _run_qc(r.get("report_text") or "", {
                        "patient": r.get("patient", ""), "gender": r.get("gender", ""),
                        "age": r.get("age", ""), "modality": r.get("modality", ""),
                        "applied_site": r.get("applied_site", ""),
                    }, False)
                    findings = []
                    for f in qc.get("findings") or []:
                        findings.append(engine.Finding(
                            rule_id=f.get("rule_id", ""), error_type=f.get("error_type", ""),
                            severity=f.get("severity", "low"), message=f.get("message", ""),
                            snippet=f.get("snippet", ""),
                            span=tuple(f.get("span", (-1, -1))),
                            suggestion=f.get("suggestion", "")))
                    samplelib.save_sample(
                        r.get("report_text") or "",
                        {"patient": r.get("patient", ""), "gender": r.get("gender", ""),
                         "age": r.get("age", ""), "modality": r.get("modality", ""),
                         "applied_site": r.get("applied_site", "")},
                        findings, qc.get("score") or {},
                        anonymize=False, user_id=emp_id,
                        dept_id=accounts.get_dept_id(emp_id))
                except Exception:
                    continue
        if cfg.get("auto_enqueue"):
            for r in new_reports:
                try:
                    _queue_add_text(r.get("report_text") or "",
                                    {"patient": r.get("patient", ""),
                                     "gender": r.get("gender", ""),
                                     "age": r.get("age", ""),
                                     "modality": r.get("modality", ""),
                                     "applied_site": r.get("applied_site", "")},
                                    source="RIS轮询")
                except Exception:
                    continue
    cfg["last_run"] = datetime_now_iso()
    cfg["last_count"] = new_count
    cfg["last_error"] = ""
    _save_poll_config(cfg)
    return {"count": new_count, "total_seen": len(cfg.get("seen") or []),
            "last_run": cfg["last_run"], "new_reports": new_reports[:5]}


def datetime_now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


def _queue_add_text(text: str, meta: dict, source: str = "RIS轮询"):
    """复用 queue 去重逻辑（正文 MD5，数据库层原子去重）。返回条目 id 或 None。"""
    m = dict(meta or {})
    m.setdefault("source", source)
    m.setdefault("_emp", "ris-poll")  # RIS 自动入队：公共复核队列（医生可见，2026-08-18）
    _id, _dup = _queue_orm_add_dedup(text, m)
    return str(_id) if _id else None


def _ris_poll_loop(stop_event: threading.Event):
    """后台守护线程：按 interval_min 周期轮询。sleep 分片避免阻塞退出。
    2026-08-18 修复：此前固定每 60s 一轮，interval_min（默认 30 分钟）完全不参与调度，
    对医院 RIS 库造成 30 倍无谓查询压力。"""
    while not stop_event.is_set():
        try:
            cfg = _poll_config()
            if cfg.get("enabled"):
                _ris_poll_once(manual=False)
        except Exception:
            # 记录最近错误，供前端展示；不崩溃线程
            try:
                _log("error", f"RIS 轮询异常: {type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}")
                cfg = _poll_config()
                cfg["last_error"] = str(sys.exc_info()[1])[:300]
                cfg["last_run"] = datetime_now_iso()
                _save_poll_config(cfg)
            except Exception:
                pass
        # 分片 sleep（每 5s 检查一次停止事件），interval 在循环内实时读取
        interval_sec = max(15, int((_poll_config().get("interval_min") or 30) * 60))
        for _i in range(max(1, interval_sec // 5)):
            if stop_event.is_set():
                return
            time.sleep(5)


# 模块级建表 + 存量数据迁移（import 即就绪，不依赖 __main__ 入口：
# TestClient / uvicorn / 桌面壳 import server.main 均需要表已存在，2026-08-18 修复）。
#   - db.init_db()            ：users/departments/queue/settings/samples 建表（幂等）
#   - migrate_legacy_samples  ：assets/samples.db → qc.db.samples（旧库归档 .bak）
#   - _migrate_queue_to_db    ：qc_queue.json → qc.db.queue
#   - _migrate_settings_to_db ：web_settings.json → qc.db.settings
db.init_db()
try:
    samplelib.migrate_legacy_samples()
    samplelib.rescue_samples_conv()   # 抢救 samples_conv_old 滞留历史样本（2026-08-18 P0）
    _migrate_queue_to_db()
    _migrate_settings_to_db()
    # 导出产物兜底清理（2026-08-18）：下载中断/未触发下载时文件残留，
    # 启动时清理超过 3 天的 samples_export_*/质控报告_*（仅导出前缀，安全）。
    _export_dir = os.path.dirname(samplelib.db_path())
    _now = time.time()
    for _fn in os.listdir(_export_dir):
        if _fn.startswith(("samples_export_", "质控报告_")):
            _fp = os.path.join(_export_dir, _fn)
            try:
                if _now - os.path.getmtime(_fp) > 3 * 86400:
                    os.remove(_fp)
            except Exception:
                pass
except Exception:
    pass

# 模块加载即启动轮询守护线程（daemon，进程退出自动终止）
_RIS_POLL_STOP = threading.Event()
_RIS_POLL_THREAD = threading.Thread(target=_ris_poll_loop,
                                    args=(_RIS_POLL_STOP,), daemon=True)
_RIS_POLL_THREAD.name = "ris-poll-loop"
_RIS_POLL_THREAD.start()


# ----------------------------- 账号（责任到人） -----------------------------
@app.post("/api/v1/accounts")
def account_create(req: AccountCreate, request: Request,
                   authorization: Optional[str] = Header(None),
                   x_emp_id: Optional[str] = Header(None)):
    # 首个账号免鉴权引导（boot）；已存在账号则必须登录后才能创建（防滥用）。
    # X-Emp-Id 头仅限本机（127.0.0.1）兜底：内网 --host 0.0.0.0 部署时，
    # 任意客户端伪造 X-Emp-Id 即可批量创建账号（2026-08-18 修复）。
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
    ok, msg = accounts.create_account(req.emp_id, req.password, req.name)
    if not ok:
        return _envelope(False, "ERR", {}, msg)
    # 首账号自动 admin 已在 create_account 的 INSERT 事务内原子判定（BEGIN IMMEDIATE），
    # 无需在此二次 count+set_role（旧实现有并发竞态，两个并发首账号可都成 admin）
    token = make_token(req.emp_id)   # 首个账号创建即登录，免去二次登录
    return _envelope(True, "OK",
                     {"token": token, "emp_id": req.emp_id, "name": req.name,
                      "role": accounts.get_role(req.emp_id)}, msg)


# 登录失败限速（内存态）：连续失败 5 次锁定该工号 5 分钟，防弱口令爆破（2026-08-18 新增）
_LOGIN_FAIL: Dict[str, List] = {}
_LOGIN_MAX_FAIL = 5
_LOGIN_LOCK_SEC = 300


def _login_locked(emp_id: str) -> bool:
    rec = _LOGIN_FAIL.get(emp_id)
    if not rec:
        return False
    fails, first_ts, locked_until = rec
    if locked_until:
        if time.time() < locked_until:
            return True
        # 锁定期满：清除记录（此前不清 fails 会退化为"锁 10 分钟窗口"，与 5 分钟承诺不符）
        _LOGIN_FAIL.pop(emp_id, None)
        return False
    if time.time() - first_ts > 600:  # 10 分钟窗口内累计，超窗重置
        _LOGIN_FAIL.pop(emp_id, None)
        return False
    return fails >= _LOGIN_MAX_FAIL


@app.post("/api/v1/accounts/login")
def account_login(req: LoginReq):
    emp_id = (req.emp_id or "").strip()
    if _login_locked(emp_id):
        return _envelope(False, "ERR", {}, "登录失败次数过多，请稍后再试")
    if not accounts.verify_account(emp_id, req.password):
        rec = _LOGIN_FAIL.setdefault(emp_id, [0, time.time(), None])
        rec[0] += 1
        if rec[0] >= _LOGIN_MAX_FAIL:
            rec[2] = time.time() + _LOGIN_LOCK_SEC
        return _envelope(False, "ERR", {}, "工号或密码错误")
    _LOGIN_FAIL.pop(emp_id, None)
    token = make_token(emp_id)
    return _envelope(True, "OK",
                      {"token": token, "emp_id": emp_id,
                       "name": accounts.get_name(emp_id),
                       "role": accounts.get_role(emp_id)})


@app.get("/api/v1/accounts/me")
def account_me(emp: str = Depends(require_emp)):
    return _envelope(True, "OK",
                     {"emp_id": emp, "name": accounts.get_name(emp), "role": accounts.get_role(emp)})


@app.get("/api/v1/accounts")
def account_list(emp: str = Depends(require_emp)):
    # 管理员看全部（含角色/科室），普通用户只看自己
    if accounts.get_role(emp) == "admin":
        return _envelope(True, "OK", accounts.list_accounts_full())
    return _envelope(True, "OK",
                     [{"emp_id": emp, "name": accounts.get_name(emp), "role": accounts.get_role(emp)}])


class RoleReq(BaseModel):
    role: str


@app.post("/api/v1/accounts/{emp_id}/role")
def account_set_role(emp_id: str, req: RoleReq, admin: str = Depends(require_admin)):
    if req.role not in ("admin", "doctor"):
        return _envelope(False, "ERR", {}, "角色只能是 admin 或 doctor")
    if not accounts.set_role(emp_id, req.role):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "角色已更新")


class PwdReq(BaseModel):
    password: str


@app.post("/api/v1/accounts/{emp_id}/password")
def account_reset_password(emp_id: str, req: PwdReq, admin: str = Depends(require_admin)):
    if len(req.password or "") < 6:
        return _envelope(False, "ERR", {}, "密码至少 6 位")
    if not accounts.reset_password(emp_id, req.password):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "密码已重置")


class DeptReq(BaseModel):
    dept_id: Optional[int] = None   # 传 null/空可清除科室归属


@app.post("/api/v1/accounts/{emp_id}/dept")
def account_set_dept(emp_id: str, req: DeptReq, admin: str = Depends(require_admin)):
    if not accounts.set_dept(emp_id, req.dept_id):
        return _envelope(False, "ERR", {}, "账号不存在")
    return _envelope(True, "OK", {}, "科室已更新")


@app.get("/api/v1/departments")
def department_list(admin: str = Depends(require_admin)):
    return _envelope(True, "OK", accounts.list_departments())


class DeptCreateReq(BaseModel):
    name: str


@app.post("/api/v1/departments")
def department_create(req: DeptCreateReq, admin: str = Depends(require_admin)):
    ok, msg = accounts.create_department(req.name)
    if not ok:
        return _envelope(False, "ERR", {}, str(msg))
    return _envelope(True, "OK", {}, "科室已创建")


# ----------------------------- 授权（免责声明 / 试用期 / 激活码） -----------------------------
# 以下端点均为公开（无需登录），因为登录/激活本身就是闸门流程的一部分。
class ActivateReq(BaseModel):
    code: str


@app.get("/api/v1/license/status")
def license_status_get():
    """前端闸门用：免责/激活/试用剩余天数/机器码/账号数。"""
    return _envelope(True, "OK",
                      license_web.license_status(_appdata_dir(), accounts.count_accounts()))


@app.get("/api/v1/license/disclaimer")
def license_disclaimer_text():
    return _envelope(True, "OK", {"text": license_web.disclaimer_text()})


@app.post("/api/v1/license/disclaimer")
def license_disclaimer_accept():
    license_web.accept_disclaimer(_appdata_dir())
    return _envelope(True, "OK", {"disclaimer_accepted": True})


@app.get("/api/v1/license/machine-code")
def license_machine_code():
    return _envelope(True, "OK", {"machine_id": license_web.machine_id()})


@app.post("/api/v1/license/activate")
def license_activate(req: ActivateReq):
    ok = license_web.activate(_appdata_dir(), req.code)
    if not ok:
        return _envelope(False, "ERR",
                         license_web.license_status(_appdata_dir(), accounts.count_accounts()),
                         "激活码无效，请检查后重试")
    return _envelope(True, "OK",
                      license_web.license_status(_appdata_dir(), accounts.count_accounts()),
                      "激活成功")


# ----------------------------- 样本库（持久化 + 统计） -----------------------------
@app.post("/api/v1/samples")
def sample_create(req: SampleCreate, request: Request, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    # 样本归属以服务端鉴权为准：远程强制用鉴权工号，本地桌面保留客户端 user_id 兜底
    # （2026-08-18 修复：此前客户端 body 的 user_id 可直接覆盖鉴权工号，可篡改样本归属）。
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        user_id = (req.user_id or emp).strip() or emp
    else:
        user_id = emp
    raw_findings = list(req.findings) if req.findings else []
    # 入库即质控：未显式提供 findings 时，自动跑引擎生成发现与评分
    if not raw_findings and req.report.strip():
        qc = _run_qc(req.report, req.meta, False)
        raw_findings = qc.get("findings") or []
        score = qc.get("score") or {}
    else:
        score = req.score or {}
    # findings 由 dict 还原为 Finding 对象（save_sample 会序列化其 __dict__）
    findings = []
    for f in raw_findings:
        try:
            findings.append(engine.Finding(
                rule_id=f.get("rule_id", ""),
                error_type=f.get("error_type", ""),
                severity=f.get("severity", "low"),
                message=f.get("message", ""),
                snippet=f.get("snippet", ""),
                span=tuple(f.get("span", (-1, -1))),
                suggestion=f.get("suggestion", ""),
            ))
        except Exception:
            continue
    sid = samplelib.save_sample(
        req.report, req.meta, findings, score,
        anonymize=req.anonymize, user_id=user_id,
        dept_id=accounts.get_dept_id(user_id))
    return _envelope(True, "OK", {"id": sid})


@app.get("/api/v1/samples")
def sample_list(page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                user_id: Optional[str] = None,
                error_type: Optional[str] = None,
                emp: str = Depends(require_emp)):
    role = accounts.get_role(emp)
    # 归属过滤提前到 SQL 层（2026-08-18 性能优化）：避免全表载入长文本再 Python 过滤
    scope = user_id if (role == "admin" and user_id) else _scope_user_id(emp)
    # M10（2026-08-19）：无 error_type 过滤时，分页直接下沉到 SQL 层
    # （LIMIT/OFFSET + COUNT），避免为分页而全表载入长文本；
    # 带 error_type 过滤时仍需在 Python 侧按 findings_json 过滤，故全量载入（该路径低频）。
    if error_type:
        rows = samplelib.list_samples_full(user_id=scope)
        kept = []
        for r in rows:
            fj = r.get("findings_json") or "[]"
            try:
                ets = {x.get("error_type") for x in json.loads(fj)}
            except Exception:
                ets = set()
            if error_type in ets:
                kept.append(r)
        rows = kept
        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start:start + page_size]
    else:
        total = samplelib.count_samples(user_id=scope)
        start = (page - 1) * page_size
        page_rows = samplelib.list_samples_full(
            user_id=scope, limit=page_size, offset=start)
    items = []
    for r in page_rows:
        scores = _eng_scores(json.loads(r.get("scores_json") or "{}"))
        findings = json.loads(r.get("findings_json") or "[]")
        items.append({
            "id": r.get("id"),
            "ts": r.get("ts"),
            "patient": r.get("patient", ""),
            "gender": r.get("gender", ""),
            "age": r.get("age", ""),
            "modality": r.get("modality", ""),
            "applied_site": r.get("applied_site", ""),
            "laterality": r.get("laterality", ""),
            "report_text": (r.get("report_text") or "")[:200],
            "findings_count": len(findings),
            "scores": scores,
        })
    return _envelope(True, "OK", {
        "total": total, "items": items, "page": page,
        "page_size": page_size, "pages": (total + page_size - 1) // page_size,
    })


@app.get("/api/v1/samples/{sid}")
def sample_get(sid: int, emp: str = Depends(require_emp_local)):
    """样本详情（含患者信息与报告全文）：本地 WebView 放行；
    远程（如内网 --host 0.0.0.0）强制凭证，避免无鉴权读取患者隐私。
    多用户隔离：普通医生仅可读自己导入的样本（2026-08-18）。"""
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    scope = _scope_user_id(emp)
    if scope and s.get("user_id") and s.get("user_id") != scope:
        raise HTTPException(403, "无权查看他人样本")
    return _envelope(True, "OK", s)


@app.delete("/api/v1/samples/{sid}")
def sample_delete(sid: int, emp: str = Depends(require_emp_local)):
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    # 归属校验：本地（require_emp_local 返回 "local"）是桌面单机唯一使用者，
    # 允许删除；远程访问则强制责任到人（只能删自己导入的样本）。
    if s.get("user_id") and emp != "local" and s.get("user_id") != emp:
        raise HTTPException(403, "无权删除他人样本")
    samplelib.delete_sample(sid)
    return _envelope(True, "OK", None, "已删除")


@app.post("/api/v1/samples/export")
def sample_export(req: SampleExportReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """导出样本库为 CSV / JSON / DOCX / PDF（修正 Flask 版把输出路径误传为库路径参数的问题）。"""
    try:
        fmt = (req.fmt or "csv").lower()
        if fmt not in ("csv", "json", "docx", "pdf"):
            raise HTTPException(400, "fmt 仅支持 csv/json/docx/pdf")
        # 安全收紧（2026-08-18）：忽略客户端 body 中的 path，固定写到样本库目录下的
        # 自动命名文件（samples_export_<时间戳>.<ext>），杜绝任意路径写入。
        # 多用户隔离：普通医生仅导出自己导入的样本（2026-08-18）。
        result_path = samplelib.export_samples(out_path=None, fmt=fmt,
                                               user_id=_scope_user_id(emp),
                                               anonymize=bool(req.anonymize))
        return _envelope(True, "OK", {"path": result_path, "fmt": fmt})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


class SampleReportExportReq(BaseModel):
    """单份质控报告单导出请求。fmt: docx | pdf"""
    fmt: str = "docx"


class QcReportExportReq(BaseModel):
    """当前工作区质控结果直接导出报告单（无需入库）。"""
    report: str = ""
    meta: Dict[str, str] = {}
    findings: List[dict] = []
    scores: Dict[str, Any] = {}
    fmt: str = "docx"


@app.post("/api/v1/qc/export-report")
def qc_report_export(req: QcReportExportReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """把当前质控结果直接导出为质控报告单（PDF/Word），无需先入库。"""
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    fmt = (req.fmt or "docx").lower()
    if fmt not in ("docx", "pdf"):
        raise HTTPException(400, "fmt 仅支持 docx/pdf")
    try:
        path = samplelib.export_qc_report(
            req.report, req.meta, req.findings or [],
            req.scores or {}, fmt=fmt)
        return _envelope(True, "OK", {"path": path, "fmt": fmt})
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@app.post("/api/v1/samples/{sid}/export-report")
def sample_report_export(sid: int, req: SampleReportExportReq,
                         emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """导出单份样本的质控报告单（PDF/Word）：标题、检查部位、原报告、质控发现、建议修正。"""
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    scope = _scope_user_id(emp)
    if scope and s.get("user_id") and s.get("user_id") != scope:
        raise HTTPException(403, "无权导出他人样本")
    fmt = (req.fmt or "docx").lower()
    try:
        if fmt == "pdf":
            path = samplelib.export_report_pdf(s)
        else:
            path = samplelib.export_report_docx(s)
        return _envelope(True, "OK", {"path": path, "fmt": fmt})
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@app.get("/api/v1/files/download")
def file_download(file: str = Query(...), emp: str = Depends(require_emp_local)):
    """下载服务端生成的导出文件（限定文件名前缀 + 导出目录，防任意路径读取/删除）。

    前端把导出接口返回的 path 的 basename 传回即可拿到文件流。
    下载完成后自动删除服务端文件，避免导出文件在资产目录无限累积（2026-08-18）。
    2026-08-18 加固：仅允许导出产物前缀（samples_export_*/质控报告_*）——此前仅按
    basename 限定目录，构造 ?file=qc.db 即可下载并删除整个样本库（数据丢失级风险）。
    """
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    name = os.path.basename(file or "")
    if not name or name in (".", ".."):
        raise HTTPException(400, "无效的文件名")
    # 白名单：仅导出产物可被下载（下载后删除），禁止任何库/配置/模型文件
    if not (name.startswith("samples_export_") or name.startswith("质控报告_")):
        raise HTTPException(404, "仅支持下载导出产物文件")
    # 限定在样本库所在目录 / 临时导出目录，防路径穿越
    export_dir = os.path.dirname(samplelib.db_path())
    full = os.path.join(export_dir, name)
    if not os.path.exists(full):
        raise HTTPException(404, "文件不存在或已被清理")

    def _cleanup():
        try:
            os.remove(full)
        except Exception:
            pass

    return FileResponse(full, filename=name, background=BackgroundTask(_cleanup))


@app.post("/api/v1/samples/import")
def sample_import(req: SampleImportReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """导入样本库文件（仅限样本库目录下已存在的 csv/json，取 basename 防任意路径读取）。"""
    try:
        name = os.path.basename((req.path or "").strip())
        if not name or os.path.splitext(name)[1].lower() not in (".csv", ".json"):
            raise HTTPException(400, "导入仅支持 csv/json 文件（文件名取 basename）")
        full = os.path.join(os.path.dirname(samplelib.db_path()), name)
        if not os.path.exists(full):
            raise HTTPException(404, "文件不存在或已被清理")
        inserted, skipped = samplelib.import_samples(full)
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@app.post("/api/v1/samples/import/upload")
async def sample_import_upload(file: UploadFile = File(...), emp: str = Depends(require_emp_local),
                          _lic: bool = Depends(require_license_active)):
    """浏览器端上传 CSV/JSON 文件导入样本库。"""
    import tempfile
    suffix = os.path.splitext(file.filename or "import.csv")[1] or ".csv"
    # 上传体积上限（与 OCR 上传一致），防止超大文件一次性读入内存造成 DoS
    _MAX_IMPORT_BYTES = 20 * 1024 * 1024
    data = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, f"导入文件超过 {_MAX_IMPORT_BYTES // (1024 * 1024)}MB 上限")
    tf = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
    try:
        tf.write(data)
        tf.close()
        inserted, skipped = samplelib.import_samples(tf.name)
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)
    finally:
        try:
            os.remove(tf.name)
        except Exception:
            pass


# ----------------------------- 统计 -----------------------------
@app.get("/api/v1/stats/error-types")
def stats_error_types(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_error_type(
        user_id=_scope_user_id(emp)))


@app.get("/api/v1/stats/trend")
def stats_trend(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_date(
        user_id=_scope_user_id(emp)))


@app.get("/api/v1/stats/report")
def stats_report(start: Optional[str] = None, end: Optional[str] = None,
                 emp: str = Depends(require_emp_local)):
    """质控问题分类统计报表（时间段筛选 + 问题类型 TOP 榜 + 科室/医生排行榜）。

    start / end 格式 YYYY-MM-DD，缺省不限。
    """
    try:
        data = samplelib.stats_report(start=start, end=end,
                                      user_id=_scope_user_id(emp))
        return _envelope(True, "OK", data)
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@app.get("/api/v1/samples/stats/dashboard")
def sample_dashboard(emp: str = Depends(require_emp_local)):
    """看板页聚合统计（迁移自 web/api/samples.py 的 dashboard_stats）。
    多用户隔离：普通医生仅统计本人样本（2026-08-18）。"""
    from datetime import datetime, timedelta
    rows = samplelib.list_samples_full(user_id=_scope_user_id(emp))
    total = len(rows)
    by_modality = {}
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    today = 0
    this_week = 0
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    today_str = now.strftime("%Y-%m-%d")
    for row in rows:
        mod = row.get("modality", "未知")
        by_modality[mod] = by_modality.get(mod, 0) + 1
        findings = json.loads(row.get("findings_json") or "[]")
        by_severity[_worst_sev(findings)] = by_severity.get(_worst_sev(findings), 0) + 1
        ts = row.get("ts", "")
        if ts and ts.startswith(today_str):
            today += 1
        if ts and ts > week_ago.strftime("%Y-%m-%d"):
            this_week += 1
    return _envelope(True, "OK", {
        "total": total, "today": today, "this_week": this_week,
        "by_modality": by_modality, "by_severity": by_severity,
    })


@app.get("/api/v1/health")
def health():
    return _envelope(True, "OK", {"status": "up", "version": APP_VERSION})


@app.get("/api/v1/update/check")
def update_check(emp: str = Depends(require_emp_local)):
    """检查更新（2026-08-18 接入：update_check.check_update_sync 此前无任何生产调用方）。

    返回 {status: update|latest|unknown|error, message, url, published_at}。
    仅读取 GitHub Release，幂等、无副作用。
    """
    import update_check as _uc
    result = _uc.check_update_sync(timeout=5)
    return _envelope(True, "OK", {
        "status": result.get("status", "error"),
        "message": result.get("message", ""),
        "url": result.get("url", ""),
        "published_at": result.get("published_at", ""),
    })


# ============================================================================
# 以下为「SPA 功能追平桌面版」新增能力（P0/P1）
#   1) 待质控队列   —— 与 Tkinter 版共用 ~/.medical_report_qc/qc_queue.json
#   2) 屏幕采集 OCR —— 真·框选 PACS 屏幕三区（基础信息/影像描述/影像诊断）
#   3) 应用设置     —— SPA 侧设置面板的持久化
#   4) 规则配置读取 —— 供规则维护页编辑 R8 错别字 / R9 矛盾对 / 忽略词
# 注意：必须注册在文件末尾的 SPA catch-all 路由「之前」，否则 GET 会被兜底吞掉。
# ============================================================================
import uuid as _uuid


class QueueItemReq(BaseModel):
    text: str
    patient: str = ""
    site: str = ""
    source: str = "手动"
    meta: Dict[str, str] = {}


@app.get("/api/v1/queue")
def queue_list(emp: str = Depends(require_emp_local)):
    items = _load_queue()
    # 归属过滤（2026-08-18）：非 admin 仅看自己提交的队列项 + RIS 公共复核项（_emp=ris-poll）
    if accounts.get_role(emp) != "admin":
        items = [it for it in items
                 if (it.get("meta") or {}).get("_emp") in (emp, "ris-poll")]
    return _envelope(True, "OK", {"items": items, "count": len(items)})


@app.post("/api/v1/queue")
def queue_add(req: QueueItemReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """加入待质控队列；按正文 MD5 去重（2026-08-18 收敛：落 qc.db QueueItem 表，
    数据库层唯一索引原子去重，杜绝并发重复入队）。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    meta = dict(req.meta or {})
    meta.setdefault("patient", (req.patient or "").strip())
    meta.setdefault("applied_site", (req.site or "").strip())
    meta.setdefault("source", req.source or "手动")
    meta.setdefault("_emp", emp)  # 记录提交人工号，供 queue_list 归属过滤（2026-08-18）
    new_id, duplicated = _queue_orm_add_dedup(text, meta)
    if duplicated:
        return _envelope(True, "OK", {"id": str(new_id), "duplicated": True}, "该报告已在队列中")
    items = _load_queue()
    return _envelope(True, "OK", {"id": str(new_id), "duplicated": False, "count": len(items) + 1})


@app.delete("/api/v1/queue")
def queue_clear(emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    # 归属校验（2026-08-18）：清空是破坏性操作，仅 admin 可整表清空；
    # 普通医生只能清掉自己的条目（走逐条删除）。
    if accounts.get_role(emp) != "admin":
        raise HTTPException(403, "仅管理员可清空队列，可逐条移出自己提交的条目")
    _queue_orm_clear()
    return _envelope(True, "OK", {"count": 0}, "队列已清空")


@app.delete("/api/v1/queue/{qid}")
def queue_remove(qid: str, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    try:
        qid_int = int(qid)
    except (TypeError, ValueError):
        raise HTTPException(404, "队列条目不存在")
    items = _load_queue()
    it = next((x for x in items if str(x.get("id")) == qid), None)
    if not it:
        raise HTTPException(404, "队列条目不存在")
    # 归属校验：非 admin 仅可移出自己提交（meta._emp == 工号）或 RIS 公共复核项
    if accounts.get_role(emp) != "admin":
        owner = (it.get("meta") or {}).get("_emp")
        if owner not in (emp, "ris-poll"):
            raise HTTPException(403, "只能移出自己提交的条目")
    if not _queue_orm_remove(qid_int):
        raise HTTPException(404, "队列条目不存在")
    return _envelope(True, "OK", {"count": len(_load_queue())}, "已移出队列")


# ----------------------------- 屏幕采集（真·框选 PACS 屏幕） -----------------------------
# 用户诉求：不是上传报告图，而是在 PACS 软件窗口上框选三个区域
#   basic=病人基础信息 / findings=影像描述 / impression=影像诊断
# 流程：/screen/capture 抓全屏（原图缓存于内存）→ SPA 在缩略图上拖三个框
#      → /screen/ocr 传比例框 → 后端在「原始分辨率」截图上裁剪 → RapidOCR
# 在原图上裁剪（而非缩略图）可避免下采样导致的小字识别率骤降。
_SHOT: Dict[str, Any] = {"img": None, "w": 0, "h": 0, "ts": 0.0}
_SHOT_MAX_W = 1600          # 传给前端的缩略图最大宽度（省带宽，不影响识别精度）

# OCR 结果缓存：按「区域 key + 裁剪图指纹」缓存识别文本，画面未变时跳过推理，降低重识别卡顿。
_OCR_CACHE: Dict[str, Any] = {}     # region_key -> {"sig": tuple, "text": str}
_OCR_CACHE_MAX = 12
# 截屏/识别共享全局（_SHOT/_OCR_CACHE）并发锁：热键连按 + SPA 同时触发时，
# 防止「一个请求重抓屏覆盖另一个正在裁剪的原图」「缓存读写非原子」的竞态。
_OCR_LOCK = threading.Lock()
# RIS 轮询互斥：后台守护线程与 /ris/poll-now 手动触发共用 _ris_poll_once，
# 无锁时两路并发会把同一批报告各自识别为"新报告"重复入库/入队（2026-08-18 修复）。
_RIS_POLL_LOCK = threading.Lock()


class ScreenRegion(BaseModel):
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


class ScreenOCRReq(BaseModel):
    regions: Dict[str, ScreenRegion] = {}
    refresh: bool = False       # True=识别前重新抓屏（画面已变动时用）
    dynamic: bool = False       # True=动态语义识别：整屏OCR后按标题切分，滚动不变形
    dynamic_region: Optional[ScreenRegion] = None  # 动态模式限定 OCR 范围（三区外接矩形）


def _grab_fullscreen():
    from PIL import ImageGrab
    img = ImageGrab.grab()
    if img is None:
        raise RuntimeError("截屏返回空图")
    # macOS 未授予「屏幕录制」权限时会返回纯黑图（不抛异常），这里做启发式检测
    try:
        ext = img.convert("L").getextrema()
        if ext == (0, 0):
            raise RuntimeError(
                "截屏结果全黑：macOS 需在『系统设置 → 隐私与安全性 → 屏幕录制』"
                "中勾选本应用（终端/星衍质控），授权后需重启应用。")
    except RuntimeError:
        raise
    except Exception:
        pass
    return img


@app.post("/api/v1/screen/capture")
def screen_capture(emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """抓取整屏，返回缩略图 base64（原图缓存于服务端供后续高精度裁剪）。"""
    try:
        with _OCR_LOCK:
            img = _grab_fullscreen()
            _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = img, img.width, img.height, time.time()
    except Exception as exc:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "SCREEN_UNAVAILABLE", None, type(exc).__name__))
    thumb = img
    if img.width > _SHOT_MAX_W:
        ratio = _SHOT_MAX_W / float(img.width)
        thumb = img.resize((_SHOT_MAX_W, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    thumb.convert("RGB").save(buf, format="PNG")
    return _envelope(True, "OK", {
        "image_base64": base64.b64encode(buf.getvalue()).decode(),
        "width": img.width, "height": img.height,
        "thumb_width": thumb.width, "thumb_height": thumb.height,
        "ts": _SHOT["ts"],
    })


@app.post("/api/v1/screen/ocr")
def screen_ocr(req: ScreenOCRReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
    """按比例框在缓存的整屏原图上裁剪并 OCR，返回三区文本 + 结构化 meta。"""
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    img = _SHOT.get("img")
    with _OCR_LOCK:
        if req.refresh or img is None:
            try:
                img = _grab_fullscreen()
                _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = \
                    img, img.width, img.height, time.time()
            except Exception as exc:
                return JSONResponse(status_code=503,
                                    content=_envelope(False, "SCREEN_UNAVAILABLE", None, type(exc).__name__))
        W, H = img.width, img.height
        texts: Dict[str, str] = {}
        errors: Dict[str, str] = {}

        if req.dynamic:
            # ---- 动态语义识别：先按三区外接矩形裁剪（限定报告区），
            #      再对裁剪结果 OCR 一次，按标题在文本流中切分 ----
            # 固定像素框在 PACS 内容上下/左右滚动后会错位；本模式不依赖精确坐标，
            # 只依赖「检查所见/影像描述 → 描述段、诊断印象/结论 → 诊断段」的文本顺序，
            # 滚动改变的是屏幕上的像素位置，不改变文本流顺序，故怎么滚都能识别对。
            # 外接矩形只需粗略覆盖报告区即可：排除 PACS 报告区外的工具栏/图像区/
            # 其他窗口文字，避免整屏 OCR 把无关内容切进描述/诊断段。
            ocr_img = img
            if req.dynamic_region:
                dr = req.dynamic_region
                x0 = max(0, min(W - 1, int(dr.x * W)))
                y0 = max(0, min(H - 1, int(dr.y * H)))
                x1 = max(x0 + 1, min(W, int((dr.x + dr.w) * W)))
                y1 = max(y0 + 1, min(H, int((dr.y + dr.h) * H)))
                ocr_img = img.crop((x0, y0, x1, y1))
            try:
                full = ocr_provider.ocr_image(ocr_img) or ""
                texts, errors = _split_dynamic(full)
            except Exception as exc:
                errors["_dynamic"] = type(exc).__name__
        else:
            # ---- 固定框位模式（原逻辑，供精确框选场景）----
            for role, r in (req.regions or {}).items():
                x0 = max(0, min(W - 1, int(r.x * W)))
                y0 = max(0, min(H - 1, int(r.y * H)))
                x1 = max(x0 + 1, min(W, int((r.x + r.w) * W)))
                y1 = max(y0 + 1, min(H, int((r.y + r.h) * H)))
                crop = img.crop((x0, y0, x1, y1))
                # 画面未变则直接复用上次识别结果，跳过 CPU 推理（解决重复识别卡顿）
                try:
                    sig = ocr_provider.image_signature(crop)
                except Exception:
                    sig = None
                cached = _OCR_CACHE.get(role)
                if cached and cached.get("sig") == sig:
                    texts[role] = cached.get("text") or ""
                    continue
                try:
                    txt = ocr_provider.ocr_image(crop) or ""
                    texts[role] = txt
                    _OCR_CACHE[role] = {"sig": sig, "text": txt}
                    if len(_OCR_CACHE) > _OCR_CACHE_MAX:
                        _OCR_CACHE.pop(next(iter(_OCR_CACHE)))
                except Exception as exc:
                    texts[role] = ""
                    errors[role] = type(exc).__name__
    meta = {}
    try:
        meta = engine.extract_meta_full(texts.get("basic", ""),
                                        texts.get("findings", ""),
                                        texts.get("impression", ""))
    except Exception:
        try:
            meta = engine.extract_meta(texts.get("basic", ""))
        except Exception:
            meta = {}
    return _envelope(True, "OK", {"texts": texts, "meta": meta, "errors": errors})


# 动态模式：整屏 OCR 文本流 → 按标题切分三区。
# 顺序遍历所有行，命中标题关键词的行作为段起点：
#   basic=患者信息 / findings=检查所见·影像描述 / impression=诊断印象·影像诊断·结论
# 若找不到某标题，则该段并入相邻段或留空，由 extract_meta_full / 前端兜底。
_FINDINGS_TITLES = ("检查所见", "影像所见", "影像描述", "所见", "检查描述", "描述")
_IMPRESSION_TITLES = ("诊断印象", "影像诊断", "诊断意见", "诊断结论", "印象", "结论", "诊断")
_BASIC_TITLES = ("患者", "病人", "姓名", "检查号", "影像号", "登记")


def _strip_title(line: str, pats) -> str:
    """剥离行首的段落标题词，保留正文。如『检查所见：双肺纹理增多』→『双肺纹理增多』。
    标题可能后接中文冒号/空格/顿点；也可能标题在行中（罕见），统一只剥行首。"""
    s = line.strip()
    for p in sorted(pats, key=len, reverse=True):
        if s.startswith(p):
            rest = s[len(p):].lstrip("：:：: .、\t")
            return rest.strip()
        # 兼容『所见：』『描述 :』等带空格的标题写法
        if s.startswith(p + " ") or s.startswith(p + "："):
            rest = s[len(p):].lstrip(" ：: .、\t")
            return rest.strip()
    return s


def _split_dynamic(full: str) -> (Dict[str, str], Dict[str, str]):
    texts = {"basic": "", "findings": "", "impression": ""}
    errors: Dict[str, str] = {}
    lines = [ln for ln in (full or "").splitlines() if ln.strip()]
    if not lines:
        return texts, errors
    # 找各段标题行下标（首个命中）
    def _first_idx(pats):
        for i, ln in enumerate(lines):
            for p in pats:
                if p in ln:
                    return i
        return -1
    f_idx = _first_idx(_FINDINGS_TITLES)
    i_idx = _first_idx(_IMPRESSION_TITLES)
    b_idx = _first_idx(_BASIC_TITLES)
    # 修正：诊断标题若出现在描述标题之前（PACS 常把「诊断」列在患者信息区），
    # 以描述标题为基准重排——取描述之后首个诊断标题。
    if f_idx >= 0 and i_idx >= 0 and i_idx < f_idx:
        for j in range(f_idx, len(lines)):
            if any(p in lines[j] for p in _IMPRESSION_TITLES):
                i_idx = j
                break
    # basic：起始（或患者标题）→ 描述标题（或诊断标题）
    # 注意：若「患者」标题出现在描述/诊断之后（部分 PACS 布局），b_start > end，
    # 直接取起始段即可（lines[0:end]），避免 basic 被截成空串。
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
    elif i_idx >= 0:
        end = i_idx
    else:
        end = len(lines)
    b_start = b_idx if 0 <= b_idx < end else 0
    texts["basic"] = "\n".join(lines[b_start:end]).strip()
    # findings：从描述标题行开始（含该行正文，标题词被剥掉）→ 诊断标题行前
    # 注意跳过中间的患者标题行（部分 PACS 布局把患者信息插在描述段里），
    # 避免「患者：张三」等 basic 内容混入描述正文。
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
        head = _strip_title(lines[f_idx], _FINDINGS_TITLES)
        body = [head]
        for ln in lines[f_idx + 1:end]:
            if b_idx >= 0 and b_idx != f_idx and any(p in ln for p in _BASIC_TITLES):
                continue
            body.append(ln)
        texts["findings"] = "\n".join(body).strip()
    # impression：从诊断标题行开始（含该行正文，标题词被剥掉）→ 末尾
    if i_idx >= 0:
        head = _strip_title(lines[i_idx], _IMPRESSION_TITLES)
        body = [head] + [ln for ln in lines[i_idx + 1:]]
        texts["impression"] = "\n".join(body).strip()
    return texts, errors


class OCRMetaReq(BaseModel):
    """图片模式下，前端已对三区分别 OCR，把三区文本送来后端做结构化抽取。"""
    basic: str = ""
    findings: str = ""
    impression: str = ""


@app.post("/api/v1/ocr/meta")
def ocr_meta(req: OCRMetaReq, emp: str = Depends(require_emp_local)):
    """对三段 OCR 文本做结构化抽取（姓名/性别/年龄/部位/侧别/检查类型）。

    与 ``/screen/ocr`` 共用 ``engine.extract_meta_full``，保证「屏幕模式」与「图片模式」
    姓名回填行为一致、都走后端最稳健的跨区补抽逻辑。前端在图片模式下拿到本结果后
    优先用于回填，避免只依赖前端解析在『独立姓名行』等边缘布局下漏抽。
    """
    try:
        meta = engine.extract_meta_full(req.basic or "", req.findings or "", req.impression or "")
    except Exception as exc:
        return _envelope(False, "META_ERR", None, type(exc).__name__)
    return _envelope(True, "OK", {"meta": meta})


def _ocr_config_path() -> str:
    """与 src/app.py 的 _ocr_config_path 同路径，实现桌面/Web 区域配置互通。"""
    ap = os.path.expandvars("%APPDATA%")
    if ap and os.path.isabs(ap):
        d = os.path.join(ap, "MedicalReportQC")
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "MedicalReportQC")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "ocr_config.json")


@app.get("/api/v1/screen/regions")
def screen_regions_get(emp: str = Depends(require_emp_local)):
    """读取 SPA 侧保存的比例框（web_regions）。"""
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    return _envelope(True, "OK", {"web_regions": cfg.get("web_regions") or {}})


@app.put("/api/v1/screen/regions")
def screen_regions_put(regions: Dict[str, Any], emp: str = Depends(require_emp_local)):
    # 坐标校验（2026-08-18）：0<=x,y<=1 且 0<w,h<=1 且非 NaN——此前原样持久化坏值，
    # 越界/NaN 会在 /screen/ocr 的 int(r.x*W) 抛 ValueError 500。
    import math
    for _k, r in (regions or {}).items():
        if not isinstance(r, dict):
            raise HTTPException(400, "区域格式应为 {key: {x,y,w,h}}")
        for _f in ("x", "y", "w", "h"):
            v = r.get(_f)
            if not isinstance(v, (int, float)) or math.isnan(v):
                raise HTTPException(400, f"区域坐标 {_f} 非法")
        if not (0 <= r["x"] <= 1 and 0 <= r["y"] <= 1
                and 0 < r["w"] <= 1 and 0 < r["h"] <= 1):
            raise HTTPException(400, "区域坐标越界（x/y 0~1，w/h 0~1 且 >0）")
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    cfg["web_regions"] = regions
    with open(_ocr_config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return _envelope(True, "OK", {"web_regions": regions}, "框选区域已保存")


# ----------------------------- 应用设置（SPA 设置面板） -----------------------------
_DEFAULT_SETTINGS = {
    "emp_id": "demo01",            # 默认工号（责任到人）
    "default_modality": "",        # 默认成像方式
    "auto_qc_on_ocr": True,        # OCR 回填后自动跑质控
    "auto_enqueue": True,          # 采集/RIS 拉取自动进待质控队列
    "ocr_min_score": 0.55,         # OCR 置信度阈值
    "screen_refresh_on_ocr": False,  # 识别前重新抓屏
    "ocr_dynamic": True,           # 动态语义识别（整屏OCR按标题切分）
    "ocr_silent": False,           # 静默质控：一键识别完成后不强制弹窗
    "anonymize": False,            # 入库脱敏
    "theme": "light",
    # ── 可配置快捷键（Windows 风 Ctrl+ 默认；设置页可逐条重绑，持久化到 web_settings.json）──
    # mods 取值: "ctrl" / "shift" / "alt" / "meta"；key 为 KeyboardEvent.key（大小写敏感）
    "shortcuts": {
        "run_qc":      {"mods": ["ctrl"], "key": "Enter"},   # 运行质控
        "save_sample": {"mods": ["ctrl"], "key": "s"},       # 存入样本库
        "ocr_capture": {"mods": ["ctrl", "shift"], "key": "o"},  # 识别并质控（框选OCR）
        "toggle_theme":{"mods": ["ctrl"], "key": "t"},       # 明暗主题切换
    },
}


def _settings_path() -> str:
    return os.path.join(_appdata_dir(), "web_settings.json")


@app.get("/api/v1/settings")
def settings_get(emp: str = Depends(require_emp_local)):
    data = dict(_DEFAULT_SETTINGS)
    data.update(_settings_orm_all())
    return _envelope(True, "OK", data)


@app.put("/api/v1/settings")
def settings_put(cfg: Dict[str, Any], emp: str = Depends(require_emp_local)):
    data = dict(_DEFAULT_SETTINGS)
    data.update(_settings_orm_all())
    for k in _DEFAULT_SETTINGS:          # 只接受已知键，避免写入杂项
        if k == "shortcuts":
            continue                     # shortcuts 走下方逐条合并，避免整体覆盖
        if k in cfg:
            data[k] = cfg[k]
    # shortcuts 为嵌套字典：以「默认值+已持久化」为基线，逐条合并已知动作键
    if isinstance(cfg.get("shortcuts"), dict):
        known = set((_DEFAULT_SETTINGS.get("shortcuts") or {}).keys())
        cur = dict(data.get("shortcuts") or {})
        for act, sc in cfg["shortcuts"].items():
            if act in known:
                cur[act] = sc
        data["shortcuts"] = cur
    _settings_orm_save(data)
    return _envelope(True, "OK", data, "设置已保存")


# ----------------------------- 规则配置（供规则维护页编辑） -----------------------------
@app.get("/api/v1/qc/rules/config")
def qc_rules_config_get(emp: str = Depends(require_emp_local)):
    """返回可编辑的规则配置：R8 错别字表 / R9 矛盾对 / 忽略词 / 模板规范。"""
    return _envelope(True, "OK", engine.load_rules_config())


@app.put("/api/v1/qc/rules/config")
def qc_rules_config_put(cfg: Dict[str, Any], emp: str = Depends(require_admin)):
    """合并保存规则配置（读→合并→写回；2026-08-18 M3 修复：保留未提交的
    enable_r19/r19_sensitivity/disabled_typos 等键，避免半量覆盖丢数据）。"""
    try:
        cur = engine.load_rules_config()
        for k, v in cfg.items():
            cur[k] = v
        engine.save_rules_config(cur)
        _reload_engine_rules()
        return _envelope(True, "OK", engine.load_rules_config(), "规则配置已保存")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@app.post("/api/v1/qc/rules/config/reset")
def qc_rules_config_reset(emp: str = Depends(require_admin)):
    """恢复出厂默认规则库（覆盖用户自定义）。"""
    try:
        cfg = engine.default_rules_config()
        engine.save_rules_config(cfg)
        return _envelope(True, "OK", engine.load_rules_config(), "已恢复默认规则库")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


class LearnTypoReq(BaseModel):
    wrong: str = ""
    correct: str = ""


@app.post("/api/v1/qc/rules/learn-typo")
def qc_rules_learn_typo(req: LearnTypoReq, emp: str = Depends(require_admin)):
    """P0 修正反馈闭环：用户确认的「错词→正确词」写入规则库，下次自动识别。"""
    ok = engine.learn_typo(req.wrong, req.correct)
    if not ok:
        raise HTTPException(400, "无效的错字对（为空或已存在反向冲突）")
    return _envelope(True, "OK", None, f"已学习错字对：{req.wrong}→{req.correct}")


# ----------------------------- 错别字词库可视化维护（P0：增删改/批量导入/启停单条） -----------------------------
class TypoItemReq(BaseModel):
    wrong: str = ""
    correct: str = ""


class TypoBatchImportReq(BaseModel):
    """批量导入：items 为 [[错词, 正确词], ...] 或 [{wrong, correct}, ...]"""
    items: list = []


@app.post("/api/v1/qc/rules/typos")
def qc_rules_typo_add(req: TypoItemReq, emp: str = Depends(require_admin)):
    """新增单条错字对（若已存在则更新正确词并自动启用）。"""
    ok = engine.learn_typo(req.wrong, req.correct)
    if not ok:
        raise HTTPException(400, "无效的错字对（为空、过长或含非中文）")
    # 新增时自动从停用列表移除（新维护的词默认启用）
    cfg = engine.load_rules_config()
    disabled = cfg.get("disabled_typos") or []
    if req.wrong.strip() in disabled:
        cfg["disabled_typos"] = [d for d in disabled if d != req.wrong.strip()]
        engine.save_rules_config(cfg)
    return _envelope(True, "OK", None, f"已新增错字对：{req.wrong}→{req.correct}")


@app.post("/api/v1/qc/rules/typos/toggle")
def qc_rules_typo_toggle(req: TypoItemReq, emp: str = Depends(require_admin)):
    """启用/停用单条错字：enabled=false 时把错词加入 disabled_typos，true 时移出。"""
    wrong = (req.wrong or "").strip()
    if not wrong:
        raise HTTPException(400, "缺少错词")
    enabled = (req.correct or "").lower() not in ("0", "false", "off", "停用")
    cfg = engine.load_rules_config()
    disabled = set(cfg.get("disabled_typos") or [])
    if enabled:
        disabled.discard(wrong)
        msg = "已启用"
    else:
        disabled.add(wrong)
        msg = "已停用"
    cfg["disabled_typos"] = sorted(disabled)
    engine.save_rules_config(cfg)
    return _envelope(True, "OK", {"wrong": wrong, "enabled": enabled}, f"{msg}错字词条「{wrong}」")


@app.post("/api/v1/qc/rules/typos/delete")
def qc_rules_typo_delete(req: TypoItemReq, emp: str = Depends(require_admin)):
    """删除单条错字（同时从停用列表移除）。"""
    wrong = (req.wrong or "").strip()
    if not wrong:
        raise HTTPException(400, "缺少错词")
    cfg = engine.load_rules_config()
    typos = cfg.get("typos") or {}
    if wrong not in typos:
        raise HTTPException(404, f"错词「{wrong}」不存在")
    typos.pop(wrong, None)
    cfg["typos"] = typos
    cfg["disabled_typos"] = [d for d in (cfg.get("disabled_typos") or []) if d != wrong]
    engine.save_rules_config(cfg)
    return _envelope(True, "OK", None, f"已删除错字词条「{wrong}」")


@app.post("/api/v1/qc/rules/typos/batch-import")
def qc_rules_typo_batch_import(req: TypoBatchImportReq, emp: str = Depends(require_admin)):
    """批量导入错字对：自动跳过无效/反向冲突项，返回成功与失败数。"""
    cfg = engine.load_rules_config()
    typos = cfg.get("typos") or {}
    disabled = set(cfg.get("disabled_typos") or [])
    ok_n = bad_n = 0
    bad_items = []
    for it in (req.items or []):
        if isinstance(it, dict):
            wrong, correct = (it.get("wrong") or "").strip(), (it.get("correct") or "").strip()
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            wrong, correct = str(it[0]).strip(), str(it[1]).strip()
        else:
            bad_n += 1
            continue
        if not wrong or not correct or wrong == correct or len(wrong) > 10 or len(correct) > 10:
            bad_n += 1
            bad_items.append([wrong, correct])
            continue
        if typos.get(correct) == wrong:   # 反向冲突保护
            bad_n += 1
            bad_items.append([wrong, correct])
            continue
        typos[wrong] = correct
        disabled.discard(wrong)
        ok_n += 1
    cfg["typos"] = typos
    cfg["disabled_typos"] = sorted(disabled)
    engine.save_rules_config(cfg)
    return _envelope(True, "OK", {"ok": ok_n, "bad": bad_n, "bad_items": bad_items[:20]},
                     f"批量导入完成：成功 {ok_n} 条，跳过 {bad_n} 条")


@app.post("/api/v1/qc/rules/scan-reports")
def qc_rules_scan_reports(emp: str = Depends(require_admin)):
    """P0 历史报告词频学习：扫描样本库，自动发现候选错字对，供一键采纳。"""
    try:
        cands = engine.scan_reports_for_typos()
        return _envelope(True, "OK", {"candidates": cands}, f"发现 {len(cands)} 个候选错字")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


# ----------------------------- 静态前端托管（SPA） -----------------------------
# 单服务同时提供 REST API 与同一套 SPA 前端，桌面 WebView 壳与浏览器共用。
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os as _os

# 静态目录：冻结后用 _APP_ROOT（exe 所在目录），否则用 __file__ 回溯。
_STATIC_DIR = _os.path.join(_APP_ROOT, "web", "static") if getattr(sys, "frozen", False) \
    else _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "web", "static"))
# 静态前端每次版本更新都直接改文件；若浏览器/WebView 命中强缓存会一直加载旧版
# app.js（曾因此出现「已修复 bodypart 仍报错」的假象）。统一给前端资源加 no-cache：
# 每次仍重新校验（ETag/Last-Modified），文件变了浏览器必然拉到新版本，无需手动改 ?v=。
class _NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return resp


if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", _NoCacheStaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # SPA 路由兜底：非 API / static / 文档 的请求一律返回 index.html
        if full_path.startswith(("api/", "static/", "docs", "openapi", "redoc")):
            raise HTTPException(404, "Not Found")
        index = _os.path.join(_STATIC_DIR, "index.html")
        if _os.path.exists(index):
            return FileResponse(index)
        raise HTTPException(404, "前端未找到")


if __name__ == "__main__":
    import argparse
    import uvicorn
    # 默认只绑定本机回环，与桌面壳(127.0.0.1)一致，避免源码启动即暴露到局域网。
    # 内网多机访问请显式：python server/main.py --host 0.0.0.0
    _p = argparse.ArgumentParser(description="星衍放射质控 API 服务")
    _p.add_argument("--host", default=os.environ.get("QC_HOST", "127.0.0.1"))
    _p.add_argument("--port", type=int, default=int(os.environ.get("QC_PORT", "8000")))
    _args = _p.parse_args()
    _log("info", f"星衍放射质控服务启动 v{APP_VERSION} @ http://{_args.host}:{_args.port}")
    uvicorn.run(app, host=_args.host, port=_args.port)
