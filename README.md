# 星衍放射质控软件

> 面向放射科医生的影像报告智能质控助手：粘贴即查、复制即控、后台快捷键一键质控、离线 OCR 自动回填、按错误严重度红/橙/蓝高亮、样本沉淀、科室报表、责任到人。

> ⚠️ **合规与免责**：本软件为报告质量**辅助核查**工具，**不替代医师诊断**。涉及二类医疗器械与等保三级相关合规要求，请在正式临床使用前完成相应注册与测评。所有报告数据**仅存本机 / 院内内网，不出域**。

---

## 文档导航

| 文档 | 说明 |
|------|------|
| [PRD.md](PRD.md) | 产品需求文档——功能规格、用户故事、验收标准 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构设计——组件设计、API 设计、部署架构 |
| [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) | 开发指南——环境搭建、编码规范、Git 工作流 |
| [TESTING_STRATEGY.md](TESTING_STRATEGY.md) | 测试策略——测试分层、CI 门禁、覆盖范围 |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | 发布清单——发布流程、验证步骤、回滚方案 |
| [USER_GUIDE.md](USER_GUIDE.md) | 用户指南——面向放射科医生的操作手册 |
| [CHANGELOG.md](CHANGELOG.md) | 变更日志——版本历史 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署指南——多机部署、浮动授权、离线更新 |
| [DATA_SECURITY.md](DATA_SECURITY.md) | 数据安全白皮书——架构安全、加密、备份 |
| [PRIVACY_POLICY.md](PRIVACY_POLICY.md) | 隐私政策——PIPL 合规 |
| [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) | 服务条款——¥59/年定价、退款政策 |
| [VERSION_LIFECYCLE.md](VERSION_LIFECYCLE.md) | 版本生命周期——支持策略 |
| [docs/接口文档_HTTP_REST.md](docs/接口文档_HTTP_REST.md) | HTTP REST 接口规范 |
| [docs/接口文档_程序化API.md](docs/接口文档_程序化API.md) | 程序化 API 调用规范 |
| [docs/INSTALL.md](docs/INSTALL.md) | 安装与权限配置 |
| [docs/ACTIVATION.md](docs/ACTIVATION.md) | 授权、试用期与激活码流程 |

---

## 一、系统简介

本系统基于 **中文放射报告 NER（命名实体识别）+ 解剖部位知识图谱 + 规则引擎**，对影像报告正文做自动化质量控制，覆盖 **性别矛盾、左右混淆、评分缺失、单位错误、描述-结论矛盾、部位不符、内部矛盾、同音错别字、自定义互斥冲突、模板合规，以及信息框-正文矛盾、前后文/上下文逻辑错误、逐部位精确比对（描述↔结论同部位正常/异常矛盾）** 等（规则族 R1–R23）。

核心能力：

- **三步即用**：粘贴报告 → 一键质控 → 红/橙/蓝高亮定位问题
- **后台全局快捷键一键质控**：焦点在 PACS / 其他窗口时，按快捷键即可触发质控
- **离线 OCR 屏幕区域监控**：对 PACS 患者信息栏 / 报告区域截图，本地 RapidOCR 识别并回填，全程**不联网**
- **按严重度高亮**：红 = 严重、橙 = 警告、蓝 = 提示，结果列表按严重度排序并附图例
- **剪贴板实时监听**：复制即质控，命中问题弹窗提醒，听写误写及时拦截
- **自动修正预览**：同音错别字逐条确认后再回填，矛盾类问题只提示不改写
- **账号系统**：工号 + 密码登录，质控责任到人
- **样本库与驾驶舱**：错误沉淀、趋势统计、一键导出科室质控报表
- **可配置规则**：错别字词典、互斥冲突、忽略名单、模板规范均可维护
- **RIS 直连（可选）**：院内内网连接 PACS/RIS，批量拉取报告质控入库
- **隐私优先**：所有数据仅存本机 SQLite，**不上传任何网络**

---

## 二、快速开始

> 📦 完整安装与权限配置见 [docs/INSTALL.md](docs/INSTALL.md)；授权、试用期与激活码流程见 [docs/ACTIVATION.md](docs/ACTIVATION.md)。

### 运行环境

- Python 3.10+（macOS 建议 3.11+）
- 桌面操作系统：macOS / Windows 11

### 安装与启动

**macOS（推荐）**
```bash
cd report_qc_app
pip install -r requirements.txt
python3 desktop_app.py
```

**Windows（推荐双击启动器）**
1. 安装 Python 3.10+，勾选 "Add python.exe to PATH"
2. 双击 `启动星衍质控.bat` —— 自动创建 `.venv` 并安装依赖
3. 若窗口打不开，安装 Edge WebView2 运行时

