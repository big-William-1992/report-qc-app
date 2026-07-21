#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Report QC App - Windows build helper (pure Python, NO .bat)

Why this exists:
    The previous .bat launcher kept failing on Chinese Windows due to
    GBK encoding, 'then'/'else' parsing, and errorlevel quirks.
    A pure Python script avoids ALL of those problems.

    How to run (PowerShell, inside the build/ folder):
        py build_windows.py

What it does:
    [1/3] create a venv and install PyInstaller
    [2/3] build the exe with PyInstaller (build/report_qc.spec)
    [3/3] build the Inno Setup installer IF Inno Setup 6 is installed
          (otherwise you can run dist\报告质控软件\报告质控软件.exe directly)
"""

import os
import sys
import subprocess


def step(msg):
    print("\n=== " + msg + " ===", flush=True)


def run(cmd):
    print("> " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    print("  (exit code: %d)" % rc, flush=True)
    return rc


def main():
    build_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(build_dir, ".."))
    os.chdir(root)
    print("Project root: " + root, flush=True)

    # ---- [1/3] venv + PyInstaller ----
    step("[1/3] Create venv and install PyInstaller")
    venv_dir = os.path.join(root, ".venv")
    if not os.path.isdir(venv_dir):
        if run([sys.executable, "-m", "venv", ".venv"]) != 0:
            print("[ERROR] Failed to create venv.", flush=True)
            return 1
    if os.name == "nt":
        vpy = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        vpy = os.path.join(venv_dir, "bin", "python")
    if run([vpy, "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        print("[WARN] pip upgrade failed, continue anyway.", flush=True)
    if run([vpy, "-m", "pip", "install", "pyinstaller"]) != 0:
        print("[ERROR] Failed to install PyInstaller.", flush=True)
        return 1
    print("[OK] PyInstaller installed.", flush=True)

    # ---- [2/3] PyInstaller ----
    step("[2/3] Build exe with PyInstaller")
    spec = os.path.join("build", "report_qc.spec")
    if run([vpy, "-m", "PyInstaller", spec, "--noconfirm", "--clean"]) != 0:
        print("[ERROR] Build failed.", flush=True)
        return 1
    print("[OK] exe generated under dist" + os.sep, flush=True)

    # ---- [3/3] Inno Setup (optional) ----
    step("[3/3] Build installer if Inno Setup 6 is present")
    iscc = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if os.path.exists(iscc):
        if run([iscc, os.path.join(build_dir, "setup.iss")]) == 0:
            print("[OK] Installer generated under installer" + os.sep, flush=True)
        else:
            print("[WARN] Inno Setup compile failed.", flush=True)
    else:
        print("[INFO] Inno Setup 6 not found; skipping.", flush=True)
        print("       You can run dist" + os.sep + "报告质控软件" + os.sep
              + "报告质控软件.exe directly.", flush=True)

    print("\n===================================================", flush=True)
    print(" DONE. exe is under: " + os.path.join(root, "dist"), flush=True)
    print("===================================================", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("[FATAL] " + str(e), flush=True)
        sys.exit(1)
