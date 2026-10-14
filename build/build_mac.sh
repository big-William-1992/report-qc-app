#!/usr/bin/env bash
# 星衍AI放射质控 · macOS 打包脚本
# 生成未签名 .app → 可选 codesign + notarytool 公证 → 打 .dmg
#
# 用法:
#   ./build_mac.sh            # 仅生成未签名 .app 与 .dmg（本地可测，分发会被 Gatekeeper 拦）
#   ./build_mac.sh sign       # 额外做 签名 + 公证（需下方环境变量，证书在你的钥匙串）
#
# 前置（sign 模式）:
#   export APPLE_ID=you@icloud.com            # 苹果开发者账号
#   export APPLE_ID_PASSWORD=xxxx-xxxx-xxxx   # 专用密码（非登录密码）
#   export APPLE_TEAM_ID=XXXXXXXXXX           # 团队 ID
#   # Developer ID Application 证书须已装进本机钥匙串
#
# 说明:
#   - macOS 用系统原生 WKWebView，无需 pythonnet/clr_loader（仅 Windows 需要）
#   - 建议把 assets/app.ico 换成 app.icns 以显示图标（脚本会优先用 .icns）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV=.venv-mac
echo "▶ 创建 macOS 虚拟环境并安装依赖"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt pyinstaller

echo "▶ PyInstaller 构建 .app"
"$VENV/bin/python" -m PyInstaller build/report_qc.spec --noconfirm --clean
APP="dist/报告质控软件.app"

# 图标：优先使用 .icns（macOS 原生）
ICNS="assets/app.icns"
if [ -f "$ICNS" ]; then
  /usr/bin/ditto "$ICNS" "$APP/Contents/Resources/app.icns" 2>/dev/null || true
fi

if [ "${1:-}" = "sign" ]; then
  : "${APPLE_ID:?sign 模式需设置 APPLE_ID}"
  : "${APPLE_ID_PASSWORD:?sign 模式需设置 APPLE_ID_PASSWORD（专用密码）}"
  : "${APPLE_TEAM_ID:?sign 模式需设置 APPLE_TEAM_ID}"
  CERT="Developer ID Application: $APPLE_TEAM_ID"
  echo "▶ codesign (hardened runtime + 时间戳)"
  codesign --deep --force --options runtime --timestamp -s "$CERT" "$APP"
fi

echo "▶ 打包 .dmg"
DMG="dist/星衍AI放射质控.dmg"
hdiutil create -volname "星衍AI放射质控" -srcfolder "$APP" -ov -format UDZO "$DMG"

if [ "${1:-}" = "sign" ]; then
  echo "▶ notarytool 公证 + stapler"
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" \
    --password "$APPLE_ID_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait
  xcrun stapler staple "$DMG"
  echo "▶ 对 .app 一并 stapler（若直接分发 .app）"
  xcrun stapler staple "$APP" 2>/dev/null || true
fi

echo "✅ 完成: $DMG"
