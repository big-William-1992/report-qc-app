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
import re
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any

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
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.schemas import *  # 请求/响应模型（2026-08-21 T56 收敛：内联模型已抽离到 schemas.py）

def _audit_log(*args):
    """审计/运维输出统一走滚动日志 (2026-08-25 可观测性改造)。"""
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

import engine
import accounts
import samplelib
import ocr_provider  # noqa: F401 (副作用导入: 确保 PyInstaller 收集 OCR 引擎)
from version import APP_VERSION
from server import db  # SQLAlchemy 统一数据层（users/departments/queue/settings）
def SessionLocal():
    """会话工厂代理：转发到当前 server.db 绑定，避免测试切库后 main 残留旧引用。"""
    return db.SessionLocal()
from server.core import (  # 共享层（2026-08-18 拆分）：日志/响应封装/数据目录/队列与设置数据层
    _log, _envelope, _eng_scores, _appdata_dir, _queue_orm_all, _queue_orm_add,  # noqa: F401 (re-export)
    _queue_orm_clear, _load_queue,  # noqa: F401 (部分符号 re-export 给旧引用)
    queue_add_text,  # 入队的唯一实现（main/deps 共用，2026-09-30）
    _migrate_queue_to_db, _settings_orm_all, _settings_orm_save, _migrate_settings_to_db,
)
from server.security import (  # noqa: F401 (re-export 给旧引用)
    QC_API_SECRET,
    SECRET,
    TOKEN_TTL,
    _is_network_host,
    _require_secret_for_network_host,
    _emp_from_auth,
    make_token,
    verify_token,
    require_emp,
    require_emp_local,
    require_admin,
    require_license_active,
)
# 质控运行时（引擎单例/限流/_run_qc）已于 2026-09-30 迁至 server/qc_runtime.py；
# 此处再导出：main.py 内仍有其它域（如 /api/v1/samples 入库即质控）引用 _run_qc。
from server.qc_runtime import _get_engine, _qc_rate_ok, _reload_engine_rules, _run_qc  # noqa: F401,E402

from server.deps import log_audit, _login_locked, _record_login_failure, _clear_login_failures  # 审计日志 + 登录限流

_require_secret_for_network_host(os.environ.get("QC_HOST", "127.0.0.1"))


# _scope_user_id 已于 2026-09-30 下沉至 server/core.py（跨域助手，供 route_queue/route_stats
# 与 samples/stats 共用）；此处从 core 再导出，保持 main.py 内既有引用不变。
from server.core import _scope_user_id  # noqa: E402  (re-export)


# require_license_active 已于 2026-09-30 下沉至 server/security.py（路由拆分前置改造）。
# 这里保留名字（下方 security 导入处再导出），使 main.py 内既有引用与外部 import 不变。

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
















# ----------------------------- 请求模型（Phase1 补充） -----------------------------






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


# 引擎单例 / 规则刷新 / _run_qc / IP 限流（2026-09-30 迁出）→ server/qc_runtime.py。
# 迁移原因：这些是跨端点共享的**进程级状态**，必须只有一份；随 qc 端点拆到 routes/ 后
# 由该模块持有，route_qc.py 从那里导入。



# 质控计算与规则/错字表端点（/api/v1/qc/*）已于 2026-09-30 拆分至
# server/routes/route_qc.py；共享运行时（引擎单例/限流/_run_qc）见 server/qc_runtime.py。

@app.post("/api/v1/feedback")
def submit_feedback(req: FeedbackReq,
                    emp: str = Depends(require_emp_local),
                    _lic: bool = Depends(require_license_active)):
    """医生反馈回流: 误报👎/漏报➕ → feedback.db (P1-4 badcase 闭环入口)。
    存储失败不影响质控主流程(降级返回 ok=False 而非 500)。"""
    try:
        from badcase_store import record
        data = req.model_dump()
        data["user_id"] = emp
        fid = record(data)
        return _envelope(True, "已记录，感谢反馈", {"id": fid})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        from log_utils import log_quiet; log_quiet(__name__)
        return _envelope(False, "反馈暂存失败（不影响质控结果）", {})


