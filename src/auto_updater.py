"""
auto_updater.py — 自动下载并更新（零依赖，纯标准库）

分发模型与策略：
  macOS：源码 + .command 启动脚本。
    1) 下载 GitHub 源码 tarball（含最新发布源码）到本地缓存。
    2) 主程序退出后，由**独立进程**的安装脚本替换应用目录里的
       src/ assets/ 等文件（删除旧文件再写入新文件）。
    3) 保留用户私有数据：assets/license.dat（激活码）、logs/（日志）。
    4) 清除 macOS 隔离属性，避免无签名源码被拦截。
    5) 通过「启动星衍质控软件.command」重新启动。

  Windows：PyInstaller 打包的便携 exe（报告质控软件.exe + _internal/）。
    1) 下载 Releases 里的 report-qc-portable.zip 到本地缓存。
    2) 主程序退出后，由**独立的 PowerShell 安装器**解包并替换
       exe 与 _internal/（删除不在新包里的旧文件，清理残留）。
    3) 保留用户私有数据：assets/license.dat、logs/。
    4) 通过 报告质控软件.exe 重新启动。
    之所以用独立 PowerShell 进程（而非让 exe 自己替换自己），是为了避免
    Windows 文件锁：主 exe 退出后安装器才能安全覆盖 exe 本体。

为什么这样设计：macOS 分发绕开 Gatekeeper 对 .app 的签名/公证要求；
Windows 便携版无需安装即可替换，安装版（ReportQcSetup.exe）作为人工
兜底入口保留。两套逻辑均通过 AU_NO_LAUNCH=1 跳过重启，便于自动化测试。
"""
import os
import sys
import shutil
import tarfile
import subprocess
import threading
import urllib.request

APP_NAME = "星衍放射质控软件"

IS_WINDOWS = sys.platform.startswith("win")

# macOS：源码 tarball（任何 tag 都可用，无需额外 CI 产物）
TARBALL_URL = "https://api.github.com/repos/big-William-1992/report-qc-app/tarball/latest"
# Windows：Releases 里的便携 zip（解压即得 报告质控软件.exe + _internal/）
PORTABLE_ZIP_URL = ("https://github.com/big-William-1992/report-qc-app/"
                    "releases/download/latest/report-qc-portable.zip")
RELEASE_PAGE = "https://github.com/big-William-1992/report-qc-app/releases/latest"

# 应用目录中永不触碰的项（用户私有 / 运行时 / 密钥）
# macOS 安装器会删 assets 后再用 tarball 的 assets 覆盖（tarball 含此目录）；
# Windows 安装器保留 assets（zip 不含 assets，里面是用户激活码/配置）。
_MAC_EXCLUDE = {".git", ".workbuddy", "keys", "update"}
_WIN_EXCLUDE = {".git", ".workbuddy", "keys", "update", "assets", "logs"}

# macOS 需备份并恢复的用户数据（相对应用根目录）。
# 注意：源码运行时样本库/账号库就在 assets/ 下（samplelib.py / server/db.py），
# 而安装器会先删整个 assets/ 再用 tarball 覆盖——tarball 不含这些 *.db（被
# .gitignore 排除），若不备份恢复，更新后用户样本库/账号库会全部丢失。
_PRESERVE_FILES = [
    os.path.join("assets", "license.dat"),
    os.path.join("assets", "samples.db"),     # 样本库（用户质控结果）
    os.path.join("assets", "qc.db"),          # 账号/科室/权限库
    os.path.join("assets", "accounts.db"),    # 旧版账号库（兼容读取）
]
_PRESERVE_DIRS = ["logs"]


