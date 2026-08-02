#!/bin/zsh
# 星衍AI放射质控软件 · macOS 启动器
# 改进：① 程序异常退出时不再静默关闭，而是显示错误并等待按键；
#       ② Python stderr 重定向到 /tmp/xingyan_qc_stderr.log 便于排查。
cd "$(dirname "$0")/src"

# 优先使用已安装 OCR 依赖的托管虚拟环境；缺失时回退到基础解释器（仅 OCR 功能不可用）
VENV="/Users/xiejun/.workbuddy/binaries/python/envs/default/bin/python3"
BASE="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
if [ -x "$VENV" ]; then
  PY="$VENV"
else
  PY="$BASE"
fi

# venv 的 tkinter 自带 tcl/tk 库不完整，需指向基础解释器内置的 tcl9.0/tk9.0 资源
TCLTK_LIB="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/lib"
if [ -d "$TCLTK_LIB/tcl9.0" ]; then
  export TCL_LIBRARY="$TCLTK_LIB/tcl9.0"
  export TK_LIBRARY="$TCLTK_LIB/tk9.0"
fi

ERR_LOG="/tmp/xingyan_qc_stderr.log"
"$PY" app.py 2> "$ERR_LOG"
EC=$?

if [ $EC -ne 0 ]; then
  echo "❌ 程序异常退出（退出码 $EC）。"
  echo "━━━━━━━━━━ 错误信息 ━━━━━━━━━━"
  cat "$ERR_LOG"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "完整日志：~/Library/Application Support/星衍放射质控软件/logs/app.log"
  read -k 1 "按任意键关闭本窗口…"
fi