@app.get("/api/v1/feedback/stats")
def feedback_stats(emp: str = Depends(require_emp_local)):
    """反馈计数(驾驶舱展示用)。"""
    try:
        from badcase_store import stats
        return _envelope(True, "OK", stats())
    except Exception:
        from log_utils import log_quiet; log_quiet(__name__)
        return _envelope(True, "OK", {"total": 0, "by_type": {}, "last_7d": 0})


@app.get("/api/v1/feedback/export")
def feedback_export(limit: int = 1000,
                    emp: str = Depends(require_emp_local)):
    """导出最近反馈(JSONL), 供 tools/export_badcase_training.py 精调管线消费。"""
    from badcase_store import list_recent
    import json as _json
    rows = list_recent(limit=limit)
    body = "\n".join(_json.dumps(r, ensure_ascii=False) for r in rows)
    return JSONResponse(content=body or "", media_type="application/x-ndjson")


# 质控计算与规则/错字表端点（/api/v1/qc/*）已于 2026-09-30 拆分至
# server/routes/route_qc.py；共享运行时（引擎单例/限流/_run_qc）见 server/qc_runtime.py。

# ----------------------------- RIS 主动轮询质检（P0：发现即质控闭环） -----------------------------
# 后台守护线程按 interval_min 周期性拉取 RIS 新报告 → 自动质控 → 结果入库样本库 + 进待质控队列。
# 配置与「已处理去重指纹」持久化在 appdata/ris_poll.json，重启不丢失、不重复处理。
# RIS/PACS 端点（/api/v1/ris/*）已于 2026-09-30 拆分至 server/routes/route_ris.py，
# 并经 app.include_router 注册（见文件末尾）。轮询状态见 server/ris_runtime.py。

def datetime_now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


def _queue_add_text(text: str, meta: dict, source: str = "RIS轮询"):
    """入队（转调 core.queue_add_text —— 唯一实现）。返回条目 id 或 None。"""
    return queue_add_text(text, meta, source=source)


# RIS 轮询运行时（配置/互斥锁/轮询引擎/守护线程）已于 2026-09-30 迁至
# server/ris_runtime.py；main.py 在文件末尾调用 start_poll_thread() 启动它。

# 模块级建表 + 存量数据迁移（import 即就绪，不依赖 __main__ 入口：
# TestClient / uvicorn / 桌面壳 import server.main 均需要表已存在，2026-08-18 修复）。
#   - db.init_db()            ：users/departments/queue/settings/samples 建表（幂等）
#   - migrate_legacy_samples  ：assets/samples.db → qc.db.samples（旧库归档 .bak）
#   - _migrate_queue_to_db    ：qc_queue.json → qc.db.queue
#   - _migrate_settings_to_db ：web_settings.json → qc.db.settings
# 2026-08-24：APPDATA 不可写检测（企业漫游配置/权限受限）——提前告警而非静默丢数据
_APPDATA_DIR = os.path.dirname(samplelib.db_path())
try:
    os.makedirs(_APPDATA_DIR, exist_ok=True)
    _test_fp = os.path.join(_APPDATA_DIR, ".write_test")
    with open(_test_fp, "w") as _tw:
        _tw.write("ok")
    os.remove(_test_fp)
except Exception as _w_e:
    try:
        _log("warning",
             f"数据目录不可写（{_APPDATA_DIR}）：{_w_e}。"
             "质控数据/配置/账号将无法持久化，每次重启丢失。"
             "请检查目录权限或设置 QC_DB_OVERRIDE 环境变量。")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
db.init_db()
# ── 自动备份调度器（P0 改造，2026-09-12）─────────────────────────────
try:
    import backup as _bk
    _bk.start_scheduler()
except Exception:
    try:
        from .log_utils import log_quiet
    except ImportError:
        from log_utils import log_quiet
    log_quiet(__name__)
