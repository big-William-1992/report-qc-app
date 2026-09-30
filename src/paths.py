"""
paths.py — 全项目路径解析单一事实源
=====================================
统一此前分散在 engine / ocr_provider / samplelib / license_utils / ris /
accounts / feedback_collector / auto_updater / version / log_utils 与
server/{db,main,deps} 各处的 frozen/源码双分支路径逻辑（此前仅定位
assets/ 就有 5 种写法，且 frozen 语义不一致）。

语义约定
--------
- bundle_root()：随包资源根目录。frozen 优先 sys._MEIPASS（单目录打包时
  等于 exe 所在目录），回退 exe 目录；源码运行 = 项目根（src/ 的上级）。
- install_root()：frozen 时为 exe 所在目录（自动更新器安装/替换文件的
  位置），源码运行同 bundle_root()。
- user_data_dir()：用户可写数据目录，QC_APPDATA 环境变量可覆盖
  （E2E 测试隔离）。与历史 server/deps._appdata_dir 落盘位置完全一致，
  保证 license/密钥/队列等既有数据文件位置不变。
- 其余 assets_dir / rules_config_path / ... 均为上述之上的派生。
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

APP_NAME = "星衍放射质控软件"
APPDATA_NAME = "MedicalReportQC"


def bundle_root() -> str:
    """随包资源根目录：frozen 用 _MEIPASS 优先，源码用项目根。

    ⚠️ server/main.py 的 _bundle_root() 是引导期副本（src 未进 sys.path 前必须
    本地实现），与本函数逻辑必须保持一致，修改时两处同步。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return str(Path(meipass))
        return os.path.dirname(os.path.abspath(sys.executable))
    return str(Path(__file__).resolve().parent.parent)


def install_root() -> str:
    """应用安装根目录：frozen 为 exe 所在目录（更新器替换对象），源码同 bundle_root。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return bundle_root()


def assets_dir() -> str:
    return os.path.join(bundle_root(), "assets")


def user_data_dir() -> str:
    """用户可写数据目录（自动创建）。QC_APPDATA 环境变量可覆盖（E2E 隔离）。

    2026-09-30：改为**平台正确**且与 server/db.py / accounts._assets_dir /
    samplelib._appdata_db 同一个根（Windows: %APPDATA%/MedicalReportQC；
    macOS: ~/Library/Application Support/MedicalReportQC；
    Linux: $XDG_DATA_HOME|~/.local/share/MedicalReportQC）。
    此前非 Windows 一律落 ~/.medical_report_qc，与数据库所在目录不是同一个，
    于是 frozen 打包态"规则配置/会话在一个目录、数据库在另一个目录"。
    """
    override = os.environ.get("QC_APPDATA", "").strip()
    if override:
        base = os.path.abspath(override)
    else:
        ap = os.path.expandvars("%APPDATA%")
        if platform.system() == "Windows" and ap and os.path.isabs(ap):
            base = os.path.join(ap, APPDATA_NAME)
        elif platform.system() == "Darwin":
            base = os.path.join(os.path.expanduser("~"),
                                "Library", "Application Support", APPDATA_NAME)
        else:
            base = os.path.join(
                os.environ.get("XDG_DATA_HOME") or os.path.join(
                    os.path.expanduser("~"), ".local", "share"),
                APPDATA_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def log_user_dir() -> str:
    """日志/诊断数据目录（跨平台，自动创建）。与历史 log_utils.user_data_dir 一致。"""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif system == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def ocr_config_path() -> str:
    """OCR 区域配置（与历史 server/deps._ocr_config_path 同路径，桌面/Web 互通）。

    2026-09-30：统一到 user_data_dir()（与数据库同一个根）；此前非 Windows 落
    ~/.config/MedicalReportQC，与其它数据目录不一致。若旧位置已有配置且新位置
    没有，则一次性搬过来，避免用户画好的区域框"消失"。
    """
    new = os.path.join(user_data_dir(), "ocr_config.json")
    if not os.path.exists(new):
        legacy = os.path.join(os.path.expanduser("~"), ".config", APPDATA_NAME,
                              "ocr_config.json")
        if os.path.isfile(legacy):
            try:
                shutil.copyfile(legacy, new)
            except Exception:
                pass
    return new


def rules_config_path() -> str:
    """规则配置：打包后优先用户可写目录，不存在则从随包 assets 复制初始文件。"""
    if getattr(sys, "frozen", False):
        user_path = os.path.join(user_data_dir(), "rules_config.json")
        if not os.path.exists(user_path):
            src = os.path.join(assets_dir(), "rules_config.json")
            try:
                if os.path.exists(src):
                    shutil.copyfile(src, user_path)
            except Exception:
                return src
        return user_path
    return os.path.join(assets_dir(), "rules_config.json")


def license_path() -> str:
    """许可证数据文件。frozen 时位于 exe 同级 assets/（自动更新器的备份/恢复对象）。"""
    return os.path.join(install_root(), "assets", "license.dat")


def ris_config_path() -> str:
    """RIS 连接配置持久化路径。"""
    return os.path.join(assets_dir(), "ris_config.json")


def user_lexicon_path() -> str:
    """用户/反馈闭环可编辑词表（assets/lexicons/user_whitelist.json）。

    frozen 下 assets 可能只读，放用户数据目录；源码用 assets/lexicons
    （与 rules_config.json 的用户目录机制一致）。
    """
    if getattr(sys, "frozen", False):
        d = os.path.join(user_data_dir(), "lexicons")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "user_whitelist.json")
    return os.path.join(assets_dir(), "lexicons", "user_whitelist.json")


def feedback_data_dir() -> str:
    """反馈闭环数据目录：源码用项目 data/feedback，frozen 用用户数据目录。"""
    if getattr(sys, "frozen", False):
        d = os.path.join(user_data_dir(), "feedback")
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.join(bundle_root(), "data", "feedback")


def ocr_models_dir() -> str:
    return os.path.join(assets_dir(), "ocr_models")


def test_reports_path() -> str:
    """测试报告生成器产物（data/processed/test_reports.json，不入库）。

    统一消费/生成路径：generate_test_reports.py 生成到此，
    medical_whitelist 的 bigram 频率表也从这里读（此前生成器写 src/data/、
    消费者读 data/ 两侧不一致，靠手工拷贝才可用）。
    """
    return os.path.join(bundle_root(), "data", "processed", "test_reports.json")


def build_info_path() -> str:
    """CI 打包时写入的构建信息文件。"""
    return os.path.join(assets_dir(), "build_info.json")