**Windows 打包成 exe**
- 详见 `build/` 目录：双击 `build_windows.bat`
- 或通过 GitHub Actions 自动构建（`.github/workflows/build-windows.yml`）

> **macOS 权限**：首次使用剪贴板监听或后台快捷键时，请在「系统设置 → 隐私与安全性 → 辅助功能 / 自动化」中授权。

---

## 三、界面与功能

程序主界面为三页签结构：**📋 报告质控 · 📊 质控驾驶舱 · 🔗 RIS 直连**。

### 3.1 报告质控页

| 区域 | 说明 |
|------|------|
| 报告文本区 | 粘贴或导入报告 |
| 元信息栏 | 性别 / 年龄 / 检查部位 / 申请部位 / 侧别 |
| 质控结果区 | 按严重度排序，正文红/橙/蓝高亮 |
| 评分依据区 | 逐项扣分明细 |
| 监听捕获记录 | 审计面板 |

**核心操作**：
- **▶ 运行质控**：对当前文本跑完整规则
- **📋 监听剪贴板**：开启后每 1 秒轮询，复制 ≥15 字自动质控
- **⌨️ 后台快捷键**：焦点在 PACS 时也能触发（需 pynput + 系统权限）
- **🖥 OCR 屏幕监控**：框选 PACS 区域，本地 RapidOCR 识别回填
- **✏️ 自动修正**：同音错别字预览确认后再回填
- **💾 存入样本库**：将报告与质控结果存档（可选脱敏）

### 3.2 质控驾驶舱页

- 错误类型分布饼图 + 每日趋势折线图
- 样本库列表：双击查看完整报告与质控结果
- 导出质控报表：CSV（UTF-8-BOM，Excel 中文不乱码）
- 入库时脱敏（去除患者姓名）

### 3.3 RIS 直连页

- 配置数据库连接（SQL Server / Oracle / MySQL / PostgreSQL）
- 填写拉取 SQL（须返回 `report_text` 列）
- 批量拉取报告质控入库
- 配置保存在本机 `assets/ris_config.json`

### 3.4 账号系统

- 工号 + 密码登录（PBKDF2 600k 迭代）
- 首次启动引导创建管理员
- 密码策略：最少 8 位 + 字母数字组合 + 弱密码黑名单
- 登录锁定：失败 5 次锁定 15 分钟
- 审计日志：21 个敏感端点全覆盖

---

## 四、规则引擎详解

| 规则 | 名称 | 严重度 | 严重度权重 |
|------|------|--------|-----------|
| **R1** | 性别矛盾 | 高 | −30 |
| **R2** | 左右侧混淆 | 高 | −30 |
| **R3** | 评分标准缺失 | 中 | −15 |
| **R4** | 计量单位错误 | 低 | −5 |
| **R5** | 描述-结论矛盾 | 中 | −15 |
| **R6** | 登记部位不符 | 高 | −30 |
| **R8** | 同音错别字 | 中 | −15 |
| **R9** | 自定义互斥冲突 | 中 | −15 |
| **R10** | 模板合规 | 低 | −5 |
| **R12** | 句子前后文 | 中 | −15 |
| **R14** | 前后文（跨段） | 中 | −15 |
| **R15** | 上下文（段内） | 中 | −15 |
| **R17** | 逐部位精确比对 | 高 | −30 |
| **R18** | 检查完整性 | 中 | −15 |
| **R19** | 形近字/读音候选 | 中/高 | −15/−30 |
| **R22** | 术语与测量一致性 | 中 | −15 |
| **R23** | 繁体字检测 | 低 | −5 |

> 详细规则逻辑见 [PRD.md](PRD.md) §5 规则引擎规格。

**严重度权重**：高 = −30，中 = −15，低 = −5。UI 配色：红 = 严重 / 橙 = 警告 / 蓝 = 提示。

**否定语境防误报**：阳性征匹配对「未见 / 未见明显 / 无 / 不伴…」等否定前缀后的词不计入异常。

---

## 五、评分说明

四个维度：**准确性 / 完整性 / 规范性 / 及时性**。

- **准确性**：100 − Σ(严重度权重)，最低 0
- **完整性**：缺 R3 评分标准 → 80，否则 100
- **规范性**：存在问题 → 90，否则 100
- **及时性**：固定 100（预留维度）

---

## 六、配置文件

| 文件 | 说明 |
|------|------|
| `assets/rules_config.json` | 用户可维护规则（错别字/互斥/忽略/模板） |
| `assets/ris_config.json` | RIS 连接配置 |
| `assets/accounts.db` | 账号库（工号 + 密码哈希） |
| `assets/ocr_models/` | RapidOCR 模型权重 |
| `assets/lexicons/` | 词表 JSON（5 个外部化词表） |

---

## 七、数据安全与隐私

