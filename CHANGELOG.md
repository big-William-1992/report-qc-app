# 星衍放射质控软件 · 变更日志

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号遵循语义化版本（MAJOR.MINOR.PATCH）。

---

## v4.3.7 (2026-09-30)

> 本轮为**审计后的缺陷修复**（以代码为准逐条核实），不新增功能。
> 修复了 4 个此前会静默损坏数据/交付物的问题 + 1 类「两套实现」隐患。

### 许可 (License)
- **许可由 MIT 变更为 PolyForm Noncommercial License 1.0.0**（D2 决策，见
  `docs/OPEN_DECISIONS.md`）：**非商业用途免费**（科研、教学、个人学习与试验、
  非营利与公共机构），**商业用途需取得商业许可**（年费订阅即商业许可）。
  动机：仓库为 public 的 MIT 明文允许"修改、再分发、**销售**"，与服务条款的
  年费订阅 + 激活码 + 禁止修改分发**正面冲突**，且 MIT 对已发布版本**不可撤销**
  （当时 0 fork / 0 star / 1 次下载，是修正成本最低的窗口）。
  事实与选项留档见 `docs/LICENSE_OPTIONS.md`。
- **旧授权留档**：`LICENSE-MIT` 保存原 MIT 文本；`LICENSE-HISTORY.md` 记录分界点
  （≤ `a535f27` 及此前已发布产物为 MIT，**永久有效**；之后为 PolyForm）。
- **随副本传递（许可的硬性义务）**：新增 `NOTICE.md`（含 `Required Notice:` 行与
  第三方组件清单）；CI 打包新增**阻断式**步骤，把 `LICENSE`/`NOTICE.md`/`免责声明.md`
  复制进 `dist/报告质控软件/`，因此**绿色版 zip 与安装包都会带许可与免责声明**。
- **对外口径同步**：`README.md`、`docs/DISCLAIMER.md`、`TERMS_OF_SERVICE.md`、
  `docs/index.html`（落地页）已从"MIT 免费、可自由再分发"改为
  "非商业免费 / 商业需授权"；ToS 中与新许可冲突的"禁止修改或分发"条款改为
  按许可范围分述（非商业依 `LICENSE`，商业许可范围内限机构内部使用、不得再分发转售）。
- **仍待律师**：商业许可（订购协议）正式条款文本。

### 修复 (Fixed)
- **前端 bundle 语法错误导致整个界面失效**（**本轮最严重**）：`index.html` 以
  **经典脚本**方式加载 `js/app.bundle.js`（要兼容 file:// 与 http:// 两条路径），
  但 v4.3.6 的 `d73e2b9` 重新生成 bundle 时忘了剥掉顶层 `import/export` →
  浏览器直接 `SyntaxError: Unexpected token 'export'`，**SPA 的 JS 一行都不执行**
  （登录闸门/按钮/渲染全废），而 HTTP 仍 200、pytest 全绿、无人察觉。
  修复：新增 `tools/build_bundle.py`（剥 import/export 后按依赖顺序拼接）并重新生成；
  `tests/test_frontend_bundle.py` 守着「无顶层 import/export + 与 modules/ 同步 +
  `node --check` 可解析」。已用 Chromium 实测：修复前 `pageerror: SyntaxError`
  且 `window.APP_SETTINGS` 等全部缺失；修复后零错误、界面正常渲染。
- **PACS 推送的报告在队列里看不到**（功能缺陷）：`route_push.py` 从 `server/deps.py`
  导入的 `_queue_add_text` 把队列写进 `qc_queue.json`，而队列端点
  `GET /api/v1/queue` 读的是 qc.db 的 `QueueItem` 表 → 推送入队的报告永远看不到
  （RIS 轮询那条路径当时已改用 ORM，只有推送踩坑）。修复：入队收敛到
  `core.queue_add_text()`，main / deps / route_push 全部转调；新增
  `tests/test_queue_ingest_paths.py` 从 HTTP 入口验证「推送 → 队列端点可见」。
