# 星衍放射质控软件 · 新 Mac 恢复步骤清单

> 适用场景：已把项目整体（含 `.git`、运行数据、队列、OCR 模型）放进 iCloud Drive，
> 现在换到一台新 MacBook，需要把它跑起来。
> 当前 iCloud 副本 `git HEAD = 57ed7f5e35c19da9cc02b1dba1e872f7028036d4`。

---

## 步骤 0 · 旧 Mac 上确认 iCloud 上传完成
- 菜单栏点 iCloud 图标，等**同步圈消失**再动新 Mac。
- 否则新 Mac 取到的可能是半成品（缺 `.git` 对象或数据文件）。

## 步骤 1 · 新 Mac 取项目（复制到本机，不要在 iCloud 内开发）
```bash
cd ~/Desktop            # 或 ~/Projects，任意本地目录
cp -R ~/Library/Mobile\ Documents/com~apple~CloudDocs/report_qc_app ./report_qc_app
cd report_qc_app
```

## 步骤 2 · 解除 macOS 隔离属性（否则双击 .command 会被拦）
```bash
xattr -dr com.apple.quarantine ~/Desktop/report_qc_app
# 或只针对单个启动器：
xattr -dr com.apple.quarantine 星衍质控启动.command
```

## 步骤 3 · 删掉复制产生的 " 文件名 2" 重名副本（无害，可删）
iCloud 复制时可能生成若干 `" 文件名 2"` 副本，列出来确认后删除：
```bash
ls -la | grep " 2"
rm -f ".gitignore 2" "LICENSE 2" "README 2.md" \
      "requirements 2.txt" "requirements-windows 2.txt" \
      "gen_activation_code 2.py" "gen_activation_gui 2.py" \
      "ui_prototype_app 2.html" "ui_prototype_redesign 2.html" \
      "星衍质控启动 2.command" "启动星衍质控软件 2.command" "星衍发卡工具 2.spec"
```

## 步骤 4 · 确认 git 仓库完整并还原受控文件
```bash
git rev-parse HEAD     # 应为 57ed7f5e35c19da9cc02b1dba1e872f7028036d4
git status             # 正常仅多 .external_data/（队列数据）与 assets 下运行数据
git checkout -- .      # 用 git 把受控文件还原到干净提交状态
```

## 步骤 5 · 安装 Python 与依赖（推荐 venv，避开 macOS 系统包保护）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # 含 cryptography、rapidocr、opencv 等，约 1–3 分钟
```
> 说明：`requirements.txt` 已含 `cryptography>=41.0`（激活码校验必需）。
> 若你手上的是更早的快照缺这一行，请补：`pip install cryptography`。

## 步骤 6 · 运行
- 方式 A（推荐，开发/调试）：
  ```bash
  source .venv/bin/activate
  python src/app.py
  ```
- 方式 B（双击启动器）：
  - `星衍质控启动.command` → 走 Homebrew / 系统 `python3`，执行 `src/app.py`。
  - `启动星衍质控软件.command` → 优先 WorkBuddy 托管 venv，没有则回退到 Homebrew / 系统 python3。

## 步骤 7 · 重新激活（重要）
- 旧 `assets/license.dat` 是**绑定旧 Mac 机器码**的，换机器后通常失效。
- 软件会进入 **90 天免费试用**，可先正常使用。
- 要正式激活：在软件内打开激活对话框，复制显示的「机器识别码（发卡用）」，然后：
  ```bash
  python gen_activation_code.py "<粘贴机器码>"
  ```
  （需 `cryptography`，已在步骤 5 装好；用 iCloud 副本里的 `keys/private_key.pem` 私钥签名）
- 把输出的激活码粘贴回激活对话框即可。

## 步骤 8 · 授权屏幕录制（仅用「屏幕区域 OCR 监控」功能时）
系统设置 → 隐私与安全性 → 屏幕录制 → 勾选运行所用的「终端」/ Python。
首次启用 OCR 功能会弹权限请求，允许即可。

---

## 备选：用 GitHub 作为代码源（替代 iCloud）
```bash
git clone git@github.com:big-William-1992/report-qc-app.git report_qc_app
cd report_qc_app
# OCR 模型已随仓库(assets/ocr_models, 13M)，clone 即得，无需联网
# 但患者样本库(*.db)与 license.dat / session.json 不入库，
# 需从 iCloud 或旧机单独把 assets/ 运行数据、.external_data/ 拷过来
```

## 注意事项
1. **不要在 iCloud 文件夹内 `git commit` 或直接开发**，先复制到本机再操作。
2. 患者数据已落 iCloud，合规风险自担；新机拿到后可按需删除 `assets/samples.db`。
3. OCR 模型已随仓库，无需联网下载。
4. 若运行报 `ModuleNotFoundError: No module named 'cryptography'`，执行 `pip install cryptography`。
5. `keys/` 含发卡私钥，**切勿再提交到公开仓库**；iCloud / 本机保管即可。
