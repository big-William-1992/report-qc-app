"""
auto_updater.py — 自动下载并更新（macOS 源码分发模型，纯标准库零依赖）

策略：
  1) 下载 GitHub 源码 tarball（含最新发布源码）到本地缓存。
  2) 主程序退出后，由一个**独立进程**的安装脚本替换应用目录里的
     src/ assets/ 等文件（删除旧文件再写入新文件）。
  3) 保留用户私有数据：assets/license.dat（激活码）、logs/（日志与诊断包）。
  4) 清除 macOS 隔离属性（com.apple.quarantine），避免无签名源码被拦截。
  5) 通过「启动星衍质控软件.command」重新启动。

为什么这样设计：当前分发是“源码 + .command 启动脚本”，不是打包 .app，
因此完全绕开 Gatekeeper 对 .app 的代码签名/公证要求，自动更新在 Mac 上
也能无阻碍运行。Windows 分支预留（下载 ReportQcSetup.exe 并运行安装包）。
"""
import os
import sys
import shutil
import tarfile
import subprocess
import threading
import urllib.request

APP_NAME = "星衍放射质控软件"

# GitHub 源码 tarball（任何 tag 都可用，无需额外 CI 产物）
TARBALL_URL = "https://api.github.com/repos/big-William-1992/report-qc-app/tarball/latest"
RELEASE_PAGE = "https://github.com/big-William-1992/report-qc-app/releases/latest"

# 应用目录中永不触碰的项（用户私有 / 运行时 / 密钥）
_EXCLUDE = {".git", ".workbuddy", "keys", "update"}

# 需要备份并恢复的显式用户数据（相对应用根目录）
_PRESERVE_FILES = [os.path.join("assets", "license.dat")]
_PRESERVE_DIRS = ["logs"]

# 独立安装脚本源码（仅用标准库，由主程序退出后运行，不依赖本模块导入）
_INSTALLER_SRC = r'''import os, sys, shutil, tarfile, subprocess, time, json

APP_DIR = sys.argv[1]
TAR = sys.argv[2]
PUBLISHED_AT = sys.argv[3] if len(sys.argv) > 3 else ""
time.sleep(3)  # 等待主程序完全退出

TMP = os.path.join(APP_DIR, "update", "_extract")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
with tarfile.open(TAR) as tf:
    tf.extractall(TMP)
roots = [d for d in os.listdir(TMP) if os.path.isdir(os.path.join(TMP, d))]
SRC = os.path.join(TMP, roots[0]) if roots else TMP

# 备份用户私有数据
BAK = os.path.join(APP_DIR, "update", "_bak")
shutil.rmtree(BAK, ignore_errors=True)
os.makedirs(BAK, exist_ok=True)
lic = os.path.join(APP_DIR, "assets", "license.dat")
if os.path.isfile(lic):
    shutil.copy(lic, os.path.join(BAK, "license.dat"))
logs_src = os.path.join(APP_DIR, "logs")
if os.path.isdir(logs_src):
    shutil.copytree(logs_src, os.path.join(BAK, "logs"))

# 删除旧文件（保留 .git/.workbuddy/keys/update）
EXCLUDE = {".git", ".workbuddy", "keys", "update"}
for name in os.listdir(APP_DIR):
    if name in EXCLUDE:
        continue
    p = os.path.join(APP_DIR, name)
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    except Exception:
        pass

# 写入新文件
for name in os.listdir(SRC):
    s = os.path.join(SRC, name)
    d = os.path.join(APP_DIR, name)
    try:
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    except Exception:
        pass

# 恢复用户私有数据
lic_bak = os.path.join(BAK, "license.dat")
if os.path.isfile(lic_bak):
    os.makedirs(os.path.join(APP_DIR, "assets"), exist_ok=True)
    shutil.copy(lic_bak, os.path.join(APP_DIR, "assets", "license.dat"))
logs_bak = os.path.join(BAK, "logs")
if os.path.isdir(logs_bak):
    dest = os.path.join(APP_DIR, "logs")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(logs_bak, dest)

# 清除隔离属性（对源码无副作用，能让无签名分发正常启动）
try:
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", APP_DIR],
                   capture_output=True, timeout=30)
except Exception:
    pass

# 写入本地 build_info.json，记录本次更新到的发布时间，形成比对闭环
if PUBLISHED_AT:
    try:
        os.makedirs(os.path.join(APP_DIR, "assets"), exist_ok=True)
        with open(os.path.join(APP_DIR, "assets", "build_info.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"build_time": PUBLISHED_AT, "commit": "from-release"}, f)
    except Exception:
        pass

# 重新启动（测试时可设 AU_NO_LAUNCH=1 跳过，仅验证文件替换）
if os.environ.get("AU_NO_LAUNCH") != "1":
    cmd = os.path.join(APP_DIR, "启动星衍质控软件.command")
    if os.path.exists(cmd):
        subprocess.Popen(["open", cmd])
    else:
        subprocess.Popen([sys.executable, os.path.join(APP_DIR, "src", "app.py")],
                         cwd=APP_DIR)

# 清理
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(BAK, ignore_errors=True)
try:
    os.remove(TAR)
except Exception:
    pass
'''


def app_dir():
    """应用根目录：src/auto_updater.py 的上两级。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def update_cache_dir():
    base = os.path.expanduser("~/Library/Application Support/" + APP_NAME)
    d = os.path.join(base, "update")
    os.makedirs(d, exist_ok=True)
    return d


def download(dest, progress_cb=None, timeout=180):
    """流式下载 tarball 到 dest。progress_cb(done, total) 回调（done/total 单位字节）。"""
    req = urllib.request.Request(
        TARBALL_URL,
        headers={"User-Agent": "xingyan-qc-update",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length", "0") or "0")
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    return dest


def make_installer(app_dir_path, tar_path):
    """写出独立安装脚本，返回脚本路径。"""
    path = os.path.join(update_cache_dir(), "update_install.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_INSTALLER_SRC)
    return path


def install_and_relaunch(tar_path, published_at=None):
    """启动独立安装进程（detached）。调用方随后应退出主程序。"""
    appdir = app_dir()
    script = make_installer(appdir, tar_path)
    args = [sys.executable, script, appdir, tar_path]
    if published_at:
        args.append(published_at)
    subprocess.Popen(
        args,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def download_async(dest, progress_cb=None, done_cb=None, timeout=180):
    """后台线程下载；done_cb(exc_or_None, dest) 在子线程调用。"""
    def _run():
        err = None
        try:
            download(dest, progress_cb, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            err = e
        if done_cb:
            done_cb(err, dest)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