- **app 数据目录两套解析**：`core._appdata_dir()`（非 Windows 落 `~/.medical_report_qc`）
  与 `deps._appdata_dir()`（用户数据目录）不一致 → 同一个 `qc_queue.json` /
  `web_settings.json` 被写在两个地方、读写互相看不见。修复：统一为
  `paths.user_data_dir()`（并使其平台正确：macOS → Application Support、
  Windows → `%APPDATA%`、Linux → XDG），三方一致性由测试断言。
- **`server/deps.py` 里的第二套（更弱的）鉴权栈**：`SECRET/TOKEN_TTL/make_token/
  verify_token/_emp_from_auth/require_emp/require_emp_local/require_admin`。
  三处实证危害：① 缺省密钥是字面量 `change-me-in-prod`（security.py 用随机持久化
  密钥）→ 可伪造 token 且两套互不通用；② `require_emp` 的工号存在性校验写成
  `import src.accounts`，而 src 不是包 → ImportError 被吞、校验是死代码
  （任意填 X-Emp-Id 即可冒充）；③ 本地来源不校验账号。三者当前无人引用，但
  `ARCHITECTURE.md` 恰把 deps.py 描述为「依赖注入（require_emp/require_admin…）」。
  修复：deps.py 只从 security.py **再导出**，并加同一性守卫测试。
- **试用期可"删文件重置"**：`check_trial` 在 `first_run` 缺失时直接给满 90 天，
  HMAC 只防改日期不防删文件 → `rm license.dat` 即可无限续期。修复：试用起点冗余写到
  同目录 `.license_trial_anchor`（+ Windows 注册表 + 用户数据目录），取**最早**的
  有效签名日期为准；新增 `tests/test_trial_anchor.py`。
- **自动更新的完整性校验形同虚设**：客户端取 `<下载URL>.sha256` 强制校验，但发布
  流程**从不生成**该文件 → 永远走「无校验文件：跳过（宽容）」分支。修复：发布流程为
  每个产物生成同名 `.sha256` 一并发布，并加测试把「客户端要校验」与「流水线会产出」钉死。
- **诊断包里的 `feedback.db` 从来没进去**：该写入落在 `with ZipFile(...)` **块外**
  （归档已关闭 → 必然抛 ValueError 又被吞）。修复：移入块内，并改为默认**不含**
  患者正文（显式 `--include-patient-data` 才纳入），包内附 `README_诊断包.txt` 说明。
- **错误日志脱敏口径不一致（隐私）**：`report_exception` 只按 key 名剔除
  `patient/report_text`，`report_info` **一个都不剔**；而 `/api/v1/user-feedback`
  把用户反馈正文写进 `report_info` → 患者内容可能落盘。修复：两入口共用
  「白名单键 + 去标识擦洗」（未知键一律丢弃），并如实标注 `QC_ERROR_REPORT_URL`
  **未实现**（此前文档把它说成可用的上传地址）。
- **规则配置路径在 POSIX 打包态落到野路径**：`engine/config_store._rules_config_path()`
  硬编码 `%APPDATA%` 且缺 `os.path.isabs()` 守卫 → 得到相对路径
  `%APPDATA%/MedicalReportQC/`（在启动目录建目录），而 `backup` 用
  `paths.rules_config_path()` 备份/恢复 → 用户错别字表既进不了备份也恢复不回去。
  修复：委托 `paths.rules_config_path()`（单一来源、带守卫）。
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

### 性能 (Performance)
- **R19（同音/近音/形近错字）提速 8–28 倍，行为逐条不变**：它此前对**每个 2~4 字滑窗**
  遍历同长度分桶里的**全部拼音键**逐个算编辑距离（527 字报告 ≈10 万次 `_edit_distance`，
  实测 **977ms**；RIS 一次拉 200 份长报告约 **195s**，批量质控事实上不可用）。
  改用「删一字签名」反查表做**数学等价裁剪**（等长编辑距离≤1 ⇔ 汉明≤1 ⇔ 删同一位后相同），
  形近字同理用「掩码签名」反查。结果：527 字报告 977ms → **34.5ms（28x）**，
  短报告 121.8ms → **15.1ms（8x）**，`_edit_distance` 调用约 10 万 → **146 次**；
  200 份长报告 195s → **6.9s**。等价性用 2 万余探针逐条比对旧实现（集合/顺序/评分/类型全一致），
  且评测基线保持 100%/100%。守卫：`tests/test_r19_performance.py`
  （含**结构指标**——编辑距离调用次数，不受机器性能影响，退回全桶扫描立刻爆掉）。

