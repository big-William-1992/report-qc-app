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
from tkinter import ttk, messagebox
import sys
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
    """激活状态真实性校验（2026-08-18 防绕过）：
    ① 激活时绑定的机器指纹必须与当前机器一致（防复制 license.dat 一码多机）；
    ② activation_code 必须仍能通过 Ed25519 验签（防手改 {activated:true} 伪造）。"""
    if not lic.get("activated"):
        return False
    if lic.get("machine_id") != _machine_id():
        return False
    code = (lic.get("activation_code") or "").strip()
    return bool(code) and validate_activation_code(code)


def check_trial():
    """检查试用期状态。
    返回: ("ok", "") - 可用
          ("trial", 剩余天数) - 试用中
          ("expired", "") - 已过期
          ("activated", "") - 已激活
    """
    lic = _read_license()
    if _activated_valid(lic):
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

# ---------- 激活码（Ed25519 非对称，离线验证） ----------
# 机制：开发者用私钥对硬件标识签名生成激活码；客户端用内置公钥验签。
# 客户端仅持有公钥，没有私钥即无法伪造激活码。
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


def validate_activation_code(code):
    """用内置公钥验证激活码（须为『本机机器识别码 machine_id』的有效 Ed25519 签名）。

    验签对象必须与发卡工具（gen_activation_code.py）及 Web 版（server/license_web.py）
    保持一致，否则同一台机器两版激活码互不通用（历史 bug：此文件此前绑定完整硬件标识
    _stable_hw_id，而发卡/Web 版绑定 12 位短码 machine_id）。
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
        public_key.verify(sig, _machine_id().encode("utf-8"))
        return True
    except Exception:
        return False


def activate(code):
    """尝试用输入的激活码激活软件。
    返回 True/False。
    """
    if validate_activation_code(code):
        lic = _read_license()
        lic["activated"] = True
        lic["activation_code"] = code
        lic["machine_id"] = _machine_id()   # 绑定机器指纹：防复制已激活文件一码多机（2026-08-18）
        lic["activated_at"] = datetime.date.today().isoformat()
        _write_license(lic)
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
