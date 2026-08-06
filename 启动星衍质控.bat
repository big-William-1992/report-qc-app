@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ===================================================
echo   星衍AI放射质控软件 · Windows 启动器（双击启动）
echo ===================================================
echo.

rem 1) 选择 Python 解释器：优先 py -3，其次 python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 未找到 Python。请先安装 Python 3.10+，并在安装时勾选
  echo         "Add python.exe to PATH"（添加到 PATH）。
  echo         下载: https://www.python.org/downloads/windows/
  goto END
)
echo [OK] 使用解释器: %PY%

rem 2) 首次运行：创建虚拟环境并安装依赖（之后复用，不再重装）
if not exist ".venv\Scripts\python.exe" (
  echo [*] 首次运行：创建虚拟环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败，请确认 Python 安装完整。
    goto END
  )
  echo [*] 安装运行依赖（首次需几分钟，请耐心等待）...
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试；或删除 .venv 目录重来。
    goto END
  )
  echo [OK] 依赖安装完成。
) else (
  echo [OK] 检测到已有 .venv，跳过安装。
)

rem 3) 启动桌面端（原生 WebView 窗口；若缺 WebView2 会自动降级到浏览器）
echo [*] 启动星衍AI放射质控...
.venv\Scripts\python.exe desktop_app.py
if errorlevel 1 (
  echo.
  echo [错误] 程序异常退出。常见排查：
  echo   1) 缺少 Edge / WebView2 运行时：安装 https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/
  echo   2) 依赖异常：删除 .venv 目录后重新双击本文件
  echo   3) 端口 8500 被占用：关闭占用程序后重试
)

:END
echo.
echo 按任意键关闭...
pause >nul
