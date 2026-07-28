#!/bin/zsh
cd "$(dirname "$0")/src"
# 优先使用已安装 OCR 依赖的托管虚拟环境；缺失时回退到基础解释器（仅 OCR 功能不可用）
VENV="/Users/xiejun/.workbuddy/binaries/python/envs/default/bin/python3"
BASE="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
# venv 的 tkinter 自带 tcl/tk 库不完整，需指向基础解释器内置的 tcl9.0/tk9.0 资源
TCLTK_LIB="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/lib"
if [ -d "$TCLTK_LIB/tcl9.0" ]; then
  export TCL_LIBRARY="$TCLTK_LIB/tcl9.0"
  export TK_LIBRARY="$TCLTK_LIB/tk9.0"
fi
if [ -x "$VENV" ]; then
  exec "$VENV" app.py
else
  exec "$BASE" app.py
fi
