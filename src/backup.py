"""
backup.py — 数据库自动备份模块（P0 改造，2026-09-12）

功能：
- SQLite VACUUM INTO 在线备份（不锁库，不中断服务）
- 定时调度：默认每日一次，可通过 env 配置
- 保留策略：7/30/90 天三级轮转（可配）
- 备份对象：所有数据库 + 配置文件（license.dat, rules_config.json, ris_config.json）
- 导出/导入 API 端点

环境变量：
  QC_BACKUP_ENABLED        — 是否启用自动备份（默认 true）
  QC_BACKUP_INTERVAL_DAYS  — 备份间隔天数（默认 1）
  QC_BACKUP_KEEP_DAYS      — 保留天数列表，逗号分隔（默认 7,30,90）
  QC_BACKUP_DIR            — 备份目录（默认 assets/backups/）
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Optional

APP_NAME = "星衍放射质控软件"

# ─── 配置 ──────────────────────────────────────────────

def _enabled() -> bool:
    return os.environ.get("QC_BACKUP_ENABLED", "true").lower() not in ("0", "false", "no")


def _lg(where: str) -> None:
    """降级点留痕（局部导入，避免模块级依赖循环）。

    2026-09-30：备份链路上的"静默 return/跳过"改为调用它 —— log_quiet 现在会带上
    异常类型、消息与调用位置（见 src/logger.py::_context），所以这一行就足够定位问题。
    """
    try:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(where)
    except Exception:
        pass


def _interval_days() -> int:
    try:
        return max(1, int(os.environ.get("QC_BACKUP_INTERVAL_DAYS", "1")))
    except (ValueError, TypeError):
        return 1


def _keep_days() -> list[int]:
    raw = os.environ.get("QC_BACKUP_KEEP_DAYS", "7,30,90")
    days: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                days.append(max(1, int(part)))
            except ValueError:      # silent-except-ok: 保留策略里的非法条目直接跳过
                pass
    return sorted(days) or [7]


def backup_dir() -> str:
    d = os.environ.get("QC_BACKUP_DIR", "").strip()
    if not d:
        try:
            import paths
            d = os.path.join(paths.log_user_dir(), "backups")
        except Exception:
            d = os.path.join(os.path.expanduser("~"), ".local", "xingyan_qc", "backups")
    os.makedirs(d, exist_ok=True)
    return d


# ─── 备份核心 ──────────────────────────────────────────

def _resolve_known(fname: str) -> str:
    """把一个已知文件名解析为它的**真实运行时位置**（备份与恢复的唯一解析入口）。

    2026-09-30 修复：此前备份只扫 <assets>/、恢复也只写回 <assets>/，但打包态下
    qc.db / rules_config.json / ris_config.json / license.dat 的真实位置在用户
    可写目录（见 src/paths.py），于是「恢复成功」但数据完全不生效。
    备份与恢复共用本函数，保证两者永远指向同一个文件。
    """
    if fname in ("qc.db", "samples.db"):
        from samplelib import db_path
        return db_path()
    if fname == "accounts.db":
        from samplelib import db_path
        return os.path.join(os.path.dirname(db_path()), "accounts.db")
    try:
        import paths as _p
        fn = {"rules_config.json": _p.rules_config_path,
              "ris_config.json": _p.ris_config_path,
              "license.dat": _p.license_path}.get(fname)
        if callable(fn):
            return fn()
    except Exception:               # silent-except-ok: 解析失败即走下方 <assets>/ 回退
        pass
    # 回退：<assets>/<name>
    try:
        import app_paths
        base = app_paths.frozen_resource_dir()
    except ImportError:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", fname)


def _dedup_existing(cands: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """过滤不存在的文件，并按绝对路径去重（同一文件只备份一份）。"""
    pairs: list[tuple[str, str]] = []
    seen = set()
    for label, p in cands:
        if not p or not os.path.isfile(p):
            continue
        key = os.path.abspath(p)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((label, p))
    return pairs


def _database_files() -> list[tuple[str, str]]:
    """扫描需要备份的数据库文件。返回 (标签, 绝对路径) 列表。

    2026-09-30：样本库与账号/科室库已统一为同一个 qc.db（见 server/db.py 与
    src/samplelib.py 的路径修正），此前「样本库 + 质控库」会把同一文件备份两份。
    """
    sample_db = _resolve_known("samples.db")
    qc_db = _resolve_known("qc.db")
    cands: list[tuple[str, str]] = []
    if qc_db and os.path.abspath(qc_db) == os.path.abspath(sample_db):
        cands.append(("qc.db", qc_db))          # 已同库：只备一份
    else:
        cands.append(("samples.db", sample_db))
        cands.append(("qc.db", qc_db))
    cands.append(("accounts.db", _resolve_known("accounts.db")))   # 旧版账号库（兼容）
    return _dedup_existing(cands)


def _config_files() -> list[tuple[str, str]]:
    """扫描需要备份的配置文件（按真实运行时位置解析，见 _resolve_known）。"""
    return _dedup_existing(
        [(fname, _resolve_known(fname))
         for fname in ("license.dat", "rules_config.json", "ris_config.json")])


def _vacuum_into(src_path: str, dest_path: str) -> bool:
    """SQLite VACUUM INTO 在线备份（不锁库，WAL 安全）。"""
    try:
        conn = sqlite3.connect(src_path, timeout=30)
        conn.execute(f"VACUUM INTO '{dest_path}'")
        conn.close()
        return os.path.isfile(dest_path)
    except Exception:
        try:
            import logger as _lm
            _lm.log_error("backup._vacuum_into",
                           f"VACUUM INTO failed: {src_path} -> {dest_path}",
                           exc=None)
        except Exception:           # silent-except-ok: 上报失败信息本身失败，不能影响回退
            pass
        # 回退：直接文件复制
        try:
            shutil.copy2(src_path, dest_path)
            return os.path.isfile(dest_path)
        except Exception:
            # 2026-09-30：备份文件复制失败必须留痕（此前静默 return False，
            # 用户只看到"备份失败"却不知原因）
            _lg(__name__)
            return False


def run_backup() -> dict[str, Any]:
    """执行一次完整备份。返回结果摘要。"""
    result: dict[str, Any] = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": [],
        "errors": [],
    }

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 数据库文件
    for label, src_path in _database_files():
        dest = os.path.join(backup_dir(), f"{label}.{ts}")
        ok = _vacuum_into(src_path, dest)
        entry = {"label": label, "src": src_path, "dest": dest, "ok": ok}
        result["files"].append(entry)
        if not ok:
            result["errors"].append(f"备份失败: {label}")

    # 配置文件（直接复制）
    for label, src_path in _config_files():
        dest = os.path.join(backup_dir(), label)
        try:
            shutil.copy2(src_path, dest)
            result["files"].append({"label": label, "dest": dest, "ok": True})
        except Exception as e:
            result["files"].append({"label": label, "dest": dest, "ok": False})
            result["errors"].append(f"配置备份失败: {label}: {e}")

    # 清理过期备份
    pruned = prune_backups()
    result["pruned"] = pruned

    # 写状态文件
    status_path = os.path.join(backup_dir(), "last_backup.json")
    try:
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        _lg(__name__)   # 2026-09-30：状态写不进去会让界面显示过期状态，必须留痕

    try:
        import logger as _lm
        _lm.log_info("backup.run_backup",
                     f"备份完成: {len(result['files'])} 文件, {len(result['errors'])} 错误",
                     files_count=len(result["files"]),
                     errors_count=len(result["errors"]))
    except Exception:               # silent-except-ok: 日志上报失败不影响备份结果
        pass

    return result


def prune_backups() -> int:
    """按保留策略清理过期备份。返回清理数量。"""
    bdir = backup_dir()
    keep = _keep_days()
    now = datetime.datetime.now()
    removed = 0

    for fname in os.listdir(bdir):
        fpath = os.path.join(bdir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.startswith("last_backup"):
            continue  # 保留状态文件

        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
        except (OSError, ValueError):   # silent-except-ok: 读不到 mtime 就跳过该文件
            continue

        age_days = (now - mtime).days

        # 判断是否需要删除：不在任何保留期内且超过最长保留期
        should_keep = False
        for kd in keep:
            if age_days <= kd:
                should_keep = True
                break

        if not should_keep:
            try:
                os.remove(fpath)
                removed += 1
            except OSError:
                # 2026-09-30：删除旧备份失败必须留痕——保留策略静默失效会撑满磁盘
                _lg(__name__)

    return removed


def get_backup_status() -> dict[str, Any]:
    """获取备份状态摘要。"""
    bdir = backup_dir()
    status_path = os.path.join(bdir, "last_backup.json")

    last_backup: Optional[dict[str, Any]] = None
    if os.path.isfile(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                last_backup = json.load(f)
        except Exception:
            _lg(__name__)   # 2026-09-30：状态文件损坏要留痕（界面会显示"无备份记录"）

    # 统计现有备份
    backup_files: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(bdir)):
        if fname.startswith("last_backup"):
            continue
        fpath = os.path.join(bdir, fname)
        if os.path.isfile(fpath):
            try:
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                backup_files.append({
                    "name": fname,
                    "size": size,
                    "mtime": datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                })
            except OSError:
                _lg(__name__)   # 2026-09-30：列备份时 stat 失败要留痕（文件可能已被外部删除）

    return {
        "enabled": _enabled(),
        "interval_days": _interval_days(),
        "keep_days": _keep_days(),
        "dir": bdir,
        "last_backup": last_backup,
        "backup_files": backup_files,
        "total_size": sum(f["size"] for f in backup_files),
    }


def restore_backup(backup_name: str) -> dict[str, Any]:
    """从备份文件恢复。返回结果摘要。"""
    bdir = backup_dir()
    src = os.path.join(bdir, backup_name)

    if not os.path.isfile(src):
        return {"ok": False, "error": f"备份文件不存在: {backup_name}"}

    result: dict[str, Any] = {"ok": True, "restored": [], "errors": []}

    # 目标路径解析（2026-09-30 修复）：必须与备份时的位置一致，否则打包态下
    # 「恢复成功」但数据不生效（此前一律写回 <assets>/）。
    dest = None
    for known in ("samples.db", "qc.db", "accounts.db",
                  "rules_config.json", "ris_config.json", "license.dat"):
        if backup_name.startswith(known):
            dest = _resolve_known(known)
            break
    if dest is None:
        dest = _resolve_known(backup_name)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # 数据库用 VACUUM INTO 产出，直接复制即可（内容一致且完整）
        shutil.copy2(src, dest)
        result["restored"].append({"from": backup_name, "to": dest})
    except Exception as e:
        result["errors"].append(f"恢复失败: {e}")
        result["ok"] = False

    return result


# ─── 后台调度器 ──────────────────────────────────────────

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop: threading.Event = threading.Event()
_last_run: float = 0.0


def _scheduler_loop() -> None:
    """后台线程：按间隔执行备份。"""
    global _last_run
    interval = _interval_days() * 86400  # 秒

    while not _scheduler_stop.is_set():
        # 首次运行延迟 60 秒（让系统启动完毕）
        time.sleep(60)
        if _scheduler_stop.is_set():
            break

        # 检查是否到期
        now = time.time()
        if now - _last_run >= interval:
            try:
                run_backup()
                _last_run = now
            except Exception:
                try:
                    import logger as _lm
                    _lm.log_error("backup._scheduler_loop", "备份调度异常")
                except Exception:
                    pass

        # 每 5 分钟检查一次（避免长时间 sleep 导致无法响应停止信号）
        for _ in range(300):
            if _scheduler_stop.is_set():
                break
            time.sleep(1)


def start_scheduler() -> None:
    """启动后台备份调度器（幂等）。"""
    global _scheduler_thread
    if not _enabled():
        return
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="QC_BackupScheduler")
    _scheduler_thread.start()

    try:
        import logger as _lm
        _lm.log_info("backup.start_scheduler",
                     f"自动备份调度器已启动，间隔 {_interval_days()} 天")
    except Exception:               # silent-except-ok: 日志失败不影响调度器启动
        pass


def stop_scheduler() -> None:
    """停止后台备份调度器。"""
    _scheduler_stop.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=10)
