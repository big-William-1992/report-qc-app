"""
report_qc_app/src/app.py
星衍放射质控软件 · 桌面 GUI（纯标准库 Tkinter，零依赖）

视觉风格：专业医疗浅色主题（青蓝主色 / 卡片化 / 统一字体 / 环形饼图 + 双轴趋势图）

标签页：
  1) 报告质控：元信息录入 + 报告粘贴/导入 + 运行质控 + 错误高亮 + 多维评分 + 存入样本库
  2) 质控驾驶舱：错误类型环形图、每日趋势图（报告数+平均准确性）、样本库表格
  3) RIS 直连：院内 RIS 库配置与拉取
"""

import os
import sys
import csv
import json
import platform
import datetime
import subprocess
import difflib
import hashlib
import time
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import RuleEngine, score, error_type_counts
import engine
import samplelib
import ris
import license_utils
import version
import log_utils
import accounts
import update_check
import auto_updater
import ocr_provider
import uia_provider

# 反馈通道：GitHub Issues（公开仓库任何人可提），可按需改为飞书/腾讯问卷链接
FEEDBACK_URL = "https://github.com/big-William-1992/report-qc-app/issues"
FEEDBACK_CONTACT = "抖音 / B站：蜗牛学长（医学AI）"


# ----------------------------- 主题 -----------------------------
if platform.system() == "Windows":
    FAMILY, MONO = "Microsoft YaHei", "Consolas"
elif platform.system() == "Darwin":
    FAMILY, MONO = "PingFang SC", "Menlo"
else:
    FAMILY, MONO = "Noto Sans CJK SC", "DejaVu Sans Mono"


THEME = {
    # ponytail: v2 palette — deeper background for contrast, richer teal for medical feel.
    # All colors are WCAG AA compliant against their intended backgrounds.
    "bg":        "#E8EDF2",   # 应用背景（暖灰蓝，比旧版稍深降低眼疲劳）
    "panel":     "#FFFFFF",   # 卡片 / 面板
    "panel_alt": "#F0F4F8",   # 次级面板
    "primary":   "#0B8A9E",   # 主色 医疗深青（旧 #0E7C9B，饱和度更高）
    "primary_d": "#076B7C",   # 主色深（hover / 按下）
    "primary_l": "#E5F4F7",   # 主色浅（选中行、淡背景）
    "accent":    "#12A0B8",   # 强调亮青
    "text":      "#1A2332",   # 主文本（旧 #24323C，更深提高对比度）
    "text_dim":  "#5A6B7A",   # 次要文本（旧 #6B7A86，更深可读）
    "border":    "#C8D4DF",   # 边框（旧 #D6DEE6，更深明确边界）
    "header_bg": "#0B8A9E",   # 顶栏
    "header_fg": "#FFFFFF",
    "ok":        "#0D8A5E",   # 正常/通过（旧 #1E8E5A，更深）
    # 严重度配色：红=严重(high) / 橙=警告(medium) / 蓝=提示(low)
    "sev_high":  "#D32F2F",   # 严重错误——红
    "sev_med":   "#E08A00",   # 警告——橙
    "sev_low":   "#1976D2",   # 提示——蓝
    "sev_high_bg": "#FBE3E3", # 严重行/块背景（清晰红染）
    "sev_med_bg":  "#FCEFD6", # 警告行/块背景（清晰橙染）
    "sev_low_bg":  "#E4F1FB", # 提示行/块背景（清晰蓝染）
    "hl_high":   "#F8C9C9",   # 内文高亮背景——红
    "hl_med":    "#FBE0A8",   # 内文高亮背景——橙
    "hl_low":    "#CFE6FB",   # 内文高亮背景——蓝
    # 图表调色（协调、低饱和医疗感）
    "chart": ["#0B8A9E", "#12A0B8", "#1E8E5A", "#C8780E", "#C0392B", "#7A5CC9", "#3C8DBC"],
}

SEV_COLOR = {"high": THEME["sev_high"], "medium": THEME["sev_med"], "low": THEME["sev_low"]}
SEV_BG = {"high": THEME["sev_high_bg"], "medium": THEME["sev_med_bg"], "low": THEME["sev_low_bg"]}
SEV_CN = {"high": "严重", "medium": "警告", "low": "提示"}
SEV_TAG = {"high": "hl_high", "medium": "hl_med", "low": "hl_low"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def F(family, size, weight="normal"):
    return (family, size, weight)


def apply_theme(root):
    """配置 ttk 主题（基于 clam，支持更多定制）。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    s = THEME

    # ponytail: v2 styling — more padding, bolder fonts, cleaner separators.
    style.configure("TFrame", background=s["bg"])
    style.configure("TLabel", background=s["bg"], foreground=s["text"], font=F(FAMILY, 10))
    style.configure("TNotebook", background=s["bg"], borderwidth=0)
    style.configure("TNotebook.Tab",
                    background=s["panel_alt"], foreground=s["text_dim"],
                    padding=[20, 10], font=F(FAMILY, 10, "bold"), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", s["primary"]), ("!selected", s["panel_alt"])],
              foreground=[("selected", "#FFFFFF"), ("!selected", s["text_dim"])])

    style.configure("TLabelFrame", background=s["panel"], foreground=s["primary"],
                    borderwidth=0, relief="flat", padding=14)
    style.configure("TLabelFrame.Label", background=s["panel"], foreground=s["primary"],
                    font=F(FAMILY, 10, "bold"))

    style.configure("TEntry", fieldbackground=s["panel"], foreground=s["text"],
                    bordercolor=s["border"], relief="solid", padding=6, font=F(FAMILY, 10))
    style.configure("TCombobox", fieldbackground=s["panel"], foreground=s["text"],
                    padding=6, font=F(FAMILY, 10))
    style.map("TCombobox", fieldbackground=[("readonly", s["panel"])])

    style.configure("Treeview", background=s["panel"], foreground=s["text"],
                    fieldbackground=s["panel"], bordercolor=s["border"], rowheight=28,
                    font=F(FAMILY, 10))
    style.configure("Treeview.Heading", background=s["panel_alt"], foreground=s["primary"],
                    font=F(FAMILY, 10, "bold"), relief="flat", padding=[8, 6])
    style.map("Treeview", background=[("selected", s["primary"])],
              foreground=[("selected", "#FFFFFF")])

    # v2: buttons with more padding and subtle shadow feel
    style.configure("TButton", background=s["panel_alt"], foreground=s["text"],
                    bordercolor=s["border"], relief="solid", padding=[16, 8],
                    font=F(FAMILY, 10, "bold"), borderwidth=1)
    style.map("TButton",
              background=[("active", s["border"]), ("pressed", s["primary_d"])],
              foreground=[("pressed", "#FFFFFF")],
              relief=[("pressed", "sunken")])

    style.configure("Primary.TButton", background=s["primary"], foreground="#FFFFFF",
                    borderwidth=0, padding=[20, 9], font=F(FAMILY, 10, "bold"))
    style.map("Primary.TButton",
              background=[("active", s["primary_d"]), ("pressed", s["primary_d"])])

    # 顶栏「帮助」按钮：与 TButton 同几何（同 padding → 同高度），仅改配色以突出"新增"
    style.configure("Help.TButton", background="#EF9F27", foreground="#1A2332",
                    borderwidth=0, padding=[16, 8], font=F(FAMILY, 10, "bold"))
    style.map("Help.TButton",
              background=[("active", "#BA7517"), ("pressed", "#BA7517")])

    style.configure("TCheckbutton", background=s["panel"], foreground=s["text"],
                    font=F(FAMILY, 10))
    style.map("TCheckbutton", background=[("active", s["panel"])])

    # v2: separator styling
    style.configure("TSeparator", background=s["border"])

    return style


def show_error(msg):
    messagebox.showerror("错误", msg)


class ScrollableFrame(tk.Frame):
    """纵向滚动容器：在跨平台（含 macOS 触控板/滚轮）下原生可用。
    子控件放入 .inner。内容超出可视区时自动出现纵向滚动条。

    注意：部分桌面环境（如 KDE/X11）下鼠标滚轮不会自动转发到 Canvas，
    此处对 Linux/X11 显式绑定鼠标滚轮事件作为兜底。
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=THEME["bg"])
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 背景点击空白处可拖动滚动（macOS 触控板拖拽）
        self.canvas.bind("<2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y))
        # Linux/X11 鼠标滚轮兜底
        if platform.system() == "Linux":
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
            self.canvas.bind_all("<Button-4>", self._on_mousewheel, add="+")
            self.canvas.bind_all("<Button-5>", self._on_mousewheel, add="+")

    def _on_inner_configure(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, _=None):
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width())

    def _on_mousewheel(self, event):
        # 仅当指针位于本容器上方时响应
        try:
            x = self.canvas.winfo_rootx()
            y = self.canvas.winfo_rooty()
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            if not (x <= event.x_root <= x + w and y <= event.y_root <= y + h):
                return
        except Exception:
            return
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ----------------------------- 主应用 -----------------------------
# 屏幕区域 OCR 策略（v2.3 起）：**不做任何后台轮询**。
# 识别只在两个入口触发，每次触发只跑一遍：
#   1) 用户按下自定义快捷键（Windows 全局热键，PACS 聚焦时可用）；
#   2) 点击「🔍 识别并质控」按钮。
# 触发后逐区域截图 → OCR → 回填 → 质控 → 与剪贴板交叉核对身份。
# （历史上的 10s 无条件轮询 / 3s 变化检测 + 60s 兜底方案已按用户要求移除，
#   避免后台持续截图/推理的资源占用与干扰。）

# 多区域 OCR 角色：basic=基础信息（结构化回填 影像号/姓名/性别/年龄/部位）
# findings=影像描述 / impression=影像结论（可编辑自由文本回填）
OCR_ROLE_CN = {"basic": "基础信息", "findings": "影像描述", "impression": "影像结论"}

# ---------------- 「识别并质控」全局快捷键 ----------------
# Windows 用 RegisterHotKey 注册系统级热键：用户在 PACS 等其他窗口聚焦时按下
# 也能触发（这正是主用法——报告在 PACS 里写完，直接按快捷键质控）。
# 非 Windows / 注册失败时退化为应用内 Tk 绑定（本软件聚焦时可用）。
HOTKEY_ID = 0xA11                       # RegisterHotKey 自定义 id
HOTKEY_MOD_FLAGS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
_HOTKEY_MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Win_L", "Win_R",
    "Caps_Lock", "Num_Lock", "ISO_Level3_Shift",
}
# Tk keysym → Windows 虚拟键码（字母/数字按 ord 处理，此表补常用特殊键）
_HOTKEY_VK_SPECIAL = {
    "space": 0x20, "return": 0x0D, "tab": 0x09,
    "prior": 0x21, "next": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "pause": 0x13,
    "grave": 0xC0, "minus": 0xBD, "equal": 0xBB,
    "bracketleft": 0xDB, "bracketright": 0xDD, "backslash": 0xDC,
    "semicolon": 0xBA, "apostrophe": 0xDE, "comma": 0xBC,
    "period": 0xBE, "slash": 0xBF,
}


def hotkey_vk(key):
    """Tk keysym → Windows 虚拟键码；不支持的键返回 None（则只做窗口内绑定）。"""
    k = (key or "")
    if len(k) == 1 and (k.isalpha() and k.isascii() or k.isdigit()):
        return ord(k.upper())
    kl = k.lower()
    if kl.startswith("f") and kl[1:].isdigit() and 1 <= int(kl[1:]) <= 24:
        return 0x70 + int(kl[1:]) - 1          # F1=0x70
    if kl.startswith("kp_") and kl[3:].isdigit():
        return 0x60 + int(kl[3:])              # 小键盘 0-9
    return _HOTKEY_VK_SPECIAL.get(kl)


def hotkey_display(hk):
    """快捷键配置 → 人类可读文本，如 Ctrl+Alt+F9；未设置返回「未设置」。"""
    if not hk or not hk.get("key"):
        return "未设置"
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    mods = hk.get("mods") or []
    parts = [names[m] for m in ("ctrl", "alt", "shift", "win") if m in mods]
    k = hk["key"]
    parts.append(k.upper() if len(k) == 1 else k)
    return "+".join(parts)


def hotkey_tk_sequence(hk):
    """快捷键配置 → Tk 绑定序列，如 <Control-Alt-F9>（应用内兜底绑定用）。"""
    if not hk or not hk.get("key"):
        return None
    mods = hk.get("mods") or []
    seq = ""
    if "ctrl" in mods:
        seq += "Control-"
    if "alt" in mods:
        seq += "Alt-"
    if "shift" in mods:
        seq += "Shift-"
    key = hk["key"]
    if len(key) == 1 and key.isalpha():
        key = key.upper() if "shift" in mods else key.lower()
    return f"<{seq}{key}>"


class ReportQcApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"星衍放射质控软件  v{version.APP_VERSION}")
        # ponytail: force visible position on multi-monitor setups where Tk
        # may default to a negative Y. Update these if your screen is different.
        self.geometry("1180x760+100+50")
        self.minsize(1000, 720)
        self.engine = RuleEngine()
        self.current_findings = []
        self.current_scores = {}
        self.anon_var = tk.BooleanVar(value=False)
        # 当前登录账号（工号）：质控责任到人，保存样本时写入 user_id
        self.current_user = ""
        # 误报反馈闭环：会话级忽略名单（key 由 _ig_key 生成），可从配置文件持久化
        self.ignored = set(self.engine.rules_config.get("ignores", []))
        # 监听内容去重
        self._last_alert_clip = ""
        self._last_alert_ts = 0.0

        # 屏幕区域 OCR（识别患者信息 / 影像描述 / 影像结论，仅快捷键/按钮触发）
        self.ocr_cfg = self._load_ocr_config()
        # 多区域模型：role -> (x,y,w,h)，角色 basic/findings/impression
        self.ocr_regions = {k: tuple(v) for k, v in (self.ocr_cfg.get("regions") or {}).items()
                            if v}
        # 兼容旧配置：单 region 视为 basic（基础信息区）
        old = self.ocr_cfg.get("region")
        if old and "basic" not in self.ocr_regions:
            self.ocr_regions["basic"] = tuple(old)
        self.ocr_meta = {}            # 最近一次识别出的屏幕侧元信息
        self._ocr_alert_sig = None   # 身份不符提示去重
        self._ocr_alert_cooldown = 0.0
        self.ocr_status = tk.StringVar(value="● 待触发（按快捷键或点『识别并质控』）")
        self.ocr_regions_status = tk.StringVar(value="")
        self._ocr_status("off", "● 待触发（按快捷键或点『识别并质控』）")
        self._update_region_status()
        # Windows UI Automation 采集提供器（读前景 PACS 窗口控件文本，无滚动漂移）
        self.uia = uia_provider.UIAProvider()

        # 「识别并质控」采集方式（快捷键触发时按此分派；显式按钮直连各自方法不受影响）
        # auto=UIA 优先否则 OCR；uia=仅 UIA；ocr=仅 OCR 三区；ask=每次弹菜单选择
        self.capture_mode = self._norm_capture_mode(self.ocr_cfg.get("capture_mode"))

        # 「识别并质控」快捷键（任意组合，持久化在 ocr_config.json）
        self.qc_hotkey = self.ocr_cfg.get("hotkey") or None   # {"mods":[...], "key":"F9"}
        self.qc_hotkey_status = tk.StringVar(value=f"快捷键：{hotkey_display(self.qc_hotkey)}")
        self._hotkey_thread_id = None      # Windows 全局热键消息循环线程 id
        self._hotkey_tk_seq = None         # 应用内 Tk 绑定序列（兜底）
        self._pynput_listener = None       # 非 Windows 全局键盘监听（后台快捷键）
        self._hotkey_busy = False          # 防抖：质控执行中忽略重复触发
        self._last_hotkey_ts = 0.0         # 防抖时间戳：忽略 0.5s 内的重复触发（含 Tk 与全局监听同时触发）
        if self.qc_hotkey:
            self.after(400, self._register_qc_hotkey)   # 主循环起来后再注册

        self.style = apply_theme(self)

        # 菜单栏（macOS 显示在屏幕顶部，Windows 显示在窗口标题栏下方）
        self._build_menubar()

        # ponytail: v2 — configure root background + header font
        self.configure(bg=THEME["bg"])

        self._build_header()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # 常驻状态条：监听指示灯 + 本次会话统计（全局可见，独立于页签）
        self.session_hits = 0
        self._session_last_ts = ""
        self._out_ranges = {}
        self._txt_marks = {}
        sb = ttk.Frame(self)
        sb.pack(side="bottom", fill="x", padx=12, pady=(0, 6))
        self.monitor_dot = tk.Canvas(sb, width=12, height=12, bd=0,
                                     highlightthickness=0, bg=THEME["bg"])
        self.monitor_dot.pack(side="left", padx=(0, 4))
        self._dot_id = self.monitor_dot.create_oval(1, 1, 11, 11, fill="#9AA0A6", outline="")
        ttk.Label(sb, text="监听", foreground=THEME["text_dim"]).pack(side="left", padx=(0, 8))
        self.session_var = tk.StringVar(value="未监听 · 本次会话命中 0 份")
        ttk.Label(sb, textvariable=self.session_var, foreground=THEME["primary"]).pack(side="left")
        # 当前登录账号（工号）状态：质控责任到人的落点
        self.user_var = tk.StringVar(value="👤 未登录")
        ttk.Label(sb, textvariable=self.user_var, foreground=THEME["text_dim"]).pack(side="left", padx=(12, 0))
        # 授权入口：主动激活 / 重新激活（默认显示"激活"，激活成功后变"重新激活"）
        self._activate_btn = ttk.Button(sb, text="激活", width=9,
                                        command=self._open_activation)
        self._activate_btn.pack(side="right", padx=(10, 0))
        self._update_status_bar()

        self.tab_qc = ttk.Frame(self.notebook)
        self.tab_dash = ttk.Frame(self.notebook)
        self.tab_ris = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_qc, text="📋  报告质控")
        self.notebook.add(self.tab_dash, text="📊  质控驾驶舱")
        self.notebook.add(self.tab_ris, text="🔗  RIS 直连")

        self._build_qc_tab()
        self._build_dash_tab()
        self._build_ris_tab()
        self._refresh_samples()

    # -------------------- 顶栏 --------------------
    def _build_header(self):
        s = THEME
        # ponytail: v2 — subtle shadow effect with a bottom border line
        bar = tk.Frame(self, bg=s["header_bg"], height=56)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        # Shadow line at bottom
        shadow = tk.Frame(self, bg=s["primary_d"], height=2)
        shadow.pack(fill="x")

        tk.Label(bar, text="星衍放射质控软件", bg=s["header_bg"], fg=s["header_fg"],
                 font=F(FAMILY, 17, "bold"), padx=16).pack(side="left", anchor="center")
        tk.Label(bar, text="第一代 · NER + 知识图谱 + 规则引擎（R1–R10）",
                 bg=s["header_bg"], fg="#B8E4EE", font=F(FAMILY, 10)).pack(side="left", padx=10, anchor="center")
        tk.Label(bar, text=f"v{version.APP_VERSION}", bg=s["header_bg"], fg="#B8E4EE",
                 font=F(FAMILY, 10, "bold")).pack(side="right", padx=14, anchor="center")
        # 窗口内可见入口：帮助菜单（检查更新 / 问题反馈 / 导出诊断包 / 关于）。
        # 解决 macOS 上 Tk 菜单栏显示在屏幕最顶部、用户不易发现的问题。
        # 尺寸与「规则维护」按钮保持一致（同 width、同 TButton 几何）。
        self._help_btn = ttk.Button(bar, text="❓ 帮助", style="Help.TButton", width=10,
                                    command=self._post_header_help_menu)
        self._help_btn.pack(side="right", padx=4, anchor="center")
        ttk.Button(bar, text="⚙ 规则维护", width=10,
                   command=self._open_rules_editor).pack(side="right", padx=8, anchor="center")

    # -------------------- 顶栏帮助按钮（窗口内可见入口） --------------------
    def _post_header_help_menu(self):
        """顶栏「❓ 帮助」按钮：在窗口内弹出下拉菜单，提供与系统菜单栏一致的功能入口。"""
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="输入激活码…", command=self._open_activation)
        m.add_separator()
        m.add_command(label="检查更新…", command=self._check_update_manual)
        m.add_command(label="问题反馈…", command=self._open_feedback)
        m.add_command(label="导出诊断包…", command=lambda: self._export_diagnostic(self))
        m.add_separator()
        m.add_command(label="关于星衍放射质控软件", command=self._show_about)
        try:
            x = self._help_btn.winfo_rootx()
            y = self._help_btn.winfo_rooty() + self._help_btn.winfo_height()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    # -------------------- 菜单栏 / 授权入口 --------------------
    def _build_menubar(self):
        """构建顶部菜单栏，提供账号入口、激活入口与关于信息。"""
        menubar = tk.Menu(self)
        # 账号菜单：登录/切换/创建/退出，使质控责任到人
        acct_menu = tk.Menu(menubar, tearoff=0)
        self._acct_menu = acct_menu
        acct_menu.add_command(label="当前账号", command=self._show_account_info)
        acct_menu.add_separator()
        acct_menu.add_command(label="切换账号…", command=self._switch_account)
        acct_menu.add_command(label="创建账号…", command=lambda: self._create_account_dialog(first=False))
        acct_menu.add_separator()
        acct_menu.add_command(label="退出登录并切换…", command=self._logout_and_switch)
        menubar.add_cascade(label="账号", menu=acct_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="输入激活码…", command=self._open_activation)
        help_menu.add_separator()
        help_menu.add_command(label="检查更新…", command=self._check_update_manual)
        help_menu.add_command(label="问题反馈…", command=self._open_feedback)
        help_menu.add_command(label="导出诊断包…", command=self._export_diagnostic)
        help_menu.add_separator()
        help_menu.add_command(label="关于星衍放射质控软件", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    def _open_activation(self):
        """主动激活 / 重新激活：弹出激活码输入框，成功则刷新授权状态。"""
        if license_utils.show_activation_dialog(self):
            self.session_var.set("已激活 · 未监听")
            self._activate_btn.configure(text="重新激活")
            messagebox.showinfo("激活成功",
                                "软件已成功激活，感谢使用「星衍放射质控软件」！")

    def _show_about(self):
        """关于对话框。"""
        ver = getattr(version, "APP_VERSION", "2.0")
        bt = getattr(version, "BUILD_TIME", "") or "本地开发版"
        commit = getattr(version, "COMMIT", "dev")
        info = (f"星衍放射质控软件  v{ver}\n"
                f"构建时间：{bt}\n"
                f"版本标识：{commit}\n\n"
                "第一代 · NER + 知识图谱 + 规则引擎（R1–R10）\n\n"
                "本软件提供的报告质控结果仅供参考，不构成最终诊断依据；\n"
                "所有结果均需由具备资质的放射科医师审核确认。\n\n"
                "开发者：谢君\n"
                "联系方式：17380009231")
        messagebox.showinfo("关于", info)

    # -------------------- 账号：登录 / 创建 / 切换 --------------------
    def _update_user_status(self):
        """刷新状态栏与菜单的账号显示。"""
        u = (self.current_user or "").strip()
        if u:
            name = accounts.get_name(u)
            label = f"👤 {u}" + (f"（{name}）" if name else "")
            self.user_var.set(label)
            menu_label = f"当前账号：{u}"
        else:
            self.user_var.set("👤 未登录")
            menu_label = "当前账号：未登录"
        try:
            self._acct_menu.entryconfigure(0, label=menu_label)
        except Exception:
            pass

    def _show_account_info(self):
        u = (self.current_user or "").strip()
        if not u:
            messagebox.showinfo("账号", "当前未登录。可在「账号」菜单选择「切换账号」登录。")
            return
        name = accounts.get_name(u)
        messagebox.showinfo("当前账号",
                            f"工号：{u}\n姓名：{name or '（未填写）'}\n\n"
                            f"此后保存的质控样本将记录为该工号，便于责任追溯。")

    def _require_login(self):
        """启动登录关口：无账号→强制创建首个账号；有账号→登录。返回工号或 None。"""
        if accounts.count_accounts() == 0:
            ok, emp_id = self._create_account_dialog(first=True)
            if not ok:
                return None
            self.current_user = emp_id
        else:
            ok, emp_id = self._login_dialog()
            if not ok:
                return None
            self.current_user = emp_id
        accounts.set_session(self.current_user)
        self._update_user_status()
        return self.current_user

    def _login_dialog(self, prefill: str = ""):
        """登录对话框。返回 (ok, emp_id)。可在「创建账号」按钮中嵌套创建流程。"""
        res = {"ok": False, "emp_id": ""}
        win = tk.Toplevel(self)
        win.title("登录")
        win.geometry("320x220")
        win.configure(bg=THEME["panel"])
        win.transient(self)
        win.grab_set()
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(win, text="登录星衍放射质控软件", bg=THEME["panel"],
                 fg=THEME["text"], font=F(FAMILY, 13, "bold")).pack(pady=(14, 10))

        f = ttk.Frame(win)
        f.pack(padx=18, fill="x")
        ttk.Label(f, text="工号").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(f, text="密码").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        eid = ttk.Entry(f, width=22)
        epw = ttk.Entry(f, width=22, show="*")
        eid.grid(row=0, column=1, pady=4)
        epw.grid(row=1, column=1, pady=4)
        if prefill:
            eid.insert(0, prefill)
        eid.focus_set()

        err = ttk.Label(win, text="", foreground="#C62828")
        err.pack(pady=(2, 0))

        def do_login():
            emp_id = eid.get().strip()
            if not accounts.verify_account(emp_id, epw.get()):
                err.config(text="工号或密码错误")
                return
            res["ok"] = True
            res["emp_id"] = emp_id
            win.destroy()

        def do_create():
            win.destroy()
            ok, new_id = self._create_account_dialog(first=False)
            if ok:
                res["ok"] = True
                res["emp_id"] = new_id

        bar = ttk.Frame(win)
        bar.pack(pady=(10, 4))
        ttk.Button(bar, text="登录", command=do_login, width=10).pack(side="left", padx=6)
        ttk.Button(bar, text="创建账号", command=do_create, width=10).pack(side="left", padx=6)
        ttk.Button(bar, text="取消", command=win.destroy, width=10).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: do_login())

        self.wait_window(win)
        # 登录成功后确保会话与状态一致（do_create 路径已设置）
        if res["ok"]:
            accounts.set_session(res["emp_id"])
            self.current_user = res["emp_id"]
            self._update_user_status()
        return res["ok"], res["emp_id"]

    def _create_account_dialog(self, first: bool = False):
        """创建账号对话框。返回 (ok, emp_id)。first=True 时禁止取消（必须创建首个账号）。"""
        res = {"ok": False, "emp_id": ""}
        win = tk.Toplevel(self)
        win.title("创建账号" if first else "创建新账号")
        win.geometry("340x300")
        win.configure(bg=THEME["panel"])
        win.transient(self)
        win.grab_set()
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(win, text="创建账号（用户名即工号）" if first else "创建新账号",
                 bg=THEME["panel"], fg=THEME["text"], font=F(FAMILY, 13, "bold")).pack(pady=(14, 8))
        if first:
            ttk.Label(win, text="首次使用需先创建账号，质控将责任到人",
                      foreground=THEME["text_dim"]).pack(pady=(0, 6))

        f = ttk.Frame(win)
        f.pack(padx=18, fill="x")
        ttk.Label(f, text="工号").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(f, text="姓名(可选)").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(f, text="密码").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(f, text="确认密码").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=4)
        eid = ttk.Entry(f, width=22)
        ename = ttk.Entry(f, width=22)
        epw = ttk.Entry(f, width=22, show="*")
        epw2 = ttk.Entry(f, width=22, show="*")
        eid.grid(row=0, column=1, pady=4)
        ename.grid(row=1, column=1, pady=4)
        epw.grid(row=2, column=1, pady=4)
        epw2.grid(row=3, column=1, pady=4)
        eid.focus_set()

        err = ttk.Label(win, text="", foreground="#C62828")
        err.pack(pady=(2, 0))

        def do_create():
            emp_id = eid.get().strip()
            pw = epw.get()
            if pw != epw2.get():
                err.config(text="两次输入的密码不一致")
                return
            ok, msg = accounts.create_account(emp_id, pw, ename.get().strip())
            if not ok:
                err.config(text=msg)
                return
            res["ok"] = True
            res["emp_id"] = emp_id
            accounts.set_session(emp_id)
            self.current_user = emp_id
            self._update_user_status()
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(pady=(10, 4))
        ttk.Button(bar, text="创建", command=do_create, width=10).pack(side="left", padx=6)
        if not first:
            ttk.Button(bar, text="取消", command=win.destroy, width=10).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: do_create())

        self.wait_window(win)
        return res["ok"], res["emp_id"]

    def _switch_account(self):
        """切换账号：重新登录（保留当前会话数据，仅更换责任人）。"""
        ok, emp_id = self._login_dialog(prefill=self.current_user)
        if ok:
            messagebox.showinfo("已切换", f"已切换至工号 {emp_id}")

    def _logout_and_switch(self):
        """退出登录并切换：清除会话后重新登录。"""
        accounts.clear_session()
        self.current_user = ""
        self._update_user_status()
        ok, emp_id = self._login_dialog()
        if ok:
            messagebox.showinfo("已登录", f"已登录工号 {emp_id}")
        else:
            # 取消登录则退回未登录态（保存样本时 user_id 为空，仍可使用）
            self._update_user_status()

    # -------------------- 内测支撑：更新 / 反馈 / 诊断 --------------------
    def _check_update_manual(self):
        """手动检查更新：弹「正在检查」→ 后台请求 → 回主线程展示结果。"""
        prog = tk.Toplevel(self)
        prog.title("检查更新")
        prog.geometry("300x110")
        prog.configure(bg=THEME["panel"])
        prog.transient(self)
        prog.grab_set()
        try:
            prog.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(prog, text="正在检查更新…", bg=THEME["panel"],
                 fg=THEME["text"], font=F(FAMILY, 12)).pack(expand=True)

        def cb(res):
            self.after(0, lambda: self._show_update_result(res, prog))

        update_check.check_update_async(cb)

    def _show_update_result(self, res, prog=None):
        if prog is not None:
            try:
                prog.destroy()
            except Exception:
                pass
        status = res.get("status")
        msg = res.get("message", "")
        if status == "update":
            if messagebox.askyesno("发现新版本", msg + "\n\n是否下载并更新？（也可稍后到发布页手动下载）"):
                self._start_update(res)
            elif messagebox.askyesno("仅查看", "是否打开发布下载页？"):
                webbrowser.open(res.get("url", update_check.RELEASE_PAGE))
        elif status == "latest":
            messagebox.showinfo("检查更新", msg)
        elif status == "unknown":
            if messagebox.askyesno("检查更新", msg + "\n\n是否打开发布页查看？"):
                webbrowser.open(res.get("url", update_check.RELEASE_PAGE))
        else:
            messagebox.showwarning("检查更新", msg or "检查更新失败，请稍后重试。")

    def _check_update_background(self):
        """启动后静默检查更新，仅在发现新版时才打扰用户。"""
        def cb(res):
            self.after(0, lambda: self._bg_update_notify(res))
        update_check.check_update_async(cb)

    def _bg_update_notify(self, res):
        if res.get("status") == "update":
            if messagebox.askyesno(
                    "发现新版本",
                    res.get("message", "") + "\n\n是否下载并更新？"):
                self._start_update(res)

    def _start_update(self, res):
        """下载并更新流程：进度窗 → 下载 → 安装并重启确认。"""
        win = tk.Toplevel(self)
        win.title("下载更新")
        win.geometry("420x180")
        win.configure(bg=THEME["panel"])
        win.transient(self)
        win.grab_set()
        win.lift()
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(win, text="正在下载新版本…", bg=THEME["panel"],
                 fg=THEME["text"], font=F(FAMILY, 12)).pack(pady=(16, 6))
        bar = ttk.Progressbar(win, orient="horizontal", length=340,
                              mode="determinate")
        bar.pack(padx=20)
        pct = tk.Label(win, text="0%", bg=THEME["panel"], fg=THEME["text"],
                       font=F(FAMILY, 10))
        pct.pack(pady=(4, 2))
        status_lbl = tk.Label(win, text="准备中…", bg=THEME["panel"],
                              fg=THEME["text"], font=F(FAMILY, 10))
        status_lbl.pack(pady=(2, 8))

        dest = os.path.join(auto_updater.update_cache_dir(),
                             auto_updater.default_archive_name())

        def on_progress(done, total):
            frac = (done / total) if total else 0.0
            kb_done = done // 1024
            kb_total = total // 1024 if total else 0
            self.after(0, lambda: (bar.config(value=frac * 100),
                                   pct.config(text="%d%%" % int(frac * 100)),
                                   status_lbl.config(
                                       text="%d / %d KB" % (kb_done, kb_total))))

        def worker():
            try:
                auto_updater.download(dest, on_progress, timeout=240)
                self.after(0, lambda: self._finish_update(dest, win))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: (win.destroy(),
                                       messagebox.showerror("下载失败", str(e))))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update(self, dest, win):
        """下载完成：切换为「安装并重启」确认界面。"""
        for w in list(win.winfo_children()):
            w.destroy()
        win.geometry("420x210")
        tk.Label(win, text="下载完成，是否立即安装并重启？",
                 bg=THEME["panel"], fg=THEME["text"],
                 font=F(FAMILY, 12)).pack(pady=(18, 6))
        tk.Label(win, text="安装会替换程序文件，并保留你的激活码与日志。",
                 bg=THEME["panel"], fg=THEME["text"],
                 font=F(FAMILY, 10)).pack()
        bf = ttk.Frame(win)
        bf.pack(pady=16)

        def do_install():
            auto_updater.install_and_relaunch(dest, res.get("published_at"))
            win.after(400, lambda: os._exit(0))

        ttk.Button(bf, text="安装并重启", command=do_install).pack(
            side="left", padx=10)
        ttk.Button(bf, text="稍后", command=win.destroy).pack(
            side="left", padx=10)

    def _open_feedback(self):
        """问题反馈入口：引导导出诊断包 + 打开反馈通道 + 复制联系方式。"""
        s = THEME
        win = tk.Toplevel(self)
        win.title("问题反馈 · 星衍放射质控软件")
        win.geometry("470x380")
        win.configure(bg=s["panel"])
        win.transient(self)
        win.grab_set()
        win.lift()
        try:
            win.attributes("-topmost", True)
            win.after(400, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        tk.Label(win, text="遇到问题？欢迎反馈", bg=s["panel"], fg=s["text"],
                 font=F(FAMILY, 15, "bold")).pack(pady=(18, 6))
        msg = ("你的反馈将帮助我们快速改进。建议按以下方式反馈：\n\n"
               "1. 点「导出诊断包」生成 zip（含运行日志）\n"
               "2. 点「打开反馈通道」在网页描述遇到的问题\n"
               "3. 把诊断包 zip 一并发给开发者，便于定位\n\n"
               "也可通过以下方式直接联系开发者：")
        tk.Label(win, text=msg, bg=s["panel"], fg=s["text_dim"], justify="left",
                 font=F(FAMILY, 10), wraplength=420).pack(padx=24, anchor="w")

        ent = tk.Entry(win, font=F(MONO, 10), justify="center")
        ent.insert(0, FEEDBACK_CONTACT)
        ent.configure(state="readonly")
        ent.pack(fill="x", padx=24, pady=(6, 14))

        btnbar = ttk.Frame(win)
        btnbar.pack(pady=4)
        ttk.Button(btnbar, text="打开反馈通道",
                   command=lambda: webbrowser.open(FEEDBACK_URL)).pack(side="left", padx=6)
        ttk.Button(btnbar, text="导出诊断包",
                   command=lambda: self._export_diagnostic(parent=win)).pack(side="left", padx=6)

        def _copy():
            self.clipboard_clear()
            self.clipboard_append(FEEDBACK_CONTACT)
            messagebox.showinfo("已复制", "联系方式已复制到剪贴板。", parent=win)

        ttk.Button(btnbar, text="复制联系方式", command=_copy).pack(side="left", padx=6)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(12, 4))

    def _export_diagnostic(self, parent=None):
        """导出诊断包（日志 + 系统信息 + 授权状态）为 zip。"""
        parent = parent or self
        try:
            dest = filedialog.askdirectory(
                title="选择诊断包保存位置（取消则默认存到桌面）", parent=parent)
            path = log_utils.export_diagnostic_bundle(dest or None)
            messagebox.showinfo(
                "诊断包已导出",
                f"诊断包已生成：\n{path}\n\n请把该 zip 文件发给开发者以便排查问题。",
                parent=parent)
            # 顺手在文件管理器中定位到该文件
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(["explorer", "/select,", path])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", "-R", path])
            except Exception:
                pass
        except Exception as e:
            log_utils.get_logger().error("诊断包导出失败: %s", e)
            messagebox.showerror("导出失败", f"诊断包导出失败：{e}", parent=parent)

    # -------------------- 规则维护弹窗 --------------------
    def _open_rules_editor(self):
        """可视化维护用户规则：错别字词典(R8) + 自定义互斥冲突(R9)。"""
        s = THEME
        win = tk.Toplevel(self)
        win.title("⚙ 规则维护 · 错别字 / 互斥冲突")
        win.geometry("700x560")
        win.configure(bg=s["bg"])
        win.resizable(True, True)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        # ---- 错别字词典（R8）----
        tab_typo = ttk.Frame(nb)
        nb.add(tab_typo, text="📝 错别字词典")
        self._build_typo_editor(tab_typo, win)

        # ---- 互斥冲突（R9）----
        tab_conf = ttk.Frame(nb)
        nb.add(tab_conf, text="⚠ 互斥冲突")
        self._build_conflict_editor(tab_conf, win)

    @staticmethod
    def _make_tree(parent, columns, headings, widths, height=None):
        """构造带纵向滚动条的 Treeview（外部用 pack 布局时，树区可滚动、按钮区固定在外层）。"""
        f = ttk.Frame(parent)
        vsb = ttk.Scrollbar(f, orient="vertical")
        tree = ttk.Treeview(f, columns=columns, show="headings",
                            yscrollcommand=vsb.set, height=height)
        vsb.configure(command=tree.yview)
        for col, hd, w in zip(columns, headings, widths):
            tree.heading(col, text=hd)
            tree.column(col, width=w)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        f.tree = tree
        return f

    def _build_typo_editor(self, tab, win):
        s = THEME
        # 列表（带滚动条，占满上部剩余空间）
        listf = self._make_tree(tab, ("wrong", "correct"),
                                ("错词（疑似）", "正确词"), (170, 170), height=14)
        listf.pack(fill="both", expand=True, padx=4, pady=(4, 6))
        tree = listf.tree

        inputf = ttk.Frame(tab)
        inputf.pack(fill="x", side="top", padx=4, pady=4)
        ttk.Label(inputf, text="错词", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_wrong = ttk.Entry(inputf, width=16)
        e_wrong.pack(side="left", padx=2)
        ttk.Label(inputf, text="正确词", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_correct = ttk.Entry(inputf, width=16)
        e_correct.pack(side="left", padx=2)

        def refresh():
            for r in tree.get_children():
                tree.delete(r)
            for w, c in self.engine.rules_config.get("typos", {}).items():
                tree.insert("", "end", values=(w, c))

        def on_add():
            w, c = e_wrong.get().strip(), e_correct.get().strip()
            if not w or not c:
                messagebox.showwarning("提示", "错词与正确词均不能为空")
                return
            self.engine.rules_config.setdefault("typos", {})[w] = c
            e_wrong.delete(0, "end")
            e_correct.delete(0, "end")
            refresh()

        def on_del():
            sel = tree.selection()
            if not sel:
                return
            w = tree.item(sel[0], "values")[0]
            self.engine.rules_config.get("typos", {}).pop(w, None)
            refresh()

        def on_save():
            engine.save_rules_config(self.engine.rules_config)
            self.engine.reload_rules()
            messagebox.showinfo("已保存", "错别字词典已保存并立即生效。")

        btns = ttk.Frame(tab)
        btns.pack(fill="x", side="bottom", padx=4, pady=4)
        ttk.Button(btns, text="添加", command=on_add).pack(side="left", padx=3)
        ttk.Button(btns, text="删除所选", command=on_del).pack(side="left", padx=3)
        ttk.Button(btns, text="保存并生效", style="Primary.TButton", command=on_save).pack(side="right", padx=3)
        refresh()

    def _build_conflict_editor(self, tab, win):
        s = THEME
        cols = ("a", "b", "scope", "severity", "note")
        heads = ("词A", "词B", "范围", "严重度", "说明")
        widths = (90, 90, 70, 70, 220)
        listf = self._make_tree(tab, cols, heads, widths, height=10)
        listf.pack(fill="both", expand=True, padx=4, pady=(4, 6))
        tree = listf.tree

        inputf = ttk.Frame(tab)
        inputf.pack(fill="x", side="top", padx=4, pady=4)
        ttk.Label(inputf, text="词A", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_a = ttk.Entry(inputf, width=12)
        e_a.pack(side="left", padx=2)
        ttk.Label(inputf, text="词B", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_b = ttk.Entry(inputf, width=12)
        e_b.pack(side="left", padx=2)
        ttk.Label(inputf, text="范围", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_scope = ttk.Combobox(inputf, width=10, state="readonly", values=["正文", "描述段"])
        e_scope.set("正文")
        e_scope.pack(side="left", padx=2)
        ttk.Label(inputf, text="严重度", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_sev = ttk.Combobox(inputf, width=8, state="readonly", values=["high", "medium", "low"])
        e_sev.set("medium")
        e_sev.pack(side="left", padx=2)
        ttk.Label(inputf, text="说明", foreground=s["text_dim"]).pack(side="left", padx=2)
        e_note = ttk.Entry(inputf, width=22)
        e_note.pack(side="left", padx=2, fill="x", expand=True)

        def refresh():
            for r in tree.get_children():
                tree.delete(r)
            for it in self.engine.rules_config.get("conflicts", []):
                tree.insert("", "end", values=(
                    it.get("a", ""), it.get("b", ""), it.get("scope", "正文"),
                    it.get("severity", "medium"), it.get("note", "")))

        def on_add():
            a, b = e_a.get().strip(), e_b.get().strip()
            if not a or not b:
                messagebox.showwarning("提示", "词A 与 词B 均不能为空")
                return
            self.engine.rules_config.setdefault("conflicts", []).append({
                "a": a, "b": b,
                "scope": e_scope.get() or "正文",
                "severity": e_sev.get() or "medium",
                "note": e_note.get().strip(),
            })
            e_a.delete(0, "end"); e_b.delete(0, "end"); e_note.delete(0, "end")
            refresh()

        def on_del():
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            lst = self.engine.rules_config.get("conflicts", [])
            if 0 <= idx < len(lst):
                lst.pop(idx)
                refresh()

        def on_save():
            engine.save_rules_config(self.engine.rules_config)
            self.engine.reload_rules()
            messagebox.showinfo("已保存", "互斥冲突规则已保存并立即生效。")

        btns = ttk.Frame(tab)
        btns.pack(fill="x", side="bottom", padx=4, pady=4)
        ttk.Button(btns, text="添加", command=on_add).pack(side="left", padx=3)
        ttk.Button(btns, text="删除所选", command=on_del).pack(side="left", padx=3)
        ttk.Button(btns, text="保存并生效", style="Primary.TButton", command=on_save).pack(side="right", padx=3)
        refresh()

    # -------------------- 报告质控页 --------------------
    def _build_qc_tab(self):
        s = THEME
        # 主工作区：直接挂在 tab 上，用 grid 随窗口伸缩（移出可滚动容器，
        # 解决「窗口拉大输入区仍挤在顶部靠滚动」的大小问题）
        root = self.tab_qc
        root.configure(padding=12)
        root.grid_rowconfigure(1, weight=1)        # 中央双栏行：占满剩余高度
        root.grid_columnconfigure(0, weight=1)

        # 元信息卡片（响应式 grid：标签列固定、输入框列随窗口铺开）
        meta = ttk.LabelFrame(root, text="📋  报告元信息")
        meta.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.vars = {k: tk.StringVar() for k in
                     ["exam_no", "name", "gender", "age",
                      "applied_site", "modality", "laterality"]}
        rows = [
            ("影像号", "exam_no"), ("姓名", "name"), ("性别（男/女）", "gender"),
            ("年龄", "age"), ("检查部位", "applied_site"),
            ("成像方式（CT/MR等）", "modality"), ("侧别（左/右/双侧）", "laterality"),
        ]
        for i, (lab, key) in enumerate(rows):
            r, c = i // 3, (i % 3) * 2
            ttk.Label(meta, text=lab, foreground=s["text_dim"]).grid(
                row=r, column=c, sticky="e", padx=10, pady=7)
            ttk.Entry(meta, textvariable=self.vars[key]).grid(
                row=r, column=c + 1, sticky="ew", padx=6, pady=7)
            meta.columnconfigure(c + 1, weight=1)

        # 报告文本 + 结果 双栏（grid 随窗口伸缩：左输入区 weight=2 / 右结果区 weight=1）
        body = ttk.Frame(root)
        body.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=3)   # 左：输入区（60%）
        body.grid_columnconfigure(1, weight=2)   # 右：结果区（40%，加宽便于即时反馈）
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        # 影像描述框 / 影像结论框（独立控件：分别编辑、OCR 回填、跨框质控）
        fb = ttk.LabelFrame(left, text="🩻  影像描述（检查所见）")
        fb.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        fb.grid_rowconfigure(0, weight=1)
        fb.grid_columnconfigure(0, weight=1)
        self.findings_txt = scrolledtext.ScrolledText(fb, wrap="word",
                                                       font=F(FAMILY, 11), bg=s["panel"], fg=s["text"],
                                                       insertbackground=s["primary"], relief="solid",
                                                       borderwidth=1, highlightthickness=1,
                                                       highlightbackground=s["border"])
        self.findings_txt.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        ib = ttk.LabelFrame(left, text="📑  影像结论（诊断印象）")
        ib.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        ib.grid_rowconfigure(0, weight=1)
        ib.grid_columnconfigure(0, weight=1)
        self.impression_txt = scrolledtext.ScrolledText(ib, wrap="word",
                                                        font=F(FAMILY, 11), bg=s["panel"], fg=s["text"],
                                                        insertbackground=s["primary"], relief="solid",
                                                        borderwidth=1, highlightthickness=1,
                                                        highlightbackground=s["border"])
        self.impression_txt.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        for w in (self.findings_txt, self.impression_txt):
            w.tag_configure("hl_high", background=s["hl_high"], foreground="#B71C1C")
            w.tag_configure("hl_med", background=s["hl_med"], foreground="#8A5A00")
            w.tag_configure("hl_low", background=s["hl_low"], foreground="#0B5394")
        # 粘贴整份报告到描述框时，按标题自动拆分到两框并回填元信息
        self.findings_txt.bind("<<Paste>>", lambda e: self.after(60, self._on_paste_report))

        # ===== 主操作条（核心三步流程，绿色高亮，置于中央双栏下方）=====
        main_bar = ttk.Frame(root)
        main_bar.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(main_bar, text="▶ 运行质控", style="Primary.TButton",
                   command=self._run).pack(side="left", padx=3)
        ttk.Button(main_bar, text="🔍 识别并质控", style="Primary.TButton",
                   command=self._capture_and_qc).pack(side="left", padx=3)
        ttk.Button(main_bar, text="🪟 从PACS读取(UIA)", style="Primary.TButton",
                   command=self._capture_via_uia).pack(side="left", padx=3)

        # ===== 次级操作条 =====
        sub_bar = ttk.Frame(root)
        sub_bar.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(sub_bar, text="📂 导入文件", command=self._import).pack(side="left", padx=3)
        ttk.Button(sub_bar, text="🗑 清空", command=lambda: (self.findings_txt.delete("1.0", "end"),
                                                  self.impression_txt.delete("1.0", "end"))).pack(side="left", padx=3)
        ttk.Button(sub_bar, text="💾 存入样本库", command=self._save).pack(side="left", padx=3)
        ttk.Button(sub_bar, text="🔍 自动识别元信息", command=self._auto_meta_btn).pack(side="left", padx=3)
        ttk.Button(sub_bar, text="✏️ 自动修正并复制", style="Primary.TButton",
                   command=self._auto_fix_copy).pack(side="left", padx=3)

        # ===== 设置 / 高级功能（默认折叠，点击展开；主界面不再被 10+ 按钮淹没）=====
        self.settings_open = tk.BooleanVar(value=False)
        set_frame = ttk.LabelFrame(root, text="⚙  设置 / 高级功能")
        set_frame.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(set_frame, text="展开高级功能 ▾",
                        variable=self.settings_open,
                        command=self._toggle_settings).pack(anchor="w", padx=6, pady=4)
        self._settings_inner = ttk.Frame(set_frame)
        self._build_settings_inner()
        self._toggle_settings()  # 应用初始（折叠）状态

        # 结果区（右栏，随窗口纵向伸缩；结果框占主空间，监听记录次之）
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.grid_rowconfigure(1, weight=4)   # 结果框主伸缩
        right.grid_rowconfigure(4, weight=1)   # 监听记录次伸缩
        right.grid_columnconfigure(0, weight=1)
        ttk.Label(right, text="📊  质控结果", font=F(FAMILY, 12, "bold"),
                  foreground=s["primary"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.out = scrolledtext.ScrolledText(right, wrap="word",
                                              font=F(MONO, 10), bg=s["panel_alt"], fg=s["text"],
                                              relief="solid", borderwidth=1,
                                              highlightthickness=1, highlightbackground=s["border"])
        self.out.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        ttk.Label(right, text="📈  多维评分", font=F(FAMILY, 12, "bold"),
                  foreground=s["primary"]).grid(row=2, column=0, sticky="w", pady=(8, 4))
        self.score_var = tk.StringVar(value="准确性 - | 完整性 - | 规范性 - | 及时性 -")
        score_lbl = ttk.Label(right, textvariable=self.score_var, foreground=s["primary_d"],
                              font=F(MONO, 10), wraplength=360)
        score_lbl.grid(row=3, column=0, sticky="w", pady=(0, 4))

        # 监听捕获记录（审计）：每次复制触发质控时追加一行
        hist = ttk.LabelFrame(right, text="📋  监听捕获记录（审计）")
        hist.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        hist.grid_rowconfigure(0, weight=1)
        hist.grid_columnconfigure(0, weight=1)
        self.history_box = scrolledtext.ScrolledText(hist, wrap="word",
                                                     font=F(MONO, 9), bg=s["panel"], fg=s["text"],
                                                     relief="solid", borderwidth=1,
                                                     highlightthickness=1,
                                                     highlightbackground=s["border"], state="disabled")
        self.history_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

    # -------------------- 设置 / 高级功能（可折叠） --------------------
    def _build_settings_inner(self):
        """构建设置折叠区内部控件：剪贴板监听 / 屏幕区域 OCR / 分区 OCR + UIA / 快捷键采集方式。
        控件引用均挂到 self，保持与原有功能函数（_toggle_clip/_select_ocr_region/_capture_via_uia 等）一致。
        """
        s = THEME
        p = self._settings_inner

        # 剪贴板监听控制
        self.clip_watch = False
        self._alerted_sig = None  # 同一份复制内容只弹窗提醒一次，避免监听死循环
        self._alert_cooldown = 0.0  # 弹窗最小冷却时间戳（秒），防止抖动/旧exe导致狂弹
        self.clip_var = tk.BooleanVar(value=False)
        clip_bar = ttk.Frame(p)
        clip_bar.pack(fill="x", pady=(2, 0))
        ttk.Separator(clip_bar, orient="horizontal").pack(fill="x", pady=(0, 6))
        self.clip_chk = ttk.Checkbutton(clip_bar, text="📋  监听剪贴板（复制即质控）",
                                        variable=self.clip_var, command=self._toggle_clip)
        self.clip_chk.pack(side="left", padx=2)
        self.clip_status = tk.StringVar(value="● 未监听")
        ttk.Label(clip_bar, textvariable=self.clip_status, foreground=s["text_dim"]).pack(side="left", padx=6)
        self.clip_auto_save = tk.BooleanVar(value=False)
        ttk.Checkbutton(clip_bar, text="命中即自动入库", variable=self.clip_auto_save).pack(side="left", padx=2)
        self.clip_alert = tk.BooleanVar(value=True)
        ttk.Checkbutton(clip_bar, text="🔔 发现问题即弹窗提醒", variable=self.clip_alert).pack(side="left", padx=2)

        # 屏幕区域 OCR（识别患者信息 + 与剪贴板交叉核对身份；仅快捷键/按钮触发）
        ocr_bar = ttk.Frame(p)
        ocr_bar.pack(fill="x", pady=(6, 0))
        ttk.Separator(ocr_bar, orient="horizontal").pack(fill="x", pady=(0, 6))
        ttk.Button(ocr_bar, text="🎯 框选基础信息区", width=14,
                   command=lambda: self._select_ocr_region("basic")).pack(side="left", padx=2)
        # OCR 状态灯（颜色含义见 _ocr_status：灰=待触发 绿=识别正常 黄=区域无患者信息 红=异常/身份不符）
        self.ocr_dot = tk.Canvas(ocr_bar, width=12, height=12, bd=0,
                                  highlightthickness=0, bg=THEME["bg"])
        self.ocr_dot.pack(side="left", padx=(2, 4))
        self._ocr_dot_id = self.ocr_dot.create_oval(1, 1, 11, 11, fill="#9AA0A6", outline="")
        ttk.Label(ocr_bar, textvariable=self.ocr_status, foreground=s["text_dim"]).pack(side="left", padx=6)

        # 分区 OCR：描述区 / 结论区 框选 + 快捷键 / UIA 检测
        ocr_bar2 = ttk.Frame(p)
        ocr_bar2.pack(fill="x", pady=(6, 0))
        ttk.Separator(ocr_bar2, orient="horizontal").pack(fill="x", pady=(0, 6))
        ttk.Button(ocr_bar2, text="🎯 框选描述区", width=12,
                   command=lambda: self._select_ocr_region("findings")).pack(side="left", padx=2)
        ttk.Button(ocr_bar2, text="🎯 框选结论区", width=12,
                   command=lambda: self._select_ocr_region("impression")).pack(side="left", padx=2)
        ttk.Button(ocr_bar2, text="⌨ 设置快捷键", width=12,
                   command=self._set_qc_hotkey).pack(side="left", padx=2)
        ttk.Separator(ocr_bar2, orient="vertical").pack(side="left", padx=4, fill="y")
        ttk.Button(ocr_bar2, text="🔎 UIA检测", width=10,
                   command=self._uia_diagnose).pack(side="left", padx=2)
        ttk.Label(ocr_bar2, textvariable=self.qc_hotkey_status,
                  foreground=s["text_dim"]).pack(side="left", padx=(2, 0))
        ttk.Label(ocr_bar2, textvariable=self.ocr_regions_status,
                  foreground=s["text_dim"]).pack(side="left", padx=6)

        # 快捷键「识别并质控」采集方式选择（按钮直连不受影响，仅快捷键分派受此控）
        cap_bar = ttk.Frame(p)
        cap_bar.pack(fill="x", pady=(6, 0))
        ttk.Separator(cap_bar, orient="horizontal").pack(fill="x", pady=(0, 6))
        ttk.Label(cap_bar, text="⌨ 快捷键采集：").pack(side="left", padx=2)
        self.capture_mode_var = tk.StringVar(value=self.capture_mode)
        for _val, _lab in (("auto", "自动"), ("uia", "仅UIA"),
                           ("ocr", "仅OCR"), ("ask", "每次询问")):
            ttk.Radiobutton(cap_bar, text=_lab, variable=self.capture_mode_var,
                            value=_val,
                            command=self._on_capture_mode_change).pack(side="left", padx=4)

    def _toggle_settings(self):
        """展开 / 收起『设置 / 高级功能』折叠区（默认收起）。"""
        if self.settings_open.get():
            self._settings_inner.pack(fill="x", padx=6, pady=(0, 6))
        else:
            self._settings_inner.forget()

    # -------------------- 剪贴板监听 --------------------
    @staticmethod
    def _norm_clip(s: str) -> str:
        """规范化剪贴板文本：统一换行符、去除首尾空白。
        关键：使 macOS/Windows 两端对『同一份内容』的比对一致，
        避免 _last_clip 去重因换行符或首尾空白差异而失效（Windows 狂弹的根因）。"""
        if not s:
            return ""
        return s.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _get_clipboard_text(self):
        """跨平台读取系统剪贴板纯文本。

        设计原则：每个平台只允许『单一、稳定、一致』的读取来源，
        绝不混用两个来源——否则 Windows 上两来源返回的换行符/编码不同，
        会让监听去重失效、每轮都当『新复制』，导致狂弹窗口（macOS 不出现）。

        - macOS: 仅用 pbpaste（Tk 在 macOS 读取系统剪贴板有缺陷，不回退 Tk）。
        - Windows/Linux: 仅用 Tk clipboard_get()（读 CF_UNICODETEXT，稳；
          与 `_auto_fix_copy` 写回的是同一来源，自动修正不会触发回环）。
        """
        if platform.system() == "Darwin":
            try:
                p = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
                if p.returncode == 0:
                    return self._norm_clip(p.stdout)
            except Exception:
                pass
            return ""  # macOS 不 fallback 到 Tk
        # Windows / Linux：直接用 Tk 读取系统剪贴板（单一来源，避免与 subprocess 交替）
        try:
            return self._norm_clip(self.clipboard_get())
        except Exception:
            return ""

    def _update_status_bar(self):
        """刷新底部常驻状态条：监听指示灯颜色 + 本次会话命中统计。"""
        watching = getattr(self, "clip_watch", False)
        self.monitor_dot.itemconfig(self._dot_id,
                                    fill="#2ECC71" if watching else "#9AA0A6")
        last = f" · 最后捕获 {self._session_last_ts}" if self._session_last_ts else ""
        self.session_var.set(
            f"{'监听中' if watching else '未监听'} · 本次会话命中 {self.session_hits} 份{last}")

    # -------------------- OCR 状态灯 --------------------
    _OCR_DOT_COLORS = {
        "off": "#9AA0A6",        # 待触发（灰）
        "ok": "#2ECC71",         # 识别正常并已回填（绿）
        "empty": "#F1C40F",      # 区域识别不到患者信息，可能框错（黄）
        "alert": "#E74C3C",      # 屏幕与剪贴板患者信息不符（红）
        "error": "#E74C3C",      # OCR 异常 / 不可用（红）
    }

    def _ocr_status(self, state, text):
        """统一设置 OCR 状态灯颜色 + 文字（最近一次识别结果的可视化指示）。"""
        color = self._OCR_DOT_COLORS.get(state, "#9AA0A6")
        try:
            self.ocr_dot.itemconfig(self._ocr_dot_id, fill=color)
        except Exception:
            pass
        self.ocr_status.set(text)

    def _toggle_clip(self):
        self.clip_watch = self.clip_var.get()
        if self.clip_watch:
            # 不把当前剪贴板当基线，开启即刻处理已复制的内容，避免“先复制后开监听不触发”
            self._last_clip = ""
            self._alerted_sig = None
            # 取消可能残留的旧轮询任务，避免叠加出多条监听链导致重复弹窗
            if getattr(self, "_clip_job", None):
                try:
                    self.after_cancel(self._clip_job)
                except Exception:
                    pass
                self._clip_job = None
            self.clip_status.set("● 监听中（每1秒轮询）…")
            self._update_status_bar()
            self._poll_clipboard()
        else:
            self.clip_status.set("● 未监听")
            self._update_status_bar()

    # -------------------- 屏幕区域 OCR 监控 --------------------
    def _ocr_config_path(self) -> str:
        """OCR 区域/开关配置持久化路径（用户可写）。"""
        if getattr(sys, "frozen", False):
            d = os.path.join(os.path.expandvars("%APPDATA%"), "MedicalReportQC")
        else:
            d = os.path.join(os.path.expanduser("~"), ".config", "MedicalReportQC")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "ocr_config.json")

    def _load_ocr_config(self) -> dict:
        try:
            with open(self._ocr_config_path(), encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_ocr_config(self):
        try:
            cfg = {"regions": {k: list(v) for k, v in self.ocr_regions.items()},
                   "hotkey": getattr(self, "qc_hotkey", None),
                   "capture_mode": getattr(self, "capture_mode", "auto")}
            with open(self._ocr_config_path(), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _select_ocr_region(self, role="basic"):
        """透明全屏覆盖层，拖拽框选指定角色的屏幕区域。

        role: basic=基础信息(患者监护+结构化回填) / findings=影像描述 /
              impression=影像结论（按需『识别并质控』时分别识别）。
        """
        ok, reason = ocr_provider.availability()
        if not ok:
            messagebox.showwarning("OCR 不可用", reason)
            return
        role_cn = OCR_ROLE_CN.get(role, role)
        sel = tk.Toplevel(self)
        try:
            sel.attributes("-fullscreen", True)
        except Exception:
            sel.geometry("3000x2000+0+0")
        sel.attributes("-alpha", 0.28)
        sel.configure(bg="black")
        sel.attributes("-topmost", True)
        sel.wait_visibility()
        canvas = tk.Canvas(sel, cursor="cross", bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        hint = tk.Label(sel, text=f"拖拽框选『{role_cn}』屏幕区域，松开确认；Esc 取消\n"
                                  "（如 PACS 患者信息栏 / 影像所见文本区 / 诊断印象文本区）\n"
                                  "（多显示器：请在本窗口所在屏幕上完成框选）",
                        bg="black", fg="white", font=(FAMILY, 14), justify="center",
                        wraplength=760)
        hint.place(relx=0.5, rely=0.04, anchor="center")
        rect = [None]
        start = [0, 0]

        def on_down(e):
            start[0], start[1] = e.x_root, e.y_root
            if rect[0]:
                canvas.delete(rect[0])
            rect[0] = canvas.create_rectangle(e.x_root, e.y_root, e.x_root, e.y_root,
                                              outline="#00E5FF", width=2)

        def on_move(e):
            if rect[0] is None:
                return
            canvas.coords(rect[0], start[0], start[1], e.x_root, e.y_root)

        def on_up(e):
            x0, y0 = min(start[0], e.x_root), min(start[1], e.y_root)
            x1, y1 = max(start[0], e.x_root), max(start[1], e.y_root)
            w, h = x1 - x0, y1 - y0
            sel.destroy()
            if w < 10 or h < 10:
                messagebox.showinfo("提示", "选区过小，已取消。")
                return
            self.ocr_regions[role] = (x0, y0, w, h)
            self._save_ocr_config()
            self._update_region_status()
            self._ocr_status("off", f"● {role_cn}区已设定 {w}×{h}")

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_up)
        sel.bind("<Escape>", lambda e: sel.destroy())

    # -------------------- 分区 OCR：识别并质控 --------------------
    def _update_region_status(self):
        """刷新『三区框选状态』文本标签，并同步实时监护区（basic）。"""
        if not hasattr(self, "ocr_regions_status"):
            return
        parts = []
        for r in ("basic", "findings", "impression"):
            parts.append(f"{OCR_ROLE_CN[r]}{'已设' if r in self.ocr_regions else '未设'}")
        self.ocr_regions_status.set("区域：" + " / ".join(parts))

    def _compose_report(self, meta, findings_text, impression_text):
        """把三区识别结果拼成带分段标题的报告。

        引擎按『检查所见/影像描述→findings』『诊断印象/结论→impression』分段，
        因此这里用标准标题包裹，后续 _run 的分区规则（R2/R5/R7/R10）自动生效。
        """
        meta = dict(meta or {})
        meta["name"] = (meta.get("name") or meta.get("patient") or "").strip()
        head = []
        for k in ("exam_no", "name", "gender", "age", "modality", "applied_site", "laterality"):
            v = (meta.get(k) or "").strip()
            if v:
                head.append(f"{self._META_CN.get(k, k)}：{v}")
        parts = []
        if head:
            parts.append("【患者信息】 " + "；".join(head))
        parts.append("检查所见：\n" + (findings_text or "").strip())
        parts.append("诊断印象：\n" + (impression_text or "").strip())
        return "\n\n".join(parts)

    def _append_region_qc(self, meta, findings_text, impression_text):
        """在质控结果区追加『分区域识别状态 + 分区专属核查』，便于用户逐项订正。"""
        meta = dict(meta or {})
        meta["name"] = (meta.get("name") or meta.get("patient") or "").strip()
        f_len = len((findings_text or "").strip())
        i_len = len((impression_text or "").strip())
        notes = ["----- 分区域识别状态 -----"]
        if meta:
            filled = [self._META_CN.get(k, k) for k in
                      ("exam_no", "name", "gender", "age",
                       "modality", "applied_site", "laterality")
                      if (meta.get(k) or "").strip()]
            notes.append(f"  基础信息：已识别（{', '.join(filled)}）")
        else:
            notes.append("  基础信息：未识别到患者字段（请确认框选了患者信息栏）")
        notes.append(f"  影像描述：{f_len} 字" + ("（过短，建议补充）" if f_len < 10 else ""))
        notes.append(f"  影像结论：{i_len} 字" + ("（过短，建议补充）" if i_len < 5 else ""))
        # 分区专属核查：结论不应照抄描述
        if findings_text and impression_text and self._similar(impression_text, findings_text) > 0.85:
            notes.append("  ⚠ 影像结论与影像描述高度雷同，疑似照抄描述，请修正。")
        self.out.insert("end", "\n" + "\n".join(notes) + "\n")

    def _capture_and_qc(self):
        """按需分区域截图识别并质控（不走轮询，报告写完后手动触发）：

        1) 基础信息区 → 结构化解析 + 回填元信息输入框（复用姓名护栏逻辑）；
        2) 影像描述区 → 自由文本（可编辑）回填到报告正文；
        3) 影像结论区 → 自由文本（可编辑）回填到报告正文；
        4) 拼成带分段标题的报告 → 运行现有 QC 引擎（R2/R5/R7/R10 自动生效）；
        5) 追加分区域识别状态与分区专属核查（描述非空 / 结论非空 / 结论非照抄描述）。
        """
        miss = [OCR_ROLE_CN[r] for r in ("basic", "findings", "impression")
                if r not in self.ocr_regions]
        if miss:
            messagebox.showinfo("请先框选区域",
                "以下区域尚未框选，无法识别并质控：\n  - " + "\n  - ".join(miss)
                + "\n\n请先用上方『🎯 框选…』按钮设定对应屏幕区域。")
            return
        ok, reason = ocr_provider.availability()
        if not ok:
            messagebox.showwarning("OCR 不可用", reason)
            return
        try:
            # 1) 三区截图识别（先全部取文本，便于元信息跨区补抽部位/侧别）
            img_b = ocr_provider.capture_region(self.ocr_regions["basic"])
            text_b = ocr_provider.ocr_image(img_b) or ""
            img_f = ocr_provider.capture_region(self.ocr_regions["findings"])
            text_f = ocr_provider.ocr_image(img_f) or ""
            img_i = ocr_provider.capture_region(self.ocr_regions["impression"])
            text_i = ocr_provider.ocr_image(img_i) or ""
            # 2) 基础信息区结构化解析 + 跨区补抽部位/侧别（多数 PACS 患者栏不含）
            meta = engine.extract_meta_full(text_b, text_f, text_i)
            exam_no = (meta.get("exam_no") or "").strip()
            name = (meta.get("patient") or "").strip()  # 引擎以 'patient' 承载姓名
            if exam_no and self.vars["exam_no"].get().strip() != exam_no:
                self.vars["exam_no"].set(exam_no)
            if name and self.vars["name"].get().strip() != name:
                self.vars["name"].set(name)
            for k in ("gender", "age", "modality", "applied_site", "laterality"):
                v = (meta.get(k) or "").strip()
                if v and self.vars[k].get().strip() != v:
                    self.vars[k].set(v)
            # 记录屏幕侧元信息 + 与剪贴板（报告）交叉核对身份（防张冠李戴）
            self.ocr_meta = meta
            self._compare_ocr_clipboard(meta)
            # 3) 影像描述区 / 影像结论区：自由文本回填到对应框
            self.findings_txt.delete("1.0", "end")
            self.findings_txt.insert("1.0", (text_f or "").strip())
            self.impression_txt.delete("1.0", "end")
            self.impression_txt.insert("1.0", (text_i or "").strip())
            self._run()
            # 4) 分区域状态 + 分区专属核查
            self._append_region_qc(meta, text_f, text_i)
            if any((meta.get(k) or "").strip() for k in
                   ("exam_no", "name", "gender", "age",
                    "modality", "applied_site", "laterality")):
                self._ocr_status("ok", "● 已识别并质控（三区）")
            else:
                self._ocr_status("empty",
                    "● 已质控，但基础信息区未识别到患者字段（请检查框选位置）")
        except Exception as e:
            self._ocr_status("error", f"● 识别并质控失败：{e}")
            messagebox.showerror("识别并质控失败", str(e))

    # -------------------- Windows UIA 采集（无滚动漂移） --------------------
    def _dispatch_qc(self):
        """「识别并质控」统一入口（快捷键触发）：按 self.capture_mode 分派采集方式。

        - auto：UIA 可用则 UIA，否则 OCR 三区（保持原默认行为）
        - uia ：强制 UIA（UIA 不可用时提示）
        - ocr ：强制 OCR 三区（未框选则提示）
        - ask ：弹菜单让用户每次选择 UIA / OCR
        注：显式按钮（🪟 从PACS读取(UIA) / 🔍 识别并质控(OCR)）直连各自方法，不受此影响。
        """
        mode = self.capture_mode
        if mode == "uia":
            self._capture_via_uia()
        elif mode == "ocr":
            if not self.ocr_regions:
                messagebox.showinfo("请先框选区域",
                    "当前采集方式设为『OCR』，但尚未框选屏幕区域。\n"
                    "请先用『🎯 框选…』按钮设定三区，或把采集方式切回『自动/UIA/每次询问』。")
                return
            ok, reason = ocr_provider.availability()
            if not ok:
                messagebox.showwarning("OCR 不可用", reason)
                return
            self._capture_and_qc()
        elif mode == "ask":
            self._ask_capture_mode()
        else:  # auto
            if getattr(self, "uia", None) and self.uia.is_available():
                self._capture_via_uia()
            else:
                if not self.ocr_regions:
                    messagebox.showinfo("请先框选区域或切换采集方式",
                        "自动模式下 UIA 不可用（需 Windows 原生 PACS 客户端），"
                        "且尚未框选 OCR 三区。\n请先框选区域，或把采集方式切到『UIA/每次询问』。")
                    return
                ok, reason = ocr_provider.availability()
                if not ok:
                    messagebox.showwarning("OCR 不可用", reason)
                    return
                self._capture_and_qc()

    def _capture_via_uia(self):
        """从前景 PACS 窗口（UIA 读控件文本）读取完整报告并质控。

        根治 OCR「屏幕滚动后识别区域偏差」：UIA 读的是控件内存里的完整文本，
        与滚动/分辨率/字号无关，一次读出整份报告（含屏幕外部分）。
        """
        if not getattr(self, "uia", None):
            messagebox.showinfo("UIA 不可用", "UIA 提供器未初始化。")
            return
        if not self.uia.is_available():
            messagebox.showinfo("UIA 不可用", self.uia.unavailable_reason()
                + "\n\nUIA 仅适用于 Windows 原生 PACS 客户端（联影/东软/飞利浦/GE 等）。\n"
                  "当前环境不可用，请改用『🔍 识别并质控(OCR)』或剪贴板。")
            return
        try:
            text = self.uia.capture_text()
        except Exception as e:
            messagebox.showerror("UIA 读取失败", str(e))
            return
        if not text or not text.strip():
            messagebox.showwarning("未读到报告",
                "UIA 未在前景 PACS 窗口找到可读取的报告文本控件。\n\n"
                "可能原因：\n"
                "  ① PACS 报告区是自绘 canvas/OpenGL（UIA 读不到文本）；\n"
                "  ② 当前焦点不在 PACS 报告窗口（请先点一下报告窗口使其激活）。\n\n"
                "可点『🔎 UIA检测』确认窗口文本控件情况，或改用 OCR / 剪贴板。")
            return
        # 1) 整段文本结构化解析 + 回填元信息（与 OCR 路径共用 engine.extract_meta）
        meta = engine.extract_meta(text)
        exam_no = (meta.get("exam_no") or "").strip()
        name = (meta.get("patient") or "").strip()
        if exam_no and self.vars["exam_no"].get().strip() != exam_no:
            self.vars["exam_no"].set(exam_no)
        if name and self.vars["name"].get().strip() != name:
            self.vars["name"].set(name)
        for k in ("gender", "age", "modality", "applied_site", "laterality"):
            v = (meta.get(k) or "").strip()
            if v and self.vars[k].get().strip() != v:
                self.vars[k].set(v)
        self.ocr_meta = meta
        # 2) 按标题切分 检查所见/诊断印象（复用引擎分段，等价于手工输入流）
        secs = RuleEngine._split_for_r5(text)
        findings = (secs.get("findings") or text).strip()
        impression = (secs.get("impression") or "").strip()
        self.findings_txt.delete("1.0", "end")
        self.findings_txt.insert("1.0", findings)
        self.impression_txt.delete("1.0", "end")
        self.impression_txt.insert("1.0", impression)
        # 3) 运行质控（R1–R12、R14、R15 全量生效；R13 预留未启用）
        self._run()
        self._ocr_status("ok", "● 已从 PACS 窗口读取并质控（UIA，无滚动漂移）")

    def _uia_diagnose(self):
        """弹窗展示前景窗口的文本控件，帮助用户确认 PACS 是否支持 UIA。"""
        if not getattr(self, "uia", None):
            messagebox.showinfo("UIA 不可用", "UIA 提供器未初始化。")
            return
        msg = self.uia.diagnose_foreground()
        messagebox.showinfo("UIA 检测", msg)

    @staticmethod
    def _norm_capture_mode(v):
        """采集方式取值规范化：仅 auto/uia/ocr/ask 合法，其余一律回退 auto。"""
        return v if v in ("auto", "uia", "ocr", "ask") else "auto"

    def _ask_capture_mode(self):
        """弹菜单让用户选择本次采集方式（UIA / OCR），快捷键 ask 模式使用。Esc 关闭不触发。"""
        dlg = tk.Toplevel(self)
        dlg.title("选择采集方式")
        dlg.transient(self)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="本次「识别并质控」用哪种方式？",
                  font=("TkDefaultFont", 11, "bold")).pack(pady=(0, 10))

        def choose(m):
            dlg.destroy()
            if m == "uia":
                self._capture_via_uia()
            else:
                if not self.ocr_regions:
                    messagebox.showinfo("请先框选区域",
                        "尚未框选 OCR 三区，无法用 OCR 识别。\n"
                        "请先用『🎯 框选…』按钮设定区域，或改用 UIA。")
                    return
                ok, reason = ocr_provider.availability()
                if not ok:
                    messagebox.showwarning("OCR 不可用", reason)
                    return
                self._capture_and_qc()

        ttk.Button(frm, text="🪟 UIA（读 PACS 控件文本，无滚动漂移）",
                   style="Primary.TButton", width=36,
                   command=lambda: choose("uia")).pack(pady=4, fill="x")
        ttk.Button(frm, text="🔍 OCR（屏幕三区截图识别）",
                   width=36, command=lambda: choose("ocr")).pack(pady=4, fill="x")
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.grab_set()
        dlg.update_idletasks()
        dlg.wait_visibility()
        w, h = dlg.winfo_width(), dlg.winfo_height()
        x = max(0, (dlg.winfo_screenwidth() - w) // 2)
        y = max(0, (dlg.winfo_screenheight() - h) // 2)
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_set()

    def _on_capture_mode_change(self):
        """采集方式单选变更：持久化到 ocr_config.json（下次启动与快捷键均生效）。"""
        self.capture_mode = self.capture_mode_var.get()
        self._save_ocr_config()

    # -------------------- 「识别并质控」快捷键 --------------------
    def _set_qc_hotkey(self):
        """弹窗捕获任意快捷键组合：按下即设定；Esc 取消；Backspace/Delete 清除。"""
        dlg = tk.Toplevel(self)
        dlg.title("设置「识别并质控」快捷键")
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="请按下要设置的快捷键组合",
                  font=(FAMILY, 12, "bold")).pack()
        preview = tk.StringVar(value=f"当前：{hotkey_display(self.qc_hotkey)}")
        ttk.Label(frm, textvariable=preview, font=(FAMILY, 14)).pack(pady=(10, 6))
        ttk.Label(frm, text="推荐 F 键或 Ctrl/Alt 组合键（如 F9、Ctrl+Alt+Q）\n"
                            "Esc 取消 · Backspace/Delete 清除快捷键",
                  foreground="#7A8794", justify="center").pack()
        held = set()   # 自行跟踪按住的修饰键，跨平台比 event.state 可靠

        def _mods_of():
            mods = []
            if held & {"Control_L", "Control_R"}:
                mods.append("ctrl")
            if held & {"Alt_L", "Alt_R", "Meta_L", "Meta_R"}:
                mods.append("alt")
            if held & {"Shift_L", "Shift_R"}:
                mods.append("shift")
            if held & {"Super_L", "Super_R", "Win_L", "Win_R"}:
                mods.append("win")
            return mods

        def on_press(e):
            ks = e.keysym
            if ks == "Escape":
                dlg.destroy()
                return "break"
            if ks in ("BackSpace", "Delete"):
                dlg.destroy()
                self._apply_qc_hotkey(None)
                return "break"
            if ks in _HOTKEY_MODIFIER_KEYSYMS:
                held.add(ks)
                m = _mods_of()
                preview.set("+".join(hotkey_display({"mods": m, "key": "x"}).split("+")[:-1]) + "+…"
                            if m else "…")
                return "break"
            mods = _mods_of()
            # 补充 event.state 兜底（个别平台 KeyPress 序列不含修饰键按下事件）
            if e.state & 0x4 and "ctrl" not in mods:
                mods.append("ctrl")
            if e.state & 0x1 and "shift" not in mods:
                mods.append("shift")
            hk = {"mods": mods, "key": ks}
            # 无修饰的普通字符键会全局劫持打字，给出确认
            if not mods and len(ks) == 1:
                if not messagebox.askyesno(
                        "确认快捷键",
                        f"「{hotkey_display(hk)}」没有修饰键，任何窗口里输入该字符都会触发质控，"
                        "容易误触。\n\n仍要使用吗？（推荐 F 键或加 Ctrl/Alt）",
                        parent=dlg):
                    return "break"
            dlg.destroy()
            self._apply_qc_hotkey(hk)
            return "break"

        def on_release(e):
            held.discard(e.keysym)
            return "break"

        dlg.bind("<KeyPress>", on_press)
        dlg.bind("<KeyRelease>", on_release)
        dlg.focus_force()
        # 居中于主窗口
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _apply_qc_hotkey(self, hk):
        """设定/清除快捷键：注销旧的 → 持久化 → 注册新的 → 刷新状态文本。"""
        self._unregister_qc_hotkey()
        self.qc_hotkey = hk or None
        self._save_ocr_config()
        if self.qc_hotkey:
            self._register_qc_hotkey()
        else:
            self.qc_hotkey_status.set("快捷键：未设置")

    def _register_qc_hotkey(self):
        """注册快捷键。三种机制按平台叠加，确保「聚焦时」与「后台（焦点在 PACS 等其它窗口）」都能触发：

        - 所有平台：Tk bind_all（应用窗口聚焦时可用，作为兜底）
        - Windows：RegisterHotKey 系统级全局热键（PACS 聚焦时也能触发，且不与非全局按键冲突）
        - 非 Windows（macOS/Linux）：pynput 全局键盘监听（后台也能触发，需安装 pynput
          并（macOS）授予辅助功能权限）。pynput 为被动监听，与 Tk bind_all 同时触发时
          由 _on_qc_hotkey 的时间戳防抖去重，不会重复质控。
        """
        hk = self.qc_hotkey
        if not hk:
            return
        # 1) 应用内 Tk 绑定（所有平台兜底）
        seq = hotkey_tk_sequence(hk)
        if seq:
            try:
                self.bind_all(seq, lambda e: (self._on_qc_hotkey(), "break")[1])
                self._hotkey_tk_seq = seq
            except Exception:
                self._hotkey_tk_seq = None
        label = f"快捷键：{hotkey_display(hk)}"
        # 2) Windows 全局热键
        if sys.platform.startswith("win"):
            vk = hotkey_vk(hk.get("key"))
            if vk is None:
                self.qc_hotkey_status.set(label + "（仅本软件窗口内有效）")
                return
            mod_flags = 0
            for m in hk.get("mods") or []:
                mod_flags |= HOTKEY_MOD_FLAGS.get(m, 0)
            mod_flags |= 0x4000  # MOD_NOREPEAT：按住不重复触发

            def loop():
                import ctypes
                from ctypes import wintypes
                u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
                self._hotkey_thread_id = k32.GetCurrentThreadId()
                if not u32.RegisterHotKey(None, HOTKEY_ID, mod_flags, vk):
                    # 被其他程序占用等：退化为仅应用内绑定
                    self.after(0, lambda: self.qc_hotkey_status.set(
                        label + "（全局注册失败，可能被占用；仅本软件窗口内有效）"))
                    self._hotkey_thread_id = None
                    return
                self.after(0, lambda: self.qc_hotkey_status.set(label + "（全局）"))
                msg = wintypes.MSG()
                while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:   # WM_HOTKEY
                        self.after(0, self._on_qc_hotkey)
                u32.UnregisterHotKey(None, HOTKEY_ID)

            threading.Thread(target=loop, daemon=True).start()
        # 3) 非 Windows：pynput 全局键盘监听（后台也能触发）
        else:
            self._start_pynput_listener(label)

    def _start_pynput_listener(self, label):
        """非 Windows：启动 pynput 全局键盘监听，使快捷键在应用处于后台（焦点在其它窗口）时也能触发。

        把已配置的快捷键组合（mods + key）映射到 pynput 的键表示；按键时若修饰键集合与
        配置完全一致且主键匹配，则经主线程（after(0)）触发一键质控。macOS 需授予本程序
        「辅助功能」权限，否则监听收不到事件。
        """
        try:
            from pynput import keyboard
        except Exception:
            self.after(0, lambda: self.qc_hotkey_status.set(
                label + "（窗口内有效；后台监听需 pip install pynput）"))
            return
        hk = self.qc_hotkey
        target_mods = set(hk.get("mods") or [])
        target_key = (hk.get("key") or "").lower()
        if not target_key:
            return

        def _mod_of(k):
            if k in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                return "ctrl"
            if k in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                return "alt"
            if k in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                return "shift"
            # Command（macOS）/ Win 键：按平台归一到配置里的 "win"
            if k in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
                     keyboard.Key.win, keyboard.Key.win_l, keyboard.Key.win_r):
                return "win" if sys.platform.startswith("win") else "alt"
            return None

        def _key_of(k):
            if isinstance(k, keyboard.Key):
                n = k.name
                if n and n.startswith("f") and n[1:].isdigit():
                    return n          # F9 等
                return n
            if isinstance(k, keyboard.KeyCode):
                if k.char:
                    return k.char.lower()
                return None
            return None

        pressed = set()

        def on_press(k):
            m = _mod_of(k)
            if m:
                pressed.add(m)
                return
            kn = _key_of(k)
            if kn == target_key and pressed == target_mods:
                self.after(0, self._on_qc_hotkey)

        def on_release(k):
            m = _mod_of(k)
            if m:
                pressed.discard(m)

        try:
            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
            self._pynput_listener = listener
            self.after(0, lambda: self.qc_hotkey_status.set(
                label + "（后台全局监听已启用）"))
        except Exception:
            self.after(0, lambda: self.qc_hotkey_status.set(
                label + "（后台监听启动失败；窗口内仍可用）"))

    def _unregister_qc_hotkey(self):
        """注销现有快捷键（Tk 解绑 + 结束 Windows 热键消息循环线程 + 停止 pynput 监听）。"""
        if self._hotkey_tk_seq:
            try:
                self.unbind_all(self._hotkey_tk_seq)
            except Exception:
                pass
            self._hotkey_tk_seq = None
        if self._hotkey_thread_id is not None:
            try:
                import ctypes
                # WM_QUIT 结束 GetMessageW 循环，线程内自行 UnregisterHotKey
                ctypes.windll.user32.PostThreadMessageW(
                    self._hotkey_thread_id, 0x0012, 0, 0)
            except Exception:
                pass
            self._hotkey_thread_id = None
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None

    def _on_qc_hotkey(self):
        """快捷键触发入口：时间戳防抖后执行「识别并质控」。

        防抖双保险：
        - 0.5s 时间戳去重：避免应用聚焦时 Tk bind_all 与 pynput 全局监听同时触发、
          或系统按键重复（key repeat）导致的重复质控；
        - 执行中 _hotkey_busy 标记：单次质控未结束时忽略并发触发。
        """
        now = time.time()
        if now - self._last_hotkey_ts < 0.5:
            return
        self._last_hotkey_ts = now
        if self._hotkey_busy:
            return
        self._hotkey_busy = True
        try:
            self._dispatch_qc()
        finally:
            self._hotkey_busy = False

    def _compare_ocr_clipboard(self, ocr_meta):
        """屏幕侧元信息 vs 剪贴板（报告）元信息：姓名/性别/年龄/申请部位不一致即告警。

        核心价值：防止『报告文本』与『PACS 屏幕患者』张冠李戴。
        """
        clip = self._get_clipboard_text()
        if not clip:
            return
        cmeta = engine.extract_meta(clip)
        mismatches = []
        for label, key in (("name", "patient"), ("gender", "gender"),
                           ("age", "age"), ("applied_site", "applied_site")):
            o, c = (ocr_meta.get(key) or "").strip(), (cmeta.get(key) or "").strip()
            if o and c and o != c:
                mismatches.append((label, o, c))
        if mismatches:
            detail = "；".join(f"{self._META_CN.get(k, k)} 屏幕『{o}』≠ 剪贴板『{c}』"
                               for k, o, c in mismatches)
            sig = hashlib.md5(detail.encode("utf-8", "ignore")).hexdigest()
            now = time.time()
            if sig != self._ocr_alert_sig and (now - self._ocr_alert_cooldown) > 8:
                self._ocr_alert_sig = sig
                self._ocr_alert_cooldown = now
                self._ocr_status("alert", "● ⚠ 屏幕与剪贴板患者信息不符！")
                messagebox.showwarning("⚠ 患者身份核对不符",
                    "屏幕区域识别出的患者信息与剪贴板（报告）不一致，请核对是否张冠李戴：\n\n"
                    + detail)
        else:
            self._ocr_alert_sig = None

    def _similar(self, a: str, b: str) -> float:
        """两段文本相似度（0~1），用于监听内容去重。"""
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

    def _poll_clipboard(self):
        try:
            if self.clip_watch:
                try:
                    data = self._get_clipboard_text()
                except Exception:
                    data = ""
                last = getattr(self, "_last_clip", "")
                if data and last and self._similar(data, last) > 0.9:
                    # 与近期捕获高度相似 → 合并提醒，不重复弹窗/入库
                    self._last_clip = data
                    self.clip_status.set("● 已捕获（与近期内容高度相似，已合并提醒）")
                elif data and data != last:
                    self._last_clip = data
                    if len(data.strip()) >= 15:
                        self._set_report_text(data)
                        try:
                            self._run()
                        except Exception as e:
                            self.clip_status.set(f"● 捕获但质控异常：{e}")
                        else:
                            self.clip_status.set(
                                f"● 已捕获并质控（{len(self.current_findings)} 项）")
                            saved = False
                            if self.clip_auto_save.get():
                                try:
                                    self._save_silent()
                                    saved = True
                                except Exception:
                                    pass
                            self._log_capture(len(self.current_findings), saved)
                            # 监听命中问题即弹窗提醒（同一份内容只弹一次 + 5秒冷却，避免死循环）
                            if self.current_findings and self.clip_alert.get():
                                sig = hashlib.md5(data.encode("utf-8", "ignore")).hexdigest()
                                now = time.time()
                                if sig != self._alerted_sig and (now - self._alert_cooldown) > 5:
                                    self._alerted_sig = sig
                                    self._alert_cooldown = now
                                    self._alert_findings()
                    else:
                        self.clip_status.set(f"● 已捕获 {len(data)} 字（过短，未质控）")
                        self._log_capture(0, False)
        except Exception as e:
            self.clip_status.set(f"● 监听异常：{e}")
        finally:
            # 关键：无论是否报错都重新调度，保证监听循环不会静默死亡
            if getattr(self, "clip_watch", False):
                self._clip_job = self.after(1000, self._poll_clipboard)

    def _log_capture(self, n_findings, saved):
        """向『监听捕获记录』审计面板追加一行，供事后核对每份复制报告都跑过质控。"""
        box = getattr(self, "history_box", None)
        if box is None:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        chars = len(self._build_report().strip())
        line = f"[{ts}] 复制 {chars} 字 · 命中 {n_findings} 项" + (" · 已入库" if saved else "")
        box.configure(state="normal")
        box.insert("end", line + "\n")
        box.see("end")
        box.configure(state="disabled")
        # 更新常驻状态条：累计命中数 + 最后捕获时间
        self._session_last_ts = ts
        if n_findings > 0:
            self.session_hits += 1
        self._update_status_bar()

    def _alert_findings(self, force=False):
        """监听命中问题时弹出醒目提醒窗。逐条可『忽略』，并支持把忽略规则写入配置永久生效。

        force=True 时（如点击『忽略』后刷新）即使窗口已存在也重建；否则若窗口已
        存在则直接返回，避免重复弹出/闪烁造成的“一直弹窗”观感。
        """
        win = getattr(self, "_alert_win", None)
        if win is not None and win.winfo_exists() and not force:
            return  # 已有一个提醒窗，不再重复弹出
        findings = [f for f in (self.current_findings or []) if self._ig_key(f) not in self.ignored]
        if not findings:
            # 没有待提醒项：若窗口还在就关掉它
            w = getattr(self, "_alert_win", None)
            if w is not None and w.winfo_exists():
                w.destroy()
                self._alert_win = None
            return
        win = getattr(self, "_alert_win", None)
        if win is not None and win.winfo_exists():
            for w in win.winfo_children():
                w.destroy()
        else:
            win = tk.Toplevel(self)
            win.title("⚠ 报告质控提醒")
            win.geometry("520x440")
            win.configure(bg="#FAFAFA")
            self._alert_win = win
        # 高/中/低分级统计
        n = len(findings)
        n_high = sum(1 for fd in findings if getattr(fd, "severity", "") == "high")
        head_txt = f"⚠ 本次复制的报告发现 {n} 处问题"
        if n_high:
            head_txt += f"（其中 {n_high} 处高风险）"
        # ponytail: v2 — cleaner alert header with icon + severity badge
        header_frame = tk.Frame(win, bg="#FAFAFA")
        header_frame.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header_frame, text="⚠️", font=("Segoe UI Emoji", 20), bg="#FAFAFA").pack(side="left", padx=(0, 8))
        tk.Label(header_frame, text=head_txt, font=F(FAMILY, 13, "bold"),
                 fg="#C0392B", bg="#FAFAFA", wraplength=440, justify="left").pack(side="left", fill="x", expand=True)

        # 严重度图例
        legend = tk.Frame(win, bg="#FAFAFA")
        legend.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(legend, text="● 红=严重   ● 橙=警告   ● 蓝=提示",
                 font=F(FAMILY, 9), fg=THEME["text_dim"], bg="#FAFAFA").pack(side="left")
        # Findings list：每条按严重度着色（红=严重 / 橙=警告 / 蓝=提示）+ 左侧色条 + 忽略按钮
        list_frame = tk.Frame(win, bg="#FAFAFA")
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        for i, fd in enumerate(findings, 1):
            sev = getattr(fd, "severity", "")
            sev_label = SEV_CN.get(sev, "?")
            rid = getattr(fd, "rule_id", "")
            msg = getattr(fd, "message", "")
            snip = (getattr(fd, "snippet", "") or "").strip()
            row = tk.Frame(list_frame, bg=SEV_BG.get(sev, "#EEEEEE"),
                           relief="solid", borderwidth=1)
            row.pack(fill="x", pady=3)
            # 左侧严重度色条（红/橙/蓝），一眼区分错误程度
            bar = tk.Frame(row, bg=SEV_COLOR.get(sev, "#999999"), width=6)
            bar.pack(side="left", fill="y")
            badge = tk.Label(row, text=f"  {sev_label}  ", font=F(FAMILY, 9, "bold"),
                             fg=SEV_COLOR.get(sev, "#333333"),
                             bg=SEV_BG.get(sev, "#EEEEEE"))
            badge.pack(side="left", padx=(6, 0), pady=4)
            content = tk.Frame(row, bg=SEV_BG.get(sev, "#EEEEEE"))
            content.pack(side="left", fill="both", expand=True, padx=(6, 6), pady=4)
            tk.Label(content, text=f"[{rid}]  {msg}", font=F(FAMILY, 10),
                    fg="#333333", bg=SEV_BG.get(sev, "#EEEEEE"),
                    wraplength=360, justify="left", anchor="w").pack(fill="x")
            if snip:
                tk.Label(content, text=f"原文：…{snip[:50]}…", font=F(FAMILY, 9),
                        fg="#666666", bg=SEV_BG.get(sev, "#EEEEEE"),
                        wraplength=360, justify="left", anchor="w").pack(fill="x")
            tk.Button(row, text="忽略", command=lambda k=self._ig_key(fd): self._ignore_one(k)
                      ).pack(side="right", padx=4, pady=4)

        # Buttons
        bar = tk.Frame(win, bg="#FAFAFA")
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bar, text="写入永久忽略规则", command=self._persist_ignores).pack(side="left")
        ttk.Button(bar, text="打开自动修正", command=lambda: (win.destroy(), self._auto_fix_copy())
                  ).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="知道了", command=win.destroy).pack(side="right")
        # 瞬时置顶抢焦点，随后释放
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.bell()
            win.after(600, lambda: win.winfo_exists() and win.attributes("-topmost", False))
        except Exception:
            pass

    def _ignore_one(self, key: str):
        """会话内忽略某条 finding（弹窗重建后该条不再显示）。"""
        self.ignored.add(key)
        self._alert_findings(force=True)

    def _persist_ignores(self):
        """把当前会话忽略名单写入 rules_config.json，重启后仍生效。"""
        if not self.ignored:
            messagebox.showinfo("提示", "当前没有待保存的忽略项。")
            return
        cfg = self.engine.rules_config
        cfg["ignores"] = sorted(self.ignored)
        try:
            engine.save_rules_config(cfg)
            self.engine.reload_rules()
            messagebox.showinfo("已保存",
                f"已将 {len(self.ignored)} 条忽略规则写入配置文件，重启后仍生效。")
        except Exception as e:
            show_error(f"保存失败：{e}")

    def _auto_fill_meta(self, text):
        """自动抽取元信息并回填输入框（仅填空字段，不覆盖手动输入）。返回回填的字段列表。"""
        meta = engine.extract_meta(text)
        filled = []
        # 引擎以 'patient' 承载姓名；映射为界面独立字段 name / exam_no
        mapping = [("exam_no", "exam_no"), ("patient", "name"),
                   ("gender", "gender"), ("age", "age"),
                   ("modality", "modality"), ("applied_site", "applied_site"),
                   ("laterality", "laterality")]
        for src, dst in mapping:
            v = (meta.get(src) or "").strip()
            if v and not self.vars[dst].get().strip():
                self.vars[dst].set(v)
                filled.append(dst)
        return filled

    # 报告正文拆分：整份报告（含/不含分段标题）→ 描述框/结论框
    def _set_report_text(self, text):
        secs = engine.RuleEngine._split_for_r5(text)
        self.findings_txt.delete("1.0", "end")
        self.findings_txt.insert("1.0", secs["findings"].strip())
        self.impression_txt.delete("1.0", "end")
        self.impression_txt.insert("1.0", secs["impression"].strip())
        self._auto_fill_meta(text)

    def _build_report(self):
        meta = {k: self.vars[k].get().strip() for k in self.vars}
        return self._compose_report(meta,
                                     self.findings_txt.get("1.0", "end"),
                                     self.impression_txt.get("1.0", "end"))

    def _on_paste_report(self):
        """粘贴整份报告到描述框：自动拆分到两框并回填元信息。"""
        self._set_report_text(self.findings_txt.get("1.0", "end"))

    _META_CN = {"name": "姓名", "exam_no": "影像号", "gender": "性别",
                "age": "年龄", "modality": "成像方式", "applied_site": "检查部位",
                "laterality": "侧别"}

    def _auto_meta_btn(self):
        """手动触发：对当前报告文本自动识别并回填元信息。"""
        text = self._build_report()
        filled = self._auto_fill_meta(text)
        if filled:
            names = ", ".join(self._META_CN.get(k, k) for k in filled)
            messagebox.showinfo("自动识别", f"已自动识别并回填：{names}")
        else:
            messagebox.showinfo("自动识别", "未发现可自动识别的元信息，或字段均已填写。")

    def _save_silent(self):
        report = self._build_report()
        meta = {k: self.vars[k].get().strip() for k in self.vars}
        sid = samplelib.save_sample(report, meta, self.current_findings, self.current_scores,
                                    anonymize=self.anon_var.get(),
                                    user_id=self.current_user)
        self._refresh_samples()
        self.clip_status.set(f"● 已捕获并入库 #{sid}（{len(self.current_findings)} 项）")

    def _import(self):
        p = filedialog.askopenfilename(filetypes=[("文本", "*.txt *.md *.csv"), ("全部", "*.*")])
        if p:
            try:
                with open(p, encoding="utf-8") as fh:
                    self._set_report_text(fh.read())
            except Exception as e:
                show_error(f"读取失败：{e}")

    def _ig_key(self, fd) -> str:
        """为一条 finding 生成稳定的忽略标识键（用于误报白名单）。"""
        snip = (getattr(fd, "snippet", "") or "").strip()
        if snip:
            return f"{fd.rule_id}|{snip}"
        return f"{fd.rule_id}|{(getattr(fd, 'message', '') or '')[:24]}"

    def _run(self):
        report = self._build_report()
        meta = {k: self.vars[k].get().strip() for k in self.vars}
        findings = self.engine.run(report, meta)
        # 误报反馈闭环：过滤掉已在忽略名单中的条目（会话级 + 持久化）
        findings = [f for f in findings if self._ig_key(f) not in self.ignored]
        self.current_findings = findings
        # 按严重度排序：严重(high) → 警告(medium) → 提示(low)，让高风险错误排在最前
        findings.sort(key=lambda f: SEV_ORDER.get(getattr(f, "severity", "low"), 2))
        self.current_scores = score(findings)

        self._out_ranges = {}
        self._txt_marks = {}
        for w in (self.findings_txt, self.impression_txt):
            for tag in ("hl_high", "hl_med", "hl_low", "mk_click", "flash_find"):
                w.tag_remove(tag, "1.0", "end")
            w.tag_bind("mk_click", "<Button-1>", self._on_txt_click)
        # 内文高亮：优先用 finding 自带 span；span 缺失（如 R2 左右侧混淆）时用 snippet 在文本框中定位，
        # 确保每条错误都能按严重度着色，不再因 span=(-1,-1) 漏高亮。
        for i, fd in enumerate(findings, 1):
            loc = self._locate_finding(fd)
            if not loc:
                continue
            box, lo, hi = loc
            w = self.impression_txt if box == "impression" else self.findings_txt
            w.tag_add(SEV_TAG.get(fd.severity, "hl_med"), f"1.0+{lo}c", f"1.0+{hi}c")
            w.tag_add("mk_click", f"1.0+{lo}c", f"1.0+{hi}c")
            self._txt_marks[i] = (box, lo, hi)

        self.out.delete("1.0", "end")
        # 严重度图例：红=严重 / 橙=警告 / 蓝=提示
        self.out.insert("end",
            "图例：  ■ 红=严重(高)   ■ 橙=警告(中)   ■ 蓝=提示(低)\n\n", "sev_legend")
        self.out.tag_configure("sev_legend", foreground=THEME["text_dim"], font=F(MONO, 9))
        if not findings:
            self.out.insert("end", "✅ 未检出确定性错误。\n", "ok")
        else:
            for i, fd in enumerate(findings, 1):
                start = self.out.index("end-1c")
                self.out.insert("end",
                    f"[{i}] {fd.rule_id} | {fd.error_type} | 严重度={SEV_CN.get(fd.severity, fd.severity)}({fd.severity})\n"
                    f"    {fd.message}\n")
                end = self.out.index("end-1c")
                self._out_ranges[i] = (start, end)
                self.out.tag_add(f"res{i}", start, end)
                # 整条结果按严重度着色：严重=红 / 警告=橙 / 提示=蓝
                self.out.tag_configure(f"res{i}",
                    foreground=SEV_COLOR.get(fd.severity, THEME["primary"]), underline=True)
                self.out.tag_bind(f"res{i}", "<Button-1>", lambda e, i=i: self._goto_finding(i))
        # 评分依据透明化：列出各维度扣分明细，让临床看到扣分逻辑
        self.out.insert("end", "\n----- 评分依据 -----\n")
        for dim in ("准确性", "完整性", "规范性", "及时性"):
            ds = self.current_scores[dim]["deductions"]
            if ds:
                for d in ds:
                    self.out.insert("end", f"  {dim} {d['delta']:+}  〔{d['rule']}〕{d['reason']}\n")
            else:
                self.out.insert("end", f"  {dim} 0  无扣分\n")
        self.out.tag_configure("ok", foreground=THEME["ok"], font=F(MONO, 10, "bold"))
        sc = self.current_scores
        self.score_var.set(
            f"准确性 {sc['准确性']['score']} | 完整性 {sc['完整性']['score']} | "
            f"规范性 {sc['规范性']['score']} | 及时性 {sc['及时性']['score']}")

    # ---------- 结果区 ↔ 原文 双向定位 ----------
    @staticmethod
    def _section_start(report, header):
        idx = report.find(header)
        return idx + len(header) if idx >= 0 else -1

    def _map_span(self, st, en, f_start, i_start):
        """将 finding 的全局偏移映射到 (box, local_st, local_en)。"""
        if st < 0 or en <= st:
            return None, -1, -1
        if i_start >= 0 and st >= i_start:
            return "impression", st - i_start, en - i_start
        if f_start >= 0 and st >= f_start:
            return "findings", st - f_start, en - f_start
        return None, -1, -1

    def _locate_finding(self, fd):
        """返回 finding 所在的 (box, local_st, local_en)；无法定位返回 None。"""
        st, en = fd.span
        if st >= 0 and en > st:
            report = self._build_report()
            f_start = self._section_start(report, "检查所见：\n")
            i_start = self._section_start(report, "诊断印象：\n")
            box, lo, hi = self._map_span(st, en, f_start, i_start)
            if box:
                return box, lo, hi
        snip = (getattr(fd, "snippet", "") or "").strip()
        if snip:
            for box, w in (("findings", self.findings_txt), ("impression", self.impression_txt)):
                pos = w.search(snip, "1.0", stopindex="end")
                if pos:
                    st_off = w.count("1.0", pos)[0]
                    return box, st_off, st_off + len(snip)
        return None

    def _flash_box(self, box, st, en):
        w = self.impression_txt if box == "impression" else self.findings_txt
        tag = "flash_find"
        w.tag_configure(tag, background="#FFE082")
        for k in range(6):
            t = k * 220
            w.after(t, lambda add=(k % 2 == 0):
                (w.tag_add(tag, f"1.0+{st}c", f"1.0+{en}c") if add
                 else w.tag_remove(tag, f"1.0+{st}c", f"1.0+{en}c")))
        w.after(6 * 220 + 120, lambda: w.tag_remove(tag, "1.0", "end"))

    def _goto_finding(self, i):
        """点结果条目 → 对应框滚动并闪烁高亮。"""
        fd = self.current_findings[i - 1] if 0 < i <= len(self.current_findings) else None
        if fd is None:
            return
        loc = self._locate_finding(fd)
        if not loc:
            return
        box, st, en = loc
        w = self.impression_txt if box == "impression" else self.findings_txt
        w.focus_set()
        w.see(f"1.0+{st}c")
        self._flash_box(box, st, en)

    def _on_txt_click(self, event):
        """点原文高亮 → 定位回结果条目。"""
        w = event.widget
        box = "impression" if w is self.impression_txt else "findings"
        idx = w.index(f"@{event.x},{event.y}")
        off = w.count("1.0", idx)[0]
        for i, (b, st, en) in self._txt_marks.items():
            if b == box and st <= off <= en:
                self._goto_result(i)
                return

    def _goto_result(self, i):
        """点原文高亮 → 结果区定位并闪烁。"""
        a, b = self._out_ranges.get(i, (None, None))
        if not a:
            return
        self.out.see(a)
        tag = "flash_res"
        self.out.tag_configure(tag, background="#FFE082")
        for k in range(6):
            t = k * 220
            self.out.after(t, lambda add=(k % 2 == 0):
                (self.out.tag_add(tag, a, b) if add else self.out.tag_remove(tag, a, b)))
        self.out.after(6 * 220 + 120, lambda: self.out.tag_remove(tag, "1.0", "end"))
        self.out.focus_set()

    def _save(self):
        if not self.current_findings and self._build_report().strip():
            self._run()
        report = self._build_report()
        meta = {k: self.vars[k].get().strip() for k in self.vars}
        sid = samplelib.save_sample(report, meta, self.current_findings, self.current_scores,
                                    anonymize=self.anon_var.get(),
                                    user_id=self.current_user)
        self._refresh_samples()
        tag = "（已脱敏）" if self.anon_var.get() else ""
        messagebox.showinfo("已保存", f"样本已存入样本库（ID={sid}）{tag}")

    def _auto_fix_copy(self):
        """发现错别字后：先弹预览框逐条确认 → 仅应用被选中的改动 → 回填 → 复制系统剪贴板。
        矛盾类错误不自动改，提示人工。"""
        report = self._build_report()
        if not report.strip():
            messagebox.showwarning("提示", "报告内容为空，无法修正。")
            return
        # 基于当前文本先跑一次质控
        self._run()
        findings = self.current_findings or []
        fixed_all, n_fix, n_manual, changes = self.engine.auto_fix(report, findings)

        # 无可自动修正的错别字：提示并复制原文（便于后续人工粘贴）
        if n_fix == 0:
            msg = "未发现可自动修正的错别字。"
            if n_manual:
                msg += f"\n另有 {n_manual} 处矛盾/规范类问题需人工确认，未改动。"
            msg += "\n已复制当前文本到剪贴板。"
            self.clipboard_clear()
            self.clipboard_append(report)
            self.update_idletasks()
            self._last_clip = self._norm_clip(report)
            messagebox.showinfo("自动修正完成", msg)
            return

        # 弹预览框，让用户逐条确认
        selected = self._preview_autofix(changes, n_manual)
        if selected is None:                       # 取消
            return

        # 仅应用被选中的改动（在原文上从右往左替换，规避位置偏移）
        chosen = [c for c, on in zip(changes, selected) if on]
        new_fixed = report
        for fx in sorted(chosen, key=lambda x: x["start"], reverse=True):
            s, e = fx["start"], fx["end"]
            new_fixed = new_fixed[:s] + fx["correct"] + new_fixed[e:]

        # 回填并重新质控，刷新高亮与结果
        self._set_report_text(new_fixed)
        self._run()
        # 复制到系统剪贴板
        self.clipboard_clear()
        self.clipboard_append(new_fixed)
        self.update_idletasks()
        norm_fixed = self._norm_clip(new_fixed)
        self._last_clip = norm_fixed   # 防止监听把刚写出的内容当成"新复制"再次捕获
        # 自动修正后写入剪贴板的内容也记为已提醒，避免监听重新捕获后又弹窗
        self._alerted_sig = hashlib.md5(norm_fixed.encode("utf-8", "ignore")).hexdigest()

        applied = len(chosen)
        skipped = n_fix - applied
        msg = f"已自动修正 {applied} 处错别字，并复制到剪贴板。"
        if skipped:
            msg += f"\n你跳过了 {skipped} 处（未改动）。"
        if n_manual:
            msg += f"\n另有 {n_manual} 处矛盾/规范类问题需人工确认，未改动。"
        messagebox.showinfo("自动修正完成", msg)

    def _preview_autofix(self, changes, n_manual):
        """自动修正预览弹窗：逐条列出『错词 → 正词』及上下文，用户勾选确认。
        返回与 changes 等长的 bool 列表（True=应用）；取消返回 None。"""
        s = THEME
        win = tk.Toplevel(self)
        win.title("✏️  自动修正预览 · 请确认每处改动")
        win.geometry("700x480")
        win.configure(bg=s["bg"])
        win.resizable(True, True)
        win.grab_set()

        tk.Label(win, text="以下为同音错别字自动修正建议，请逐条确认（矛盾/规范类问题不自动改）。",
                 bg=s["bg"], fg=s["text_dim"], font=F(FAMILY, 10)).pack(anchor="w", padx=14, pady=(12, 6))

        sf = ScrollableFrame(win)
        sf.pack(fill="both", expand=True, padx=14, pady=4)
        inner = sf.inner
        inner.configure(padding=6)
        vars_list = []
        for c in changes:
            v = tk.BooleanVar(value=True)
            vars_list.append(v)
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=3)
            ttk.Checkbutton(row, variable=v).pack(side="left")
            ttk.Label(row, text=f"『{c['wrong']}』 → 『{c['correct']}』",
                      font=F(FAMILY, 10, "bold"), foreground=s["primary"]).pack(side="left", padx=8)
            ctx = c["snippet"].replace("\n", " ")
            ttk.Label(row, text=f"上下文：…{ctx}…", foreground=s["text_dim"]).pack(side="left", padx=4)
        if n_manual:
            ttk.Label(inner, text=f"⚠ 另有 {n_manual} 处矛盾/规范类问题需人工确认，本次不改。",
                      foreground=s["sev_med"], font=F(FAMILY, 10, "bold")).pack(anchor="w", pady=(8, 0))

        result = {"sel": None}

        def on_all(val):
            for v in vars_list:
                v.set(val)

        def on_ok():
            result["sel"] = [v.get() for v in vars_list]
            win.destroy()

        def on_cancel():
            result["sel"] = None
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="全选", command=lambda: on_all(True)).pack(side="left", padx=3)
        ttk.Button(btns, text="全不选", command=lambda: on_all(False)).pack(side="left", padx=3)
        ttk.Button(btns, text="取消", command=on_cancel).pack(side="right", padx=3)
        ttk.Button(btns, text="确认并复制", style="Primary.TButton", command=on_ok).pack(side="right", padx=3)

        win.wait_window(win)
        return result["sel"]

    # -------------------- 驾驶舱页 --------------------
    def _build_dash_tab(self):
        s = THEME
        sf = ScrollableFrame(self.tab_dash)
        sf.pack(fill="both", expand=True)
        f = sf.inner
        f.configure(padding=12)

        charts = ttk.Frame(f)
        charts.pack(fill="both", expand=True, pady=(0, 10))
        self.pie = tk.Canvas(charts, width=400, height=300, bg="#FFFFFF",
                             highlightthickness=1, highlightbackground=s["border"])
        self.pie.pack(side="left", padx=6)
        self.trend = tk.Canvas(charts, width=440, height=300, bg="#FFFFFF",
                               highlightthickness=1, highlightbackground=s["border"])
        self.trend.pack(side="left", padx=6)
        ttk.Button(charts, text="📥 导出质控报表", command=self._export_report).pack(
            side="right", anchor="n", padx=4)
        ttk.Button(charts, text="🔄 刷新统计", command=self._refresh_dash).pack(side="right", anchor="n")

        lib = ttk.LabelFrame(f, text="📚  样本库")
        lib.pack(fill="both", expand=True)
        cols = ("id", "ts", "patient", "user_id", "gender", "modality", "applied_site")
        self.tree = ttk.Treeview(lib, columns=cols, show="headings", height=10)
        for c, t, w in zip(cols, ["ID", "时间", "患者", "工号", "性别", "部位", "申请部位"],
                          [60, 150, 120, 100, 70, 130, 130]):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, side="left")
        self.tree.bind("<Double-1>", self._on_view)
        ctrl = ttk.Frame(lib)
        ctrl.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(ctrl, text="查看", command=self._on_view_btn).pack(pady=3, fill="x")
        ttk.Button(ctrl, text="删除", command=self._on_delete).pack(pady=3, fill="x")
        ttk.Separator(ctrl, orient="horizontal").pack(fill="x", pady=6)
        ttk.Checkbutton(ctrl, text="入库时脱敏\n(去除患者姓名)", variable=self.anon_var).pack(pady=3, fill="x")
        ttk.Label(ctrl, text="⚠ 样本仅存于\n本机 SQLite\n不会上传网络",
                  foreground=s["text_dim"], justify="left", font=F(FAMILY, 9)).pack(pady=(6, 0))

    def _refresh_dash(self):
        self._draw_pie(samplelib.stats_by_error_type())
        self._draw_trend(samplelib.stats_by_date())
        self._refresh_samples()

    def _refresh_samples(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        for sm in samplelib.list_samples():
            self.tree.insert("", "end", values=(
                sm["id"], sm["ts"], sm.get("name") or sm.get("patient", ""),
                sm.get("user_id", ""), sm["gender"], sm["modality"],
                sm["applied_site"]))

    def _export_report(self):
        """把样本明细 + 错误类型分布 + 每日趋势导出为 CSV（utf-8-sig，Excel 中文不乱码）。"""
        rows = samplelib.list_samples_full()
        if not rows:
            messagebox.showinfo("提示", "样本库为空，暂无可导出数据。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV 报表", "*.csv")],
            initialfile=f"质控报表_{datetime.date.today().isoformat()}")
        if not path:
            return
        counts = samplelib.stats_by_error_type()
        trend = samplelib.stats_by_date()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["【样本明细】"])
                w.writerow(["ID", "时间", "患者", "工号", "性别", "年龄", "检查部位",
                            "申请部位", "侧别", "命中数", "报告字数"])
                for sm in rows:
                    fj = sm.get("findings_json") or "[]"
                    n = len(json.loads(fj)) if fj else 0
                    w.writerow([sm["id"], sm["ts"], sm.get("name") or sm.get("patient", ""),
                              sm.get("user_id", ""),
                              sm["gender"],
                                sm.get("age", ""), sm["modality"], sm["applied_site"],
                                sm.get("laterality", ""), n, len(sm.get("report_text", "") or "")])
                w.writerow([])
                w.writerow(["【错误类型分布】"])
                w.writerow(["类型", "数量", "占比"])
                total = sum(counts.values()) or 1
                for k, v in counts.items():
                    w.writerow([k, v, f"{100.0 * v / total:.1f}%"])
                w.writerow([])
                w.writerow(["【每日趋势】"])
                w.writerow(["日期", "报告数", "平均准确性"])
                for d, v in sorted(trend.items()):
                    w.writerow([d, v["n"], v["avg_acc"]])
            messagebox.showinfo("已导出", f"质控报表已导出：\n{path}")
        except Exception as e:
            show_error(f"导出失败：{e}")

    def _draw_pie(self, counts: dict):
        s = THEME
        cv = self.pie
        cv.delete("all")
        cv.create_text(200, 16, text="错误类型分布", font=F(FAMILY, 13, "bold"), fill=s["primary"])
        if not counts:
            cv.create_text(200, 160, text="暂无数据", fill=s["text_dim"], font=F(FAMILY, 11))
            return
        total = sum(counts.values())
        cx, cy, r = 130, 170, 95
        ir = r * 0.58  # 内半径 → 环形
        start = 90.0  # 从正上方开始
        for i, (k, v) in enumerate(counts.items()):
            extent = 360.0 * v / total
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=extent,
                          fill=s["chart"][i % len(s["chart"])], outline="#FFFFFF", width=2)
            start += extent
        # 中心留白圆
        cv.create_oval(cx - ir, cy - ir, cx + ir, cy + ir, fill="#FFFFFF", outline="#FFFFFF")
        cv.create_text(cx, cy - 8, text=str(total), font=F(FAMILY, 20, "bold"), fill=s["text"])
        cv.create_text(cx, cy + 16, text="总检出", font=F(FAMILY, 10), fill=s["text_dim"])
        # 图例（带占比）
        lx = 252
        for i, (k, v) in enumerate(counts.items()):
            y = 70 + i * 26
            pct = f"{100.0 * v / total:.0f}%"
            cv.create_rectangle(lx, y, lx + 14, y + 14, fill=s["chart"][i % len(s["chart"])],
                                outline="")
            cv.create_text(lx + 20, y + 7, text=f"{k}: {v} ({pct})", anchor="w",
                           font=F(FAMILY, 10), fill=s["text"])

    def _draw_trend(self, by_date: dict):
        s = THEME
        cv = self.trend
        cv.delete("all")
        cv.create_text(220, 16, text="每日报告数 / 平均准确性", font=F(FAMILY, 13, "bold"),
                       fill=s["primary"])
        if not by_date:
            cv.create_text(220, 160, text="暂无数据", fill=s["text_dim"], font=F(FAMILY, 11))
            return
        items = sorted(by_date.items())
        n = len(items)
        x0, y0, w, h = 56, 250, 360, 175
        bw = max(24, min(56, w // n - 8))
        maxn = max(v["n"] for v in by_date.values()) or 1

        # 网格 + 左轴刻度（报告数）
        for g in range(0, 5):
            gy = y0 - int(h * g / 4)
            cv.create_line(x0, gy, x0 + w, gy, fill=s["border"], width=1)
            cv.create_text(x0 - 10, gy, text=str(int(maxn * g / 4)), anchor="e",
                           font=F(MONO, 9), fill=s["text_dim"])
        cv.create_text(x0 - 30, y0 - h - 6, text="报告数", anchor="w", font=F(FAMILY, 9),
                       fill=s["text_dim"])

        # 柱：每日报告数
        for i, (day, v) in enumerate(items):
            x = x0 + 8 + i * (bw + 8)
            bh = int(h * v["n"] / maxn)
            cv.create_rectangle(x, y0 - bh, x + bw, y0, fill=s["primary"], outline="")
            cv.create_text(x + bw / 2, y0 - bh - 7, text=str(v["n"]), anchor="s",
                           font=F(MONO, 9), fill=s["text"])
            cv.create_text(x + bw / 2, y0 + 12, text=day[5:], anchor="n",
                           font=F(MONO, 8), fill=s["text_dim"])

        # 折线：平均准确性（0-100 → 右轴）
        pts = []
        for i, (day, v) in enumerate(items):
            x = x0 + 8 + i * (bw + 8) + bw / 2
            y = y0 - int(h * v["avg_acc"] / 100.0)
            pts.append((x, y))
        if len(pts) > 1:
            cv.create_line(*[c for p in pts for c in p], fill=s["sev_med"], width=2, smooth=True)
        for (x, y), (day, v) in zip(pts, items):
            cv.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, fill=s["sev_med"], outline="#FFFFFF")
        # 右轴刻度（准确性 %）
        for g in range(0, 5):
            gy = y0 - int(h * g / 4)
            cv.create_text(x0 + w + 12, gy, text=f"{g * 25}", anchor="w",
                           font=F(MONO, 9), fill=s["sev_med"])
        cv.create_text(x0 + w + 12, y0 - h - 6, text="准确性%", anchor="w",
                       font=F(FAMILY, 9), fill=s["sev_med"])

        # 图例
        cv.create_rectangle(x0, y0 + 34, x0 + 14, y0 + 48, fill=s["primary"], outline="")
        cv.create_text(x0 + 20, y0 + 41, text="报告数", anchor="w", font=F(FAMILY, 9),
                       fill=s["text"])
        cv.create_line(x0 + 90, y0 + 41, x0 + 110, y0 + 41, fill=s["sev_med"], width=2)
        cv.create_text(x0 + 116, y0 + 41, text="平均准确性", anchor="w", font=F(FAMILY, 9),
                       fill=s["text"])

    def _on_view(self, event=None):
        self._on_view_btn()

    def _on_view_btn(self):
        sel = self.tree.selection()
        if not sel:
            return
        sid = int(self.tree.item(sel[0], "values")[0])
        sm = samplelib.get_sample(sid)
        win = tk.Toplevel(self)
        win.title(f"样本 #{sid}")
        win.geometry("720x540")
        win.configure(bg=THEME["bg"])
        txt = scrolledtext.ScrolledText(win, wrap="word", font=F(FAMILY, 11),
                                        bg=THEME["panel"], fg=THEME["text"],
                                        relief="solid", borderwidth=1,
                                        highlightthickness=1, highlightbackground=THEME["border"])
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("end", f"时间: {sm.get('ts','')}\n患者: {sm.get('name') or sm.get('patient','')}  "
                          f"性别: {sm.get('gender','')} 年龄: {sm.get('age','')}\n"
                          f"检查部位: {sm.get('modality','')} 申请部位: {sm.get('applied_site','')} 侧别: {sm.get('laterality','')}\n"
                          f"质控人(工号): {sm.get('user_id','') or '（未登录）'}\n")
        txt.insert("end", "\n----- 报告原文 -----\n" + sm.get("report_text", ""))
        txt.insert("end", "\n\n----- 质控发现 -----\n")
        for fd in (sm.get("findings_json") and __import__("json").loads(sm["findings_json"]) or []):
            txt.insert("end", f"[{fd['rule_id']}] {fd['error_type']} ({fd['severity']}): {fd['message']}\n")
        txt.insert("end", "\n----- 评分 -----\n" + sm.get("scores_json", ""))

    def _on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        sid = int(self.tree.item(sel[0], "values")[0])
        if messagebox.askyesno("确认", f"删除样本 #{sid}？"):
            samplelib.delete_sample(sid)
            self._refresh_dash()

    # -------------------- RIS 直连页 --------------------
    def _build_ris_tab(self):
        s = THEME
        sf = ScrollableFrame(self.tab_ris)
        sf.pack(fill="both", expand=True)
        f = sf.inner
        f.configure(padding=10)
        cfg = ris.load_config()
        self.ris_vars = {}

        conn = ttk.LabelFrame(f, text="🔗  连接配置（保存在本机 assets/ris_config.json，请在院内内网机器使用）")
        conn.pack(fill="x", padx=2, pady=(0, 8))

        ttk.Label(conn, text="数据库类型", foreground=s["text_dim"]).grid(
            row=0, column=0, sticky="e", padx=8, pady=6)
        self.ris_vars["db_type"] = tk.StringVar(value=cfg["db_type"])
        ttk.Combobox(conn, textvariable=self.ris_vars["db_type"], width=14, state="readonly",
                     values=list(ris.DRIVERS.keys())).grid(row=0, column=1, sticky="w", padx=4, pady=6)

        fields = [
            ("主机/IP", "host", 0, 2), ("端口", "port", 0, 4),
            ("数据库/服务名", "database", 1, 0), ("用户名", "user", 1, 2),
            ("密码", "password", 1, 4),
        ]
        for lab, key, r, c in fields:
            ttk.Label(conn, text=lab, foreground=s["text_dim"]).grid(
                row=r, column=c, sticky="e", padx=8, pady=6)
            self.ris_vars[key] = tk.StringVar(value=str(cfg.get(key, "")))
            show = "*" if key == "password" else ""
            ttk.Entry(conn, textvariable=self.ris_vars[key], width=18, show=show).grid(
                row=r, column=c + 1, sticky="w", padx=4, pady=6)

        ttk.Label(f, text="拉取 SQL（由院内 IT 提供；必须返回 report_text 列，可选 "
                          "patient/gender/age/modality/applied_site/ts）",
                  foreground=s["text_dim"]).pack(anchor="w", padx=4, pady=(4, 0))
        self.ris_query = scrolledtext.ScrolledText(f, wrap="word", height=6, font=F(MONO, 10),
                                                   bg=s["panel"], fg=s["text"], relief="solid",
                                                   borderwidth=1, highlightthickness=1,
                                                   highlightbackground=s["border"])
        self.ris_query.pack(fill="x", padx=4, pady=4)
        self.ris_query.insert("1.0", cfg.get("query", ""))

        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=4, pady=4)
        ttk.Button(bar, text="保存配置", command=self._ris_save).pack(side="left", padx=3)
        ttk.Button(bar, text="🔌 测试连接", command=self._ris_test).pack(side="left", padx=3)
        ttk.Button(bar, text="📥 拉取报告", command=self._ris_fetch).pack(side="left", padx=3)
        ttk.Button(bar, text="▶ 全部质控并入库", style="Primary.TButton",
                   command=self._ris_batch).pack(side="left", padx=3)
        self.ris_status = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.ris_status, foreground=s["primary"]).pack(side="left", padx=10)

        listf = ttk.LabelFrame(f, text="📄  拉取结果（双击可送入『报告质控』页）")
        listf.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        cols = ("idx", "patient", "gender", "modality", "preview")
        self.ris_tree = ttk.Treeview(listf, columns=cols, show="headings", height=10)
        for c, t, w in zip(cols, ["#", "患者", "性别", "部位", "报告预览"], [40, 120, 60, 120, 480]):
            self.ris_tree.heading(c, text=t)
            self.ris_tree.column(c, width=w)
        self.ris_tree.pack(fill="both", expand=True, side="left")
        self.ris_tree.bind("<Double-1>", self._ris_send_to_qc)
        self._ris_rows = []

        self._ris_refresh_driver_hint()

    def _ris_refresh_driver_hint(self):
        db = self.ris_vars["db_type"].get()
        ok, mod, msg = ris.driver_available(db)
        self.ris_status.set(("✅ " if ok else "⚠ ") + msg)

    def _ris_collect(self) -> dict:
        cfg = {k: (v.get() if k == "password" else v.get().strip())
               for k, v in self.ris_vars.items()}
        cfg["query"] = self.ris_query.get("1.0", "end").strip()
        return cfg

    def _ris_save(self):
        ris.save_config(self._ris_collect())
        self.ris_status.set("✅ 配置已保存")

    def _ris_test(self):
        self.ris_status.set("测试中…")
        self.update_idletasks()
        ok, msg = ris.test_connection(self._ris_collect())
        self.ris_status.set(("✅ " if ok else "⚠ ") + msg)

    def _ris_fetch(self):
        self.ris_status.set("拉取中…")
        self.update_idletasks()
        try:
            rows = ris.fetch_reports(self._ris_collect(), limit=50)
        except Exception as e:
            self.ris_status.set(f"⚠ 拉取失败：{e}")
            return
        self._ris_rows = rows
        for r in self.ris_tree.get_children():
            self.ris_tree.delete(r)
        for i, it in enumerate(rows):
            preview = it["report_text"].strip().replace("\n", " ")[:60]
            self.ris_tree.insert("", "end", values=(
                i + 1, it["patient"], it["gender"], it["modality"], preview))
        self.ris_status.set(f"✅ 拉取 {len(rows)} 条")

    def _ris_send_to_qc(self, event=None):
        sel = self.ris_tree.selection()
        if not sel:
            return
        idx = int(self.ris_tree.item(sel[0], "values")[0]) - 1
        it = self._ris_rows[idx]
        self.vars["name"].set(it.get("patient", ""))
        self.vars["gender"].set(it.get("gender", ""))
        self.vars["age"].set(it.get("age", ""))
        self.vars["modality"].set(it.get("modality", ""))
        self.vars["applied_site"].set(it.get("applied_site", ""))
        self._set_report_text(it["report_text"])
        self.notebook.select(self.tab_qc)
        self._run()

    def _ris_batch(self):
        if not self._ris_rows:
            messagebox.showinfo("提示", "请先『拉取报告』")
            return
        n = 0
        for it in self._ris_rows:
            meta = {"name": it.get("patient", ""), "gender": it.get("gender", ""),
                    "age": it.get("age", ""), "modality": it.get("modality", ""),
                    "applied_site": it.get("applied_site", "")}
            findings = self.engine.run(it["report_text"], meta)
            scores = score(findings)
            samplelib.save_sample(it["report_text"], meta, findings, scores,
                                  anonymize=self.anon_var.get(),
                                  user_id=self.current_user)
            n += 1
        self._refresh_samples()
        self.ris_status.set(f"✅ 已质控并入库 {n} 条")
        messagebox.showinfo("完成", f"已批量质控并入库 {n} 条报告，可在『质控驾驶舱』查看统计。")


