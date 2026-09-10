# 星衍放射质控软件 · 变更日志

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号遵循语义化版本（MAJOR.MINOR.PATCH）。

---

## v4.3.3 (2026-09-09)

#
## v4.3.4 (2026-09-10)

### 修复 (Fixed)
- **前端加载修复**：ES 模块 → 经典脚本 bundle（app.bundle.js），消除 file:// 模式下 CORS 拦截导致闸门函数无法加载的问题
- **file:// 双协议兼容**：index.html 资源路径改为相对路径（css/style.css、js/app.bundle.js），
  直接双击 HTML 或经 HTTP 服务器访问均正常；server/main.py SPA 兜底路由增加根级静态文件解析（防路径穿越）

### 变更 (Changed)
- **密码策略强化**：最少 8 位 + 字母数字组合 + 弱密码黑名单（zxcvbn 校验）
- **登录锁定机制**：IP 维度 → 工号维度锁定，失败次数过多返回明确错误信息
- **审计日志页**：新增管理员可见的操作审计页面，支持按操作类型/工号/时间筛选
- **患者信息栏**：所有框统一大小排成一行（CSS grid repeat(6, 1fr)）

## 修复 (Fixed)
- **前端模块化拆分**：3410 行单文件 app.js → 10 个 ES 模块 + 入口，消除重复定义，解决跨模块状态共享（ACTIVE_QUEUE_ID / LICENSE_STATUS / APP_SETTINGS）
- **安全增强**：登录限流（IP 维度 5 次/5 分钟 → 15 分钟锁定）、审计日志（21 个敏感端点全覆盖，SQLite 持久化）、CORS 白名单（禁通配）、推送 API Key 恒定时间比较
- **引擎拆分**：单文件 2784 行 → 5 Mixin + 4 基础设施模块（types/helpers/NER/config），对外 API 完全兼容
- **反馈闭环**：R19 误报/漏报 → 人工审核 → whitelist_delta 词表闭环
- **CI 阻断闸**：pythonnet PE 头验证 / SQLAlchemy 打包检查 / exe 真实启动探测 health 端点 / 自动更新 E2E 测试
- **Ruff + pip-audit**：致命规则（F821/F401/F811/F822）接入 CI，pip-audit 依赖 CVE 扫描
- **词表外部化**：5 个 JSON 词表文件 + rules_config.json，规则变更不改代码
- **路径统一**：src/paths.py 统一资源解析（frozen 双模式：_MEIPASS / exe 目录）
- **登录闸门**：自助注册（强制 doctor 角色）/ 登录页改密 / 授权激活（Ed25519 离线）
- **PACS 推送**：JSON/XML 双格式 + X-API-Key 鉴权 + 字段化推送对接
- **UI 对齐**：Be.Healthy 医疗 SaaS 风格，暗色模式，键盘快捷键（全局 pynput + 应用内可重绑）
- **依赖锁定**：constraints.txt 锁定核心栈版本，杜绝 CI 漂移

---

## v4.3.0 (2026-08-18)

### 新增 (Added)
- Node 24 迁移：所有 GitHub Actions 升级到 Node 24 版本
- LLM 语义质控子系统：Qwen 系列 / 通义千问 API 兼容 + 本地 ollama / llama.cpp
- LLM v2 prompt 鲁棒性数据集 + FT prompt 模式 + 本地 LoRA v2 adapter
- R24/R25 规则增强
- Windows 打包路径修复、快捷键降级统一

### 修复 (Fixed)
- Git LFS 管理模型权重（*.safetensors）
- httpx 依赖声明修复（starlette 新版本 TestClient 导致 pytest 收集失败）
- CI pytest 失败输出转为 error annotations + 日志 artifact 上传

---

## v4.2.0 (2026-08-01)

### 新增 (Added)
- 回归评测基准固化
- 川蓉德政策补贴地图入库
- LLM 模型部署文档 DEPLOYMENT.md

### 修复 (Fixed)
- 锁定核心依赖版本（constraints.txt）

---

## v4.1.0 (2026-07-15)

### 新增 (Added)
- CI 门禁：pytest 全量阻断 + build + launch-test + test-update
- OCR 测试字体跨平台回退链（修复 Windows runner 缺 macOS 字体）

### 修复 (Fixed)
- CI 单字段变化检测测试改用高对比渲染

---

## v4.0.0 (2026-07-01)

### 重大变更 (Changed)
- **引擎重构**：单文件 2784 行 → 包结构（engine_types / engine_helpers / engine_ner / engine_config / engine_meta / rules_meta / rules_typo / rules_template / rules_region / rules_lesion）
- **可观测性改造**：samplelib 导出拆分 + SQL 白名单注释
- **反馈闭环 P1-4**：医生反馈回流 → 本机库 → 增量精调
- **schemas 修复**：__all__ 补 FeedbackReq，修复 star 导入下 CI 收集失败
- **engine 子模块静态导入链**：诊断包纳入 feedback.db

---

## v3.0.0 (2026-06-01)

### 重大变更 (Changed)
- **Web 化**：从 Tkinter 桌面版迁移到 FastAPI + SPA（web/static/）+ pywebview 桌面壳
- **多用户**：SQLAlchemy 多用户/科室自托管（users / departments / samples / queue / settings）
- **RIS/PACS 集成**：推送接收 + 轮询 + 数据库直连三种模式
- **OCR**：离线中文 OCR（RapidOCR + ONNX，模型内嵌 assets）
- **自动更新**：macOS 源码 tarball + Windows 便携 exe 双平台

---

## v2.0.0 (2026-05-01)

### 新增 (Added)
- 反馈审核系统（pending → reviewed → whitelist_delta）
- 样本导出多格式（PDF / Word / CSV / JSON）
- 评分系统（四维评分：准确性 / 完整性 / 规范性 / 及时性）

---

## v1.0.0 (2026-04-01)

### 初始版本
- 规则引擎基础框架（R1-R7）
- Tkinter 桌面界面
- SQLite 样本库
- 离线运行（零外部依赖）
