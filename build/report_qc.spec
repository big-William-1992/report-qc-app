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

# ---- 质控报告单 PDF 导出（reportlab，含其中文字体注册机制）----
# reportlab 纯 Python + 少量二进制，collect_all 收齐 platypus/pdfbase 等子模块，
# 确保 PDF 导出接口在冻结环境可用（缺失时引擎已有明确降级提示，不影响 DOCX/CSV 导出）。
try:
    from PyInstaller.utils.hooks import collect_all as _rl_collect
    _rlb, _rld, _rlh = _rl_collect("reportlab")
    _extra_binaries += _rlb
    _extra_datas += _rld
    _extra_hiddenimports += _rlh
except Exception as _e:
    print("WARNING: collect_all('reportlab') failed:", _e)

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

# ---- pythonnet(clr) / clr_loader 全量收集（Windows 关键！）----
# pywebview 6.x 在 Windows 上默认只用 winforms 后端，强制依赖 pythonnet(clr)。
# PyInstaller 静态分析能抓到 `import clr` 的模块，但 clr_loader 在冻结环境无法
# 定位 Python.Runtime.dll 的运行时目录，导致：
#   RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize ...
# （pywebview/pythonnet/clr_loader + PyInstaller 的经典打包冲突，见 pywebview#1215）
# 必须 collect_all 把 pythonnet 的 runtime DLL / runtimeconfig / hooks 与
# clr_loader 的加载器全部打进 dist，并配合 desktop_app.py 入口 os.add_dll_directory。
#
# 【关键】.NET 相关 DLL（Python.Runtime.dll / ClrLoader.dll）绝不能走 UPX：
# UPX 压缩 .NET 程序集会破坏其内部结构，导致「DLL 存在但无法解析
# Python.Runtime.Loader.Initialize」→ 首次 netfx import clr 失败 → pywebview
# 的 except 强制切 coreclr → 目标机无 .NET Core → 崩溃（clr_loader\vfx.py）。
# 因此把这些 DLL 从 binaries（会被 UPX 压缩）移到 datas（原样复制）。
def _pn_move_dll_to_datas(bins, datas):
    """把 collect_all 返回中 .dll/.pdb 条目从 binaries 移到 datas，规避 UPX 破坏。"""
    moved = []
    kept = []
    for _b in bins:
        _src, _dest = _b[0], _b[1]
        _low = os.path.basename(_src).lower()
        if _low.endswith((".dll", ".pdb", ".config", ".json", ".xml")):
            datas.append(_b)   # (src, dest) 元组结构一致，原样放 datas
            moved.append(_low)
        else:
            kept.append(_b)
    if moved:
        print("  [pythonnet] 移出 UPX 范围:", ", ".join(sorted(set(moved))))
    return kept

try:
    from PyInstaller.utils.hooks import collect_all as _pn_collect
    _pnb, _pnd, _pnh = _pn_collect("pythonnet")
    _pnb = _pn_move_dll_to_datas(_pnb, _pnd)
    _extra_binaries += _pnb
    _extra_datas += _pnd
    _extra_hiddenimports += _pnh
    _clb, _cld, _clh = _pn_collect("clr_loader")
    _clb = _pn_move_dll_to_datas(_clb, _cld)
    _extra_binaries += _clb
    _extra_datas += _cld
    _extra_hiddenimports += _clh
except Exception as _e:
    print("WARNING: collect_all('pythonnet'/'clr_loader') failed:", _e)

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
        # R19 读音相似错字（高频词库锚定 + pypinyin 自动推导）；pypinyin 若未安装
        # 会由引擎 try/except 降级，不影响构建
        "highfreq_lexicon", "pypinyin", "pypinyin.constants", "pypinyin.standard",
        "pypinyin.style", "pypinyin.seg",
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

# ---- 根治：彻底禁用 UPX（对 .NET 程序集破坏性过大） ----
# 此前用户下载后报：
#   RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize
# 根因：PyInstaller 内置 hook-clr.py 用 collect_dynamic_libs 把 pythonnet /
# clr_loader 的 DLL 收集进 binaries；COLLECT(upx=True) 时被 UPX 压缩，
# 破坏 .NET 混合程序集（C++/CLI）的导出表——DLL 能 LoadLibrary 但
# GetProcAddress 找不到 Python.Runtime.Loader.Initialize（clr_loader 的
# NetFx._get_callable 反射该符号失败）。
#
# 结论：UPX 压缩对 .NET 程序集（pythonnet/clr_loader）是破坏性的，且 PyInstaller
# 的 upx_exclude 只匹配「源文件路径」、依赖环境中的 UPX 版本与行为，无法做到
# 100% 可靠排除（此前 upx_exclude 方案在打包机验证仍失败）。既然稳定性优先
# 于体积，这里直接 upx=False 全局禁用 UPX——所有二进制原样保留，彻底消除
# 「UPX 破坏导出表」这一类问题；代价是 exe 体积增大（约 30~50MB），可接受。
# （collect_all 收进 datas 的 DLL 本就原样复制，与 upx 无关；保留 _pn_move_dll_to_datas
#   避免 hook 收集的 binaries 副本与 datas 同 dest 覆盖，双保险。）

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
    upx=False,
    console=False,          # Windows 下不弹控制台黑窗
    icon=os.path.join(assets_dir, "app.ico") if os.path.exists(os.path.join(assets_dir, "app.ico")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="报告质控软件",
)
