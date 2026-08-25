r"""
log_utils.py — 本地滚动日志 + 全局异常捕获 + 诊断包导出
星衍放射质控软件 · 内测支撑模块（纯标准库，零依赖）

设计要点：
- 日志写入「用户可写目录」（非程序目录），避免安装版落在 Program Files 无写权限。
  Windows: %LOCALAPPDATA%\星衍放射质控软件\logs
  macOS:   ~/Library/Application Support/星衍放射质控软件/logs
  Linux:   ~/.local/share/星衍放射质控软件/logs
- 滚动：单文件 1MB，保留 5 个历史，防止日志无限膨胀。
- 全局异常钩子 + Tk 回调异常接管：崩溃/回调异常都会落盘。
- 诊断包：一键把「所有日志 + 系统信息 + 授权状态」打成 zip，内测用户发回即可定位问题。
"""
import os
import sys
import json
import zipfile
import logging
import logging.handlers
import platform
import datetime
import traceback

APP_NAME = "星衍放射质控软件"
_LOGGER_NAME = "xingyan_qc"
_logger = None


# ----------------------------- 路径 -----------------------------
def user_data_dir():
    """返回一个用户可写的应用数据目录（跨平台，自动创建）。"""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif system == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or \
            os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def log_dir():
    d = os.path.join(user_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def log_file():
    return os.path.join(log_dir(), "app.log")


# ----------------------------- 初始化 -----------------------------
def setup_logging(level=logging.INFO):
    """初始化滚动日志（每文件 1MB，保留 5 个）。幂等，可重复调用。"""
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # 文件不可写也不能阻断程序启动

    try:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

    _logger = logger
    logger.info("=" * 60)
    logger.info("%s 启动 | %s | Python %s",
                APP_NAME, platform.platform(), platform.python_version())
    return logger


def log_quiet(where: str) -> None:
    """静默降级点统一观测口 (2026-08-25 审计新增)。

    设计意图: 项目中大量 `except Exception: pass` 属于**有意的可选能力降级**
    (如 pypinyin 未安装则 R19 关闭), 行为正确但不可见——现场排障时无从知晓
    「哪条路走过了」。本函数不改变任何控制流, 仅以 DEBUG 级别留痕,
    诊断包导出后即可还原完整决策链。

    用法(函数内局部导入, 零模块级依赖):
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
    """
    try:
        lg = get_logger()
        if lg is not None:
            lg.debug("silenced-exception at %s", where, exc_info=True)
    except Exception:  # 观测本身绝不能引入新故障
        pass


def get_logger():
    return _logger or setup_logging()


def install_excepthook():
    """安装全局未捕获异常钩子 + Tk 回调异常钩子，将崩溃写入日志。"""
    logger = get_logger()

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error("未捕获异常:\n%s",
                     "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = _hook

    # 接管 Tkinter 回调异常（默认只打印到 stderr，打包后不可见）
    try:
        import tkinter

        def _tk_report(self, exc, val, tb):
            logger.error("Tk 回调异常:\n%s",
                         "".join(traceback.format_exception(exc, val, tb)))

        tkinter.Tk.report_callback_exception = _tk_report
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


# ----------------------------- 诊断包 -----------------------------
def _system_info():
    """收集用于排障的系统与运行信息。"""
    info = {
        "app": APP_NAME,
        "time": datetime.datetime.now().isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "argv": sys.argv,
        "cwd": os.getcwd(),
    }
    try:
        import version
        info["app_version"] = version.APP_VERSION
        info["build_time"] = getattr(version, "BUILD_TIME", "")
        info["commit"] = getattr(version, "COMMIT", "")
    except Exception as e:
        info["version_error"] = str(e)

    try:
        import license_utils
        status, data = license_utils.check_trial()
        info["license_status"] = status
        info["license_data"] = str(data)
        try:
            info["machine_id"] = license_utils._stable_hw_id()
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
    except Exception as e:
        info["license_error"] = str(e)

    return info


def export_diagnostic_bundle(dest_dir=None):
    """打包「所有日志 + 系统信息 + 授权状态」为一个 zip，返回 zip 绝对路径。

    dest_dir 为 None 时默认存到桌面（无桌面则用户主目录）。
    """
    logger = get_logger()
    logger.info("开始导出诊断包")

    if not dest_dir:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        dest_dir = desktop if os.path.isdir(desktop) else os.path.expanduser("~")
    os.makedirs(dest_dir, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(dest_dir, f"星衍质控_诊断包_{ts}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system_info.json",
                    json.dumps(_system_info(), ensure_ascii=False, indent=2))
        ld = log_dir()
        if os.path.isdir(ld):
            for fn in sorted(os.listdir(ld)):
                fp = os.path.join(ld, fn)
                if os.path.isfile(fp):
                    zf.write(fp, os.path.join("logs", fn))

    # badcase 反馈库一并入包 (2026-08-25): 它是增量精调的唯一数据源。
    # ⚠️ 注意: report_text 字段含患者报告内容, 发送前请知悉/脱敏。
    try:
        from samplelib import db_path as _sdb_path
        _fb = os.path.join(os.path.dirname(_sdb_path()), "feedback.db")
        if os.path.isfile(_fb):
            zf.write(_fb, "feedback.db")
            if _logger:
                _logger.info("diagnostic bundle: feedback.db included (%d bytes)",
                             os.path.getsize(_fb))
    except Exception:
        pass  # 样本库定位失败时跳过, 不阻断诊断包

    logger.info("诊断包已导出: %s", zip_path)
    return zip_path
