# 安装与部署指南（Install & Deploy）

面向两类读者：**终端用户**（装好即用）与**开发者**（构建 / 打包 / 调试）。
本指南覆盖环境准备、依赖安装、启动、平台差异与常见故障排查。

---

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| Python | 3.10+（macOS 建议 3.11+，Tk 更稳定） |
| 操作系统 | macOS 12+ / Windows 11（Linux 可运行引擎与 OCR，UIA 不适用） |
| 内存 | ≥ 4 GB（OCR 模型常驻约 200–400 MB） |
| 磁盘 | 约 1.5 GB（含内置 OCR 模型权重 `assets/ocr_models`） |

---

## 2. 推荐：使用虚拟环境（避免污染系统 Python）

```bash
cd report_qc_app
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> 源码运行（`python3 src/app.py`）无需打包；依赖缺失时会有明确降级提示，不会崩溃。

---

## 3. 安装依赖

### 3.1 完整安装（推荐）

```bash
pip install -r requirements.txt
```

包含：OCR（`rapidocr-onnxruntime` 等）、**Windows `comtypes`**（UIA 报告采集）、其余运行库。
`requirements.txt` 中 `comtypes` 已标注「仅 Windows 生效」，跨平台安装无害。

### 3.2 仅用核心引擎（不要 OCR）

核心引擎**零第三方依赖**（标准库 `re/json/sqlite3/difflib/tkinter`）。
直接运行即可，缺 `cv2/numpy` 时 OCR 功能自动降级（已在 S1 兜底，GUI 不会崩）：

```bash
python3 src/app.py
```

### 3.3 后台全局快捷键（macOS / Linux 可选）

```bash
pip install pynput
```

未安装时，后台快捷键不可用，但「监听剪贴板（复制即质控）」仍可用。

---

## 4. 启动

```bash
cd report_qc_app
python3 src/app.py
```

首次启动：弹「用户协议与免责声明」→ 同意 → 进入 90 天免费试用。
（授权与激活细节见 [ACTIVATION.md](ACTIVATION.md)。）

---

## 5. macOS 注意事项

### 5.1 隐私权限（必开，否则功能残缺）

首次使用「监听剪贴板」或「后台快捷键」时，系统会弹窗请求访问：
**「Terminal / Python 请求访问剪贴板 / 辅助功能」**，请点允许。
补开路径：`系统设置 → 隐私与安全性 → 辅助功能 / 自动化`。
未授权表现：剪贴板读不到复制内容；后台快捷键在 PACS 聚焦时无法触发。

### 5.2 未签名（重要，当前状态）

当前安装包 / 脚本运行**未做 Apple 公证**（开发者账号与证书待办）。
首次打开打包版会被 Gatekeeper 拦截，两种绕过方式：

- **方式一（推荐普通用户）**：右键 App → 「打开」→ 在弹窗中点「仍要打开」（允许一次）。
- **方式二（命令行）**：
  ```bash
  sudo xattr -dr com.apple.quarantine /Applications/星衍放射质控.app
  ```
- **开发 / 内测阶段建议**：直接源码运行 `python3 src/app.py`，不受 Gatekeeper 限制，无需签名。

---

## 6. Windows 注意事项

- **UIA 报告采集**需 `comtypes`（已含于 `requirements.txt`，安装即具备）。
  无需管理员提权、无需驱动；但 **PACS 须为前台焦点窗口且报告区为标准文本控件**。
  自绘 canvas / OpenGL / DirectX 报告区 UIA 读不到，自动退回 OCR / 剪贴板 / DICOM SR。
  验证方法：PACS 置前台 → 点「🔎 UIA检测」确认能否读出报告文本。
- **打包版**：见 `build/` 目录（`report_qc.spec` / `build_windows.bat` / `setup.iss`），
  在 Windows 11 执行打包脚本生成安装包，安装后在「开始菜单」启动。
  资源与配置位于 `%APPDATA%\MedicalReportQC\`。

---

## 7. 跨平台能力降级对照

| 能力 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| UIA 读 PACS 文本控件 | ✅（需 comtypes） | —（不适用） | —（不适用） |
| 离线 OCR 屏幕区域识别 | ✅ | ✅ | ✅ |
| 剪贴板监听（复制即质控） | ✅ | ✅（需权限） | ✅（需权限） |
| 后台全局快捷键 | ✅ | ✅（需 pynput + 权限） | ✅（需 pynput + 权限） |
| 自动更新 | ✅ | ✅（安装器保留用户配置） | 未提供 |

> 设计原则：跨平台代码统一抽象，缺失能力**优雅降级**而非崩溃；Mac/Linux 不触碰 `comtypes`。

---

## 8. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| OCR 按钮灰显 / 报错 | 缺 `cv2/numpy` | `pip install -r requirements.txt`；或仅用引擎（OCR 自动降级） |
| 后台快捷键无效 | 缺 `pynput` 或未授权 | `pip install pynput` + 开辅助功能权限 |
| macOS 剪贴板读不到 | 未授权自动化 | 系统设置开「自动化」权限 |
| Windows UIA 读不到报告 | PACS 非标准文本控件 | 点「🔎 UIA检测」验证；退回 OCR / 剪贴板 |
| 启动即崩（曾） | 顶层硬 import 第三方库 | 已修复（S1/S2 懒加载兜底）；升级到最新版即可 |
| 激活码无效 | 机器识别码复制不全 / 换机 | 重新从激活框复制完整识别码发卡；换机需重新激活 |

---

## 9. 开发者构建（可选）

- **Windows 打包**：`build/build_windows.bat` → 生成安装包。
- **macOS 打包**：`pyinstaller 星衍影像云.spec`（当前为规划文件，签名 / 公证待完善）。
- **激活发卡工具**：`python3 gen_activation_gui.py`（需 `keys/private_key.pem`，见 ACTIVATION.md §6）。
- **Web 发卡原型**：`python3 web_issuer/server.py` → `http://localhost:8777`（演示用，生产前需补支付与密钥管理）。

---

> 文档与代码版本：v3.0 起随仓库维护。如与代码行为不符，以 `src/` 实际实现为准。