try:
    samplelib.migrate_legacy_samples()
    samplelib.rescue_samples_conv()   # 抢救 samples_conv_old 滞留历史样本（2026-08-18 P0）
    # 医生反馈库（独立 feedback.db，2026-08-25 P1-4）。
    # 2026-09-30 修复：此前启动流程从未调用 badcase_store.init_db()，导致
    # /api/v1/feedback 必然 "no such table: feedback" 并被吞成「暂存失败」。
    # badcase_store 现已改为用前自愈，这里再显式建一次，保证启动即可用。
    import badcase_store as _bcs
    _bcs.init_db()
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
                try:
                    from .log_utils import log_quiet
                except ImportError:
                    from log_utils import log_quiet
                log_quiet(__name__)
except Exception as _e:
    # 2026-08-24：APPDATA 不可写时所有 DB 写入静默失败，用户每次重启数据丢失
    # 却无任何提示——此处补日志 + 写入失败标记，供前端 health 接口透出。
    try:
        _log("warning", f"数据迁移/清理阶段异常（数据可能不完整）: {type(_e).__name__}: {_e}")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    try:
        _flag = os.path.join(os.path.dirname(samplelib.db_path()), ".init_warning")
        with open(_flag, "w", encoding="utf-8") as _fw:
            _fw.write(f"{datetime_now_iso()} migration error: {type(_e).__name__}: {_e}\n")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

# RIS 轮询守护线程：运行时在 server/ris_runtime.py，这里只负责启动
# （原先是模块级 Thread 直接 start；迁出后由该模块封装，行为一致：import 即启动）
try:
    from server.ris_runtime import start_poll_thread as _start_ris_poll
    _start_ris_poll()
except Exception:
    try:
        from .log_utils import log_quiet
    except ImportError:
        from log_utils import log_quiet
    log_quiet("server.main.start_ris_poll")


# ----------------------------- 账号（责任到人） -----------------------------
@app.post("/api/v1/accounts")
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


@app.post("/api/v1/accounts/login")
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




@app.post("/api/v1/accounts/{emp_id}/role")
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




@app.post("/api/v1/accounts/{emp_id}/password")
def account_reset_password(request: Request, emp_id: str, req: PwdReq,
                           admin: str = Depends(require_admin)):
    _ip = request.client.host if request.client else ""
    if len(req.password or "") < 6:
        return _envelope(False, "ERR", {}, "密码至少 6 位")
    if not accounts.reset_password(emp_id, req.password):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "password_reset", {"target": emp_id}, _ip)
    return _envelope(True, "OK", {}, "密码已重置")




@app.post("/api/v1/accounts/{emp_id}/dept")
def account_set_dept(request: Request, emp_id: str, req: DeptReq, admin: str = Depends(require_admin)):
    _ip = request.client.host if request.client else ""
    if not accounts.set_dept(emp_id, req.dept_id):
        return _envelope(False, "ERR", {}, "账号不存在")
    log_audit(admin, "dept_changed", {"target": emp_id, "dept_id": req.dept_id}, _ip)
    return _envelope(True, "OK", {}, "科室已更新")


@app.get("/api/v1/departments")
def department_list(admin: str = Depends(require_admin)):
    return _envelope(True, "OK", accounts.list_departments())




@app.post("/api/v1/departments")
def department_create(request: Request, req: DeptCreateReq, admin: str = Depends(require_admin)):
    _ip = request.client.host if request.client else ""
    ok, msg = accounts.create_department(req.name)
    if not ok:
        return _envelope(False, "ERR", {}, str(msg))
    log_audit(admin, "department_created", {"name": req.name}, _ip)
    return _envelope(True, "OK", {}, "科室已创建")


# ----------------------------- 授权（免责声明 / 试用期 / 激活码） -----------------------------
# 以下端点均为公开（无需登录），因为登录/激活本身就是闸门流程的一部分。


# 授权/试用/免责端点（/api/v1/license/*）已于 2026-09-30 拆分至
# server/routes/route_license.py，并经 app.include_router 注册（见文件末尾）。

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
                explain=list(f.get("explain", []) or []),
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






# 质控计算与规则/错字表端点（/api/v1/qc/*）已于 2026-09-30 拆分至
# server/routes/route_qc.py；共享运行时（引擎单例/限流/_run_qc）见 server/qc_runtime.py。

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
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)

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
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)


