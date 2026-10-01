"""
test_frontend_bundle.py — 前端 bundle 有效性 / 同步性守卫（2026-09-30 审计新增）

背景（**本轮最严重的事故**）：`index.html` 以**经典脚本**方式加载
`js/app.bundle.js`（无 type="module"，因为桌面应用要在 file:// 与 http:// 两条
路径下都能加载）。v4.3.6 的 `d73e2b9` 重新生成 bundle 时忘了剥掉顶层
`import`/`export`，于是浏览器解析直接 `SyntaxError: Unexpected token 'export'`：

    → 整个 SPA 的 JS 一行都不执行（登录闸门、按钮、渲染全废）
    → 但 HTTP 仍是 200、所有后端测试全绿、pytest 毫无察觉

本文件把「bundle 必须是合法经典脚本」+「必须与 modules/ 同步」钉死；
`node` 可用时再做一次真正的语法解析（最接近浏览器的判定）。
"""
import os
import re
import shutil
import subprocess

import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import subprocess_env  # noqa: E402


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "web", "static", "js")
_BUNDLE = os.path.join(_JS, "app.bundle.js")
_INDEX = os.path.join(_ROOT, "web", "static", "index.html")

sys.path.insert(0, os.path.join(_ROOT, "tools"))


def _bundle_text() -> str:
    with open(_BUNDLE, encoding="utf-8") as fh:
        return fh.read()


def test_index_loads_bundle_as_classic_script():
    """index.html 必须用经典脚本方式加载 bundle（file:// 下 ES 模块会被 CORS 拦）。"""
    html = open(_INDEX, encoding="utf-8").read()
    assert "js/app.bundle.js" in html, "index.html 未加载 app.bundle.js"
    for m in re.finditer(r"<script[^>]*src=[\"']js/app\.bundle\.js[^>]*>", html):
        assert 'type="module"' not in m.group(0), (
            "app.bundle.js 被当成 ES 模块加载——file:// 下会被 CORS 拦截")


def test_bundle_has_no_top_level_import_export():
    """经典脚本里 import/export 是语法错误（本次事故的直接原因）。"""
    bad = re.findall(r"^(?:import|export)\s.*$", _bundle_text(), re.M)
    assert not bad, (
        "app.bundle.js 含顶层 import/export，浏览器会 SyntaxError 导致整个前端不执行：\n"
        + "\n".join(bad[:5])
        + "\n修复：python3 tools/build_bundle.py")


def test_bundle_in_sync_with_modules():
    """bundle 必须由 modules/*.js 生成（改了模块忘了打包 = 改动不生效）。"""
    import build_bundle  # tools/build_bundle.py

    want = build_bundle.build_bundle_text()
    norm = lambda s: [ln.rstrip() for ln in s.splitlines() if ln.strip()]
    assert norm(_bundle_text()) == norm(want), (
        "app.bundle.js 与 modules/*.js 不同步。修复：python3 tools/build_bundle.py")


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_bundle_parses_as_classic_script():
    """最接近浏览器的判定：node 按脚本解析 bundle 必须通过。"""
    r = subprocess.run(["node", "--check", _BUNDLE],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"app.bundle.js 不是合法经典脚本：\n{r.stderr[:600]}\n"
        "修复：python3 tools/build_bundle.py")


def test_build_script_check_mode_passes():
    """打包脚本自带的 --check 必须通过（CI/本地一条命令即可自检）。"""
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "build_bundle.py"),
                        "--check"], capture_output=True, text=True, timeout=60, env=subprocess_env())
    assert r.returncode == 0, r.stdout + r.stderr
