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
    """返回一个用户可写的日志数据目录（跨平台，自动创建）。

    统一由 paths.log_user_dir 解析（单一事实源）。
    """
    import paths
    return paths.log_user_dir()


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
    """降级点统一观测口 (2026-08-25 审计新增, 2026-09-12 P0 改造)。

    v2: 原先 DEBUG 级别静默 → 现升级为 WARNING 级别 + 结构化 JSON 上下文。
    委托给 logger.py 的 log_quiet，原有 70+ 处调用无需修改即获得可见日志。

    新代码建议直接 import logger 并使用 warn_degraded() / log_error()。

    用法(函数内局部导入, 零模块级依赖):
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
    """
    try:
        import logger as _logger_mod
        _logger_mod.log_quiet(where)
    except Exception:
        # 降级到原始 logger（logger 模块不可用时）
        try:
            lg = get_logger()
            if lg is not None:
                lg.warning("silenced-exception at %s", where, exc_info=True)
        except Exception:
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


def export_diagnostic_bundle(dest_dir=None, include_patient_data: bool = False):
    """打包「日志 + 系统信息 + 授权状态」为 zip，返回 zip 绝对路径。

    dest_dir 为 None 时默认存到桌面（无桌面则用户主目录）。

    2026-09-30 修复：
    1) `feedback.db` 的写入原本写在 `with ZipFile(...)` **块外** → 归档已关闭，
       必然抛 ValueError 且被 except 吞掉 —— 也就是 CHANGELOG 里"诊断包纳入
       feedback.db"从来没有真正生效。现改到块内。
    2) 该库含 report_text（完整报告正文），属患者数据。现默认**不入包**，
       需显式 `include_patient_data=True` 才纳入；包内附 README 说明所含内容，
       避免"以为不含患者数据就发出去了"。
    """
    logger = get_logger()
    logger.info("开始导出诊断包")

    if not dest_dir:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        dest_dir = desktop if os.path.isdir(desktop) else os.path.expanduser("~")
    os.makedirs(dest_dir, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(dest_dir, f"星衍质控_诊断包_{ts}.zip")

    included_fb = False
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system_info.json",
                    json.dumps(_system_info(), ensure_ascii=False, indent=2))
        ld = log_dir()
        if os.path.isdir(ld):
            for fn in sorted(os.listdir(ld)):
                fp = os.path.join(ld, fn)
                if os.path.isfile(fp):
                    zf.write(fp, os.path.join("logs", fn))

        if include_patient_data:
            # badcase 反馈库：增量精调的数据源，但 report_text 含患者报告正文。
            try:
                from samplelib import db_path as _sdb_path
                _fb = os.path.join(os.path.dirname(_sdb_path()), "feedback.db")
                if os.path.isfile(_fb):
                    zf.write(_fb, "feedback.db")
                    included_fb = True
            except Exception:
                pass  # 定位失败不阻断诊断包

        zf.writestr("README_诊断包.txt", _BUNDLE_README.format(
            fb=("已包含 feedback.db（含患者报告正文）" if included_fb
                else "未包含 feedback.db（默认排除含患者正文的数据）")))

    if included_fb:
        logger.info("diagnostic bundle: feedback.db included")
    logger.info("诊断包已导出: %s", zip_path)
    return zip_path


_BUNDLE_README = """星衍放射质控软件 · 诊断包说明
================================

本包内容：
- system_info.json  运行环境、版本、授权状态
- logs/             应用日志（含错误/信息条目，写入前已做去标识擦洗，但不保证无患者信息）
- {fb}

⚠️ 隐私提示：
本包可能含有患者相关信息。**仅在本机排障使用**；如需发回开发者，请先确认
院内数据合规要求并取得授权，必要时人工删除含患者内容的部分。
"""


if __name__ == "__main__":  # pragma: no cover - 手工排障入口
    # 用法：python src/log_utils.py [--dest DIR] [--include-patient-data]
    # 2026-09-30 新增：DEPLOYMENT.md 一直写着 `python -m src.log_utils`，
    # 但 src/ 不是包（无 __init__.py）且本文件没有入口 → 该命令必然失败。
    import argparse
    _ap = argparse.ArgumentParser(description="导出星衍质控诊断包")
    _ap.add_argument("--dest", default=None, help="输出目录（默认桌面）")
    _ap.add_argument("--include-patient-data", action="store_true",
                     help="连同 feedback.db（含患者报告正文）一起打包，谨慎使用")
    _a = _ap.parse_args()
    print(export_diagnostic_bundle(_a.dest, include_patient_data=_a.include_patient_data))