# 统计端点（/api/v1/stats/*）已于 2026-09-30 拆分至 server/routes/route_stats.py，
# 并经 app.include_router 注册（见文件末尾）。

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


@app.get("/api/v1/version")
def version_info():
    """返回当前软件版本及最近发布说明。"""
    return _envelope(True, "OK", {"version": APP_VERSION, "name": "星衍放射质控软件"})


@app.get("/api/v1/health")
def health():
    """增强健康检查（P1 改造，2026-09-12）：DB 连通性 / 磁盘空间 / 活跃会话 / 授权状态。"""
    data: Dict[str, Any] = {"status": "up", "version": APP_VERSION}

    # DB 连通性检测
    # 2026-09-30 修复：此前写的是 `with _db_mod.get_db() as _sess:` —— 但 get_db()
    # 是 FastAPI 的**生成器依赖**（yield 会话），不是上下文管理器，直接抛
    # TypeError。结果是 /api/v1/health 永远返回 db="error: TypeError"：
    # 探活永远"绿"、真正的连库故障永远查不出来（CI 的 launch-test 正是探这个接口）。
    # 现改为显式取 SessionLocal 并关掉，同时探一次 samples 表（能发现"库在但表缺失"）。
    try:
        from server import db as _db_mod
        from sqlalchemy import text as _text
        _sess = _db_mod.SessionLocal()
        try:
            _sess.execute(_text("SELECT 1"))
            _sess.execute(_text("SELECT COUNT(*) FROM samples"))
        finally:
            _sess.close()
        data["db"] = "connected"
    except Exception as _db_exc:
        data["db"] = f"error: {type(_db_exc).__name__}"

    # 磁盘空间
    try:
        import shutil as _shutil
        _db_root = os.path.dirname(samplelib.db_path())
        _usage = _shutil.disk_usage(_db_root)
        _total_gb = round(_usage.total / 1e9, 2)
        _free_gb = round(_usage.free / 1e9, 2)
        data["disk"] = {
            "total_gb": _total_gb, "free_gb": _free_gb,
            "used_pct": round((_usage.total - _usage.free) / _usage.total * 100, 1),
        }
    except Exception:
        data["disk"] = "unknown"

    # 活跃会话（粗略：当前已登录 token 计数）
    try:
        data["active_sessions"] = len(_LOGIN_FAIL)  # 用限流表计数作代理
    except Exception:
        data["active_sessions"] = "unknown"

    # 授权状态
    try:
        import license_utils as _lu
        _lic_status, _lic_data = _lu.check_trial()
        data["license"] = {"status": _lic_status, "remaining_days": _lic_data or 0}
    except Exception:
        data["license"] = "unknown"

    # 初始化告警（迁移失败等）
    try:
        _warn_flag = os.path.join(os.path.dirname(samplelib.db_path()), ".init_warning")
        if os.path.isfile(_warn_flag):
            with open(_warn_flag, "r", encoding="utf-8") as _wf:
                data["init_warning"] = _wf.read().strip()[:200]
    except Exception:
        pass

    return _envelope(True, "OK", data)


@app.get("/api/v1/update/check")
def update_check(emp: str = Depends(require_emp_local)):
    """检查更新（支持在线和离线模式）。

    在线模式：查询 GitHub Releases。
    离线模式（QC_UPDATE_LOCAL_DIR）：读取本地更新包信息。
    返回 {status, message, url, published_at, source}。
    """
    import auto_updater as _au
    if _au.is_offline_update_mode():
        result = _au.check_for_update()
        return _envelope(True, "OK", {
            "status": "update" if result.get("available") else "latest",
            "message": f"离线更新模式 | {result.get('version', '')}",
            "url": "",
            "published_at": "",
            "source": "offline",
        })
    import update_check as _uc
    result = _uc.check_update_sync(timeout=5)
    return _envelope(True, "OK", {
        "status": result.get("status", "error"),
        "message": result.get("message", ""),
        "url": result.get("url", ""),
        "published_at": result.get("published_at", ""),
        "source": "online",
    })


