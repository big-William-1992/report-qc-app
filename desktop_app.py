"""
星衍AI放射质控 · 桌面端 WebView 壳
====================================
启动本地 FastAPI 服务（uvicorn 子进程），用系统原生 WebView 加载
`web/static/` 同一套 SPA 前端，实现「桌面端与 Web 端 UI 统一」：
一套前端代码，双端运行。Tkinter 版 app.py 自此退役（保留作历史参考）。

依赖：pip install pywebview（macOS 用系统 WKWebView，无需额外浏览器引擎）

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
        import webview
        webview.create_window("星衍AI放射质控", url, width=1280, height=860,
                              min_size=(1024, 700))
        webview.start()
    except ImportError:
        # 未安装 pywebview 时回退到系统浏览器
        import webbrowser
        print(f"[提示] 未安装 pywebview，改用浏览器打开：{url}")
        webbrowser.open(url)
        input("按 Enter 退出…")


if __name__ == "__main__":
    main()
