#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星衍AI放射质控软件 · Windows 一键打包脚本（纯 Python，规避 .bat 中文编码坑）

为什么用 Python 而不是 .bat：
    旧版 .bat 在中文 Windows 上常因 GBK 编码 / errorlevel 解析 / 中文路径而失败。
    本脚本为纯 Python，跨环境稳定；build_windows.bat 仅作"双击启动"入口。

用法（在 Windows 上，PowerShell 或 cmd 均可）：
    双击 build_windows.bat    或
    py build\\build_windows.py

它替你自动跑完整条流水线：
    [0] 环境预检   —— Python 版本、项目路径是否含非 ASCII 字符
    [1] 创建 .venv 虚拟环境并升级 pip
    [2] 安装运行依赖 requirements.txt（OCR/UIA 必需）+ PyInstaller
                        ★ 这是旧脚本缺失的关键步：不装依赖 collect_all 会失败或出损坏 exe
    [3] 引擎冒烟测试  —— 提前暴露逻辑层报错（build/smoke_test.py）
    [4] PyInstaller 按 build/report_qc.spec 出单目录 exe
    [5] 若装有 Inno Setup 6，自动生成安装包；否则提示可直接分发 dist

产物：
    dist\\报告质控软件\\报告质控软件.exe      （单目录，含 assets/）
    installer\\报告质控软件Setup.exe          （仅当 Inno Setup 存在）
"""

import os
import sys
import subprocess


def banner(msg):
    print("\n" + "=" * 54, flush=True)
    print(" " + msg, flush=True)
    print("=" * 54, flush=True)


def run(cmd, cwd=None):
    print("> " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd, cwd=cwd).returncode
    print("  (exit code: %d)" % rc, flush=True)
    return rc


def preflight(root):
    banner("[0/5] 环境预检")
    if sys.version_info < (3, 10):
        print("[ERROR] 需要 Python 3.10+，当前 %d.%d.%d" % sys.version_info[:3], flush=True)
        return False
    print("[OK] Python %d.%d.%d" % sys.version_info[:3], flush=True)
    try:
        root.encode("ascii")
        print("[OK] 项目路径为纯 ASCII（PyInstaller 友好）", flush=True)
    except UnicodeEncodeError:
        print("[WARN] 项目路径含中文/非 ASCII 字符：", root, flush=True)
        print("       建议把整个 report_qc_app 移到纯英文路径（如 C:\\rqc\\），", flush=True)
        print("       否则 PyInstaller 可能构建失败。仍将继续……", flush=True)
    return True


def main():
    build_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(build_dir, ".."))
    os.chdir(root)
    print("项目根目录: " + root, flush=True)

    if not preflight(root):
        return 1

    # ---- [1/5] venv ----
    banner("[1/5] 创建虚拟环境并升级 pip")
    venv_dir = os.path.join(root, ".venv")
    if not os.path.isdir(venv_dir):
        if run([sys.executable, "-m", "venv", ".venv"]) != 0:
            print("[ERROR] 创建虚拟环境失败。", flush=True)
            return 1
    vpy = (os.path.join(venv_dir, "Scripts", "python.exe") if os.name == "nt"
           else os.path.join(venv_dir, "bin", "python"))
    if run([vpy, "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        print("[WARN] pip 升级失败，继续。", flush=True)

    # ---- [2/5] 运行依赖 + PyInstaller（关键：先装 requirements）----
    banner("[2/5] 安装运行依赖(requirements.txt) 与 PyInstaller")
    if run([vpy, "-m", "pip", "install", "-r", "requirements.txt"]) != 0:
        print("[ERROR] 安装 requirements.txt 失败。", flush=True)
        print("        OCR/UIA 依赖缺失会导致 collect_all 失败或运行期崩溃。", flush=True)
        return 1
    if run([vpy, "-m", "pip", "install", "pyinstaller"]) != 0:
        print("[ERROR] 安装 PyInstaller 失败。", flush=True)
        return 1
    print("[OK] 依赖与 PyInstaller 就绪。", flush=True)

    # ---- [3/5] 引擎冒烟测试（逻辑层自检，提前暴露报错）----
    banner("[3/5] 引擎逻辑冒烟测试")
    if run([vpy, os.path.join("build", "smoke_test.py")]) != 0:
        print("[ERROR] 引擎冒烟测试未通过，请先修复引擎逻辑再打包。", flush=True)
        return 1
    print("[OK] 引擎逻辑自检通过。", flush=True)

    # ---- [4/5] PyInstaller ----
    banner("[4/5] PyInstaller 构建 exe")
    spec = os.path.join("build", "report_qc.spec")
    if run([vpy, "-m", "PyInstaller", spec, "--noconfirm", "--clean"]) != 0:
        print("[ERROR] 构建失败。", flush=True)
        return 1
    print("[OK] exe 已生成于 dist\\", flush=True)

    # ---- [5/5] Inno Setup（可选）----
    banner("[5/5] 生成安装包（若已安装 Inno Setup 6）")
    iscc = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if os.path.exists(iscc):
        if run([iscc, os.path.join(build_dir, "setup.iss")]) == 0:
            print("[OK] 安装包已生成于 installer\\", flush=True)
        else:
            print("[WARN] Inno Setup 编译失败。", flush=True)
    else:
        print("[INFO] 未检测到 Inno Setup 6，跳过。", flush=True)
        print("       可直接分发 dist\\报告质控软件\\ 目录（便携版）。", flush=True)

    banner("DONE")
    print(" exe 位置 : " + os.path.join(root, "dist"))
    print(" 安装包   : " + os.path.join(root, "installer") + "  （若已生成）", flush=True)
    print(" 下一步   : 在 Windows 上运行 exe，或从 开始菜单 启动（安装版）。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("[FATAL] " + str(e), flush=True)
        sys.exit(1)
