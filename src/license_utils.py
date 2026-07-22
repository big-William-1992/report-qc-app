"""
license_utils.py
星衍放射质控软件 · 授权管理模块
功能：免责声明 → 免费试用期（3个月）→ 激活码验证
"""

import os
import json
import hashlib
import hmac
import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import sys

# 许可证数据文件（支持 PyInstaller 打包后的路径）
if getattr(sys, 'frozen', False):
    # 打包后：exe 所在目录下的 assets/license.dat
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    # 源码运行
    _BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_LICENSE_FILE = os.path.join(_BASE_DIR, "assets", "license.dat")

# 试用期（天）
TRIAL_DAYS = 90

# 激活码 HMAC 密钥（仅用于验证，生产环境应换用非对称签名）
_SECRET_KEY = b"XingYan-Radiology-QC-2026-SecretKey!@#"

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
        pass
    return {}


def _write_license(data):
    """写许可证文件。"""
    os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
    with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


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

def check_trial():
    """检查试用期状态。
    返回: ("ok", "") - 可用
          ("trial", 剩余天数) - 试用中
          ("expired", "") - 已过期
          ("activated", "") - 已激活
    """
    lic = _read_license()
    if lic.get("activated"):
        return ("activated", "")

    first_run = lic.get("first_run")
    if not first_run:
        # 首次运行，记录日期
        today = datetime.date.today().isoformat()
        lic["first_run"] = today
        _write_license(lic)
        return ("trial", TRIAL_DAYS)

    # 计算已用天数
    try:
        first = datetime.date.fromisoformat(first_run)
    except Exception:
        first = datetime.date.today()
    delta = datetime.date.today() - first
    used = delta.days

    if used >= TRIAL_DAYS:
        return ("expired", 0)
    else:
        return ("trial", TRIAL_DAYS - used)


# ---------- 激活码 ----------

def _machine_id():
    """生成一个相对稳定的机器标识（用于激活码绑定）。"""
    # 尝试用 hostname + MAC 地址（简化版）
    try:
        import socket
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    try:
        import uuid
        mac = uuid.getnode()
        mac_hex = format(mac, "x")
    except Exception:
        mac_hex = "unknown"
    raw = f"{host}::{mac_hex}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def generate_activation_code(machine_id=None):
    """生成激活码（管理员/开发者用）。
    返回格式: XXXXX-XXXXX-XXXXX
    """
    if not machine_id:
        machine_id = _machine_id()
    msg = machine_id.encode("utf-8")
    sig = hmac.new(_SECRET_KEY, msg, hashlib.sha256).hexdigest()[:15].upper()
    # 分成三组 X5-X5-X5
    return f"{sig[:5]}-{sig[5:10]}-{sig[10:15]}"


def validate_activation_code(code):
    """验证用户输入的激活码是否有效。
    返回 True/False。（支持手动绕过的万能码）
    """
    if not code:
        return False
    code = code.strip().upper()

    # 万能激活码（仅开发调试用，发布时移除）
    MASTER_CODE = "XING-YAN-QC-2026"
    if code == MASTER_CODE:
        return True

    # 标准验证：根据本机 ID 校验
    expected = generate_activation_code()
    return code == expected


def activate(code):
    """尝试用输入的激活码激活软件。
    返回 True/False。
    """
    if validate_activation_code(code):
        lic = _read_license()
        lic["activated"] = True
        lic["activation_code"] = code
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

    # 标题与说明
    tk.Label(win, text="星衍放射质控软件", font=("PingFang SC", 16, "bold"),
             bg="#FFFFFF", fg="#0B8A9E").pack(pady=(20, 4))
    tk.Label(win, text="免费试用期已结束，请输入激活码", font=("PingFang SC", 10),
             bg="#FFFFFF", fg="#5A6B7A").pack(pady=(0, 18))

    # 机器 ID 显示（方便客服核验）
    mid = _machine_id()
    tk.Label(win, text=f"机器识别码: {mid}", font=("PingFang SC", 9),
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
