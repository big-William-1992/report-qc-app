"""
error_reporter.py — 匿名错误上报模块（2026-09-12 商业化）

功能：
- 捕获 Python 异常摘要（不含患者数据），上报到本地 JSON 日志
- 管理员可查看/导出错误报告
- 支持远程上报端点（可选，不启用时仅写本地）

环境变量：
  QC_ERROR_REPORT_URL  — 远程上报端点（空则仅本地）
  QC_ERROR_REPORT_ENABLED — 是否启用（默认 true）
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import sys
import traceback
from typing import Optional

try:
    from version import APP_VERSION
except Exception:
    APP_VERSION = "unknown"

_REPORT_ENABLED = os.environ.get("QC_ERROR_REPORT_ENABLED", "true").lower() not in (
    "0", "false", "no")
_REMOTE_URL = os.environ.get("QC_ERROR_REPORT_URL", "").strip()


def _local_dir() -> str:
    """错误报告本地存储目录。"""
    try:
        import paths
        d = os.path.join(paths.log_user_dir(), "errors")
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".local", "xingyan_qc", "errors")
    os.makedirs(d, exist_ok=True)
    return d


def _local_file(ts: Optional[str] = None) -> str:
    return os.path.join(_local_dir(), f"reports_{ts or 'current'}.jsonl")


def _safe_summary(exc: BaseException, max_len: int = 500) -> str:
    """异常摘要，截断至 max_len，去掉患者数据特征字段。"""
    msg = str(exc)
    # 简单脱敏：去掉形如"姓名:" 后的内容
    for label in ("姓名", "患者", "patient", "name=", "patient_id="):
        idx = msg.find(label)
        if idx >= 0:
            msg = msg[:idx] + "..."
    return msg[:max_len]


def report_exception(exc: BaseException, where: str = "", **context) -> None:
    """记录一个异常到本地 JSON 日志。
    where: 调用位置标识（如 module.function）
    context: 额外上下文（版本号、平台等自动填充）
    """
    if not _REPORT_ENABLED:
        return
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    frames = [(f.filename, f.lineno, f.name) for f in tb[-5:]]
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": APP_VERSION,
        "platform": sys.platform,
        "python": platform.python_version(),
        "where": where or "",
        "error_type": type(exc).__name__,
        "error_msg": _safe_summary(exc),
        "frames": frames,
        "context": {k: v for k, v in context.items() if k not in ("patient", "report_text")},
    }
    try:
        fname = _local_file()
        with open(fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def report_info(message: str, where: str = "", **context) -> None:
    """记录一条非异常的警告信息（如授权即将过期）。"""
    if not _REPORT_ENABLED:
        return
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": APP_VERSION,
        "platform": sys.platform,
        "python": platform.python_version(),
        "where": where or "",
        "level": "info",
        "message": message[:500],
        "context": {k: v for k, v in context.items()},
    }
    try:
        fname = _local_file()
        with open(fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_recent_reports(limit: int = 50) -> list[dict]:
    """读取最近的错误报告。"""
    d = _local_dir()
    results: list[dict] = []
    try:
        for fname in sorted(os.listdir(d), reverse=True):
            if not fname.endswith(".jsonl"):
                continue
            fp = os.path.join(d, fname)
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        results.append(json.loads(line))
                    except Exception:
                        pass
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results[-limit:]


def get_stats() -> dict:
    """错误统计摘要。"""
    d = _local_dir()
    total_files = 0
    total_errors = 0
    total_infos = 0
    recent = get_recent_reports(limit=100)
    for entry in recent:
        if entry.get("level") == "info":
            total_infos += 1
        else:
            total_errors += 1
    try:
        total_files = len([f for f in os.listdir(d) if f.endswith(".jsonl")])
    except Exception:
        pass
    return {
        "enabled": _REPORT_ENABLED,
        "remote_url": _REMOTE_URL,
        "local_dir": d,
        "total_report_files": total_files,
        "recent_errors": total_errors,
        "recent_infos": total_infos,
    }
