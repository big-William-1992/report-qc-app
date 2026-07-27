#!/bin/zsh
cd "$(dirname "$0")/src"
# 优先使用已安装 OCR 依赖的托管虚拟环境；缺失时回退到基础解释器（仅 OCR 功能不可用）
VENV="/Users/xiejun/.workbuddy/binaries/python/envs/default/bin/python3"
BASE="/Users/xiejun/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
if [ -x "$VENV" ]; then
  exec "$VENV" app.py
else
  exec "$BASE" app.py
fi
