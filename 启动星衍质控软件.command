#!/bin/zsh
# 星衍AI放射质控软件 · macOS 启动器（WebView 桌面壳）
# 启动本地 FastAPI 服务 + 系统原生 WebView 加载同一套 SPA 前端，
# 实现桌面端与 Web 端 UI 统一（一套前端代码双端运行）。
cd "$(dirname "$0")"

# 优先使用托管虚拟环境（已含 fastapi/uvicorn/pywebview）；
# 缺失时依次回退到基础解释器、Homebrew Python、系统 python3。
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

ERR_LOG="/tmp/xingyan_qc_stderr.log"
"$PY" desktop_app.py 2> "$ERR_LOG"
EC=$?

if [ $EC -ne 0 ]; then
  echo "❌ 程序异常退出（退出码 $EC）。"
  echo "━━━━━━━━━━ 错误信息 ━━━━━━━━━━"
  cat "$ERR_LOG"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "提示：若报错缺少 pywebview，请先安装："
  echo "  $PY -m pip install pywebview"
  read -k 1 "按任意键关闭本窗口…"
fi