### 重构 (Refactor)
- **路由拆分 S6a**：`accounts`(7) 与 `feedback`(3) 端点拆至
  `server/routes/route_accounts.py`、`route_feedback.py`；main.py 1519 → **1377 行**
  （自 S1 起累计 2567 → 1377）。
- **悬空引用守卫第二次抓到跨域回归**：移除 accounts 段后，`GET /api/v1/health` 仍引用
  `_LOGIN_FAIL`（登录失败限流表，原先定义在 accounts 段内，实为 `server/deps.py` 的同名对象）
  → 守卫标出 `GET /api/v1/health (health): ['_LOGIN_FAIL']`，已改为从 deps 显式导入。
  两次事故（S5 `_run_qc`、S6a `_LOGIN_FAIL`）共性：**被拆域的状态被另一个域以模块级全局名
  隐式依赖**，只测被改域无法发现。
- **路由拆分 S5**（最核心一片）：抽出 `server/qc_runtime.py`（进程级 RuleEngine 单例、
  规则刷新、`_run_qc`、按 IP 限流），16 个 `/api/v1/qc/*` 端点拆至
  `server/routes/route_qc.py`；main.py 1872 → **1519 行**（自 S1 起累计 2567 → 1519）。
  · **引擎单例必须只有一份**：多份会各自持有 rules_config 副本 → "改了规则只有部分端点生效"。
  · qc 与 feedback 端点原先**交错排列**，拆分按精确端点边界切（整段搬会误含 feedback）。
- **S4 新增的"悬空引用"守卫当场抓到本片引入的回归**：`_run_qc` 迁出后，
  main.py 中另一域（`POST /api/v1/samples` 入库即质控）仍引用它 → `NameError`；
  守卫立即标出 `POST /api/v1/samples (sample_create): ['_run_qc']`。
  已由 main.py 从 qc_runtime 再导出修复。这类**跨域**悬空引用，靠"只测被改域"的
  行为测试很难覆盖（test_api_guard 只覆盖了其中一条路径）。
- 静默吞异常 168 → **167**（随拆分收敛）。
- **路由拆分 S4**：抽出 `server/ris_runtime.py`（轮询配置/`RIS_POLL_LOCK`/轮询引擎/守护线程），
  8 个 `/api/v1/ris/*` 端点拆至 `server/routes/route_ris.py`；
  main.py 2171 → **1872 行**（自 S1 起累计 2567 → 1872）。
- **修复 S3 引入的回归：`/api/v1/ris/poll-now` 返回 NameError**。
  S3 用"标记区间"搬代码时连带删掉了 `_RIS_POLL_LOCK` 的定义，而该端点仍引用它；
  更值得警惕的是 **514 个测试 + e2e 全绿都没发现**（异常被包装成业务响应，
  且无测试打该端点）。除随 S4 归位该锁外，新增
  `tests/test_no_dangling_endpoint_names.py`：用 `dis` 的 `LOAD_GLOBAL` 检查**每个已注册端点**
  引用的全局名是否都存在（`co_names` 不可用——含属性名与局部 import，误报爆炸），
  并附 poll-now 回归用例。这类"没人调的端点"是既有测试的盲区。
- **修复 `/api/v1/export/data` 100% 不可用**（此前无测试覆盖）：`db.get_session()` 不存在
  （应为 `SessionLocal()`）、`_json` 仅在别处函数内局部导入、`datetime` 缺少局部导入，
  三处独立缺陷叠加。现已修复并冒烟通过。
- RIS 两模块补降级留痕（单份报告入库/入队失败继续处理但留痕；拉取与手动轮询失败服务端留痕），
  静默吞异常总数 169 → **168**。
- **路由拆分 S3**（screen + ocr，唯一需要真重构的一片）：新增
  `server/ocr_runtime.py` 承载共享运行时（推理锁 `OCR_LOCK`、整屏缓存 `SHOT`、
  识别缓存 `OCR_CACHE`、上传上限、`grab_fullscreen()`、`ocr_config_path()`），
  端点拆至 `server/routes/route_screen.py`（4）与 `route_ocr.py`（3）。
  **必须先抽状态再拆端点**：screen 与 ocr 共用同一把推理锁（RapidOCR 单次峰值约 610MB，
  并发会让低配桌面 OOM），若各模块自持一份锁/缓存，会出现锁不互斥、缓存命中率归零。
  main.py 2428 → **2171 行**。
