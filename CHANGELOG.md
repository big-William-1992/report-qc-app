# 星衍放射质控软件 · 变更日志

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号遵循语义化版本（MAJOR.MINOR.PATCH）。

---

## v4.3.7 (2026-09-30)

> 本轮为**审计后的缺陷修复**（以代码为准逐条核实），不新增功能。
> 修复了 4 个此前会静默损坏数据/交付物的问题 + 1 类「两套实现」隐患。

### 修复 (Fixed)
- **Windows 安装包的 OCR 模型是 LFS 指针**（发布级）：`assets/ocr_models/*.onnx`
  自 `8fd4e75` 起以 Git LFS 指针入库，而所有 workflow 都没开 `lfs:`，
  `actions/checkout` 只取到 131~133 字节的指针文本 → PyInstaller 把指针当模型
  打进 exe，装机后「屏幕区域 OCR」直接失效。修复：build job 显式
  `git lfs pull --include="assets/ocr_models/*.onnx"`（只拉 13MB，不拉 319MB 的
  adapters）；打包校验改为**按字节数**核对（`2432880 / 10690752 / 585532`），
  并把该校验从「非阻断告警」改为**阻断**（坏包不许发 Release）。
- **数据层源码态库位置分裂**：`server/db.py` 默认库算成 `<root>/qc.db`（漏了
  `assets/` 段），而 `paths.qc_db_path()`/`samplelib.db_path()`/本文件注释都说应是
  `<root>/assets/qc.db` → 源码运行时账号/科室一个库、样本另一个库，多用户归属
  无法靠同库约束保证（打包态恰好同目录，故长期未暴露）。修复：默认库统一为
  `assets/qc.db`，并新增一次性幂等迁移把误落在 `<root>/qc.db` 的表并回统一库
  （目标表非空则跳过，旧文件保留不删；仅在未被覆盖的默认库上执行）。
- **备份/恢复整条链路从未可用**：`src/backup.py` 一直
  `from server.db import get_db_path`，而该函数**从未存在**，且该 import 未被
  try 包住 → `run_backup()` 直接抛 ImportError：
  `POST /api/v1/admin/backup/run` 返回 500、每日自动备份被调度器静默吞掉。
  另：备份只扫 `<assets>/`、恢复也只写回 `<assets>/`，打包态真实位置在用户
  可写目录 → 「恢复成功」却不生效。修复：补 `server.db.get_db_path()`；新增
  `_resolve_known()` 作为备份与恢复**共用**的唯一路径解析入口；备份列表按绝对
  路径去重（统一库后不再备份两份）。
- **反馈闭环「误报→补白名单」在生产引擎上空转**：闭环写入的
  `assets/lexicons/user_whitelist.json` 只有已删除的旧引擎栈会读，活引擎
  `src/engine/` 的 R19 抑制集只认内置 `R19_SAFE_WORDS` 与 `r19_safe_words`。
  修复：`load_rules_config` 并入 `r19_user_whitelist`（派生键，不落盘），
  R19 按长度分别并入「安全词」与「覆盖区间」。同时收紧
  `tests/test_r19_sensitivity.py` 的 `or` 联合断言（正是它掩盖了该缺陷）。
- **跨机合并/导入丢失科室归属**：`_import_rows` 的 INSERT 漏了 `dept_id` 列。
- **医生反馈（badcase 回流）整条链路从未可用**：`badcase_store` 的 `feedback`
  表只在显式调用 `init_db()` 时创建，而启动流程**从未调用**它 → 生产环境
  `POST /api/v1/feedback` 必然 `no such table: feedback`，被端点吞成
  ok=False「反馈暂存失败」并返回 HTTP 200 → 医生点的每一次「误报/漏报」静默丢失。
  （`tests/test_badcase_store.py` 没抓到是因为它自己先调了 `init_db()`，
  即由测试代劳了生产没做的前置条件。）修复：`badcase_store` 改为「用前自愈」
  （record/stats/list_recent 自动确保建表），启动流程再显式建一次。
