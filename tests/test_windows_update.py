#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 自动更新端到端实测（在 windows-latest runner 上运行）。

流程：
  1) 构造一个"旧版"沙箱应用（含假的 报告质控软件.exe、_internal/ 残留、
     assets/license.dat 激活码、logs/ 日志）。
  2) 通过 auto_updater 下载线上 report-qc-portable.zip（真实 GitHub 产物）。
  3) 写出真实 Windows 安装器（PowerShell），在沙箱上运行（AU_NO_LAUNCH=1）。
  4) 断言：旧 _internal 残留被清理、新 exe 与 _internal 到位、激活码与日志保留、
     build_info 写入。

本脚本仅在 Windows 上有意义（auto_updater 在 Windows 下走 zip + PowerShell 分支）。
"""
import os
import sys
import time
import shutil
import subprocess

import tempfile
ROOT = os.path.join(tempfile.gettempdir(), "au_win_test")
SUD = os.path.join(ROOT, "app")
ZIP = os.path.join(ROOT, "latest.zip")
PUB = "2026-07-27T02:14:00Z"


def main():
    if not sys.platform.startswith("win"):
        print("SKIP: 非 Windows 平台，跳过 Windows 自动更新实测。")
        return 0

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    import auto_updater as au

    # 校验平台分支选择正确
    assert au.IS_WINDOWS is True, "IS_WINDOWS 应为 True"
    assert au.default_archive_name().endswith(".zip"), "Windows 应使用 zip"
    assert "report-qc-portable.zip" in au.PORTABLE_ZIP_URL, "应使用便携 zip URL"

    # 1) 构造旧版沙箱
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(os.path.join(SUD, "_internal"), exist_ok=True)
    os.makedirs(os.path.join(SUD, "assets"), exist_ok=True)
    os.makedirs(os.path.join(SUD, "logs"), exist_ok=True)
    with open(os.path.join(SUD, "报告质控软件.exe"), "w") as f:
        f.write("FAKE_OLD_EXE")  # 极小假 exe
    with open(os.path.join(SUD, "_internal", "OLD_MARKER.txt"), "w") as f:
        f.write("old_internal")  # 旧 _internal 残留，应被清掉
    with open(os.path.join(SUD, "assets", "license.dat"), "w") as f:
        f.write("ACTIVATION-CODE-123")
    with open(os.path.join(SUD, "logs", "session.log"), "w") as f:
        f.write("old log line\n")
    print("[1] 沙箱就绪:", SUD)

    # 2) 下载线上便携 zip（真实产物）
    print("[2] 下载便携 zip ...")
    au.download(ZIP, timeout=300)
    print("    zip size:", os.path.getsize(ZIP), "bytes")

    # 3) 写出真实 Windows 安装器并运行
    script = au.make_installer(SUD, ZIP)
    print("[3] 运行安装器:", script)
    env = dict(os.environ, AU_NO_LAUNCH="1")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", script, SUD, ZIP, PUB],
        env=env, capture_output=True, text=True, timeout=300)
    print("    installer rc:", proc.returncode)
    if proc.stdout.strip():
        print("    installer stdout:\n" + proc.stdout)
    if proc.stderr.strip():
        print("    installer stderr:\n" + proc.stderr)
    dbg = os.path.join(SUD, "update", "installer_debug.log")
    if os.path.exists(dbg):
        print("    installer_debug.log:\n" + open(dbg, encoding="utf-8", errors="replace").read())
    time.sleep(2)  # 等待清理

    # 4) 断言
    new_exe = os.path.join(SUD, "报告质控软件.exe")
    new_internal = os.path.join(SUD, "_internal")
    old_marker = os.path.join(SUD, "_internal", "OLD_MARKER.txt")
    lic = os.path.join(SUD, "assets", "license.dat")
    log = os.path.join(SUD, "logs", "session.log")
    bi = os.path.join(SUD, "assets", "build_info.json")

    checks = []
    checks.append(("旧 _internal 残留已清理", not os.path.exists(old_marker)))
    checks.append(("新 exe 到位（体积>假 exe）",
                   os.path.exists(new_exe) and os.path.getsize(new_exe) > 1000))
    checks.append(("新 _internal 目录到位", os.path.isdir(new_internal)
                   and len(os.listdir(new_internal)) > 0))
    checks.append(("激活码保留",
                   os.path.exists(lic) and open(lic).read() == "ACTIVATION-CODE-123"))
    checks.append(("日志保留",
                   os.path.exists(log) and "old log line" in open(log).read()))
    checks.append(("build_info 写入",
                   os.path.exists(bi) and PUB in open(bi).read()))

    print("[4] 断言结果:")
    allok = True
    for name, ok in checks:
        print(("   PASS " if ok else "   FAIL ") + name)
        allok = allok and ok

    print("\nRESULT:", "ALL_PASS" if allok else "FAILED")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
