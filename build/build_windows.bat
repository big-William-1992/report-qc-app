@echo off
setlocal
echo ===================================================
echo   星衍AI放射质控软件 · 一键打包（双击启动）
echo ===================================================
echo.

rem 选择 Python 解释器：优先 py -3，其次 python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] 未找到 Python。请先安装 Python 3.10+ 并在安装时勾选
  echo         "Add python.exe to PATH"（添加到 PATH）。
  echo         下载: https://www.python.org/downloads/windows/
  goto END
)
echo [OK] 使用解释器: %PY%
echo.

rem 真正的一键流水线在 build_windows.py（纯 Python，无 GBK/errorlevel 坑）
%PY% "%~dp0build_windows.py"
if errorlevel 1 (
  echo.
  echo [ERROR] 打包未完成，请查看上方日志定位问题。
)

:END
echo.
echo 按任意键关闭窗口...
pause >nul
