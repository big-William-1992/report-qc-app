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
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

PORT = int(os.environ.get("XY_QC_PORT", "8500"))
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


class Bridge:
    """暴露给前端 JS 的原生桥：隐藏/显示/最小化窗口。

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


def _start_uvicorn():
    """在后台线程运行 FastAPI（复用当前解释器的 uvicorn）。"""
    import uvicorn
    config = uvicorn.Config("server.main:app", host=HOST, port=PORT, log_level="warning")
    uvicorn.Server(config).run()


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
    print("星衍AI放射质控 · 启动中…")
    srv = threading.Thread(target=_start_uvicorn, daemon=True)
    srv.start()
    if not _wait_ready():
        print("[错误] 后端服务启动失败，请检查依赖：pip install fastapi uvicorn pywebview")
        sys.exit(1)

    url = f"{BASE}/"
    try:
        import webview as _wv
        global _WEBVIEW
        _WEBVIEW = _wv
        # 启动全局热键监听（后台线程，不阻塞 WebView 主事件循环）
        threading.Thread(target=_start_hotkeys, daemon=True).start()
        _wv.create_window("星衍AI放射质控", url, width=1280, height=860,
                          min_size=(1024, 700), js_api=Bridge())
        _wv.start()
    except ImportError:
        # 未安装 pywebview 时回退到系统浏览器
        import webbrowser
        print(f"[提示] 未安装 pywebview，改用浏览器打开：{url}")
        webbrowser.open(url)
        input("按 Enter 退出…")


if __name__ == "__main__":
    main()
