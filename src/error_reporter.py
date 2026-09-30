"""
error_reporter.py — 本地错误/信息上报模块（2026-09-12 商业化）

功能：
- 捕获 Python 异常摘要与信息条目，写入**本地** JSONL（本模块不联网）
- 管理员可查看/导出
- 写入前统一做「白名单键 + 去标识擦洗」，降低误落患者信息的风险

环境变量：
  QC_ERROR_REPORT_ENABLED — 是否启用（默认 true）
  QC_ERROR_REPORT_URL     — ⚠️ **尚未实现**（保留读取以便将来扩展）；设置后也不会
                            上传任何数据，get_stats() 会标明 remote_upload 状态。

2026-09-30 修复（隐私）：
  1) 两个入口的脱敏口径原本不一致：report_exception 只按 key 名剔除
     patient/report_text，report_info **完全不剔**；而 /api/v1/user-feedback
     会把用户反馈正文写进 report_info → 患者内容可能落进错误日志。
     现统一走 _scrub_context()：**未知键一律丢弃**（白名单），字符串值统一擦洗。
  2) 文档/CHANGELOG 曾把 QC_ERROR_REPORT_URL 描述为可用的上传地址，实际没有任何
     上传实现 —— 现如实标注为 not_implemented，避免"以为已匿名上报"。
  注意：擦洗是**降低风险**，不是保证无 PHI；调用方不要把报告正文塞进 context。
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import re
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

# 允许写入日志的上下文键（白名单）。未知键一律丢弃——这样"某个调用方顺手把
# report_text / meta / 报告全文塞进 context"不会静默变成 PHI 落盘。
_ALLOWED_KEYS = frozenset({
    "where", "emp_id", "category", "contact", "rule_id", "error_type", "severity",
    "engine_source", "model", "stage", "count", "status", "code", "path", "version",
    # 用户主动提交的反馈正文（可能被粘贴报告内容，见 _scrub_text 与调用点注释）
    "feedback_text",
})
# 去标识标签：命中后把该标签之后的内容整体抹除
_PHI_LABELS = (
    "姓名", "患者", "病人", "病案号", "住院号", "门诊号", "身份证", "电话", "手机",
    "patient", "name=", "patient_id=", "mrn", "id_card",
)
_LONG_DIGITS_RE = re.compile(r"\d{6,}")


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


def _scrub_text(value, max_len: int = 500) -> str:
    """粗粒度去标识：截断 + 抹掉标签后内容 + 抹掉长数字串。

    ⚠️ 这是「降低风险」而非「保证无 PHI」：一段完整报告正文无法靠正则识别。
    调用方有责任不要把报告正文塞进 context。
    """
    if not isinstance(value, str):
        value = str(value)
    out = value
    for label in _PHI_LABELS:
        idx = out.find(label)
        if idx >= 0:
            out = out[:idx] + f"{label}=<已抹除>"
            break
    out = _LONG_DIGITS_RE.sub("<数字已抹除>", out)
    return out[:max_len]


def _scrub_context(context: dict) -> dict:
    """按白名单过滤 context，并对值做去标识擦洗（两个入口共用同一口径）。"""
    out: dict = {}
    for k, v in (context or {}).items():
        if k not in _ALLOWED_KEYS:
            continue
        if v is None or isinstance(v, (int, float, bool)):
            out[k] = v
        else:
            out[k] = _scrub_text(v, 200)
    return out


def _safe_summary(exc: BaseException, max_len: int = 500) -> str:
    """异常摘要：截断 + 去标识。"""
    return _scrub_text(str(exc), max_len)


def report_exception(exc: BaseException, where: str = "", **context) -> None:
    """记录一个异常到本地 JSON 日志。
    where: 调用位置标识（如 module.function）
    context: 额外上下文（白名单键 + 自动擦洗，见 _scrub_context）
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
        "context": _scrub_context(context),
    }
    try:
        fname = _local_file()
        with open(fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def report_info(message: str, where: str = "", **context) -> None:
    """记录一条非异常的信息条目（如用户反馈、授权即将过期）。

    脱敏口径与 report_exception **完全一致**（2026-09-30 修复：此前这里一个键都不过滤）。
    """
    if not _REPORT_ENABLED:
        return
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": APP_VERSION,
        "platform": sys.platform,
        "python": platform.python_version(),
        "where": where or "",
        "level": "info",
        "message": _scrub_text(message, 500),
        "context": _scrub_context(context),
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
        # 2026-09-30：如实标明——远程上报**未实现**，配了也不会发数据。
        "remote_upload": "not_implemented" if _REMOTE_URL else "disabled",
        "local_only": True,
        "local_dir": d,
        "total_report_files": total_files,
        "recent_errors": total_errors,
        "recent_infos": total_infos,
    }
