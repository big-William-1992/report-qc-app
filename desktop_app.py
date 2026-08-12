"""
星衍AI放射质控 · 桌面端 WebView 壳
====================================
启动本地 FastAPI 服务（uvicorn 子进程），用系统原生 WebView 加载
`web/static/` 同一套 SPA 前端，实现「桌面端与 Web 端 UI 统一」：
一套前端代码，双端运行。Tkinter 版 app.py 自此退役（保留作历史参考）。

新增能力：
- 全局快捷键（后台热键）：用 pynput 注册系统级热键，即使焦点在 PACS/RIS 窗口，
  也能触发 runQC / saveToLibrary / toggleTheme / ocrHotkey。未装 pynput 时自动降级为
  仅 SPA 内快捷键（窗口聚焦时可用）。
- 原生桥 Bridge：暴露 hide_app/show_app/minimize_app 给前端 JS，供 OCR 采集前台
  窗口前让出焦点（避免截到/读到本应用自身）。

依赖：pip install pywebview pynput（macOS 用系统 WKWebView，无需额外浏览器引擎；
pynput 用于全局热键，缺失不影响主流程）

启动：python desktop_app.py   或双击「启动星衍质控软件.command」
"""
import os
import sys
import subprocess
import threading
import time
import urllib.request
import socket
from pathlib import Path

# 资源根目录：冻结（PyInstaller）后为 exe 所在目录（或 _MEIPASS），
# 普通源码运行则用本文件所在目录。冻结后 __file__ 指向 PYZ 合成路径，不能用于回溯。
if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable))))
else:
    ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
# 冻结（PyInstaller）后：把 exe 所在目录加入 path，确保 `from server import db`
# 这类「包内绝对导入」在 exe 内能解析（server 随 datas 平铺在 exe 目录）。
if getattr(sys, "frozen", False) and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 冻结关键修复：pywebview 6.x 在 Windows 上强制走 winforms/edgechromium
# 后端，两者都 import clr（pythonnet）。clr_loader 在 PyInstaller 打包后找不到
# Python.Runtime.dll 的加载目录，会抛：
#   RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize ...
# 必须在 import webview 之前，把 pythonnet 的 runtime 目录显式加入 DLL 搜索路径。
# （onedir 模式下 DLL 位于 <exe 同目录>/_internal/pythonnet/runtime/）
if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
    # 关键修复：pythonnet/clr_loader 默认走 .NET Core（coreclr）模式，靠 hostfxr
    # 探测本机 .NET 运行时；目标机器若未装 .NET Core Runtime 就抛
    # 「Failed to resolve Python.Runtime.Loader.Initialize」。
    # 而 Windows 10/11 系统级自带 .NET Framework 4.8，强制 PYTHONNET_RUNTIME=netfx
    # 走 .NET Framework 加载，无需用户安装任何额外运行时，根治 clr 加载失败。
    os.environ["PYTHONNET_RUNTIME"] = "netfx"
    _dll_dirs = [
        os.path.join(ROOT, "pythonnet", "runtime"),
        os.path.join(ROOT, "_internal", "pythonnet", "runtime"),
        os.path.join(ROOT, "clr_loader"),
        os.path.join(ROOT, "_internal", "clr_loader"),
    ]
    for _dd in _dll_dirs:
        if os.path.isdir(_dd):
            try:
                os.add_dll_directory(_dd)
            except Exception:
                pass


# ---------------------- 冻结版崩溃可见化 ----------------------
# 默认 console=False（无控制台黑窗），任何未捕获异常都会被静默吞掉，
# 表现为「双击 exe 没反应」。这里把异常写 crash.log 并弹系统错误框。
def _crash_path() -> str:
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = "."
    return os.path.join(base, "crash.log")


