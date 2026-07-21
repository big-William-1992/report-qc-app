@echo off
setlocal enabledelayedexpansion
echo ===================================================
echo  Report QC App - Windows build script
echo  Started at %DATE% %TIME%
echo ===================================================
cd /d %~dp0..
echo Working dir: %CD%
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto NOPY
echo [OK] Using interpreter: %PY%
echo.

%PY% "%~dp0check_path.py"
if errorlevel 1 goto NONASCII
echo [OK] Path check passed, continuing build.
goto AFTERCHECK
:NONASCII
echo [WARN] Current folder path may contain Chinese or non-ASCII characters.
echo        PyInstaller may fail to build. Recommend moving the whole
echo        report_qc_app folder to a PURE ENGLISH path (e.g. C:\report_qc\).
echo        Continuing build anyway...
:AFTERCHECK
echo.

set "LOG=%~dp0build_log.txt"
echo Build log file: %LOG%
echo ===== Build start %DATE% %TIME% ===== > "%LOG%"

echo [1/3] Creating venv and installing PyInstaller...
echo (this step downloads packages; may take 2-5 minutes - please wait)
%PY% -m venv .venv
if errorlevel 1 goto VENVERR
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install pyinstaller
if errorlevel 1 goto PIPERR
echo [OK] PyInstaller installed.
echo.

echo [2/3] Building exe with PyInstaller...
pyinstaller build\report_qc.spec --noconfirm --clean
if errorlevel 1 goto BUILDERR
echo [OK] exe generated under dist\
echo.

echo [3/3] Building installer if Inno Setup 6 is present...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" goto NOISCC
"%ISCC%" build\setup.iss
echo [OK] Installer generated under installer\
goto DONE
:NOISCC
echo [INFO] Inno Setup 6 not found, skipped. You can distribute dist\ directly.
goto DONE

:NOPY
echo [ERROR] Python not found. Install Python 3.12 and make sure
echo        "Add python.exe to PATH" is checked during install.
echo        Download: https://www.python.org/downloads/windows/
goto END

:VENVERR
echo [ERROR] Failed to create venv.
goto END

:PIPERR
echo [ERROR] Failed to install PyInstaller.
goto END

:BUILDERR
echo [ERROR] Build failed.
goto END

:DONE
echo ===================================================
echo  DONE. exe is under: %CD%\dist\
echo  Build log: %LOG%
echo ===================================================

:END
pause