- **`/api/v1/health` 的 DB 探针永远失败**：实现写的是
  `with _db_mod.get_db() as _sess:`，但 `get_db()` 是 FastAPI 的**生成器依赖**
  （yield 会话），不是上下文管理器 → 每次抛 TypeError 被吞成
  `db="error: TypeError"`。后果：探活永远"绿"，真正的连库/缺表故障查不出来
  （CI 的 launch-test 与桌面壳就绪判断都看这个接口）。修复：显式取
  `SessionLocal`，并额外探一次 `samples` 表（能发现"库在但表缺失"）。
- **LLM 模型部署指南被整体覆盖**：v4.3.6 的 `d73e2b9` 把 `DEPLOYMENT.md`
  （LLM 质控模型部署指南，155 行）替换成了「科室多机部署手册」，原内容只剩在
  git 历史里——「应用怎么部署」与「模型怎么部署」是两件事，不该互相覆盖。
  修复：从 `723531b` 恢复为独立文档 `docs/LLM_MODEL_DEPLOYMENT.md`
  （并在 `prompt_mode` 行补入实测对照），README 文档导航补一行。

### 变更 (Changed)
- **samplelib 并轨 ORM**：`src/samplelib.py` 不再自建裸 `sqlite3` 连接与手写
  `CREATE TABLE/ALTER`，统一走 `server/db.SessionLocal` + `models.Sample` ——
  schema 只剩一个真相源，连接池/`BEGIN IMMEDIATE`/`busy_timeout`/WAL 只有一处
  配置；`path=` 显式指定临时库的能力保留（多机合并/导入/测试隔离）。
  `db_path()` 改为直接取 ORM engine 的文件路径，与账号库**必然同库**。
  （保留 sqlite3 的唯一场景：只读外部遗留库文件，如旧 `samples.db`。）
- **删除两套「写了但从不生效」的并行实现**（共 23 个文件，约 3900 行，均可从
  git 历史取回）：① 第二套引擎 `src/engine.py` 门面 + `engine_types/helpers/
  config/ner/meta.py` + `src/rules_*.py`（被「包优先于同名模块」静默架空，
  v4.3.6 却在同时改两份 `rules_typo.py`）；② `server/routes/` 下 10 个从未
  `include_router` 的路由模块 + `server/static_spa.py`（实测 49 处「路径+方法」
  重复注册，后注册者永不生效）。
- **版本号**：`4.3.6` → `4.3.7`
- **OCR 模型移出 Git LFS**：`assets/ocr_models/*.onnx`（约 13MB）改为普通文件
  入库，从根上消灭「没有 git-lfs 的克隆/CI/Docker 构建拿到 131~133 字节指针、
  再把指针打进 exe」这一整类故障。训练权重 `*.safetensors` 仍走 LFS。
  已验证：把 LFS 过滤器替换为 `cat` 后浅克隆，三个模型字节数完全正确。

### 新增 (Added)
- **回归守卫测试**（+32 用例，均为此前完全没有覆盖的路径）：
  - `tests/test_single_implementation.py`：引擎必须命中包、旧模块不得复活、
    `server/routes/` 每个模块必须真被注册、同一路径+方法不得注册两次。
  - `tests/test_data_layer_unified.py`：样本库与 ORM 必须同库、schema 只有一个
    声明、`path=` 往返、统计口径、导入保留 `dept_id`、备份→恢复往返与目标路径。
  - `tests/test_feedback_whitelist_loop.py`：写入学习词后同一报告不再报；
    派生键不落盘；词表缺失可容忍。
  - `tests/test_llm_degrade.py`：LLM 不可用/报错时规则结果照常返回、
    两个端点的契约键恒在（此前 `run_full_qc` 零测试覆盖，而「模型未部署」
    正是默认状态）。
  - `tests/test_feedback_endpoint.py`：从 HTTP 入口提交反馈必须真落库、可导出、
    统计可读；store 在全新库上「用前自愈」。
  - `tests/test_health_endpoint.py`：health 必须报 `db=connected`，且在
    `samples` 表缺失时**必须**报错（证明探针真的在查库）。
