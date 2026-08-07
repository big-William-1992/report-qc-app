# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包规格 —— 星衍AI放射质控软件（WebView 桌面版 / FastAPI 后端）
#
# 当前产品形态：
#   - desktop_app.py  桌面壳：后台线程起 uvicorn，再用系统原生 WebView 加载同一套 SPA
#   - server/main.py  FastAPI 后端：提供 /api/v1/* 并托管 web/static/ 前端
#   - web/static/     前端 SPA（index.html + app.js + css）
#   - src/            业务模块（engine / samplelib / ris / accounts / ocr_provider ...）
#   - assets/         规则配置 + OCR 离线模型（ocr_models/*.onnx）+ 运行期数据库
#
# 在 Windows 上执行： pyinstaller build\report_qc.spec
# 产物： dist\报告质控软件\报告质控软件.exe （单目录，含 server/web/assets/src）
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
server_dir = os.path.join(root, "server")
assets_dir = os.path.join(root, "assets")
web_dir = os.path.join(root, "web")

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

# ---- WebView（pywebview）依赖全量收集 ----
# pywebview 在 Windows 上按平台动态载入 webview.platforms.edgechrome 等子模块，
# PyInstaller 静态分析抓不到，用 collect_all 把整个包（含各平台实现）收进来。
try:
    from PyInstaller.utils.hooks import collect_all as _collect_all
    _wb, _wd, _wh = _collect_all("webview")
    _extra_binaries += _wb
    _extra_datas += _wd
    _extra_hiddenimports += _wh
except Exception as _e:
    print("WARNING: collect_all('webview') failed:", _e)

# ---- 后端核心依赖 SQLAlchemy 全量收集 ----
# SQLAlchemy 含按需加载的 C 扩展(cprocessors/cresultprocessor)与方言(dialects.*)，
# 仅靠 `from sqlalchemy import create_engine` 的静态分析在冻结时常漏整包，
# 导致 exe 启动报 ModuleNotFoundError: No module named 'sqlalchemy'。
# 用 collect_all 把「包 + C 扩展 + 全部方言」一并打进 exe，确保导入链完整。
try:
    from PyInstaller.utils.hooks import collect_all as _ca
    _sb_, _sd_, _sh_ = _ca("sqlalchemy")
    _extra_binaries += _sb_
    _extra_datas += _sd_
    _extra_hiddenimports += _sh_
except Exception as _e:
    print("WARNING: collect_all('sqlalchemy') failed:", _e)

# OCR 离线模型（assets/ocr_models 三个 onnx）单独显式列为 datas，双保险
_ocr_models_dir = os.path.join(assets_dir, "ocr_models")
if os.path.isdir(_ocr_models_dir):
    for _f in os.listdir(_ocr_models_dir):
        _fp = os.path.join(_ocr_models_dir, _f)
        if os.path.isfile(_fp):
            _extra_datas.append((_fp, os.path.join("assets", "ocr_models")))

# uvicorn 通过 importlib 动态载入 loops / protocols / ws 子模块，
# 冻结后字符串导入会失败，必须显式列出 hiddenimport。
_uvicorn_hidden = [
    "uvicorn",
    "uvicorn.loops.auto", "uvicorn.loops.asyncio", "uvicorn.loops.uvloop",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11",
    "uvicorn.protocols.http.httptools",
    "uvicorn.protocols.ws.auto", "uvicorn.protocols.ws.websockets",
    "uvicorn.protocols.ws.wsproto",
    "uvicorn.logging", "uvicorn.server", "uvicorn.workers",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
]

# server.main 在 desktop_app 里以字符串 "server.main:app" 传给 uvicorn，
# 属于运行时导入；同时它又通过「无 __init__ 的 server 包」被引用，必须显式 hiddenimport。
_server_hidden = [
    "server.main", "server.db", "server.models", "server.license_web",
    # 双保险：即使上面 collect_all 因环境差异未生效，也显式让 sqlalchemy 进入冻结包
    "sqlalchemy",
]

a = Analysis(
    [os.path.join(root, "desktop_app.py")],
    pathex=[root, src_dir, server_dir],
    binaries=_extra_binaries,
    datas=[
        (src_dir, "src"),                       # 业务模块（engine/samplelib/ris/accounts）
        (server_dir, "server"),                # FastAPI 后端（main/db/models/license_web）
        (assets_dir, "assets"),                # 规则配置 + OCR 离线模型 + 运行期数据库
        (web_dir, "web"),                      # 前端 SPA（web/static）
    ] + _extra_datas,
    hiddenimports=[
        "engine", "samplelib", "ris", "accounts", "license_utils",
        "version", "log_utils", "update_check", "auto_updater", "webbrowser",
        "logging.handlers", "cryptography",
        "sqlite3",
        # 屏幕区域 OCR 监控（本地离线 RapidOCR）—— 依赖由 collect_all 兜底收集
        "ocr_provider", "rapidocr_onnxruntime",
        "rapidocr_onnxruntime.rapid_ocr_api", "rapidocr_onnxruntime.utils",
        "onnxruntime", "numpy", "PIL", "PIL.ImageGrab", "PIL.Image",
        # WebView 桌面壳（Windows 用 Edge WebView2）
        "webview",
    ] + _extra_hiddenimports + _uvicorn_hidden + _server_hidden,
    hookspath=[],
    runtime_hooks=[],
    # pynput 仅全局热键（可选，缺则降级为 SPA 内快捷键）；非打包依赖，
    # 且若被误收集会拉入 pyobjc/pywin32 导致构建失败，故显式排除。
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
