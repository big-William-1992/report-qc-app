# 星衍放射质控软件 · 系统架构设计

> MetaGPT SOP · Phase 2 — Architect
> 版本：v4.3.6 · 更新日期：2026-09-13
> 状态：生产运行

---

## 1. 系统总览

### 1.1 架构模式

**FastAPI 单服务同源托管 SPA + pywebview 桌面壳**

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户界面层                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  SPA 前端 (web/static/)                                   │   │
│  │  index.html + app.bundle.js + style.css                    │   │
│  │  ┌────────┬────────┬────────┬────────┬────────┐          │   │
│  │  │ core   │ shell  │  qc    │ rules  │  data  │  ...     │   │
│  │  └────────┴────────┴────────┴────────┴────────┘          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            ↕ HTTP /api/v1/*                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  FastAPI 后端 (server/main.py)                            │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │   │
│  │  │  QC    │ │ Sample │ │ Account│ │  OCR   │  ...       │   │
│  │  └────────┘ └────────┘ └────────┘ └────────┘           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            ↕ Python import                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  核心引擎 (src/)                                          │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │ engine   │ │  NER     │ │  Lexicon │ │  Rules   │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            ↕ SQLite                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  数据层 (SQLAlchemy + SQLite)                             │   │
│  │  qc.db: users/departments/samples/queue/settings/order   │   │
│  │  samples.db: 样本库（待迁入 qc.db）                        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                            ↕ 桌面壳
┌─────────────────────────────────────────────────────────────────┐
│  pywebview 桌面壳 (desktop_app.py)                              │
│  后台起 uvicorn + 系统原生 WebView（macOS: WKWebView /          │
│  Windows: WebView2）                                              │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 前端架构 | 经典脚本 bundle（非 ES 模块） | `file://` 协议 CORS 拦截 ES 模块，经典脚本兼容两种协议 |
| 后端框架 | FastAPI | 单服务同源托管 SPA，减少运维复杂度 |
| 数据库 | SQLite（可切 PostgreSQL） | 单文件部署，零运维，院内内网场景足够 |
| 桌面壳 | pywebview | 跨平台（macOS/Windows），复用 Web 技术栈 |
| 规则引擎 | 纯 Python 标准库 | 零第三方依赖，离线可用，部署简单 |
| OCR | RapidOCR（离线） | 数据不出域，模型内置 |
| 授权 | Ed25519 离线签名 | 不联网验证，防复制 |

---

## 2. 组件详细设计

### 2.1 核心引擎（src/）

```
src/
├── engine.py               # 引擎入口：RuleEngine 类，协调 NER + 规则 + 评分
├── engine/
│   ├── engine_core.py      # 引擎核心逻辑（Mixin 模式）
│   ├── ner.py              # NER（命名实体识别）：从报告文本抽取实体
│   ├── claims.py           # 断言提取：正常/异常声明
│   ├── rules_consistency.py # 一致性规则（R5/R14/R15/R17）
│   ├── rules_meta.py       # 元信息规则（R1/R3/R6/R21）
│   ├── rules_sentence.py   # 句子规则（R12）
│   ├── rules_size.py       # 测量规则（R4/R22）
│   ├── rules_template.py   # 模板规则（R10/R18）
│   ├── rules_typo.py       # 错别字规则（R8/R19）
│   ├── scoring.py          # 评分系统（四维评分）
│   ├── textsplit.py        # 文本切分（段落识别）
│   ├── meta_extract.py     # 元信息抽取（性别/年龄/部位/侧别）
│   ├── lexicon_region.py   # 区域器官词表
│   ├── config_store.py     # 配置持久化
│   ├── models.py           # 数据模型
│   └── _compat_lexicons.py # 词表兼容层
├── _lexicons.py            # 阳性/阴性/否定/部位词表（引擎词典层）
├── anatomy_lexicon.py      # 解剖部位知识图谱（器官族 + 侧别 + 中英文映射）
├── highfreq_lexicon.py     # 高频标准词组库（R19 形近字/读音候选）
├── medical_whitelist.py    # 医疗白名单（防误报）
├── typo_lexicon.py         # 错别字词表
├── zh_ner.py               # 中文 NER（备选方案，未全量接入）
├── zh_radiology_synonyms.py # 中文放射同义词
└── ...
```

**设计模式**：Mixin 模式（engine_core.py + rules_*.py），每个规则模块独立可测。

**数据流**：
```
报告文本 → 文本切分(textsplit) → NER(ner) → 断言提取(claims)
  → 规则引擎(rules_*.py) → 评分(scoring) → 结果列表 + 高亮标记
```

### 2.2 服务端（server/）

```
server/
├── main.py                 # FastAPI 应用入口 + SPA 静态托管（2548 行）
├── db.py                   # SQLAlchemy 连接管理
├── models.py               # ORM 模型（User/Department/Sample/QueueItem/Setting/Order）
├── schemas.py              # Pydantic 请求/响应模型
├── deps.py                 # 依赖注入（require_emp / require_admin / require_license_active）
├── security.py             # 鉴权模块（HMAC token / 登录锁定 / CORS）
├── license_web.py          # Web 授权状态
├── core.py                 # 核心配置
├── static_spa.py           # SPA 静态文件服务
├── routes/                 # 路由模块（按领域拆分）
│   ├── route_qc.py         # /api/v1/qc/* 质控
│   ├── route_sample.py     # /api/v1/samples/* 样本库
│   ├── route_queue.py      # /api/v1/queue/* 队列
│   ├── route_account.py    # /api/v1/accounts/* 账号
│   ├── route_license.py    # /api/v1/license/* 授权
│   ├── route_ocr.py        # /api/v1/ocr/* OCR
│   ├── route_ris.py        # /api/v1/ris/* RIS 直连
│   ├── route_push.py       # /api/v1/push/* PACS 推送
│   ├── route_screen.py     # /api/v1/screen/* 屏幕区域
│   ├── route_settings.py   # /api/v1/settings/* 设置
│   └── route_feedback.py   # /api/v1/feedback/* 反馈
└── __init__.py
```

### 2.3 前端（web/static/）

```
web/static/
├── index.html              # SPA 入口（1179 行，内联导航）
├── css/
│   └── style.css           # 样式（1408 行）
├── js/
│   ├── app.bundle.js       # 打包产物（3682 行，10 模块拼接）
│   └── modules/            # 模块源文件（代码组织参考，不直接加载）
│       ├── core.js         # 核心：API 封装、状态管理、工具函数
│       ├── shell.js        # 外壳：导航、主题、状态栏
│       ├── qc.js           # 质控：报告输入、运行质控、结果展示
│       ├── rules.js        # 规则：规则维护、错别字、互斥冲突
│       ├── data.js         # 数据：样本库、驾驶舱、导出
│       ├── ocr.js          # OCR：屏幕区域监控
│       ├── settings.js     # 设置：账号、授权、快捷键、版本信息
│       ├── ris.js          # RIS：直连配置、批量拉取
│       ├── feedback.js     # 反馈：用户反馈提交
│       └── auth.js         # 认证：登录、注册、激活、变更日志
```

**前端状态管理**：全局 `window` 对象，模块间通过 `Object.assign(window, {...})` 共享函数和变量。

**关键状态变量**：
```javascript
// 全局状态（通过 window 共享）
window.APP_SETTINGS      // 应用设置
window.ACTIVE_QUEUE_ID   // 当前队列 ID
window.LICENSE_STATUS    // 授权状态
window.currentUser       // 当前登录用户
window.token             // Bearer token
```

### 2.4 数据层

#### 数据库表结构

**qc.db**（SQLAlchemy ORM）：

| 表 | 模型 | 用途 |
|----|------|------|
| users | User | 用户账号（emp_id/password_hash/role/dept_id） |
| departments | Department | 科室 |
| samples | Sample | 样本库（report_text/findings_json/scores_json） |
| queue | QueueItem | 队列（待复核/待质控） |
| settings | Setting | 设置（key/value/user_id） |
| orders | Order | 订单管理（order_no/product/amount/status） |
| audit_logs | AuditLog | 审计日志 |

**samples.db**（独立 SQLite，samplelib.py 管理）：
- 待迁入 qc.db（task 229）

### 2.5 关键模块职责

| 模块 | 文件 | 职责 | 行数 |
|------|------|------|------|
| 引擎入口 | src/engine.py | RuleEngine 类，协调 NER + 规则 + 评分 | — |
| 引擎核心 | src/engine/engine_core.py | 核心逻辑（Mixin） | — |
| 服务端入口 | server/main.py | FastAPI 应用 + 路由 + 静态托管 | 2548 |
| 数据库 | server/db.py | SQLAlchemy 连接（WAL + busy_timeout 30s） | — |
| 数据模型 | server/models.py | ORM 模型定义 | — |
| 鉴权 | server/security.py | HMAC token / 登录锁定 / CORS | — |
| 依赖注入 | server/deps.py | require_emp / require_admin / require_license | 463 |
| 账号 | src/accounts.py | 账号管理（PBKDF2 600k） | 346 |
| 授权 | src/license_utils.py | Ed25519 离线激活 / 浮动授权 | 705 |
| 自动更新 | src/auto_updater.py | 更新检查 / 下载 / 安装 | 754 |
| 样本库 | src/samplelib.py | SQLite 样本存储 / 脱敏 / 统计 | 602 |
| OCR | src/ocr_provider.py | RapidOCR 屏幕区域监控 | 338 |
| 备份 | src/backup.py | SQLite VACUUM INTO 在线备份 | 396 |
| 日志 | src/logger.py | 结构化 JSON 日志 | — |
| 错误报告 | src/error_reporter.py | 匿名错误上报 | — |
| RIS | src/ris.py | RIS 数据库直连 | — |

---

## 3. API 设计

### 3.1 API 分层

```
/api/v1/
├── health                  # 健康检查（DB/磁盘/会话/授权/初始化）
├── version                 # 版本信息
├── changelog               # 变更日志
├── auth/
│   ├── register            # 注册（admin 首次创建）
│   ├── login               # 登录（工号+密码）
│   ├── logout              # 登出
│   ├── change-password     # 修改密码
│   ├── me                  # 当前用户信息
├── qc/
│   ├── check               # 单份报告质控
│   ├── batch               # 批量质控
│   └── rules/*             # 规则维护
├── samples/
│   ├── list                # 样本列表
│   ├── get                 # 样本详情
│   ├── create              # 存入样本库
│   ├── delete              # 删除样本
│   ├── export              # 导出质控报表
│   ├── import              # 导入样本
│   └── stats               # 统计（类型分布/每日趋势）
├── queue/
│   ├── list                # 队列列表
│   ├── add                 # 入队
│   ├── remove              # 移出队列
│   └── clear               # 清空队列
├── license/
│   ├── status              # 授权状态
│   ├── activate            # 激活
│   └── (admin) deactivate  # 取消激活
│   └── (admin) extend      # 试用延期
├── ocr/
│   ├── upload              # OCR 上传识别
│   └── screen/regions      # 屏幕区域配置
├── ris/
│   ├── config              # RIS 配置
│   ├── fetch               # 拉取报告
│   └── test-connection     # 测试连接
├── push/
│   └── receive             # PACS 推送接收
├── settings/
│   ├── get                 # 获取设置
│   └── update              # 更新设置
├── user-feedback           # 用户反馈提交
├── export/data             # 全量数据导出
├── update/check            # 检查更新
└── admin/
    ├── audit-logs          # 审计日志
    ├── audit-logs/export   # 审计日志导出
    ├── license/status      # 管理员授权状态
    └── errors              # 错误报告查看/导出
```

### 3.2 鉴权机制

```
请求 → require_emp 依赖 → 验证 Bearer token → 返回 user
  ├── require_admin: 额外检查 role == 'admin'
  ├── require_license_active: 检查授权状态（试用/激活）
  └── 写接口强制 require_license_active（15 个接口）
```

### 3.3 安全头

```
Content-Security-Policy: default-src 'self'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

---

## 4. 部署架构

### 4.1 桌面端部署（单机）

```
┌──────────────────────────────────────┐
│  用户电脑（macOS / Windows）          │
│  ┌────────────────────────────────┐  │
│  │  pywebview 桌面窗口            │  │
│  │  ┌──────────────────────────┐  │  │
│  │  │  WebView (WKWebView/     │  │  │
│  │  │  WebView2)               │  │  │
│  │  │  ┌────────────────────┐  │  │  │
│  │  │  │  SPA 前端          │  │  │  │
│  │  │  └────────────────────┘  │  │  │
│  │  └──────────────────────────┘  │  │
│  │         ↕ http://127.0.0.1     │  │
│  │  ┌──────────────────────────┐  │  │
│  │  │  FastAPI (uvicorn)       │  │  │
│  │  │  + 核心引擎              │  │  │
│  │  └──────────────────────────┘  │  │
│  │         ↕ SQLite               │  │
│  │  ┌──────────────────────────┐  │  │
│  │  │  %APPDATA%/MedicalReportQC/│  │  │
│  │  │  qc.db / samples.db      │  │  │
│  │  └──────────────────────────┘  │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

### 4.2 科室部署（多机浮动授权）

```
┌──────────────────────────────────────────────────────────┐
│  院内内网                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ 工作站 1 │  │ 工作站 2 │  │ 工作站 3 │  ...           │
│  │ 桌面客户端│  │ 桌面客户端│  │ 桌面客户端│               │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘               │
│       │              │              │                     │
│       └──────────────┼──────────────┘                     │
│                      ↕                                    │
│              ┌─────────────────┐                          │
│              │  共享目录        │                          │
│              │  (浮动授权心跳)  │                          │
│              └─────────────────┘                          │
│                      ↕                                    │
│              ┌─────────────────┐                          │
│              │  RIS/PACS 数据库│                          │
│              └─────────────────┘                          │
└──────────────────────────────────────────────────────────┘
```

### 4.3 环境变量配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `QC_API_SECRET` | 本机模式自动生成 | HMAC token 签名密钥；非本机监听必须设置 |
| `QC_API_TTL` | 86400 | Bearer token 有效期（秒） |
| `QC_PORT` | 8000 | 服务端口 |
| `QC_HOST` | 127.0.0.1 | 监听地址 |
| `QC_CORS_ORIGINS` | 空 | 追加允许的跨域源 |
| `QC_APPDATA` | 平台默认 | 数据目录覆盖 |
| `QC_OCR_MAX_BYTES` | 20MB | OCR base64 上传上限 |
| `DATABASE_URL` | sqlite://assets/qc.db | SQLAlchemy 连接串 |
| `QC_BACKUP_ENABLED` | true | 是否启用自动备份 |
| `QC_BACKUP_INTERVAL_DAYS` | 7 | 备份间隔 |
| `QC_BACKUP_KEEP_DAYS` | 90 | 备份保留天数 |
| `QC_UPDATE_LOCAL_DIR` | 空 | 离线更新目录 |
| `QC_FLOATING_LICENSE` | false | 浮动授权模式 |
| `QC_FLOATING_SEATS` | 1 | 浮动授权座位数 |
| `QC_ERROR_REPORT_ENABLED` | true | 是否启用错误报告 |
| `QC_ERROR_REPORT_URL` | 空 | 远程上报端点 |

---

## 5. 安全架构

### 5.1 威胁模型

```
┌─────────────────────────────────────────────────────────────┐
│  外部威胁                                                    │
│  ├── 未授权访问（非本机）→ QC_API_SECRET 强制 + Bearer token │
│  ├── 暴力破解（登录）→ 5 次失败锁 15 分钟                    │
│  ├── 弱密码 → 8 位 + 字母数字 + 黑名单                      │
│  ├── SQL 注入（RIS）→ 只读校验（仅 SELECT/WITH）            │
│  ├── 路径穿越 → 导出产物前缀白名单                           │
│  ├── XSS → escapeHtml + CSP                                │
│  ├── CSRF → SameSite cookie                                │
│  └── 授权绕过 → 机器指纹绑定 + 每次启动回验                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  内部威胁                                                    │
│  ├── 数据泄露 → 仅存本机 SQLite，不上传网络                   │
│  ├── 隐私泄露 → 入库可脱敏，错误报告脱敏                      │
│  ├── 未审计操作 → 21 个敏感端点审计日志                      │
│  └── 越权访问 → doctor 仅见自己数据，admin 见全部             │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 数据流安全

```
报告文本 → [仅存本机 SQLite] → 质控结果 → [仅存本机]
  ├── 样本入库：可选脱敏（去除患者姓名）
  ├── 错误报告：自动脱敏（去除患者标识）
  ├── 审计日志：仅记录操作，不记录报告内容
  └── 数据导出：管理员手动操作，有审计记录
```

---

## 6. 可扩展性设计

### 6.1 规则扩展

- 词表外部化：5 个 JSON 词表文件 + `rules_config.json`
- 规则变更不改代码（JSON 配置）
- 用户可通过「⚙ 规则维护」界面增删错别字/互斥冲突

### 6.2 后端扩展

- `DATABASE_URL` 可切 PostgreSQL
- 路由模块化：`server/routes/` 按领域拆分
- 依赖注入：`server/deps.py` 统一管理

### 6.3 LLM 扩展

- `src/llm_client.py`：LLM 后端抽象（通义千问 / ollama / llama.cpp）
- `src/llm_engine.py`：LLM 语义质控
- `src/llm_fusion.py`：LLM 与规则引擎融合

### 6.4 插件化

- `src/rag.py`：RAG 检索增强（预留）
- `src/radiology_translator.py`：中英文翻译（预留）

---

## 7. 构建与打包

### 7.1 打包流程

```
源码 → PyInstaller (build/report_qc.spec)
  ├── Windows: build_windows.bat → exe + Inno Setup 安装包
  ├── macOS: tarball 源码包
  └── 通用: 便携 exe
```

### 7.2 CI 流程

```
GitHub Actions (build-windows.yml)
  ├── unit-tests (ubuntu-latest): pytest 全量
  ├── build (windows-latest): PyInstaller 打包
  └── artifacts: exe + installer
```

### 7.3 资源路径

```
开发态：assets/ 目录
打包态：
  ├── macOS: ~/.local/xingyan_qc/
  ├── Windows: %APPDATA%/MedicalReportQC/
  └── 统一通过 src/paths.py 解析
```