- `benchmarks/README.md`：评测口径说明、**首次记录 LLM 基线**，并给出读法与
  下一步。实测（微调 Qwen3-4B LoRA / MLX / `prompt_mode=ft`，200 例）：

  | 链路 | recall | specificity | 耗时 |
  |---|---|---|---|
  | rules | 100.0% | 100.0% | 20s |
  | llm | 9.9% | 100.0% | 479s |

  同 20 例的 `prompt_mode` 对照：`ft` → 18.2% / **100%**；`full` → 0% / **0%**
  （对每份报告都报，含正常报告）——实测证实"必须用 ft、full 会诱导幻觉"。
  9.9% 的解读已写入该文档：本集标签是**规则注入**的确定性错误，测不出 LLM 的
  目标价值（语义级漏报），只反映"与规则的重合度"；下一步应补语义错误标注集。
- `AGENTS.md` 新增 4 节：单一实现约定、数据层约定（含两处刻意保留的裸 sqlite3）、
  Git LFS 约定（onnx 不再进 LFS）、测试隔离。

### 文档 (Docs)
- `ARCHITECTURE.md`：修正引擎/服务端目录树（删掉已删模块）、数据层章节
  （单一 `qc.db`、单一 schema 真相源）、模块职责表中的行数与表名（`audit_log`）。
- `src/llm_config.example.json`：改为**微调产物**的推荐配置
  （`provider=ollama, model=qc-qwen3, prompt_mode=ft`），原先示例指向未微调的
  `qwen2.5:3b` 且缺 `prompt_mode`，照抄会诱导幻觉。

### 测试 (Test)
- 全量 **422 passed / 7 skipped**（新增 32 用例零回归）；ruff 致命规则全绿。
  （受限沙箱下另有 2 项因被禁止写用户目录而失败，属环境限制非缺陷。）

---

## v4.3.6 (2026-09-12)

### 新增 (Added)
- **订单管理** (`server/models.py` + `server/main.py`)：Order SQLAlchemy 模型，支持订单创建/确认/取消/退款，CSV/JSON 导出
- **授权生命周期**：授权取消（`/admin/license/deactivate`）、试用延期（`/admin/license/extend`）、试用告警（7/3/1 天阈值）
- **错误报告系统** (`src/error_reporter.py`)：匿名本地 JSONL 日志，数据脱敏（去除患者标识），管理端查看/导出
- **用户反馈** (`/api/v1/user-feedback`)：应用内反馈提交（问题/建议/咨询），写入本地日志
- **全量数据导出** (`/api/v1/export/data`)：样本+队列+设置 JSON 导出，供数据迁移
- **版本信息 API** (`/api/v1/version`)：返回当前版本号和软件名称
- **变更日志 API** (`/api/v1/changelog`)：服务端解析 CHANGELOG.md，按版本返回条目
- **前端反馈模态**：顶部栏 💬 按钮，反馈类型选择 + 详细描述 + 联系方式
- **前端版本/变更日志展示**：设置页版本信息区域，动态加载当前版本变更日志
- **授权续费提示**：设置页授权区域新增续费联系方式和试用告警显示
- **法律文书**：隐私政策、服务条款、数据安全白皮书、用户指南、版本生命周期策略

### 变更 (Changed)
- **授权状态 API**：`/api/v1/license/status` 合并扩展信息（试用告警、到期日期、授权类型、座位数）
- **版本号**：`4.3.5` → `4.3.6`
- **侧边栏版本显示**：静态 v1.0 → 动态加载当前版本号

### 环境变量新增
- `QC_ERROR_REPORT_ENABLED`：是否启用错误报告（默认 true）
- `QC_ERROR_REPORT_URL`：错误报告上传地址（空则仅本地存储）

### 测试 (Test)
- 全项目 389 测试通过，ruff 致命规则全绿

---

## v4.3.5 (2026-09-12)

