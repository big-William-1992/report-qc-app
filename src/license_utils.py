"""
license_utils.py
星衍放射质控软件 · 授权管理模块
功能：免责声明 → 免费试用期（3个月）→ 激活码验证
"""

import os
import json
import hmac
import hashlib
import base64
import datetime
import platform
import tkinter as tk
from tkinter import ttk
import subprocess

# 许可证数据文件（支持 PyInstaller 打包后的路径）
try:
    import app_paths
    _BASE_DIR = app_paths.frozen_resource_dir()
except ImportError:  # 兼容 from src import license_utils 的包式导入
    # 源码运行
    _BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_LICENSE_FILE = os.path.join(_BASE_DIR, "assets", "license.dat")

# 试用期（天）
TRIAL_DAYS = 90

# ---------- 免责声明文本 ----------

DISCLAIMER_TEXT = """用户协议与免责声明

欢迎使用「星衍放射质控软件」（以下简称"本软件"）。

一、使用许可
1. 本软件免费试用期为 90 天，试用期后需输入有效的激活码方可继续使用。
2. 您仅可在获得授权的情况下使用本软件。

二、免责声明
1. 本软件提供的报告质控结果仅供参考，**不构成最终诊断依据**。
2. 所有质控结果均需由具备资质的放射科医师进行审核和确认。
3. 开发者不对因使用本软件产生的任何直接或间接损失承担责任。
4. 本软件不替代医疗专业人员的临床判断和决策。

三、数据安全
1. 本软件质控引擎在本地运行，不向任何第三方上传患者数据。
2. 样本库数据存储在本地 SQLite 数据库中，请自行做好数据备份。

四、知识产权
本软件的知识产权归开发者所有。未经授权，禁止反向工程、修改或分发。

————————————————
继续使用即表示您已阅读、理解并同意上述条款。如不同意，请退出本软件。"""


# ---------- 许可证文件 I/O ----------

def _read_license():
    """读取许可证文件，返回 dict；若文件不存在或损坏返回空 dict。"""
    try:
        if os.path.isfile(_LICENSE_FILE):
            with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return {}


def _write_license(data):
    """写许可证文件（0600：内含激活码，防科室多用户主机其他账号读取，2026-08-18）。
    2026-08-18 M7：临时文件 + os.replace 原子写，防写一半崩溃损坏 license。"""
    os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
    tmp = _LICENSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _LICENSE_FILE)
    try:
        os.chmod(_LICENSE_FILE, 0o600)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


# ---------- 免责声明 ----------

