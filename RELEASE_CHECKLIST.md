# 星衍放射质控软件 · 发布清单

> MetaGPT SOP · Phase 5 — Engineer
> 版本：v4.3.6 · 更新日期：2026-09-13
> 状态：现行流程

---

## 1. 发布流程总览

```
准备 → 验证 → 打包 → 分发 → 验证 → 归档
 │       │       │       │       │       │
 │       │       │       │       │       └── git tag + CHANGELOG 归档
 │       │       │       │       └── 部署后验证（health check + 功能抽查）
 │       │       │       └── GitHub Releases + 官网下载页
 │       │       └── PyInstaller 打包 + 签名
 │       └── pytest + ruff + 手动验收
 └── 版本号 + CHANGELOG + 文档
```

---

## 2. 准备阶段

### 2.1 版本号

**单一事实来源**：`src/version.py`

```python
APP_VERSION = "4.3.6"
```

**版本号规范**：
- MAJOR.MINOR.PATCH
- MAJOR：不兼容 API 变更
- MINOR：新功能（向后兼容）
- PATCH：Bug 修复

### 2.2 CHANGELOG

**文件**：`CHANGELOG.md`

**格式**：[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)

**必须包含**：
- [ ] 新增 (Added)：新功能列表
- [ ] 变更 (Changed)：行为变更
- [ ] 修复 (Fixed)：Bug 修复
- [ ] 环境变量新增：如有
- [ ] 测试：测试数量和结果

### 2.3 文档更新

- [ ] `README.md`：项目概览（如有架构变更）
- [ ] `PRD.md`：产品需求（如有功能变更）
- [ ] `ARCHITECTURE.md`：系统架构（如有架构变更）
- [ ] `USER_GUIDE.md`：用户指南（如有 UI/功能变更）
- [ ] `DEPLOYMENT.md`：部署指南（如有部署变更）

---

## 3. 验证阶段

### 3.1 自动化验证

```bash
# 1. 单元测试（必须 100% 通过）
python -m pytest -q

# 2. Ruff 致命规则（必须全绿）
ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018 src/ server/

# 3. 依赖安全扫描
pip-audit -r requirements.txt

# 4. 引擎自检
python -c "from src.engine import RuleEngine; e = RuleEngine(); print('Engine OK')"

# 5. OCR 自检
python -c "import sys; sys.path.insert(0,'src'); import ocr_provider; print(ocr_provider.availability())"
```

### 3.2 手动验收清单

#### 核心功能
- [ ] 粘贴报告 → 运行质控 → 结果正确显示
- [ ] 红/橙/蓝高亮标记正确
- [ ] 评分依据展示扣分原因
- [ ] 剪贴板监听开启 → 复制报告 → 自动弹窗
- [ ] 后台快捷键（焦点在 PACS 时）→ 触发质控
- [ ] OCR 屏幕区域 → 框选 → 识别回填

#### 账号与授权
- [ ] 首次启动 → 创建管理员 → 登录成功
- [ ] 非管理员账号 → 仅见自己数据
- [ ] 管理员 → 见全部数据
- [ ] 试用到期前 7/3/1 天 → 告警显示
- [ ] 激活码激活 → 授权状态更新

#### 数据管理
- [ ] 存入样本库 → 样本列表可见
- [ ] 导出质控报表 → CSV 中文不乱码
- [ ] 样本脱敏 → 患者姓名被去除
- [ ] 全量数据导出 → JSON 完整

#### 设置与维护
- [ ] 规则维护 → 增删错别字 → 即时生效
- [ ] 变更日志 → 显示当前版本变更
- [ ] 检查更新 → 显示最新版本
- [ ] 用户反馈 → 提交成功

### 3.3 安全验证

- [ ] 非本机监听（0.0.0.0）未设 QC_API_SECRET → 阻止启动
- [ ] 登录失败 5 次 → 锁定 15 分钟
- [ ] 弱密码 → 拒绝注册
- [ ] doctor 角色 → 无法访问 admin 接口
- [ ] 审计日志 → 敏感操作有记录
- [ ] 数据导出 → 仅限 admin + 有审计记录

---

## 4. 打包阶段

### 4.1 Windows 打包

```bash
# Windows 系统上运行
cd build
build_windows.bat
```

**产物**：
- `dist/report_qc/`：单目录 exe
- `dist/setup.exe`：Inno Setup 安装包

**打包检查**：
- [ ] exe 能正常启动
- [ ] health 端点可达（http://127.0.0.1:8000/api/v1/health）
- [ ] 所有依赖已包含（无 ModuleNotFoundError）
- [ ] OCR 模型已包含
- [ ] 资源路径正确（%APPDATA%/MedicalReportQC/）

