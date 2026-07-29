# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包规格 —— 医学影像报告质控软件
# 在 Windows 上执行： pyinstaller build\report_qc.spec
# 产物： dist\报告质控软件\报告质控软件.exe （单目录，含 assets/）
import os

block_cipher = None
# 项目根目录（build/ 的上一级）。
# PyInstaller 在运行 spec 时会注入 SPECPATH；旧版本则用 __file__，做兼容回退。
try:
    _spec_dir = SPECPATH
except NameError:
    _spec_dir = os.path.dirname(os.path.abspath(__file__))
root = os.path.abspath(os.path.join(_spec_dir, ".."))
src_dir = os.path.join(root, "src")
assets_dir = os.path.join(root, "assets")

# ---- OCR 依赖全量收集 ----
# rapidocr-onnxruntime 的运行依赖含 cv2(opencv)、onnxruntime、numpy、pyclipper、
# Shapely、PyYAML、six。仅列 rapidocr 顶层 hiddenimport 不稳妥（纯 Python 依赖易漏），
# 用 collect_all 自动收齐该包及其全部依赖的模块/数据/二进制（含 opencv 原生 dll、
# Shapely/pyclipper 的 C 扩展等），确保「OCR 需要的插件一并打进 exe」。
_extra_binaries = []
_extra_datas = []
_extra_hiddenimports = []
try:
    from PyInstaller.utils.hooks import collect_all
    _rb, _rd, _rh = collect_all("rapidocr_onnxruntime")
    _extra_binaries += _rb
    _extra_datas += _rd
    _extra_hiddenimports += _rh
except Exception as _e:  # collect_all 失败不应阻断构建，仅告警
    print("WARNING: collect_all('rapidocr_onnxruntime') failed:", _e)

# OCR 离线模型（assets/ocr_models 三个 onnx）单独显式列为 datas，双保险
_ocr_models_dir = os.path.join(assets_dir, "ocr_models")
if os.path.isdir(_ocr_models_dir):
    for _f in os.listdir(_ocr_models_dir):
        _fp = os.path.join(_ocr_models_dir, _f)
        if os.path.isfile(_fp):
            _extra_datas.append((_fp, os.path.join("assets", "ocr_models")))

a = Analysis(
    [os.path.join(src_dir, "app.py")],
    pathex=[src_dir],
    binaries=_extra_binaries,
    datas=[
        (src_dir, "src"),                       # 业务模块（engine/samplelib/ris）
        (assets_dir, "assets"),                 # 规则配置 + 样本库初始数据
    ] + _extra_datas,
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog",
                   "tkinter.messagebox", "tkinter.scrolledtext", "sqlite3",
                   "engine", "samplelib", "ris", "license_utils",
                   "version", "log_utils", "update_check", "auto_updater", "webbrowser",
                   "logging.handlers", "cryptography",
                   # 屏幕区域 OCR 监控（本地离线 RapidOCR）—— 依赖由 collect_all 兜底收集
                   "ocr_provider", "rapidocr_onnxruntime",
                   "rapidocr_onnxruntime.rapid_ocr_api", "rapidocr_onnxruntime.utils",
                   "onnxruntime", "numpy", "PIL", "PIL.ImageGrab", "PIL.Image"],
    hookspath=[],
    runtime_hooks=[],
    # pynput 仅在 macOS 后台全局快捷键监听时按需 pip install 使用（非打包依赖），
    # 且在 Windows 打包机上若被误收集会拉入 pyobjc/pywin32 导致构建失败，故显式排除。
    excludes=["pynput"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="报告质控软件",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # Windows 下不弹控制台黑窗
    icon=os.path.join(assets_dir, "app.ico") if os.path.exists(os.path.join(assets_dir, "app.ico")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="报告质控软件",
)