def show_disclaimer(parent):
    """显示免责声明窗口。用户点"同意"返回 True，否则返回 False。"""
    lic = _read_license()
    if lic.get("disclaimer_accepted"):
        return True  # 已同意过

    accepted = [False]  # 闭包捕获

    win = tk.Toplevel(parent)
    win.title("用户协议与免责声明")
    win.geometry("640x520")
    win.configure(bg="#FFFFFF")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()  # 模态
    # macOS 关键：transient 子窗口需显式置顶，否则父窗口失焦/隐藏时不会渲染
    win.lift()
    try:
        win.attributes("-topmost", True)
        win.after(900, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

    # 标题
    tk.Label(win, text="用户协议与免责声明", font=("PingFang SC", 15, "bold"),
             bg="#FFFFFF", fg="#0B8A9E").pack(pady=(18, 6))

    # 文本域
    txt = tk.Text(win, wrap="word", width=72, height=18,
                  font=("PingFang SC", 10), bg="#F8FAFC", fg="#1A2332",
                  relief="solid", borderwidth=1, padx=12, pady=12)
    txt.insert("1.0", DISCLAIMER_TEXT)
    txt.configure(state="disabled")  # 只读
    txt.pack(padx=20, pady=(0, 12), fill="both", expand=True)

    # 按钮行
    btn_frm = tk.Frame(win, bg="#FFFFFF")
    btn_frm.pack(pady=(0, 18))

    def on_accept():
        lic["disclaimer_accepted"] = True
        _write_license(lic)
        accepted[0] = True
        win.destroy()

    def on_reject():
        win.destroy()

    tk.Button(btn_frm, text="  同意并继续  ", command=on_accept,
              bg="#0B8A9E", fg="white", font=("PingFang SC", 11, "bold"),
              relief="flat", padx=20, pady=6, cursor="hand2").pack(side="left", padx=10)
    tk.Button(btn_frm, text="  不同意，退出  ", command=on_reject,
              bg="#E8EDF2", fg="#1A2332", font=("PingFang SC", 11),
              relief="flat", padx=20, pady=6, cursor="hand2").pack(side="left", padx=10)

    parent.wait_window(win)
    return accepted[0]


# ---------- 试用期检查 ----------

# 2026-08-18 M7：first_run 与机器硬件标识绑定做 HMAC 防篡改。
# key 派生自 _stable_hw_id（重装系统/换网卡即失效，回拨/手改日期后签名不匹配 → 视为过期）。
def _trial_hmac_key() -> bytes:
    return hashlib.sha256((_stable_hw_id() + "::xingyan-trial-v1").encode("utf-8")).digest()


def _trial_sign(date_str: str) -> str:
    return hmac.new(_trial_hmac_key(), date_str.encode("utf-8"), hashlib.sha256).hexdigest()


def _trial_verify(date_str: str, sig: str) -> bool:
    return hmac.compare_digest(_trial_sign(date_str), (sig or "").lower())


def _activated_valid(lic: dict) -> bool:
    """激活状态真实性校验。
    单机模式（2026-08-18 防绕过）：
      ① machine_id 必须与当前机器一致（防复制 license.dat 一码多机）
      ② activation_code 必须通过 Ed25519 验签
    浮动模式（2026-09-12 新增）：
      ① department_id 必须与当前配置一致
      ② activation_code 必须通过 Ed25519 验签
      ③ 座位数未满（通过共享目录心跳计数）
    """
    if not lic.get("activated"):
        return False
    code = (lic.get("activation_code") or "").strip()
    if not code:
        return False
    if not validate_activation_code(code):
        return False
    # 机器绑定检查（浮动模式跳过）
    if not lic.get("floating_license"):
        if lic.get("machine_id") != _machine_id():
            return False
    # 浮动模式座位检查
    if _QC_FLOATING_LICENSE:
        if not lic.get("floating_license"):
            # license 不是浮动模式写入的，但环境启用了浮动 → 不匹配
            return False
        ok, _ = _check_floating_license()
        if not ok:
            return False
    return True


def check_trial():
    """检查试用期状态。
    返回: ("ok", "") - 可用
          ("trial", 剩余天数) - 试用中
          ("expired", "") - 已过期
          ("activated", "") - 已激活
    浮动模式（2026-09-12）：
      - 每次检查时写心跳，保持座位活跃
      - 超过座位数时降级为 expired
    """
    lic = _read_license()
    if _activated_valid(lic):
        # 浮动模式：刷新心跳（保持座位活跃）
        if _QC_FLOATING_LICENSE and lic.get("floating_license"):
            _write_heartbeat(_machine_id())
        return ("activated", "")

    first_run_raw = lic.get("first_run")
    if not first_run_raw:
        # 首次运行，记录日期（带 HMAC 防篡改）
        today = datetime.date.today().isoformat()
        lic["first_run"] = {"date": today, "sig": _trial_sign(today)}
        _write_license(lic)
        return ("trial", TRIAL_DAYS)

    # 2026-08-18 M7 防绕过：新格式 {date,sig} 验签；旧格式（纯日期串）校验合法性后迁移补签。
    # 签名不匹配 / 日期非法 / 起点在未来 / 一年前开始却仍在试用 → 一律视为篡改，拒绝续期。
    if isinstance(first_run_raw, dict):
        first_run = first_run_raw.get("date", "")
        if not first_run or not _trial_verify(first_run, first_run_raw.get("sig", "")):
            return ("expired", 0)
    else:
        first_run = first_run_raw
        try:
            first_d = datetime.date.fromisoformat(first_run)
        except Exception:
            return ("expired", 0)  # 损坏：不再静默置今天白送试用
        _today = datetime.date.today()
        if first_d > _today or (_today - first_d).days > 366:
            return ("expired", 0)  # 回拨痕迹：未来起点 或 一年前开始却仍在试用期
        lic["first_run"] = {"date": first_run, "sig": _trial_sign(first_run)}
        _write_license(lic)

    # 计算已用天数
    try:
        first = datetime.date.fromisoformat(first_run)
    except Exception:
        return ("expired", 0)
    used = (datetime.date.today() - first).days
    if used < 0:
        return ("expired", 0)  # 时钟回拨保护：试用起点在未来

    if used >= TRIAL_DAYS:
        return ("expired", 0)
    else:
        return ("trial", TRIAL_DAYS - used)


# ---------- 激活码 ----------

# ---------- 浮动授权模式（P1 改造，2026-09-12）──────────────────────────
# 科室多机部署场景：一个激活码覆盖整个科室，通过共享目录计数控制并发座位数。
#
# 配置方式（环境变量）：
#   QC_FLOATING_LICENSE=true       — 启用浮动授权
#   QC_FLOATING_SEATS=5            — 最大并发座位数（默认 5）
#   QC_FLOATING_HEARTBEAT_DIR=...  — 共享目录路径（NFS/SMB/共享盘）
#
# 工作原理：
#   1. 验证激活码时，签名的验证对象从机器指纹改为部门标识（QC_FLOATING_DEPT_ID）
#   2. 每台机器在共享目录写心跳文件，超过座位数时拒绝激活
#   3. 心跳 30 分钟过期，机器离线自动释放座位
#   4. 无共享目录时退化为信任模式（仅验证签名，不检查座位）

_QC_FLOATING_LICENSE = os.environ.get("QC_FLOATING_LICENSE", "false").lower() in (
    "1", "true", "yes", "on")


def _floating_seats() -> int:
    try:
        return max(1, int(os.environ.get("QC_FLOATING_SEATS", "5")))
    except (ValueError, TypeError):
        return 5


def _floating_dept_id() -> str:
    """部门标识：优先用环境变量，否则从 license.dat 读取。"""
    d = os.environ.get("QC_FLOATING_DEPT_ID", "").strip()
    if d:
        return d
    lic = _read_license()
    return lic.get("department_id", "")


def _floating_heartbeat_dir() -> str:
    """共享目录路径（用于座位计数）。"""
    d = os.environ.get("QC_FLOATING_HEARTBEAT_DIR", "").strip()
    return d


def _hb_path(machine_token: str) -> str:
    """单台机器的心跳文件路径。"""
    return os.path.join(_floating_heartbeat_dir(), f"seat_{machine_token}.json")


def _write_heartbeat(machine_token: str) -> None:
    """写心跳文件（幂等），供座位计数使用。"""
    d = _floating_heartbeat_dir()
    if not d:
        return
    os.makedirs(d, exist_ok=True)
    hb = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    p = _hb_path(machine_token)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(hb, f)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def _count_active_seats() -> int:
    """统计共享目录中的活跃心跳文件数（30 分钟过期）。"""
    d = _floating_heartbeat_dir()
    if not d:
        return 0
    count = 0
    try:
        for fname in os.listdir(d):
            if not fname.startswith("seat_") or not fname.endswith(".json"):
                continue
            fp = os.path.join(d, fname)
            try:
                age = datetime.datetime.now() - datetime.datetime.fromtimestamp(
                    os.path.getmtime(fp))
                if age.days * 1440 + age.seconds / 60 < 30:  # 30 分钟内
                    count += 1
            except OSError:
                pass
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return count


def _check_floating_license() -> tuple[bool, str]:
    """检查浮动授权状态。返回 (ok, reason)。"""
    if not _QC_FLOATING_LICENSE:
        return (False, "")

    d = _floating_heartbeat_dir()
    if not d:
        return (True, "floating-trust-no-heartbeat")  # 无共享目录：信任模式

    seats = _count_active_seats()
    max_seats = _floating_seats()
    if seats < max_seats:
        return (True, f"floating:{seats}/{max_seats}")
    return (False, f"floating-exceeded:{seats}/{max_seats}")


def _floating_license_status() -> dict:
    """浮动授权状态摘要（供健康检查和 API 返回）。"""
    return {
        "floating_enabled": _QC_FLOATING_LICENSE,
        "max_seats": _floating_seats(),
        "active_seats": _count_active_seats() if _floating_heartbeat_dir() else 0,
        "heartbeat_dir": _floating_heartbeat_dir(),
        "department_id": _floating_dept_id(),
    }


def floating_license_active() -> bool:
    """是否启用了浮动授权模式（供其他模块判断）。"""
    return _QC_FLOATING_LICENSE


# ---------- 授权管理（2026-09-12 商业化）─────────────────────────────

def trial_days_remaining() -> int:
    """试用期剩余天数。已激活返回 -1，首次运行返回 TRIAL_DAYS。"""
    lic = _read_license()
    if _activated_valid(lic):
        return -1
    first_run_raw = lic.get("first_run")
    if not first_run_raw:
        return TRIAL_DAYS
    if isinstance(first_run_raw, dict):
        first_run = first_run_raw.get("date", "")
    else:
        first_run = first_run_raw
    try:
        first = datetime.date.fromisoformat(first_run)
    except Exception:
        return 0
    used = (datetime.date.today() - first).days
    return max(0, TRIAL_DAYS - used)


def trial_warning() -> str:
    """试用期到期提醒。返回空字符串表示无警告。"""
    days = trial_days_remaining()
    if days == -1:
        return ""
    if days == 0:
        return "试用期已结束，请输入激活码续费"
    if days <= 7:
        return f"试用期剩余 {days} 天，请尽快续费"
    if days <= 1:
        return f"试用期明天到期，仅剩 {days} 天"
    return ""


def deactivate(machine_id_str: str = "") -> dict:
    """吊销授权（管理员操作）。
    单机模式：删除 license.dat 中的激活信息。
    浮动模式：删除指定机器的心跳文件。
    machine_id_str: 空则吊销本机；非空则吊销指定机器（浮动模式）。
    """
    lic = _read_license()
    if not lic.get("activated"):
        return {"ok": True, "message": "当前未激活"}

    if _QC_FLOATING_LICENSE:
        # 浮动模式：删除指定机器的心跳
        if machine_id_str:
            d = _floating_heartbeat_dir()
            if d:
                p = _hb_path(machine_id_str)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                return {"ok": True, "message": f"已移除机器 {machine_id_str} 的心跳"}
            return {"ok": False, "message": "浮动模式未配置心跳目录"}

    # 单机模式：清除激活
    lic.pop("activated", None)
    lic.pop("activation_code", None)
    lic.pop("activated_at", None)
    lic.pop("machine_id", None)
    _write_license(lic)
    return {"ok": True, "message": "本机授权已吊销"}


def extend_license(days: int, reason: str = "") -> dict:
    """延长试用/授权天数（管理员操作）。
    days: 延长天数（正数）
    reason: 延长原因（备注用）
    """
    if days <= 0:
        return {"ok": False, "message": "天数必须大于 0"}
    lic = _read_license()
    # 将 first_run 前推 days 天，等于延长试用
    first_run_raw = lic.get("first_run")
    today = datetime.date.today()
    if not first_run_raw:
        new_date = (today - datetime.timedelta(days=days)).isoformat()
        lic["first_run"] = {"date": new_date, "sig": _trial_sign(new_date)}
    elif isinstance(first_run_raw, dict):
        first = first_run_raw.get("date", "")
        try:
            d = datetime.date.fromisoformat(first)
            new_date = (d - datetime.timedelta(days=days)).isoformat()
            lic["first_run"] = {"date": new_date, "sig": _trial_sign(new_date)}
        except Exception:
            new_date = (today - datetime.timedelta(days=days)).isoformat()
            lic["first_run"] = {"date": new_date, "sig": _trial_sign(new_date)}
    else:
        try:
            d = datetime.date.fromisoformat(first_run_raw)
            new_date = (d - datetime.timedelta(days=days)).isoformat()
        except Exception:
            new_date = (today - datetime.timedelta(days=days)).isoformat()
        lic["first_run"] = {"date": new_date, "sig": _trial_sign(new_date)}
    lic["extended_at"] = today.isoformat()
    lic["extended_days"] = lic.get("extended_days", 0) + days
    lic["extend_reason"] = reason
    _write_license(lic)
    return {"ok": True, "message": f"已延长 {days} 天"}


def get_license_info() -> dict:
    """完整授权信息摘要（供 API 返回）。"""
    lic = _read_license()
    status, data = check_trial()
    return {
        "status": status,
        "data": str(data) if data else "",
        "activated": bool(lic.get("activated")),
        "activated_at": lic.get("activated_at", ""),
        "machine_id": _machine_id(),
        "trial_days": TRIAL_DAYS,
        "trial_days_remaining": trial_days_remaining(),
        "trial_warning": trial_warning(),
        "floating": floating_license_active(),
        "floating_status": _floating_license_status(),
        "disclaimer_accepted": bool(lic.get("disclaimer_accepted")),
        "extended_days": lic.get("extended_days", 0),
        "extend_reason": lic.get("extend_reason", ""),
        "extended_at": lic.get("extended_at", ""),
    }


# ---------- 激活码（Ed25519 非对称，离线验证） ----------
# 机制：开发者用私钥对硬件标识签名生成激活码；客户端用内置公钥验签。
# 客户端仅持有公钥，没有私钥即无法伪造激活码。
# 浮动模式：签名对象改为部门标识，一个激活码覆盖整个科室。
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAwDFmQmjTcrHbayI4kjiirpuj+1DtpAAh3H33Gvc5VoQ=
-----END PUBLIC KEY-----
"""


def _stable_hw_id():
    """稳定的硬件标识（激活码绑定对象）。重装系统/换网卡尽量不变。

    Windows 用 MachineGuid；macOS 用硬件 UUID；Linux 用 /etc/machine-id；
    兜底退化为 hostname（仍优于 MAC）。
    """
    sysname = platform.system()
    try:
        if sysname == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                return winreg.QueryValueEx(k, "MachineGuid")[0].strip()
        elif sysname == "Darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]).decode()
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[3].strip()
        else:
            with open("/etc/machine-id") as f:
                return f.read().strip()
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    import socket
    return socket.gethostname().strip() or "UNKNOWN"


def _machine_id():
    """展示用短机器码（供激活对话框显示、客服核对）。"""
    return hashlib.md5(_stable_hw_id().encode("utf-8")).hexdigest()[:12]


def _activation_target() -> bytes:
    """激活码验签目标：浮动模式用部门标识，单机模式用机器指纹。"""
    if _QC_FLOATING_LICENSE:
        dept = _floating_dept_id()
        if dept:
            return dept.encode("utf-8")
    return _machine_id().encode("utf-8")


def validate_activation_code(code):
    """用内置公钥验证激活码。

    单机模式：验签对象为本机 machine_id（12 位短码）。
    浮动模式（QC_FLOATING_LICENSE=true）：验签对象为部门标识。
    """
    if not code:
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        public_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)
    except Exception:
        return False
    raw = code.strip().upper().replace("-", "").replace(" ", "")
    if len(raw) % 8 != 0:
        raw += "=" * (8 - len(raw) % 8)
    try:
        sig = base64.b32decode(raw)
    except Exception:
        return False
    try:
        public_key.verify(sig, _activation_target())
        return True
    except Exception:
        return False


def activate(code):
    """尝试用输入的激活码激活软件。
    浮动模式：检查座位数上限，写心跳。单机模式：绑定机器指纹。
    返回 True/False。
    """
    if validate_activation_code(code):
        # 浮动模式：检查座位数
        if _QC_FLOATING_LICENSE:
            ok, reason = _check_floating_license()
            if not ok:
                return False  # 座位已满

        lic = _read_license()
        lic["activated"] = True
        lic["activation_code"] = code
        lic["activated_at"] = datetime.date.today().isoformat()
        if _QC_FLOATING_LICENSE:
            # 浮动模式：存部门标识，不绑定单台机器
            lic["department_id"] = _floating_dept_id()
            lic["floating_license"] = True
        else:
            lic["machine_id"] = _machine_id()  # 绑定机器指纹：防复制已激活文件一码多机
        _write_license(lic)

        # 浮动模式：写心跳
        if _QC_FLOATING_LICENSE:
            _write_heartbeat(_machine_id())
        return True
    return False


def show_activation_dialog(parent):
    """显示激活码输入对话框。返回 True（激活成功）或 False（退出应用）。"""
    result = [False]

    win = tk.Toplevel(parent)
    win.title("软件激活")
    win.geometry("480x300")
    win.configure(bg="#FFFFFF")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    win.lift()
    try:
        win.attributes("-topmost", True)
        win.after(900, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

    # 标题与说明
    tk.Label(win, text="星衍放射质控软件", font=("PingFang SC", 16, "bold"),
             bg="#FFFFFF", fg="#0B8A9E").pack(pady=(20, 4))
    tk.Label(win, text="免费试用期已结束，请输入激活码", font=("PingFang SC", 10),
             bg="#FFFFFF", fg="#5A6B7A").pack(pady=(0, 18))

    # 机器 ID 显示（方便客服核验）—— 与验签对象 machine_id 一致
    mid = _machine_id()
    tk.Label(win, text=f"机器识别码（发卡用）: {mid}", font=("PingFang SC", 9),
             bg="#FFFFFF", fg="#9AA0A6").pack()

    # 激活码输入
    code_var = tk.StringVar()
    entry = ttk.Entry(win, textvariable=code_var, font=("PingFang SC", 12),
                      width=24, justify="center")
    entry.pack(pady=(8, 4))
    entry.focus_set()

    err_label = tk.Label(win, text="", font=("PingFang SC", 9),
                         bg="#FFFFFF", fg="#C0392B")
    err_label.pack()

    def do_activate():
        code = code_var.get().strip()
        if not code:
            err_label.configure(text="请输入激活码")
            return
        if activate(code):
            result[0] = True
            win.destroy()
        else:
            err_label.configure(text="激活码无效，请检查后重试")

    def do_exit():
        win.destroy()

    # 按钮
    btn_frm = tk.Frame(win, bg="#FFFFFF")
    btn_frm.pack(pady=(12, 0))
    tk.Button(btn_frm, text="  激活  ", command=do_activate,
              bg="#0B8A9E", fg="white", font=("PingFang SC", 11, "bold"),
              relief="flat", padx=20, pady=6, cursor="hand2").pack(side="left", padx=10)
    tk.Button(btn_frm, text="  退出程序  ", command=do_exit,
              bg="#E8EDF2", fg="#1A2332", font=("PingFang SC", 11),
              relief="flat", padx=20, pady=6, cursor="hand2").pack(side="left", padx=10)

    # Enter 键触发激活
    entry.bind("<Return>", lambda e: do_activate())

    parent.wait_window(win)
    return result[0]