- **修复"Web 与桌面读写不同 OCR 配置文件"**（回归缺陷）：main.py 里另有一份
  `_ocr_config_path()`（`%APPDATA%/MedicalReportQC` 或 `~/.config/MedicalReportQC`），
  与已统一的 `paths.ocr_config_path()`（`user_data_dir()`）**指向不同文件**，
  但其 docstring 却写着"实现桌面/Web 区域配置互通"。现统一委托 paths（单一来源）。
  副作用：原先因写 `~/.config` 被沙箱拒绝而失败的 `TestRegionsValidation`（3 例）现已通过。
- **抓屏/OCR 失败补服务端留痕**：原 `except Exception: return JSONResponse(503, …)`
  只把错误名返给前端，服务端无日志可查（医生只会说"识别不了"）。
  静默吞异常总数 171 → **169**。
- **路由拆分 S2**：`/api/v1/license/*`（5 端点）迁出至 `server/routes/route_license.py`
  （公开端点——登录/激活本身是闸门流程，要求先登录会死锁）。main.py 2568 → 2428 行。
  顺带把 `license_status_get` 里合并扩展信息的 `except Exception: pass` 改为
  `log_quiet`（按"降级必须留痕"规约）。验证：5 端点冒烟全 200（无效激活码返回
  `ok=false` 而非 500）、全量 pytest 绿、e2e 2/2、OpenAPI 78 路径无重复。
- **修正"静默吞异常"门禁自身的口径缺陷**：新增文件规则原先把 `log_quiet` 也判为回归，
  而 `log_quiet` 恰是本仓文档**推荐**的留痕方式（"新增 except 必须留痕：logger.warning
  或 log_quiet"）→ 按规矩写的新文件反而过不了门禁（拆 S2 时实际踩到）。
  现新增文件规则只看**真静默**（pass/直接 return），与回归判定口径一致，并加反证测试。
- **S2 的取舍（记录在案）**：原计划把 `screen` 一起拆，动手前查依赖发现
  `_OCR_LOCK` 被 `/api/v1/screen/ocr` 与 `/api/v1/ocr/base64` **共用**（防并发推理 OOM），
  且 screen 依赖模块级截图/缓存状态 → 只搬 screen 会制造新的跨文件耦合。
  故调整为 **S3 = screen + ocr**，并抽出 `server/ocr_runtime.py` 存放共享锁与状态。
- **路由拆分 S1**（`docs/ROUTES_SPLIT_PLAN.md` 第一片）：把 7 个端点从 2567 行的
  `server/main.py` 拆到 `server/routes/route_queue.py`（4）与 `route_stats.py`（3），
  并先下沉两个跨域助手：`_scope_user_id` → `server/core.py`、
  `require_license_active` → `server/security.py`（否则路由模块只能反向 import main，
  形成循环依赖）。main.py 保留二者的再导出，既有引用不变。
  验证：全量 pytest 绿、引擎基线 100%/100% 持平、Playwright e2e 2/2、
  OpenAPI 78 条路径无重复。
- **顺带修好"重复路由守卫"本身失效**（门禁空跑）：该守卫原只遍历 `app.routes`，
  但本仓 FastAPI 版本 `include_router()` **不摊平子路由**，而是插入 `_IncludedRouter`
  包装对象（无 `.path`/`.routes`，仅 `original_router`）→ 对"拆分到 routes/ 的端点被重复
  注册"这一**正是它要防的场景完全失明**（实测 8 个端点不可见）。
  修复：递归跟进 `routes` 与 `original_router`；新增**反证测试**确保收集器真能看到
  `include_router` 注册的端点；反向验证（故意重复注册 stats/trend）确认守卫会报错。

### 工程 (Engineering)
- **数据库迁移框架**（`server/migrations.py`）：`schema_migrations` 表 + 编号迁移 +
  **迁移前自动快照** + 失败留痕且不写版本号（下次重试）。此前 schema 变更散落在
  `db.init_db()` 里无条件重跑、**没有任何版本记录**，医院就地升级出问题无法判断库升到第几版。