### 4.2 macOS 打包

```bash
# macOS 系统上运行
cd build
./build_macos.sh
```

**产物**：
- `dist/`：源码 tarball

### 4.3 打包后验证

```bash
# 启动 exe 后验证
curl http://127.0.0.1:8000/api/v1/health

# 验证返回：
# {
#   "success": true,
#   "data": {
#     "status": "ok",
#     "db": "connected",
#     "disk_free": "100GB",
#     "sessions": 0,
#     "license": "trial"
#   }
# }
```

---

## 5. 分发阶段

### 5.1 GitHub Releases

```bash
# 1. 创建标签
git tag -a v4.3.6 -m "Release v4.3.6"
git push origin v4.3.6

# 2. 创建 GitHub Release
gh release create v4.3.6 \
  --title "星衍放射质控软件 v4.3.6" \
  --notes-file CHANGELOG.md \
  --files "dist/*.exe" "dist/*.dmg" "dist/*.tar.gz"
```

### 5.2 官网下载页

- [ ] 更新官网最新版本号
- [ ] 更新下载链接（GitHub Releases）
- [ ] 上传安装包到服务器（如需）
- [ ] 更新版本信息 API（/api/v1/version）

### 5.3 通知渠道

- [ ] GitHub Release 发布
- [ ] 用户反馈渠道通知（如有）
- [ ] 应用内变更日志更新

---

## 6. 发布后验证

### 6.1 部署验证

```bash
# 1. 安装最新版
# 2. 启动应用
# 3. 验证核心功能
python -m pytest tests/test_desktop_singleton.py -q

# 4. 验证 health 端点
curl http://127.0.0.1:8000/api/v1/health

# 5. 验证版本号
curl http://127.0.0.1:8000/api/v1/version
```

### 6.2 回归验证

- [ ] 安装后首次启动正常
- [ ] 旧版本升级后数据不丢失
- [ ] 激活码在旧版本上仍可激活
- [ ] 样本库数据迁移正常

### 6.3 监控

- [ ] GitHub Issues 无新报告
- [ ] 错误报告系统无异常
- [ ] 用户反馈无负面反馈

---

## 7. 归档

### 7.1 Git 标签

```bash
git tag -a v4.3.6 -m "Release v4.3.6 (2026-09-13)"
git push origin v4.3.6
```

### 7.2 版本生命周期

| 状态 | 期限 | 内容 |
|------|------|------|
| 活跃支持 | 6 个月 | 新功能 + Bug 修复 |
| 安全支持 | 6 个月 | 仅安全修复 |
| 归档 | 之后 | 不再更新 |

**参考**：`VERSION_LIFECYCLE.md`

### 7.3 发布记录

```
v4.3.6 - 2026-09-13
  新增：订单管理、授权生命周期、错误报告、用户反馈、全量数据导出、变更日志展示
  变更：授权状态 API 合并扩展信息、版本号 4.3.5 → 4.3.6
  测试：399 收集 / 389 通过 / 10 跳过
```

---

## 8. 紧急回滚

### 8.1 回滚触发条件

- [ ] 严重 Bug（数据丢失/崩溃/安全漏洞）
- [ ] 功能不可用（核心功能失效）
- [ ] 用户反馈负面（≥3 个相同问题）

### 8.2 回滚流程

```bash
# 1. 撤回 GitHub Release
gh release delete v4.3.6 --yes

# 2. 删除标签
git tag -d v4.3.6
git push origin :refs/tags/v4.3.6

# 3. 恢复版本号
# src/version.py: APP_VERSION = "4.3.5"

# 4. 更新 CHANGELOG.md 标记为撤回

# 5. 发布修复版本
# APP_VERSION = "4.3.7"（跳号，不回退到 4.3.6）
```

### 8.3 回滚验证

- [ ] 用户可安装上一版本
- [ ] 数据不丢失
- [ ] 激活码仍有效

---

## 9. 发布检查清单（快速版）

```
□ 版本号已更新（src/version.py）
□ CHANGELOG.md 已更新
□ USER_GUIDE.md 已更新（如影响用户）
□ 测试全绿（python -m pytest -q）
□ Ruff 全绿（ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018）
□ 手动验收通过（核心功能 + 安全）
□ Windows 打包通过
□ macOS 打包通过
□ Git 标签已推送
□ GitHub Release 已发布
□ 官网下载页已更新
□ 发布后验证通过
```
