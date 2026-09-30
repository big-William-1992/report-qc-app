"""server/core.py — 星衍质控后端共享层（2026-09-09 改为动态 SessionLocal）"""
import os
import sys
import json
import hashlib
import threading
import datetime
from typing import Any

from server import db as _db


def SessionLocal():
    """会话工厂代理：始终读取 server.db 当前绑定，避免测试切库后残留旧引用。"""
    return _db.SessionLocal()



# ----------------------------- 跨平台文件权限保护 -----------------------------
def _restrict_file_access(path: str) -> None:
    """限制敏感文件（密钥/口令/激活码）仅当前用户可读写。

    POSIX: os.chmod 0o600（标准做法）。
    Windows: os.chmod 静默无效——改用 icacls 禁用继承，仅授予当前用户完全控制。
    icacls 是 Windows 内置工具（Vista+），无需额外依赖。失败静默忽略（不阻塞主流程）。
    """
    try:
        if sys.platform.startswith("win"):
            import subprocess
            user = os.environ.get("USERNAME") or os.getlogin()
            subprocess.run(
                ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(F)"],
                capture_output=True, timeout=5,
            )
        else:
            os.chmod(path, 0o600)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


# ----------------------------- 统一日志 -----------------------------
try:
    import log_utils
    log_utils.setup_logging()
    _LOG = log_utils.get_logger()
except Exception:
    _LOG = None


def _log(level: str, msg: str) -> None:
    """统一日志入口；log_utils 缺失时静默丢弃。"""
    if _LOG is not None:
        getattr(_LOG, level, lambda m: None)(msg)


# ----------------------------- 响应封装 -----------------------------
def _envelope(ok: bool, code: str, data: Any, message: str = ""):
    return {"ok": ok, "code": code, "data": data, "message": message}


# 引擎 score_summary 输出中文维度键；前端（app.js）样本列表直接读英文键。
_SCORE_EN = {"准确性": "accuracy", "完整性": "completeness",
             "规范性": "normalization", "及时性": "timeliness"}


def _eng_scores(cn: dict) -> dict:
    """把引擎中文维度键映射为前端期望的英文键；未知键透传。"""
    out = {}
    for k, v in (cn or {}).items():
        out[k] = _SCORE_EN.get(k, k)
    return out


# ----------------------------- 数据目录 / 原子写 -----------------------------
def _appdata_dir() -> str:
    """配置态数据目录（**单一来源** = paths.user_data_dir()；QC_APPDATA 可覆盖）。

    2026-09-30 修复：此处此前自带一套解析（非 Windows 落 ~/.medical_report_qc），
    与 src/paths.user_data_dir()（平台正确目录）不一致 —— deps.py 用后者，
    core/main 用前者，于是同一个 qc_queue.json / web_settings.json
    会被写在两个目录里，读写互相看不见。现统一委托 paths。
    注：**导出产物与写权限探测**用的是「数据库所在目录」（见 main._APPDATA_DIR），
    与这里是两个不同角色，故不在此合并。
    """
    try:
        import paths
        return paths.user_data_dir()
    except Exception:  # pragma: no cover - paths 随包分发
        override = os.environ.get("QC_APPDATA", "").strip()
        if override:
            base = os.path.abspath(override)
        else:
            base = os.path.join(os.path.expanduser("~"), ".medical_report_qc")
        os.makedirs(base, exist_ok=True)
        return base


_JSON_IO_LOCK = threading.Lock()


