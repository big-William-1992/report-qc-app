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

a = Analysis(
    [os.path.join(src_dir, "app.py")],
    pathex=[src_dir],
    binaries=[],
    datas=[
        (src_dir, "src"),                       # 业务模块（engine/samplelib/ris）
        (assets_dir, "assets"),                 # 规则配置 + 样本库初始数据
    ],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog",
                   "tkinter.messagebox", "tkinter.scrolledtext", "sqlite3"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