# ============================================================================
# 以下为「SPA 功能追平桌面版」新增能力（P0/P1）
#   1) 待质控队列   —— 与 Tkinter 版共用 ~/.medical_report_qc/qc_queue.json
#   2) 屏幕采集 OCR —— 真·框选 PACS 屏幕三区（基础信息/影像描述/影像诊断）
#   3) 应用设置     —— SPA 侧设置面板的持久化
#   4) 规则配置读取 —— 供规则维护页编辑 R8 错别字 / R9 矛盾对 / 忽略词
# 注意：必须注册在文件末尾的 SPA catch-all 路由「之前」，否则 GET 会被兜底吞掉。
# ============================================================================




# 队列端点（GET/POST/DELETE /api/v1/queue[/{qid}]）已于 2026-09-30 拆分至
# server/routes/route_queue.py，并经 app.include_router 注册（见文件末尾）。
# 注意：拆分**必须**删掉这里的原实现，否则同一路径两份处理器，后者永不生效
# （tests/test_single_implementation.py 会拦截重复注册）。

# 屏幕采集/框选 OCR（/api/v1/screen/*）、文本抽取（/api/v1/ocr/meta）与截图/识别共享状态
# 已于 2026-09-30 拆分：端点 → server/routes/route_screen.py 与 route_ocr.py；
# 共享状态（推理锁 OCR_LOCK / 截图缓存 SHOT / 识别缓存 OCR_CACHE / 上传大小上限）→
# server/ocr_runtime.py（screen 与 ocr **必须共用同一份**，否则锁不互斥、缓存命中率归零）。

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
# 质控计算与规则/错字表端点（/api/v1/qc/*）已于 2026-09-30 拆分至
# server/routes/route_qc.py；共享运行时（引擎单例/限流/_run_qc）见 server/qc_runtime.py。



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


# ── 路由模块注册（2026-09-09）──────────────────────────────────────────
# 2026-09-30 清理：server/routes/ 下原有 10 个「未注册的重复路由模块」
# （account/feedback/license/ocr/qc/queue/ris/sample/screen/settings）已删除——
# 它们从未被 include_router（见 git 历史 8f5b274），且内容比 main.py 陈旧
# （例如 route_qc 的规则保存缺少 _reload_engine_rules()），属于「两套实现」隐患。
# 现仅保留真正注册的 route_push。若日后要继续拆分路由，请一次拆完并删除
# main.py 中的同名端点，否则 tests/test_single_implementation.py 会失败。
from server.routes.route_push import router as _router_push
from server.routes.route_queue import router as _router_queue
from server.routes.route_stats import router as _router_stats
from server.routes.route_license import router as _router_license
from server.routes.route_screen import router as _router_screen
from server.routes.route_ocr import router as _router_ocr
from server.routes.route_ris import router as _router_ris
from server.routes.route_qc import router as _router_qc
app.include_router(_router_push)
app.include_router(_router_queue)
app.include_router(_router_stats)
app.include_router(_router_license)
app.include_router(_router_screen)
app.include_router(_router_ocr)
app.include_router(_router_ris)
app.include_router(_router_qc)

# ── 审计日志查询（2026-09-09 新增）────────────────────────────────────
@app.get("/api/v1/admin/audit-logs")
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


@app.get("/api/v1/admin/audit-logs/export")
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
@app.get("/api/v1/admin/license/status")
def license_status(admin: str = Depends(require_admin)):
    """查看授权状态：单机/浮动、座位数、试用期、剩余天数等。"""
    import license_utils as _lu
    return _envelope(True, "OK", _lu.get_license_info())