### 新增 (Added)
- **结构化日志模块** (`src/logger.py`)：替代全项目 log_quiet 静默模式，以 WARNING 级别输出结构化 JSON 上下文，70+ 调用点自动升级
- **数据库自动备份** (`src/backup.py`)：SQLite VACUUM INTO 在线备份 + 7/30/90 天轮转 + 后台调度器 + API 端点（status/run/restore）
- **离线更新通道**：`QC_UPDATE_LOCAL_DIR` 环境变量支持从共享盘/U盘读取更新包，跳过 GitHub 下载
- **浮动授权模型** (`src/license_utils.py`)：科室多机部署，按座位数计费，心跳共享目录控制并发
- **运维手册** (`DEPLOYMENT.md`)：多机部署、浮动授权、离线更新、备份恢复、集中审计、健康监控全流程指南
- **审计日志批量导出**：`/api/v1/admin/audit-logs/export` 支持 JSON/CSV 格式，多机合并归档
- **授权状态 API**：`/api/v1/admin/license/status` 查询单机/浮动授权、座位数使用详情

### 变更 (Changed)
- **健康检查增强**：`/api/v1/health` 新增 DB 连通性、磁盘空间、会话数、授权状态、初始化告警
- **log_quiet 静默升级**：DEBUG → WARNING 级别，结构化 JSON 上下文（where/ts/platform/python/frozen）
- **激活码验证**：浮动模式下签名对象从机器指纹改为部门标识

### 环境变量新增
- `QC_BACKUP_ENABLED` / `QC_BACKUP_INTERVAL_DAYS` / `QC_BACKUP_KEEP_DAYS` / `QC_BACKUP_DIR`
- `QC_UPDATE_LOCAL_DIR`
- `QC_FLOATING_LICENSE` / `QC_FLOATING_SEATS` / `QC_FLOATING_DEPT_ID` / `QC_FLOATING_HEARTBEAT_DIR`

### 测试 (Test)
- 全项目 389 测试通过，ruff 致命规则全绿

---

## v4.3.4 (2026-09-10)

### 修复 (Fixed)
- **前端加载修复**：ES 模块 → 经典脚本 bundle（app.bundle.js），消除 file:// 模式下 CORS 拦截导致闸门函数无法加载的问题
- **file:// 双协议兼容**：index.html 资源路径改为相对路径（css/style.css、js/app.bundle.js），
  直接双击 HTML 或经 HTTP 服务器访问均正常；server/main.py SPA 兜底路由增加根级静态文件解析（防路径穿越）
- **非本机监听安全强化**：`0.0.0.0` 等非本机地址未设 `QC_API_SECRET` 时阻止启动（`SystemExit`），本机回环保持桌面端零配置兼容

### 变更 (Changed)
- **鉴权模块抽离**：`server/security.py` 独立模块（`_load_or_create_secret` / `make_token` / `verify_token` / `require_emp` / `_require_secret_for_network_host` 等），`server/main.py` 精简 152 行
- **全项目无用导入清理**（ruff F401 / F821 全绿）：`main.py` / `deps.py` / `engine.py` / `llm_engine.py` / `static_spa.py` / `test_ocr_fix.py` / 全部 `route_*` 统一瘦身
- **密码策略强化**：最少 8 位 + 字母数字组合 + 弱密码黑名单（zxcvbn 校验）
- **登录锁定机制**：IP 维度 → 工号维度锁定，失败次数过多返回明确错误信息
- **审计日志页**：新增管理员可见的操作审计页面，支持按操作类型/工号/时间筛选
- **患者信息栏**：所有框统一大小排成一行（CSS grid repeat(6, 1fr)）
- **README 环境变量说明更新**：明确非本机部署必须显式配置 `QC_API_SECRET`

### 测试 (Test)
- **新增 `TestNetworkHostSecretPolicy`**：3 用例覆盖本机允许 / 非本机拒绝 / 非本机允许

## v4.3.3 (2026-09-09)

### 修复 (Fixed)
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
