#!/bin/bash
# 星衍AI放射质控系统 - 桌面端双击启动器
# 双击本文件即可在 macOS 上打开应用（会弹出原生 Tk 窗口，关闭窗口即退出）。

# 切到项目目录（双击 .command 时 Terminal 的初始目录是 Home，必须显式切）
cd "/Users/xiejun/WorkBuddy/2026-07-14-00-08-19/report_qc_app" || {
    echo "✗ 找不到项目目录，请确认路径是否正确。"
    read -p "按回车关闭"
    exit 1
}

# 优先用 Homebrew Python（已验证 tkinter 可用），否则回退 python3
PYTHON="/opt/homebrew/bin/python3"
if [ ! -x "$PYTHON" ]; then PYTHON="python3"; fi

echo "▶ 正在启动星衍AI放射质控..."
"$PYTHON" src/app.py

echo ""
echo "程序已退出（返回码 $?）。"
read -p "按回车关闭此窗口"