@app.post("/api/v1/admin/license/deactivate")
def license_deactivate(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """吊销授权。单机模式清除本机激活；浮动模式移除指定机器心跳。"""
    import license_utils as _lu
    machine_id_str = (req.get("machine_id") or "").strip()
    result = _lu.deactivate(machine_id_str)
    if not result.get("ok"):
        raise HTTPException(500, result.get("message", "吊销失败"))
    return _envelope(True, "OK", result, "吊销成功")


@app.post("/api/v1/admin/license/extend")
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
@app.get("/api/v1/admin/orders")
def orders_list(status: str = "", limit: int = Query(100, ge=1, le=500),
                admin: str = Depends(require_admin)):
    """查询订单列表。"""
    from sqlalchemy import desc
    from server.models import Order
    db_s = db.get_session()
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


@app.post("/api/v1/admin/orders/create")
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
    db_s = db.get_session()
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


@app.post("/api/v1/admin/orders/confirm")
def orders_confirm(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """确认收款（手动核销）。"""
    from datetime import datetime
    from server.models import Order
    order_no = (req.get("order_no") or "").strip()
    if not order_no:
        raise HTTPException(400, "缺少订单号")
    db_s = db.get_session()
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


@app.post("/api/v1/admin/orders/cancel")
def orders_cancel(req: Dict[str, Any], admin: str = Depends(require_admin)):
    """取消订单或退款。"""
    from server.models import Order
    order_no = (req.get("order_no") or "").strip()
    if not order_no:
        raise HTTPException(400, "缺少订单号")
    new_status = (req.get("new_status") or "cancelled").strip()
    if new_status not in ("cancelled", "refunded"):
        raise HTTPException(400, "状态必须是 cancelled 或 refunded")
    db_s = db.get_session()
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


@app.get("/api/v1/admin/orders/export")
def orders_export(format: str = "csv", admin: str = Depends(require_admin)):
    """导出订单记录为 CSV。"""
    import csv, io
    from datetime import datetime
    from sqlalchemy import desc
    from server.models import Order
    from fastapi.responses import Response
    db_s = db.get_session()
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
@app.post("/api/v1/user-feedback")
def submit_user_feedback(req: Dict[str, Any], emp: str = Depends(require_emp_local)):
    """用户提交反馈/建议（写入本地日志，供开发者查看）。"""
    message = (req.get("message") or "").strip()
    category = (req.get("category") or "general").strip()
    contact = (req.get("contact") or "").strip()
    if not message:
        raise HTTPException(400, "反馈内容不能为空")
    # 写入本地信息日志（用户反馈）
    # 2026-09-30：字段名由 message_full 改为 feedback_text（在 error_reporter 的
    # 白名单内），长度收到 500 —— 用户可能在此粘贴报告正文，写入前会经统一擦洗；
    # 该文件仅存本机，且诊断包默认不再收纳 errors/ 目录（见 log_utils）。
    try:
        import error_reporter as _er
        _er.report_info(
            f"[反馈] {category}",
            where="feedback.submit",
            emp_id=emp, category=category, contact=contact,
            feedback_text=message[:500])
    except Exception:
        pass
    return _envelope(True, "OK", {"received": True}, "反馈已收到，谢谢！")


# ── 版本变更日志 API（2026-09-12 商业化）──────────────────────────────────
@app.get("/api/v1/changelog")
def changelog_get(emp: str = Depends(require_emp_local)):
    """获取当前版本的变更日志。"""
    from pathlib import Path
    _base = Path(__file__).resolve().parent.parent
    cl_path = _base / "CHANGELOG.md"
    if not cl_path.exists():
        return _envelope(True, "OK", {"version": APP_VERSION, "entries": []})
    text = cl_path.read_text(encoding="utf-8")
    # 找到当前版本的条目
    current = APP_VERSION
    entries = []
    in_current = False
    for line in text.splitlines():
        if line.strip().startswith(f"## v{current}"):
            in_current = True
            entries.append(line.strip())
            continue
        if in_current:
            if line.strip().startswith("## ") and not line.strip().startswith(f"## v{current}"):
                break
            if line.strip():
                entries.append(line.strip())
    return _envelope(True, "OK", {"version": current, "entries": entries})


# ── 错误报告管理 API（2026-09-12 商业化）──────────────────────────────────
@app.get("/api/v1/admin/errors")
def errors_list(limit: int = Query(50, ge=1, le=200),
                admin: str = Depends(require_admin)):
    """查看最近的错误报告。"""
    import error_reporter as _er
    return _envelope(True, "OK", {"reports": _er.get_recent_reports(limit), "stats": _er.get_stats()})


@app.get("/api/v1/admin/errors/export")
def errors_export(admin: str = Depends(require_admin)):
    """导出错误报告为 JSON。"""
    import error_reporter as _er
    return _envelope(True, "OK", _er.get_recent_reports(limit=500))


# ── 数据导出 API（2026-09-12 商业化：数据可移植性）──────────────────────────
@app.get("/api/v1/export/data")
def data_export(emp: str = Depends(require_emp_local)):
    """导出全量数据（样本 + 队列 + 设置），供用户迁移或备份。"""
    from fastapi.responses import Response
    from datetime import datetime          # 本文件惯例：函数内局部导入（见 sample_dashboard 等）
    from server.models import Sample, QueueItem, Setting
    # 2026-09-30 修复（该端点此前 100% 不可用，且无测试覆盖）：
    #   ① `db.get_session()` 在 server/db.py 中**不存在**（正确入口是 SessionLocal，
    #      db 只提供 get_db 生成器依赖）→ 调用即 AttributeError；
    #   ② 下方 `_json.dumps` 的 `_json` 只在另一个函数的函数体内被局部导入 →
    #      NameError。此处统一改为模块级 json 与统一会话入口。
    db_s = SessionLocal()
    try:
        samples = db_s.query(Sample).all()
        queues = db_s.query(QueueItem).all()
        settings = db_s.query(Setting).all()
    finally:
        db_s.close()
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "version": APP_VERSION,
        "samples": [
            {"ts": s.ts, "patient": s.patient, "gender": s.gender, "age": s.age,
             "modality": s.modality, "applied_site": s.applied_site,
             "laterality": s.laterality, "user_id": s.user_id, "dept_id": s.dept_id,
             "report_text": s.report_text, "findings_json": s.findings_json,
             "scores_json": s.scores_json,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in samples
        ],
        "queue": [
            {"report_text": q.report_text, "meta_json": q.meta_json,
             "status": q.status, "user_id": q.user_id,
             "created_at": q.created_at.isoformat() if q.created_at else None}
            for q in queues
        ],
        "settings": [
            {"key": s.key, "value_json": s.value_json, "user_id": s.user_id}
            for s in settings
        ],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=data_export_{datetime.now().strftime('%Y%m%d')}.json"})


# ── 备份管理 API（P0 改造，2026-09-12）──────────────────────────────────
@app.get("/api/v1/admin/backup/status")
def backup_status(admin: str = Depends(require_admin)):
    """查看备份状态：最近备份时间、保留策略、备份文件列表。"""
    import backup as _bk
    return _envelope(True, "OK", _bk.get_backup_status())


@app.post("/api/v1/admin/backup/run")
def backup_run_now(admin: str = Depends(require_admin)):
    """手动触发一次备份。"""
    import backup as _bk
    result = _bk.run_backup()
    return _envelope(True, "OK", result, "备份完成")


@app.post("/api/v1/admin/backup/restore")
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


if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", _NoCacheStaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # 1) 静态资源：相对路径文件存在则直接返回（兼容 file:// 与 http:// 双模式）
        #    例如 /css/style.css → web/static/css/style.css
        if not full_path.startswith(("api/", "docs", "openapi", "redoc")):
            candidate = _os.path.normpath(_os.path.join(_STATIC_DIR, full_path))
            if candidate.startswith(_STATIC_DIR) and _os.path.isfile(candidate):
                return FileResponse(candidate, headers={
                    "Cache-Control": "no-cache, max-age=0, must-revalidate"
                })
        # 2) 显式拒绝旧绝对路径 /static/ 和 API 文档路径
        if full_path.startswith(("api/", "static/", "docs", "openapi", "redoc")):
            raise HTTPException(404, "Not Found")
        # 3) SPA 路由兜底：返回 index.html
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
    _require_secret_for_network_host(_args.host)
    _log("info", f"星衍放射质控服务启动 v{APP_VERSION} @ http://{_args.host}:{_args.port}")
    uvicorn.run(app, host=_args.host, port=_args.port)