def _atomic_json_write(path: str, obj) -> None:
    """临时文件 + os.replace 原子写，配合 _JSON_IO_LOCK 防并发撕裂/覆盖。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, ensure_ascii=False, indent=2, fp=fh)
    os.replace(tmp, path)


# ----------------------------- 队列数据层（qc.db QueueItem，2026-09-09 收敛） -----------------------------
def _queue_orm_all() -> list:
    """读队列（ORM）：返回与旧 JSON 结构兼容的 dict 列表（id/hash/patient/site/text/source/ts/meta）。"""
    from server.models import QueueItem
    out = []
    with SessionLocal() as s:
        for q in s.query(QueueItem).order_by(QueueItem.id).all():
            meta = {}
            try:
                meta = json.loads(q.meta_json or "{}") or {}
            except Exception:
                meta = {}
            out.append({
                "id": str(q.id),
                "hash": q.report_hash or hashlib.md5("".join((q.report_text or "").split()).encode("utf-8", "ignore")).hexdigest(),
                "patient": meta.get("patient", ""),
                "site": meta.get("applied_site", ""),
                "text": q.report_text or "",
                "source": meta.get("source", "手动"),
                "ts": (q.created_at or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M"),
                "meta": meta,
            })
    return out


def _queue_orm_add(report_text: str, meta: dict) -> int:
    """入队（ORM），返回新条目 id。"""
    from server.models import QueueItem
    with SessionLocal() as s:
        item = QueueItem(report_text=report_text, meta_json=json.dumps(meta or {}, ensure_ascii=False),
                         status="pending")
        s.add(item)
        s.commit()
        return item.id


# 入队去重锁（2026-08-18）：report_hash 唯一索引 + 进程内锁，把
# 「算 hash → 查重 → 插入」做成原子，杜绝并发重复入队（此前是跨会话非原子读改写）。
_QUEUE_ADD_LOCK = threading.Lock()


def _queue_orm_add_dedup(report_text: str, meta: dict):
    """原子入队（含去重）：返回 (id, duplicated)。同内容报告已在队列时返回现有条目。

    report_hash 按 _queue_orm_all 同口径计算（去空格 MD5）；数据库唯一索引
    ux_queue_hash 兜底并发冲突（IntegrityError 时回退查现有条目）。
    """
    norm = "".join((report_text or "").split())
    if not norm:
        return (None, False)
    h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
    from server.models import QueueItem
    with _QUEUE_ADD_LOCK:
        with SessionLocal() as s:
            exist = s.query(QueueItem).filter(QueueItem.report_hash == h).first()
            if exist:
                return (exist.id, True)
            item = QueueItem(report_text=report_text,
                             meta_json=json.dumps(meta or {}, ensure_ascii=False),
                             status="pending", report_hash=h)
            try:
                s.add(item)
                s.commit()
                return (item.id, False)
            except Exception:
                s.rollback()
                exist = s.query(QueueItem).filter(QueueItem.report_hash == h).first()
                return (exist.id if exist else None, True)


def _queue_orm_remove(qid: int) -> bool:
    from server.models import QueueItem
    with SessionLocal() as s:
        q = s.query(QueueItem).filter(QueueItem.id == qid).first()
        if q is None:
            return False
        s.delete(q)
        s.commit()
        return True


def _queue_orm_clear() -> None:
    from server.models import QueueItem
    with SessionLocal() as s:
        s.query(QueueItem).delete()
        s.commit()


def _load_queue() -> list:
    """兼容旧接口名：RIS 轮询去重等仍按 dict 列表消费。"""
    return _queue_orm_all()


def queue_add_text(text: str, meta: dict, source: str = "RIS轮询",
                   emp_default: str = "ris-poll"):
    """入队（ORM + 正文 MD5 去重）——**唯一实现**，main.py 与 deps.py 都转调这里。

    2026-09-30 修复（功能缺陷）：server/deps.py 里曾另有一份 _queue_add_text，
    把队列写进 `qc_queue.json`；而队列端点 `GET /api/v1/queue` 读的是 qc.db 的
    QueueItem 表（core._queue_orm_all）→ **route_push（PACS 推送）入队的报告
    在界面队列里永远看不到**，RIS 轮询那份当时已改成 ORM，只有推送这条路径踩坑。

    emp_default：入队项的归属标记，写入 meta["_emp"]。队列列表对非 admin 只显示
    `meta._emp in (本人, "ris-poll")` 的条目，因此自动入队（RIS 轮询 / PACS 推送）
    统一标 "ris-poll" 作为公共复核队列，否则医生看不到。
    返回条目 id（str）或 None（正文为空）。
    """
    m = dict(meta or {})
    m.setdefault("source", source)
    if emp_default:
        m.setdefault("_emp", emp_default)
    _id, _dup = _queue_orm_add_dedup(text, m)
    return str(_id) if _id else None


def _scope_user_id(emp: str) -> "str | None":
    """多用户数据隔离（2026-08-18）：admin/本机返回 None（看全部样本与统计）；
    普通医生返回本人工号，样本读取/导出/统计仅限本人数据。

    2026-09-30 从 main.py 下沉到共享层：它是**跨域助手**（queue / stats / samples
    等多处使用），留在 main.py 会导致拆分路由时只能互相 import main（循环依赖）。
    """
    if not emp or emp == "local":
        return None
    try:
        import accounts
        if accounts.get_role(emp) == "admin":
            return None
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet("server.core._scope_user_id")
    return emp


def _migrate_queue_to_db() -> None:
    """旧 qc_queue.json → QueueItem 表（一次性，2026-08-18 收敛）；完成后改名 .bak。"""
    from server.models import QueueItem
    qpath = os.path.join(_appdata_dir(), "qc_queue.json")
    if not os.path.exists(qpath):
        return
    try:
        with open(qpath, encoding="utf-8") as fh:
            items = json.load(fh) or []
    except Exception:
        items = []
    if items:
        with SessionLocal() as s:
            if s.query(QueueItem).count() == 0:
                for it in items:
                    meta = dict(it.get("meta") or {})
                    meta.setdefault("patient", it.get("patient", ""))
                    meta.setdefault("applied_site", it.get("site", ""))
                    meta.setdefault("source", it.get("source", ""))
                    meta.setdefault("ts", it.get("ts", ""))
                    meta.setdefault("_emp", "ris-poll")
                    s.add(QueueItem(report_text=it.get("text", ""),
                                    meta_json=json.dumps(meta, ensure_ascii=False),
                                    status="pending"))
                s.commit()
    try:
        os.rename(qpath, qpath + ".bak")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


# ----------------------------- 设置数据层（qc.db Setting，2026-08-18 收敛） -----------------------------
def _settings_orm_all() -> dict:
    """读全局设置（Setting 表 user_id IS NULL）。"""
    from server.models import Setting
    data = {}
    with SessionLocal() as s:
        for row in s.query(Setting).filter(Setting.user_id.is_(None)).all():
            try:
                data[row.key] = json.loads(row.value_json or "null")
            except Exception:
                data[row.key] = None
    return data


def _settings_orm_save(data: dict) -> None:
    from server.models import Setting
    with SessionLocal() as s:
        for k, v in data.items():
            row = s.query(Setting).filter(Setting.key == k, Setting.user_id.is_(None)).first()
            val = json.dumps(v, ensure_ascii=False)
            if row:
                row.value_json = val
            else:
                s.add(Setting(key=k, value_json=val, user_id=None))
        s.commit()


def _migrate_settings_to_db() -> None:
    """旧 web_settings.json → Setting 表（user_id 为空），2026-08-18 收敛。"""
    from server.models import Setting
    spath = os.path.join(_appdata_dir(), "web_settings.json")
    if not os.path.exists(spath):
        return
    try:
        with open(spath, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception:
        data = {}
    if data:
        with SessionLocal() as s:
            for k, v in data.items():
                row = s.query(Setting).filter(Setting.key == k, Setting.user_id.is_(None)).first()
                if row is None:
                    s.add(Setting(key=k, value_json=json.dumps(v, ensure_ascii=False),
                                  user_id=None))
            s.commit()
    try:
        os.rename(spath, spath + ".bak")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
