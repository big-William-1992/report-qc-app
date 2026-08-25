"""server/core.py — 星衍质控后端共享层（2026-08-18 从 main.py 拆分）

承载与具体路由无关的通用能力：统一日志、响应封装、评分键翻译、
跨平台数据目录、JSON 原子写、队列/设置的数据层（ORM，qc.db 收敛）。
路由模块（server/main.py 等）通过 `from server.core import ...` 复用。
"""
import os
import sys
import json
import hashlib
import threading
import datetime
from typing import Any

from server.db import SessionLocal


# ----------------------------- 跨平台文件权限保护 -----------------------------
def _restrict_file_access(path: str) -> None:
    """限制敏感文件（密钥/口令/激活码）仅当前用户可读写。

    POSIX: os.chmod 0o600（标准做法）。
    Windows: os.chmod 静默无效——改用 icacls 禁用继承、仅授予当前用户完全控制。
    icacls 是 Windows 内置命令（Vista+），无需额外依赖。失败静默忽略（不阻塞主流程）。
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
        out[_SCORE_EN.get(k, k)] = v
    return out


# ----------------------------- 数据目录 / 原子写 -----------------------------
def _appdata_dir() -> str:
    """跨平台数据目录（QC_APPDATA 可覆盖，E2E 测试隔离用）。"""
    override = os.environ.get("QC_APPDATA", "").strip()
    if override:
        base = os.path.abspath(override)
    else:
        ap = os.path.expandvars("%APPDATA%")
        if ap and os.path.isabs(ap):
            base = os.path.join(ap, "MedicalReportQC")
        else:
            base = os.path.join(os.path.expanduser("~"), ".medical_report_qc")
    os.makedirs(base, exist_ok=True)
    return base


_JSON_IO_LOCK = threading.Lock()


def _atomic_json_write(path: str, obj) -> None:
    """临时文件 + os.replace 原子写，配合 _JSON_IO_LOCK 防并发撕裂/覆盖。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ----------------------------- 队列数据层（qc.db QueueItem，2026-08-18 收敛） -----------------------------
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
                # 2026-08-18：优先用 DB 列 report_hash，避免列表页对每条现算 MD5
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
                # 唯一索引冲突（并发窗口）：回退查现有条目
                s.rollback()
                exist = s.query(QueueItem).filter(QueueItem.report_hash == h).first()
                return (exist.id if exist else None, True)


def _queue_orm_remove(qid: int) -> bool:
    from server.models import QueueItem
    with SessionLocal() as s:
        q = s.query(QueueItem).filter(QueueItem.id == qid).first()
        if not q:
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
                    meta.setdefault("source", it.get("source", "手动"))
                    meta.setdefault("ts", it.get("ts", ""))
                    # 2026-08-18：历史条目补归属（ris-poll）——否则非 admin
                    # 的 queue_list 归属过滤对迁移条目全部不可见
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
    """旧 web_settings.json → Setting 表（一次性，2026-08-18 收敛）；完成后改名 .bak。"""
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
            if s.query(Setting).filter(Setting.user_id.is_(None)).count() == 0:
                for k, v in data.items():
                    s.add(Setting(key=k, value_json=json.dumps(v, ensure_ascii=False), user_id=None))
                s.commit()
    try:
        os.rename(spath, spath + ".bak")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
