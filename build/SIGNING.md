# 星衍AI放射质控 · 跨平台签名指南

> 目标：让官网 `downloads/` 里的安装包在用户机器上不被系统拦截
> （Windows SmartScreen / macOS Gatekeeper）。
> 签名证书属于密钥，**由你（老谢）提供，本工程不内置任何证书或私钥**。

---

## 为什么必须签名

| 平台 | 不签名后果 | 签名后 |
|---|---|---|
| Windows | SmartScreen 红字拦截"未知发布者" | 需代码签名证书；EV 证书可快速积累信誉，OV 需时间 |
| macOS | Gatekeeper 直接禁止打开 | Developer ID + 公证(notarize) 后放行 |
| Linux | 无强制拦截，但用户需手动信任 | 可选 GPG 分离签名，增强可信度 |

> ⚠️ **未签名的应用无法真正"上线分发"**——即便传上官网，用户下载后也打不开或被吓退。
> 因此签名是公开下载的前置条件之一（与 NMPA 分类界定并行）。

---

## 1. Windows 代码签名

1. 购买**代码签名证书**（OV 或 EV）：Sectigo / DigiCert / GlobalSign。
   - 推荐 EV（Extended Validation）：SmartScreen 信誉即时生效，无需累计。
   - 证书通常为 `.pfx`（含私钥），**切勿提交进仓库 / 勿泄露**。
2. 安装 Windows SDK（含 `signtool.exe`）。
3. 在 `build_windows.py` 产出 `dist\报告质控软件\报告质控软件.exe` 与 `installer\ReportQcSetup.exe` 后，对两者签名：
   ```bat
   signtool sign /fd SHA256 /td SHA256 ^
     /f your-code-signing.pfx /p <pfx密码> ^
     /tr http://timestamp.digicert.com ^
     installer\ReportQcSetup.exe
   ```
4. （可选）用 `osslsigncode` 在 Linux/macOS CI 上签。

---

## 2. macOS 签名 + 公证（最严）

1. 加入 **Apple Developer Program**（年费 $99）。
2. 在本机钥匙串安装 **Developer ID Application** 证书。
3. 生成**专用密码**（`appleid.apple.com` → 安全 → 专用密码），用于 notarytool。
4. 直接用仓库自带脚本：
   ```bash
   export APPLE_ID=you@icloud.com
   export APPLE_ID_PASSWORD=xxxx-xxxx-xxxx   # 专用密码，非登录密码
   export APPLE_TEAM_ID=XXXXXXXXXX
   ./build/build_mac.sh sign
   ```
   脚本会 `codesign --options runtime --timestamp`（hardened runtime）
   → `notarytool submit` 公证 → `stapler staple` 装订工单凭证。
5. 未公证的 .app 即便签名，跨机器仍可能被拦；**公证是 macOS 分发的硬性一步**。

---

## 3. Linux（可选签名）

- 无强制 CA 签名体系。
- 推荐：用 GPG 对 AppImage 做分离签名，并在下载页附 `.asc`：
  ```bash
  gpg --detach-sign --armor dist/星衍AI放射质控-x86_64.AppImage
  ```
- 注意：pywebview 在 Linux 依赖系统 **WebKit2GTK**，目标机需自带（`apt install libwebkit2gtk-4.1-dev`），或改用 qt 后端并打包 Qt。

---

## 4. 签名后上官网

1. 把签名的安装包放进 `xingyan-qc-site/downloads/`（与官网下载页链接名一致）：
   - `xingyan-qc-windows.exe` / `ReportQcSetup.exe`
   - `xingyan-qc-macos.dmg`
   - `xingyan-qc-linux.AppImage`
2. 计算每个包的 SHA256：`shasum -a 256 文件名`
3. 把 SHA256 填进 `index.html` 三处 `待放置安装包后填入`（官网下载按钮的"复制"功能依赖它）。
4. 按 `部署说明.md` 用 `deploy.sh` 推送到 `www` 静态根。

> 私钥 / pfx / 专用密码 **永远不要写进脚本或提交仓库**。CI 中通过密钥变量（secret）注入。

---

## 五、GitHub Actions 自动构建 + 签名（推荐用于公开分发）

仓库已内置两条流水线：
- `.github/workflows/build-windows.yml` —— Windows（exe + Inno 安装包），已上线。
- `.github/workflows/build-macos-linux.yml` —— macOS(.dmg) + Linux(.AppImage)，新增大门。

两者都在「推到 main / 手动 dispatch」时触发。**签名是可选的**：仅在仓库配置了对应 Secret 时才签名；未配置则照常产出未签名包（不影响构建与发布）。

### 需在仓库 Settings → Secrets and variables → Actions 配置的密钥

| Secret 名 | 平台 | 内容 |
|---|---|---|
| `WINDOWS_CODESIGN_PFX` | Windows | base64 编码的代码签名证书 `.pfx` |
| `WINDOWS_CODESIGN_PASSWORD` | Windows | `.pfx` 导出密码 |
| `MACOS_CERT_P12` | macOS | base64 编码的 Developer ID Application `.p12` |
| `MACOS_CERT_PASSWORD` | macOS | `.p12` 导出密码 |
| `APPLE_ID` | macOS | 苹果账号邮箱 |
| `APPLE_APP_PASSWORD` | macOS | 苹果「专用密码」(app-specific password) |
| `APPLE_TEAM_ID` | macOS | 10 位团队 ID |
| `LINUX_GPG_KEY` | Linux | base64 编码的 GPG 私钥导出 |
| `LINUX_GPG_PASSPHRASE` | Linux | GPG 私钥口令（若设置） |

> base64 导出示例（本地执行，绝不上传明文）：
> - Windows: `base64 -i cert.pfx | pbcopy`
> - macOS: `base64 -i cert.p12 | pbcopy`
> - Linux: `gpg --export-secret-keys -a KEYID | base64 | pbcopy`

### 产出
- Windows：Release 标签 `latest`（便携 zip + 安装版 exe）
- macOS / Linux：Release 标签 `latest-desktop`（.dmg + .AppImage + .asc）

### 重要提醒
- **证书 / 私钥永不入仓、永不进日志**（脚本仅从 Secret 注入，流程结束即清除）。
- 未签名的 macOS 包：用户需「右键→打开」或在「安全性与隐私」中允许。
- 未签名的 Linux AppImage：目标机需自带 WebKit2GTK（pywebview 的 gtk 后端依赖）。
- macOS/Linux 构建为首次接入，spec 已做跨平台适配（webview 按平台裁剪、macOS 增加 BUNDLE）；
  首跑若暴露问题（如 macOS entitlements、Linux 桌面文件），按 CI 日志迭代修复即可。
