"""
logger.py — 结构化日志模块（P0 改造，2026-09-12）

替代全项目 log_quiet 的「静默吞异常」模式，提供结构化、可见的降级/错误记录。

设计要点：
- 向后兼容：log_quiet() 保留签名，但内部改为 WARNING 级别 + 结构化 JSON 上下文
- 新 API：warn_degraded() / log_error() / log_info() 供新代码使用
- 日志输出到与 log_utils 相同的滚动文件（1MB x 5）
- 零外部依赖，纯标准库
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import sys
import time
import traceback
from typing import Any, Optional

_LOGGER_NAME = "xingyan_qc_structured"
_logger: Optional[logging.Logger] = None


def _log_dir() -> str:
    try:
        import paths
        return os.path.join(paths.log_user_dir(), "logs")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".local", "xingyan_qc", "logs")


def _ensure_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    d = _log_dir()
    os.makedirs(d, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    try:
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(d, "app.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # 文件不可写不阻断

    try:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    except Exception:
        pass

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    return _ensure_logger()


# ─── 上下文构建 ────────────────────────────────────────────

def _context(where: str, **extra: Any) -> dict:
    ctx = {
        "where": where,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.system(),
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    # 2026-09-30：自动带上"当前正在处理的异常"与其发生位置。
    # 背景：此前 degraded 日志只有 where+环境，没有异常类型/消息/行号 ——
    # 于是"功能不生效但没人知道为什么"（健康探针 TypeError、恢复 ImportError、
    # 反馈表 no such table 等）在日志里都看不出来。全仓 71 个降级调用点
    # 无需修改，统一在此获得异常上下文。
    if "exc" not in extra:
        _e = sys.exc_info()[1]
        if _e is not None:
            ctx["exc"] = f"{type(_e).__name__}: {_e}"
            try:
                # 向上找到**真正的调用方**（跳过 logger/log_utils 自身的转发帧），
                # 这样日志里的行号指向业务代码，而不是观测层。
                _fr = sys._getframe(1)
                while _fr is not None and os.path.basename(
                        _fr.f_code.co_filename) in ("logger.py", "log_utils.py"):
                    _fr = _fr.f_back
                if _fr is not None:
                    ctx["at"] = "%s:%d" % (os.path.basename(_fr.f_code.co_filename),
                                           _fr.f_lineno)
            except Exception:
                pass
    ctx.update(extra)
    return ctx


# ─── 公开 API ──────────────────────────────────────────────

def log_quiet(where: str, **extra: Any) -> None:
    """向后兼容入口：替代原来的 DEBUG 静默，现在以 WARNING 级别输出结构化信息。

    原有调用 `log_quiet(__name__)` 仍然正常工作，但现在会在日志文件中留下记录。
    新代码建议改用 warn_degraded() 或 log_error()。
    """
    try:
        lg = _ensure_logger()
        ctx = _context(where, **extra)
        lg.warning("degraded:%s | %s", where, json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:
        pass  # 观测本身绝不能引入新故障


def warn_degraded(where: str, reason: str = "", **extra: Any) -> None:
    """记录功能降级（可选依赖缺失、外部服务不可达等）。"""
    try:
        lg = _ensure_logger()
        ctx = _context(where, reason=reason, **extra)
        lg.warning("degraded:%s | %s", where, json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:
        pass


def log_error(where: str, msg: str = "", exc: Optional[Exception] = None, **extra: Any) -> None:
    """记录错误事件（带异常栈）。"""
    try:
        lg = _ensure_logger()
        ctx = _context(where, msg=msg, **extra)
        tb = ""
        if exc is not None:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            ctx["traceback"] = "".join(tb)[:2000]
        lg.error("error:%s | %s", where, json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:
        pass


def log_info(where: str, msg: str = "", **extra: Any) -> None:
    """记录信息事件（备份完成、更新开始等运维关注点）。"""
    try:
        lg = _ensure_logger()
        ctx = _context(where, msg=msg, **extra)
        lg.info("info:%s | %s", where, json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:
        pass