def main():
    # ---------- 日志与崩溃捕获（最先初始化，确保全程可记录）----------
    log_utils.setup_logging()
    log_utils.install_excepthook()
    log_utils.get_logger().info("应用启动流程开始 | v%s | build=%s",
                                getattr(version, "APP_VERSION", "?"),
                                getattr(version, "BUILD_TIME", "") or "dev")

    # ---------- 启动授权检查 ----------
    # Windows 高 DPI：在创建任何 Tk 窗口前声明 DPI 感知（PROCESS_SYSTEM_DPI_AWARE），
    # 使 Tk 逻辑坐标与 ImageGrab 物理坐标一致，避免在高缩放屏（如 150%）上
    # OCR 框选区域错位（Tk 报逻辑像素、ImageGrab 截物理像素，两者不一致会偏/缩放）。
    import sys as _sys
    if _sys.platform.startswith("win"):
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    app = ReportQcApp()
    # macOS 关键：必须先把主窗口显示出来，再弹 transient 子窗口（免责声明/激活）。
    # 若先 withdraw 主窗口，macOS 的 transient 子窗口会因父窗口隐藏而不渲染，
    # 导致 wait_window 死等、程序表现为"完全没启动"（用户看不到任何窗口）。
    app.deiconify()
    app.update_idletasks()
    app.lift()
    app.focus_force()

    # 1. 免责声明（首次运行才弹，同意后写入 license.dat，后续不再弹）
    if not license_utils.show_disclaimer(app):
        app.destroy()
        return

    # 2. 试用期检查
    status, data = license_utils.check_trial()
    if status == "expired":
        # 过期且未激活：弹激活框（模态 grab 覆盖主界面）。
        # macOS 注意：transient 子窗口需父窗口可见才能渲染，故不 withdraw 主窗口，
        # 由激活框的 grab_set + topmost 锁定交互并置顶。
        if not license_utils.show_activation_dialog(app):
            app.destroy()
            return
        app.lift()
        app.focus_force()

    # 在状态栏标注授权状态
    if status == "trial":
        app.session_var.set(f"试用期剩余 {data} 天 · 未监听")
        app._activate_btn.configure(text="激活")
    elif status == "activated":
        app.session_var.set("已激活 · 未监听")
        app._activate_btn.configure(text="重新激活")

    # 3. 账号登录关口：无账号强制创建首个账号，有账号则需登录（质控责任到人）
    if not app._require_login():
        app.destroy()
        return

    # ---------- 授权通过，启动后静默检查更新（延迟 3s 避免与启动争抢）----------
    app.after(3000, app._check_update_background)

    # ---------- 进入主循环 ----------
    log_utils.get_logger().info("进入主循环")
    app.mainloop()


if __name__ == "__main__":
    main()