- **静默吞异常治理**：AST 审计脚本 `scripts/audit_silent_except.py` + 基线
  `scripts/silent_except_baseline.json` + "不得新增"守卫（`tests/test_silent_exceptions.py`），
  支持 `# silent-except-ok: <原因>` 显式豁免（刻意不用 `# noqa:` 前缀以免与 ruff 指令冲突）。
  同时**降级日志自动带异常类型/消息/调用位置**（`src/logger.py::_context`）——
  全仓 71 个降级调用点无需改动即获得可诊断信息。
- **前端 e2e 接入 CI**（`.github/workflows/web-e2e.yml`）：真实拉起 uvicorn + Chromium 点界面。
  此前 Playwright 用例与配置都在仓库里却**从未接进任何 workflow**，这正是本轮 P0 白屏
  能一路合进 main 且全绿的原因；`tests/test_repo_hygiene.py` 现在断言"必须有 workflow 跑
  `playwright test`"。
- **统一自检入口** `scripts/check_all.sh`（pytest + ruff + bundle 同步性 + node 解析 +
  引擎基线；可选 `WITH_E2E=1` / `WITH_COVERAGE=1`）。
- **覆盖率门禁**（`scripts/coverage_gate.py`，基线 60.1%，只判"不得下降"）。
  顺带修掉 `.coveragerc` 里**未闭合的排除正则**——它会让 coverage 直接抛 ConfigError，
  所以 `--cov` 从来没有真正跑起来过。
- **测试隔离**：`tests/conftest.py` 新增全局"库绑定快照/还原"自动夹具与 `temp_db()` 工具，
  并归一化 `QC_DB_OVERRIDE` 与当前 engine 的一致性。此前多个测试模块在 import 时各自改绑
  **全局** engine/环境变量 → 一个模块的改绑会连锁搞挂另外 6 个毫不相干的用例
  （实测 `data_layer_unified`/`health_endpoint`/`queue_ingest_paths`）。
- **语义评测集工具链** `tools/semantic_eval.py`（`template`/`validate`/`score`）：
  把"医生反馈 → 待标注模板 → 校验 → 打分"固化，**拒收银标**（`label_source` 必须为 `human`），
  且研究 A（规则效能）与研究 B（LLM 增量 Δrecall/ΔFP）**分开输出、禁止合并**；
  配套 `docs/EVAL_SET_GUIDE.md`。
- **仓库卫生守卫**（`tests/test_repo_hygiene.py`）：版本号单一来源（`version.py` 与 CHANGELOG/
  RELEASE_CHECKLIST 一致、安装包版本由 CI 注入）、审查报告不得堆在根目录（归档 `docs/reviews/`）、
  `check_all.sh` 语法可用、CI 必须有前端 e2e 闸。
- **交付与准入文档**（本阶段新增）：`docs/COMPLIANCE_GAP_ANALYSIS.md`（法规清单/定性判定/
  等保逐控制点现状/PHI 分级与红线/里程碑/待问询清单）、`docs/CLINICAL_VALIDATION_PLAN.md`
  （回顾性双人盲法 + 仲裁、样本量公式与示例、统计方法、图表清单）、
  `docs/DELIVERY_HARDENING.md` + `deploy/`（nginx/WinSW/systemd/备份脚本模板、上线清单、回滚预案）、
  `docs/PRICING_AND_PACKAGING.md`（分层打包/价格带/报价单模板/红线话术）、
  `docs/ROUTES_SPLIT_PLAN.md`（main.py 89 端点分阶段拆分计划）、
  `docs/OPEN_DECISIONS.md`（**需要人/律师/临床拍板的 10 项待决清单**）。

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
- **回归守卫测试**（+67 用例，均为此前完全没有覆盖的路径）：
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
- 全量 **494 passed / 7 skipped**（本轮累计新增 104 个守卫用例零回归）；ruff 致命规则全绿。
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
  > ⚠️ 2026-09-30 更正：**该上传从未实现**（`error_reporter` 只写本地，不联网）；该变量仅被读出显示，`get_stats()` 现返回 `remote_upload: not_implemented`。

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
