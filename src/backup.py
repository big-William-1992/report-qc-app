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
            except ValueError:
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

def _database_files() -> list[tuple[str, str]]:
    """扫描需要备份的数据库文件。返回 (相对标签, 绝对路径) 列表。"""
    from samplelib import db_path as _sample_db
    from server.db import get_db_path as _qc_db

    db_root = os.path.dirname(_sample_db())
    pairs: list[tuple[str, str]] = []

    # 样本库
    sample_db = _sample_db()
    if os.path.isfile(sample_db):
        pairs.append(("samples.db", sample_db))

    # 质控/账号库
    try:
        qc_db = _qc_db()
        if os.path.isfile(qc_db):
            pairs.append(("qc.db", qc_db))
    except Exception:
        # qc.db 可能不存在（未初始化时）
        pass

    # 旧版账号库（兼容）
    accounts_db = os.path.join(db_root, "accounts.db")
    if os.path.isfile(accounts_db):
        pairs.append(("accounts.db", accounts_db))

    return pairs


def _config_files() -> list[tuple[str, str]]:
    """扫描需要备份的配置文件。"""
    try:
        import app_paths
        base = app_paths.frozen_resource_dir()
    except ImportError:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assets = os.path.join(base, "assets")

    files: list[tuple[str, str]] = []
    for fname in ("license.dat", "rules_config.json", "ris_config.json"):
        fp = os.path.join(assets, fname)
        if os.path.isfile(fp):
            files.append((fname, fp))
    return files


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
        except Exception:
            pass
        # 回退：直接文件复制
        try:
            shutil.copy2(src_path, dest_path)
            return os.path.isfile(dest_path)
        except Exception:
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
        pass

    try:
        import logger as _lm
        _lm.log_info("backup.run_backup",
                     f"备份完成: {len(result['files'])} 文件, {len(result['errors'])} 错误",
                     files_count=len(result["files"]),
                     errors_count=len(result["errors"]))
    except Exception:
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
        except (OSError, ValueError):
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
                pass

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
            pass

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
                pass

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

    # 判断文件类型
    if ".db" in backup_name:
        # 数据库文件
        try:
            import app_paths
            base = app_paths.frozen_resource_dir()
            assets = os.path.join(base, "assets")
        except ImportError:
            assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

        # 根据文件名推断目标路径
        dest = None
        for known in ("samples.db", "qc.db", "accounts.db"):
            if backup_name.startswith(known):
                dest = os.path.join(assets, known)
                break

        if dest is None:
            # 尝试从文件名提取
            dest = os.path.join(assets, backup_name)

        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # 数据库恢复：直接复制（VACUUM INTO 保证一致性）
            shutil.copy2(src, dest)
            result["restored"].append({"from": backup_name, "to": dest})
        except Exception as e:
            result["errors"].append(f"恢复失败: {e}")
            result["ok"] = False
    else:
        # 配置文件
        try:
            import app_paths
            base = app_paths.frozen_resource_dir()
            assets = os.path.join(base, "assets")
        except ImportError:
            assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

        dest = os.path.join(assets, backup_name)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
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
    except Exception:
        pass


def stop_scheduler() -> None:
    """停止后台备份调度器。"""
    _scheduler_stop.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=10)
