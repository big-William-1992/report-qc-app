#!/usr/bin/env bash
# 星衍AI放射质控 · Linux 打包脚本（AppImage）
#
# 用法:
#   ./build_linux.sh            # 生成未签名 AppImage
#   ./build_linux.sh sign       # 额外用 GPG 对 AppImage 做分离签名
#
# 前置:
#   - 系统需安装 WebKit2GTK（pywebview 在 Linux 默认用 gtk 后端）：
#       Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev
#       （或改用 qt 后端：pip 装 pywebview[qt] 并设 PYWEBVIEW_BACKEND=qt）
#   - linuxdeploy + python 插件（脚本自动下载到 tools/ 缓存）
#   - sign 模式需有 GPG 私钥：gpg --detach-sign
#
# 说明:
#   - AppImage 无法可靠打包 WebKit2GTK 系统库，目标机需自带该运行时
#   - 若选择 qt 后端，可用 linuxdeploy 的 Qt 插件一并打包 Qt（体积更大）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV=.venv-linux
echo "▶ 创建 Linux 虚拟环境并安装依赖"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt pyinstaller

echo "▶ PyInstaller 构建单目录"
"$VENV/bin/python" -m PyInstaller build/report_qc.spec --noconfirm --clean

APPDIR="dist/星衍AI放射质控.AppDir"
mkdir -p "$APPDIR"
cp -a "dist/报告质控软件/." "$APPDIR/"

# AppImage 启动器：确保 WebView 后端与库路径
cat > "$APPDIR/AppRun" <<'EOS'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PYWEBVIEW_BACKEND="${PYWEBVIEW_BACKEND:-gtk}"
exec "$HERE/报告质控软件" "$@"
EOS
chmod +x "$APPDIR/AppRun"

TOOLS="tools/linuxdeploy"
mkdir -p "$TOOLS"
LDEPLOY="$TOOLS/linuxdeploy-x86_64.AppImage"
if [ ! -f "$LDEPLOY" ]; then
  echo "▶ 下载 linuxdeploy"
  curl -fsSL -o "$LDEPLOY" https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
  chmod +x "$LDEPLOY"
fi

echo "▶ 生成 AppImage"
"$LDEPLOY" --appdir "$APPDIR" --output appimage
OUT="dist/星衍AI放射质控-$(uname -m).AppImage"

if [ "${1:-}" = "sign" ]; then
  echo "▶ GPG 分离签名"
  gpg --detach-sign --armor "$OUT"
fi

echo "✅ 完成: $OUT"
