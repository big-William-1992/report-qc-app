#!/bin/zsh
# 星衍AI放射质控软件 · 旧启动器（兼容重定向）
# 原 Tkinter 桌面端(src/app.py)已退役，主交付为 WebView 桌面端 desktop_app.py。
# 本启动器改为直接拉起 WebView 版，双击任意旧图标均可打开正确的软件。

cd "$(dirname "$0")" || {
    echo "✗ 找不到启动器所在目录。"
    read -k 1 "按任意键关闭…"
    exit 1
}

# 与「启动星衍质控软件.command」保持一致：优先托管 venv，再回退基础/Homebrew/系统 Python
VENV="/Users/xiejun/.workbuddy/binaries/python/envs/default/bin/python3"
BASE="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
HB="/opt/homebrew/bin/python3"
if [ -x "$VENV" ]; then
  PY="$VENV"
elif [ -x "$BASE" ]; then
  PY="$BASE"
elif [ -x "$HB" ]; then
  PY="$HB"
else
  PY="python3"
fi

echo "▶ 正在启动星衍AI放射质控（WebView 桌面版）…"
"$PY" desktop_app.py
EC=$?

if [ $EC -ne 0 ]; then
  echo "❌ 程序异常退出（退出码 $EC）。请确认依赖已安装："
  echo "  $PY -m pip install fastapi uvicorn pywebview"
  read -k 1 "按任意键关闭…"
fi