def app_dir():
    """应用根目录。

    - 冻结（PyInstaller）时：exe 所在目录（报告质控软件.exe + _internal/）。
    - 源码运行时：src/auto_updater.py 的上两级。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def update_cache_dir():
    d = os.path.join(app_dir(), "update")
    os.makedirs(d, exist_ok=True)
    return d


def default_archive_name():
    """下载产物的缓存文件名（平台相关）。"""
    return "latest.zip" if IS_WINDOWS else "latest.tar.gz"


# ---- macOS 安装器（独立 Python 进程）---------------------------------------
_INSTALLER_SRC = r'''import os, sys, shutil, tarfile, subprocess, time, json

APP_DIR = sys.argv[1]
TAR = sys.argv[2]
PUBLISHED_AT = sys.argv[3] if len(sys.argv) > 3 else ""
time.sleep(3)  # 等待主程序完全退出

TMP = os.path.join(APP_DIR, "update", "_extract")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
with tarfile.open(TAR) as tf:
    # filter="data" 需 Python 3.12+；macOS 系统 Python（3.9）不支持会 TypeError，
    # 这里做版本兼容，低版本退化为无过滤解包（tarball 来自自有仓库，信任源）。
    try:
        tf.extractall(TMP, filter="data")
    except TypeError:
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
# 备份用户数据库（样本库/账号库）——tarball 不含 *.db，不恢复即丢失
for _db in ("samples.db", "qc.db", "accounts.db"):
    _src_db = os.path.join(APP_DIR, "assets", _db)
    if os.path.isfile(_src_db):
        shutil.copy(_src_db, os.path.join(BAK, _db))
logs_src = os.path.join(APP_DIR, "logs")
if os.path.isdir(logs_src):
    shutil.copytree(logs_src, os.path.join(BAK, "logs"))
# 备份用户自定义规则配置（避免更新被 tarball 覆盖丢失）
rc_src = os.path.join(APP_DIR, "assets", "rules_config.json")
if os.path.isfile(rc_src):
    shutil.copy(rc_src, os.path.join(BAK, "rules_config.json"))
# 备份本地 OCR 模型（可能被用户替换为医院自训模型，或新 tarball 漏带时兜底）
ocr_src = os.path.join(APP_DIR, "assets", "ocr_models")
if os.path.isdir(ocr_src):
    shutil.copytree(ocr_src, os.path.join(BAK, "ocr_models"))

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
# 恢复用户数据库（样本库/账号库）
for _db in ("samples.db", "qc.db", "accounts.db"):
    _bak_db = os.path.join(BAK, _db)
    if os.path.isfile(_bak_db):
        os.makedirs(os.path.join(APP_DIR, "assets"), exist_ok=True)
        shutil.copy(_bak_db, os.path.join(APP_DIR, "assets", _db))
logs_bak = os.path.join(BAK, "logs")
if os.path.isdir(logs_bak):
    dest = os.path.join(APP_DIR, "logs")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(logs_bak, dest)
# 恢复用户自定义规则配置（用户版本优先，避免更新被 tarball 覆盖丢失）
rc_bak = os.path.join(BAK, "rules_config.json")
if os.path.isfile(rc_bak):
    os.makedirs(os.path.join(APP_DIR, "assets"), exist_ok=True)
    shutil.copy(rc_bak, os.path.join(APP_DIR, "assets", "rules_config.json"))
# 恢复 OCR 模型：仅当新版 tarball 未携带该目录时补齐（新版有则跟随新模型，不覆盖）
ocr_bak = os.path.join(BAK, "ocr_models")
ocr_dst = os.path.join(APP_DIR, "assets", "ocr_models")
if os.path.isdir(ocr_bak) and not os.path.isdir(ocr_dst):
    os.makedirs(os.path.join(APP_DIR, "assets"), exist_ok=True)
    shutil.copytree(ocr_bak, ocr_dst)

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


# ---- Windows 安装器（独立 PowerShell 进程）--------------------------------
_INSTALLER_PS_WIN = r'''
param(
    [string]$AppDir,
    [string]$Zip,
    [string]$PublishedAt = ""
)

Start-Sleep -Seconds 3

$TMP = Join-Path (Join-Path $AppDir "update") "_extract"
$BAK = Join-Path (Join-Path $AppDir "update") "_bak"
$DBG = Join-Path (Join-Path $AppDir "update") "installer_debug.log"
function Log($m) { "$m" | Add-Content -Encoding UTF8 $DBG }

New-Item -ItemType Directory -Path (Join-Path $AppDir "update") -Force | Out-Null
"START $(Get-Date) AppDir=$AppDir Zip=$Zip" | Set-Content -Encoding UTF8 $DBG

if (Test-Path $TMP) { Remove-Item $TMP -Recurse -Force }
if (Test-Path $BAK) { Remove-Item $BAK -Recurse -Force }
New-Item -ItemType Directory -Path $TMP -Force | Out-Null
New-Item -ItemType Directory -Path $BAK -Force | Out-Null

# 解包便携 zip（顶层为 报告质控软件.exe + _internal/）
try {
    Expand-Archive -Path $Zip -DestinationPath $TMP -Force -ErrorAction Stop
    $extracted = @(Get-ChildItem $TMP)
    Log "EXPAND OK items=$($extracted.Count) names=$($extracted.Name -join ',')"
} catch {
    Log "EXPAND FAIL ($_); 回退 tar.exe"
    try {
        & tar.exe -xf $Zip -C $TMP 2>&1 | Out-Null
        $extracted = @(Get-ChildItem $TMP)
        Log "TAR OK items=$($extracted.Count) names=$($extracted.Name -join ',')"
    } catch {
        Log "TAR FAIL: $_"
        exit 1
    }
}

# 永不删除的项（用户私有 / 运行时 / 密钥 / 用户数据）
$EXCLUDE = @(".git", ".workbuddy", "keys", "update", "assets", "logs")

# 备份用户私有数据：激活码 + 日志
$lic = Join-Path (Join-Path $AppDir "assets") "license.dat"
if (Test-Path $lic) { Copy-Item $lic (Join-Path $BAK "license.dat") -Force; Log "BACKUP lic OK" }
$logsSrc = Join-Path $AppDir "logs"
if (Test-Path $logsSrc) { Copy-Item $logsSrc (Join-Path $BAK "logs") -Recurse -Force; Log "BACKUP logs OK" }

# 新包顶层条目
$newItems = Get-ChildItem $TMP | Select-Object -ExpandProperty Name

# 删除旧版中：不在排除集、且不在新包顶层中的项（清理残留旧 _internal / 旧 dll 等）
foreach ($item in Get-ChildItem $AppDir) {
    if ($EXCLUDE -contains $item.Name) { continue }
    if ($newItems -contains $item.Name) { continue }
    Remove-Item $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
    Log "DEL $($item.Name)"
}

# 写入新文件（覆盖 exe 与 _internal）
foreach ($item in Get-ChildItem $TMP) {
    $dest = Join-Path $AppDir $item.Name
    try {
        if ($item.PSIsContainer) {
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            Copy-Item $item.FullName $dest -Recurse -Force -ErrorAction Stop
        } else {
            Copy-Item $item.FullName $dest -Force -ErrorAction Stop
        }
        Log "COPY $($item.Name) OK"
    } catch {
        Log "COPY $($item.Name) FAIL: $_"
    }
}

# 恢复用户私有数据
$licBak = Join-Path $BAK "license.dat"
if (Test-Path $licBak) {
    New-Item -ItemType Directory -Path (Join-Path $AppDir "assets") -Force | Out-Null
    Copy-Item $licBak (Join-Path (Join-Path $AppDir "assets") "license.dat") -Force
    Log "RESTORE lic OK"
}
$logsBak = Join-Path $BAK "logs"
if (Test-Path $logsBak) {
    $logsDest = Join-Path $AppDir "logs"
    if (Test-Path $logsDest) { Remove-Item $logsDest -Recurse -Force }
    Copy-Item $logsBak $logsDest -Recurse -Force
    Log "RESTORE logs OK"
}

# 记录 build_info，形成版本比对闭环
if ($PublishedAt -ne "") {
    New-Item -ItemType Directory -Path (Join-Path $AppDir "assets") -Force | Out-Null
    @{ build_time = $PublishedAt; commit = "from-release" } | ConvertTo-Json | `
        Set-Content (Join-Path (Join-Path $AppDir "assets") "build_info.json") -Encoding UTF8
    Log "BUILD_INFO written"
}

# 重新启动（AU_NO_LAUNCH=1 时跳过，仅验证文件替换）
if ($env:AU_NO_LAUNCH -ne "1") {
    $exe = Join-Path $AppDir "报告质控软件.exe"
    if (Test-Path $exe) { Start-Process -FilePath $exe }
}

# 清理
if (Test-Path $TMP) { Remove-Item $TMP -Recurse -Force }
if (Test-Path $BAK) { Remove-Item $BAK -Recurse -Force }
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Log "DONE"
'''


def _download_url():
    return PORTABLE_ZIP_URL if IS_WINDOWS else TARBALL_URL


def download(dest, progress_cb=None, timeout=180):
    """流式下载更新包到 dest。progress_cb(done, total) 回调（单位字节）。

    平台自动选择：macOS 下载源码 tarball，Windows 下载便携 zip。
    """
    req = urllib.request.Request(
        _download_url(),
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


def make_installer(app_dir_path, archive_path):
    """写出独立安装脚本，返回脚本路径（平台相关）。"""
    if IS_WINDOWS:
        path = os.path.join(update_cache_dir(), "win_update.ps1")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_INSTALLER_PS_WIN)
    else:
        path = os.path.join(update_cache_dir(), "update_install.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_INSTALLER_SRC)
    return path


def install_and_relaunch(archive_path, published_at=None):
    """启动独立安装进程（detached）。调用方随后应退出主程序。

    - macOS：用系统 Python 运行 update_install.py。
    - Windows：用 PowerShell 运行 win_update.ps1（独立于 exe，避免文件锁）。
    """
    appdir = app_dir()
    script = make_installer(appdir, archive_path)
    if IS_WINDOWS:
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", script, appdir, archive_path]
        if published_at:
            args.append(published_at)
    else:
        args = [sys.executable, script, appdir, archive_path]
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