- 所有报告、样本、配置、账号**仅存于本机**，**不发起任何网络上传**
- 「入库时脱敏」可去除患者姓名后再入库
- RIS 直连仅连接院内内网数据库，凭据存本机
- OCR 为本地推理，**截图不出本机**
- 错误报告自动脱敏（去除患者标识）
- 审计日志记录 21 个敏感端点操作

详见 [DATA_SECURITY.md](DATA_SECURITY.md) 和 [PRIVACY_POLICY.md](PRIVACY_POLICY.md)。

---

## 八、接口文档

仓库 `docs/` 提供两类接口规范：

- **[docs/接口文档_程序化API.md](docs/接口文档_程序化API.md)**：核心模块程序化调用
- **[docs/接口文档_HTTP_REST.md](docs/接口文档_HTTP_REST.md)**：HTTP / REST 接口规范

完整 API 设计见 [ARCHITECTURE.md](ARCHITECTURE.md) §3。

---

## 九、常见问题

**Q1：监听功能好像没启动？**
- 检查状态栏是否显示「● 监听中」
- macOS 需在系统设置允许终端/Python 访问剪贴板
- 确保复制内容 ≥15 字

**Q2：后台快捷键在 PACS 里按了没反应？**
- Windows：已用系统级热键，焦点在 PACS 也能触发
- macOS/Linux：需 `pip install pynput` + 系统辅助功能授权
- 检查「设置 → 一键质控快捷键」是否已配置

**Q3：OCR 屏幕监控识别不准？**
- 确认已 `pip install -r requirements.txt`
- 框选区域需包含清晰文字
- 内置变化检测：区域内容未变会跳过

**Q4：自动修正把对的词改错了？**
- 自动修正只改 R8 同音错别字，且**先预览确认**
- 矛盾类绝不自动改
- 点弹窗「忽略」或「写入永久忽略规则」固化

**Q5：导出的报表中文乱码？**
- 报表为 UTF-8-BOM 编码，用 Excel 直接打开正常

**Q6：忘记账号密码？**
- 重置本地 `assets/accounts.db`，首次启动重新创建管理员

---

## 十、技术架构

> 完整架构设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

```
┌─────────────────────────────────────────────────────────┐
│  SPA 前端 (web/static/)                                 │
│  index.html + app.bundle.js + style.css                  │
├─────────────────────────────────────────────────────────┤
│  FastAPI 后端 (server/main.py)                           │
│  /api/v1/* REST 接口 + 静态文件托管                       │
├─────────────────────────────────────────────────────────┤
│  核心引擎 (src/)                                         │
│  NER + 知识图谱 + 规则引擎 + 评分                          │
├─────────────────────────────────────────────────────────┤
│  数据层 (SQLAlchemy + SQLite)                            │
│  users / departments / samples / queue / settings / order│
└─────────────────────────────────────────────────────────┘
         ↕ pywebview 桌面壳 (desktop_app.py)
```

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `QC_API_SECRET` | 本机自动生成 | HMAC 密钥；非本机监听必须设置 |
| `QC_PORT` | 8000 | 服务端口 |
| `QC_HOST` | 127.0.0.1 | 监听地址 |
| `QC_APPDATA` | 平台默认 | 数据目录覆盖 |
| `DATABASE_URL` | sqlite://assets/qc.db | SQLAlchemy 连接串 |
| `QC_BACKUP_ENABLED` | true | 自动备份 |
| `QC_UPDATE_LOCAL_DIR` | 空 | 离线更新目录 |
| `QC_FLOATING_LICENSE` | false | 浮动授权模式 |
| `QC_ERROR_REPORT_ENABLED` | true | 错误报告 |

完整列表见 [ARCHITECTURE.md](ARCHITECTURE.md) §4.3。

---

## 十一、版本与更新

- **当前版本**：v4.3.6（2026-09-13）
- **变更日志**：详见 [CHANGELOG.md](CHANGELOG.md)
- **版本支持**：详见 [VERSION_LIFECYCLE.md](VERSION_LIFECYCLE.md)

### 发布流程

> 完整发布清单见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。

```
准备 → 验证 → 打包 → 分发 → 验证 → 归档
```

---

## 十二、开发

> 完整开发指南见 [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md)。

### 快速开发

```bash
# 安装依赖
pip install -r requirements.txt -c constraints.txt

# 启动开发服务
uvicorn server.main:app --port 8000 --reload

# 运行测试
python -m pytest -q

# 代码检查
ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018 src/ server/
```

### 测试策略

> 完整测试策略见 [TESTING_STRATEGY.md](TESTING_STRATEGY.md)。

- 单元测试：399 收集 / 389 通过 / 10 跳过（pytest）
- E2E 测试：Playwright
- CI 门禁：pytest 全量 + ruff 致命规则