def show_fatal(title: str, text: str) -> None:
    """弹系统错误框（Windows 优先），并把内容追加写入同目录 crash.log。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10)
    except Exception:
        pass
    try:
        with open(_crash_path(), "a", encoding="utf-8") as f:
            f.write(f"\n[{title}] @ {__import__('datetime').datetime.now().isoformat()}\n{text}\n")
    except Exception:
        pass


def _excepthook(et, ev, tb) -> None:
    import traceback as _tb
    show_fatal("星衍质控 启动失败", "".join(_tb.format_exception(et, ev, tb)))


sys.excepthook = _excepthook


def _free_preferred_port(port: int):
    """尽力释放被占用的端口——但只杀本应用的残留实例，绝不误杀无关进程。

    判断依据：占用进程的命令行含本应用特征（exe 名 / desktop_app.py / uvicorn server.main）。
    仅 macOS / Linux 生效（用 lsof + ps 查命令行）；失败静默忽略，不影响后续流程。
    """
    def _is_ours(cmdline: str) -> bool:
        return any(tok in cmdline for tok in (
            "desktop_app", "report_qc", "server.main", "星衍质控", "report-qc-app",
        ))

    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                             capture_output=True, text=True).stdout.strip()
        if out:
            for pid in out.split():
                try:
                    cmd = subprocess.run(["ps", "-o", "command=", "-p", pid],
                                         capture_output=True, text=True).stdout or ""
                except Exception:
                    continue
                if not _is_ours(cmd):
                    continue  # 别人的进程，不碰
                try:
                    os.kill(int(pid), 15)
                except Exception:
                    pass
            time.sleep(0.6)
    except Exception:
        pass


def _resolve_port(preferred: int) -> int:
    """选一个可用端口：优先 preferred；被占用则尝试释放后复用，仍失败则让系统分配。

    避免「上一个实例没退出 / 测试遗留进程占用 8500」导致新实例 uvicorn 绑定失败、
    后端起不来、桌面窗口永远弹不出（表现为"软件打不开"）。
    """
    def _open(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return s
        except OSError:
            s.close()
            return None

    s = _open(preferred)
    if s:
        s.close()
        return preferred
    _free_preferred_port(preferred)
    s = _open(preferred)
    if s:
        s.close()
        return preferred
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _resolve_port(int(os.environ.get("XY_QC_PORT", "8500")))
HOST = "127.0.0.1"
BASE = f"http://{HOST}:{PORT}"

# 全局热键库（pynput）；缺失时降级
try:
    from pynput import keyboard as _pynput_kb
    _HAVE_PYNPUT = True
except Exception:
    _pynput_kb = None
    _HAVE_PYNPUT = False

# 默认全局热键（与 SPA 默认快捷键一致）。
# 注意：SPA 设置页重绑快捷键不会自动同步到全局热键（v1 已知限制）。
GLOBAL_HOTKEYS = {
    "<ctrl>+<enter>": "runQC()",
    "<ctrl>+s": "saveToLibrary()",
    "<ctrl>+t": "toggleTheme()",
    "<ctrl>+<shift>+o": "ocrHotkey()",
}

# pywebview 模块引用（main 中按需导入后写入，便于后台线程调用）
_WEBVIEW = None


# ---------------------- 剪贴板监听（复制即质控） ----------------------
# 浏览器 JS 受安全模型限制，无法在后台监听系统剪贴板；由桌面壳轮询实现：
# 检测到剪贴板出现新报告文本 → 通过 evaluate_js 推给前端自动分栏 + 质控。
_CLIP_WATCH = False          # 监听开关（前端设置页控制）
_CLIP_LAST = ""              # 上次已处理文本（去重）
_CLIP_COOLDOWN = 0.0         # 防抖：同一时刻不重复处理


def _norm_clip(s: str) -> str:
    if not s:
        return ""
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def _get_clipboard_text() -> str:
    """跨平台读取系统剪贴板纯文本（零第三方依赖）。
    - macOS:  pbpaste（Tk 在 macOS 读剪贴板有缺陷，用系统命令最稳）
    - Windows: ctypes Win32 CF_UNICODETEXT（读 Unicode 文本）
    - Linux:  xclip / wl-paste
    """
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["pbpaste"], capture_output=True, timeout=2).stdout
            return out.decode("utf-8", "ignore")
        if sys.platform.startswith("win"):
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            CF_UNICODETEXT = 13
            if not user32.OpenClipboard(None):
                return ""
            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return ""
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        for cmd in (["xclip", "-selection", "clipboard", "-o"], ["wl-paste"]):
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=2).stdout
                return out.decode("utf-8", "ignore")
            except Exception:
                continue
    except Exception:
        return ""
    return ""


def _clipwatch_loop():
    """后台线程：每 1s 轮询剪贴板，发现新文本推给前端 onClipboardCopy。"""
    global _CLIP_LAST, _CLIP_COOLDOWN
    while True:
        try:
            if _CLIP_WATCH:
                data = _norm_clip(_get_clipboard_text())
                now = time.time()
                if data and data != _CLIP_LAST and now - _CLIP_COOLDOWN > 2.0:
                    _CLIP_LAST = data
                    _CLIP_COOLDOWN = now
                    import json as _json
                    # json.dumps 产出合法 JS 字符串字面量，换行/引号/反斜杠均安全
                    _eval_js("window.onClipboardCopy && onClipboardCopy("
                             + _json.dumps(data) + ")")
            time.sleep(1.0)
        except Exception:
            time.sleep(1.0)


class Bridge:
    """暴露给前端 JS 的原生桥：隐藏/显示/最小化窗口 + 剪贴板监听开关。

    OCR 采集「前台 PACS 窗口」前需先让出焦点，否则会截到/读到本应用自身。
    """

    @staticmethod
    def _each(fn):
        if _WEBVIEW is None:
            return
        for w in _WEBVIEW.windows:
            try:
                fn(w)
            except Exception:
                pass

    def hide_app(self):
        Bridge._each(lambda w: w.hide())

    def show_app(self):
        Bridge._each(lambda w: w.show())

    def minimize_app(self):
        Bridge._each(lambda w: w.minimize())

    def setClipWatch(self, on: bool):
        """前端设置页开关：开启后后台监听剪贴板，复制报告即自动质控。"""
        global _CLIP_WATCH, _CLIP_LAST
        _CLIP_WATCH = bool(on)
        if on:
            _CLIP_LAST = ""   # 开启即刻处理当前剪贴板里已复制的内容
            print("[剪贴板] 监听已开启（复制即质控）")
        else:
            print("[剪贴板] 监听已关闭")

    def getClipWatch(self):
        return _CLIP_WATCH


def _eval_js(js: str):
    if _WEBVIEW is None:
        return
    for w in _WEBVIEW.windows:
        try:
            w.evaluate_js(js)
        except Exception:
            pass


def _start_hotkeys():
    """后台线程：注册系统级全局热键，触发时调用 SPA 内对应全局函数。"""
    if not _HAVE_PYNPUT:
        print("[提示] 未安装 pynput，全局快捷键（后台热键）不可用；"
              "窗口聚焦时仍可用 SPA 内快捷键。安装：pip install pynput")
        return
    try:
        bindings = {combo: (lambda js=js: _eval_js(js))
                    for combo, js in GLOBAL_HOTKEYS.items()}
        hot = _pynput_kb.GlobalHotKeys(bindings)
        print(f"[全局热键] 已注册 {len(bindings)} 个：", ", ".join(GLOBAL_HOTKEYS.keys()))
        hot.run()
    except Exception as e:
        print(f"[警告] 全局热键启动失败（可能缺少辅助功能/输入监控权限）：{e}")


# 冻结（PyInstaller，console=False）后 sys.stdout/sys.stderr 为 None。
# 许多库会调用 sys.stdout.isatty()（典型：uvicorn 的日志 formatter），
# 对 None 调用即抛 AttributeError: 'NoneType' object has no attribute 'isatty'，
# 导致后端日志初始化失败、整个后端起不来、桌面窗口打不开。
# 这里在进程早期把 None 的流重定向到 crash.log（UTF-8、行缓冲），
# 既解决 isatty 崩溃，也便于后续排查。源码运行（非冻结）不动。
def _ensure_std_streams():
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        _f = open(_crash_path(), "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = _f
    if sys.stderr is None:
        sys.stderr = _f


# uvicorn 默认 formatter 在 use_colors=None 时会调用 sys.stdout.isatty()，
# 冻结环境 sys.stdout 为 None 即崩溃。这里显式 use_colors=False 规避。
def _build_uvicorn_log_config():
    import uvicorn
    cfg = dict(uvicorn.config.LOGGING_CONFIG)
    for _name in ("default", "access"):
        _fmt = cfg.get("formatters", {}).get(_name)
        if isinstance(_fmt, dict):
            _fmt["use_colors"] = False
    return cfg


# 后台 uvicorn 线程的启动异常。daemon 线程内的异常不会冒泡到主线程，
# 且 console=False（冻结版无控制台）时 stderr 无处可去，异常会被静默吞掉——
# 结果是用户只看到「20s 内未就绪」，crash.log 里却没有任何根因。这里显式留存。
_UVICORN_ERROR: dict = {}


def _start_uvicorn():
    """在后台线程运行 FastAPI（复用当前解释器的 uvicorn）。"""
    try:
        import uvicorn
        config = uvicorn.Config("server.main:app", host=HOST, port=PORT,
                                log_level="warning", log_config=_build_uvicorn_log_config())
        uvicorn.Server(config).run()
    except BaseException:  # noqa: BLE001  含 SystemExit：端口绑定失败时 uvicorn 会直接退出
        import traceback as _tb
        _UVICORN_ERROR["tb"] = _tb.format_exc()


def _wait_ready(timeout: int = 20) -> bool:
    """轮询 health 接口，直到后端就绪。"""
    url = f"{BASE}/api/v1/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    # Windows 冻结（PyInstaller）下需尽早调用，避免子进程/线程相关异常。
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass

    # 冻结版无控制台，sys.stdout/stderr 为 None，尽早重定向到 crash.log，
    # 否则 uvicorn 等库初始化日志时调用 isatty() 会崩、后端起不来。
    _ensure_std_streams()

    print("星衍AI放射质控 · 启动中…")

    # 依赖预检：后端模块（含 cv2 / uvicorn / fastapi / sqlalchemy 等）必须可导入，
    # 否则后端起不来、health 永远不通、_wait_ready 超时后整个进程静默退出，
    # 表现为"软件打不开"。提前 import 一次，缺依赖时给出明确指引而不是干等。
    try:
        import server.main  # noqa: F401  触发后端全部依赖的导入
    except Exception as e:  # noqa: BLE001
        import traceback as _tb
        msg = ("后端依赖缺失或导入失败：\n" + repr(e) +
               "\n\n请先安装运行依赖：pip install -r requirements.txt\n"
               "Windows 用户可双击「启动星衍质控.bat」自动安装。\n"
               "详细错误已写入同目录 crash.log。")
        show_fatal("启动失败", msg + "\n\n" + _tb.format_exc())
        sys.exit(2)

    srv = threading.Thread(target=_start_uvicorn, daemon=True)
    srv.start()
    if not _wait_ready():
        # 带上后端线程的真实堆栈：否则 crash.log 里只有「未就绪」这句话本身，
        # 用户按提示去看日志却仍然定位不到根因（端口占用 / 运行期异常等）。
        detail = _UVICORN_ERROR.get("tb") or (
            "后台线程未抛出异常，后端可能仍在初始化或 health 未正常响应。\n"
            "请确认端口未被占用：Windows `netstat -ano | findstr :%d`，"
            "macOS/Linux `lsof -i :%d`。" % (PORT, PORT))
        show_fatal("启动失败",
                   "后端服务启动失败（20s 内未就绪）。\n"
                   f"监听地址：{BASE}\n"
                   "请检查依赖：pip install fastapi uvicorn pywebview\n"
                   "或查看同目录 crash.log 了解具体原因。\n\n"
                   "-- 后端线程详情 --\n" + detail)
        sys.exit(1)

    url = f"{BASE}/"
    try:
        import webview as _wv
        global _WEBVIEW
        _WEBVIEW = _wv
        # 启动全局热键监听（后台线程，不阻塞 WebView 主事件循环）
        threading.Thread(target=_start_hotkeys, daemon=True).start()
        # 剪贴板监听（复制即质控）：默认关闭，由前端设置页开关控制
        threading.Thread(target=_clipwatch_loop, daemon=True).start()
        try:
            _wv.create_window("星衍AI放射质控", url, width=1280, height=860,
                              min_size=(1024, 700), js_api=Bridge())
            _wv.start()
        except Exception as e:  # noqa: BLE001
            # WebView2 / Edge 运行时缺失、pythonnet(clr) 打包问题等导致原生窗口创建失败：
            # 降级到系统浏览器，而不是让进程崩溃（否则用户只看到"闪一下打不开"）。
            import webbrowser
            print(f"[警告] 原生窗口创建失败（{e}），改用系统浏览器打开：{url}")
            print("        若提示缺少 WebView2，请安装 Edge WebView2 运行时：")
            print("        https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/")
            try:
                webbrowser.open(url)
            except Exception:
                pass
            # 冻结版（console=False）下 sys.stdin 为 None，input() 会抛
            # RuntimeError: lost sys.stdin，造成「二次崩溃」盖住真实错误。这里兜底。
            if getattr(sys, "frozen", False):
                show_fatal("星衍质控 启动降级",
                           "原生窗口创建失败，已尝试改用系统浏览器打开。\n\n"
                           f"详情：{e}\n\n"
                           "若提示缺少 WebView2，请安装 Edge WebView2 运行时：\n"
                           "https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/")
            else:
                try:
                    input("按 Enter 退出…")
                except Exception:
                    pass
    except ImportError:
        # 未安装 pywebview 时回退到系统浏览器
        import webbrowser
        print(f"[提示] 未安装 pywebview，改用浏览器打开：{url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        if not getattr(sys, "frozen", False):
            try:
                input("按 Enter 退出…")
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback as _tb
        show_fatal("星衍质控 未捕获异常", _tb.format_exc())
        raise
