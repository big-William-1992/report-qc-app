# 星衍放射质控软件 · Agent 工作备忘

## 1. 前端加载：永远不要用 ES 模块（type="module"）

**不要犯的错**：把 app.js 改成 ES 模块入口 `type="module"` 并让 index.html 引用。

**原因**：
- 桌面应用有两条加载路径：
  - `file://` 协议（用户双击 HTML / 本地预览）— ES 模块被浏览器 CORS 拦截（`Cross-Origin Request Blocked`）
  - `http://` 协议（FastAPI 服务 + pywebview）— ES 模块正常
- 同一条 `<script>` 无法同时兼容两种协议。

**正确做法**：
- 所有前端 JS 保持 **经典脚本**（无 `import`/`export`），合并为单个 `app.bundle.js`
- 模块源文件（`modules/*.js`）是**源**，bundle 是**产物**；改完模块必须重新打包：
  ```bash
  python3 tools/build_bundle.py          # 生成 app.bundle.js
  python3 tools/build_bundle.py --check  # 自检（tests/test_frontend_bundle.py 会跑）
  ```
- ⚠️ **2026-09-30 事故**：v4.3.6 重新生成 bundle 时忘了剥掉顶层 `import/export`，
  浏览器直接 `SyntaxError: Unexpected token 'export'` → **整个 SPA 的 JS 一行都不执行**
  （登录闸门、按钮、渲染全废），而 HTTP 仍 200、pytest 全绿，无人察觉。
  `tests/test_frontend_bundle.py` 现在守着：bundle 无顶层 import/export、
  与 modules/ 同步、`node --check` 可解析。

## 2. 静态资源路径：绝对路径 vs 相对路径

**不要犯的错**：用 `/static/css/style.css` 这种绝对路径。

**原因**：
- `file://` 下，`/static/...` 指向 `file:///static/...`（文件系统根目录），不存在
- `http://` 下，FastAPI 通过 `app.mount("/static", ...)` 正确映射

**正确做法**：使用**相对路径**（相对于 `index.html` 所在目录）：
```html
<link rel="stylesheet" href="css/style.css?v=..." />
<script src="js/app.bundle.js?v=..."></script>
```
然后在服务端 `spa_fallback` 函数中，对根级路径（`/css/...`、`/js/...`）检查 `_STATIC_DIR` 下是否存在真实文件，存在则直接返回。

## 3. Git 合并冲突常丢的类

- `server/schemas.py` 中的 `RegisterReq`、`ChangePwdReq`
- 合并后必须检查这些 schema 类是否存在，否则登录/注册/改密 422 错误

## 4. 模块合并注意事项

将 ES 模块（各自独立作用域）合并为单一经典脚本时：
- 检查顶层 `const`/`let` 重复声明（SyntaxError）
- 检查 `function` 重复声明（后覆盖前，一般无害）
- `Object.assign(window, ...)` 保留，每个模块把自己的函数挂到 window

## 5. 密码策略变更后要同步更新测试

`src/accounts.py` 密码长度要求改动后（6→8 位），以下测试断言需要同步：
- `tests/test_accounts.py::TestAccounts::test_password_min_length`
- `tests/test_auth_security.py::TestPasswordPolicy::test_min_password_length`

## 6. GitHub 推送

远程仓库使用 SSH 协议（`git@github.com:big-William-1992/report-qc-app.git`），如果 SSH 超时需要：
- 确认 VPN 连接
- `ssh -T git@github.com` 测试连通性
- 重试 `git push github main`

## 7. 单一实现：严禁「写了但从不生效」的第二套实现

本仓因多 Agent 并行开发出过两次同类事故，都是**代码在跑的是 A，改动改在 B**：

| 事故 | 现象 | 现状 |
|------|------|------|
| 第二套引擎 | `src/engine.py` 门面 + `engine_types/helpers/config/ner/meta.py` + `src/rules_*.py`。Python「常规包优先于同名模块」→ `import engine` 永远命中 `src/engine/` 包，门面及其规则模块**从不执行**，却仍被 patch | 2026-09-30 已删除 |
| 第二套路由 | `server/routes/` 下 10 个模块从未被 `include_router`（main.py 只注册了 route_push），且内容比 main.py 陈旧；实测 49 处「路径+方法」重复注册，后注册的永不生效 | 2026-09-30 已删除 |

**规矩**：
- 引擎只有一个实现：`src/engine/` 包。**不要**再新建 `src/engine.py`。
- `server/routes/` 下每个模块都必须被 `main.py` `include_router`；要拆路由就一次拆完并删掉 main.py 里的同名端点。
- 别在同一路径上重复注册端点（FastAPI 只认第一个，第二份是静默死代码）。
- 这些约定由 `tests/test_single_implementation.py` 守着——它失败说明又长出来了。

## 8. 数据层：一个库、一个 schema 真相源

- **schema 唯一真相源 = `server/models.py`**。`src/samplelib.py` 必须走
  `server/db.SessionLocal` + `models.Sample`，**不要**再写裸 `sqlite3` 连接或
  `CREATE TABLE`（唯一例外：读取外部遗留库文件，如旧 `samples.db`）。
- 数据库文件位置只有一处定义：`server/db.py` 的 `_DEFAULT_DB_FILE`
  （源码态 `<root>/assets/qc.db`，打包态用户数据目录）。
  `samplelib.db_path()` 直接取 ORM engine 的文件路径——**不要**在别处再推算一遍
  （曾因 `db.py` 少写一段 `assets/` 导致源码态账号库与样本库分裂成两个文件）。
- 新增或迁移表：先改 `models.py`，再靠 `Base.metadata.create_all` 落地；
  旧库一次性升级写成幂等的 ALTER/重建，并在 `db.init_db()` 里挂上。
- 备份/恢复必须共用 `src/backup.py::_resolve_known()` 解析路径——
  备份扫哪里、恢复就得写回哪里（曾出现备份扫 `<assets>/`、恢复写 `<assets>/`，
  而打包态真实位置在用户目录 → 「恢复成功」却不生效）。
- 改动数据层后至少跑：`tests/test_data_layer_unified.py`、
  `tests/test_sample_user.py`、`tests/test_export_import.py`。
- **app 数据目录只有一个来源**：`paths.user_data_dir()`（`core._appdata_dir()` 与
  `deps._appdata_dir()` 都转调它）。曾出现 core 用 `~/.medical_report_qc`、deps 用
  用户数据目录 → 同一个 `qc_queue.json`/`web_settings.json` 写在两个地方、读写互相看不见。
  注意角色区分：**导出产物与写权限探测**用「数据库所在目录」（`main._APPDATA_DIR`），
  不在这里合并。
- **队列只有一个写入口**：`core.queue_add_text()`（main / deps / route_push 都转调）。
  曾出现 deps 版把队列写进 `qc_queue.json`，而队列端点读 qc.db 的 QueueItem 表 →
  **PACS 推送入队的报告在界面队列里永远看不到**。
- **刻意保留的裸 sqlite3（不要"顺手统一"掉）**：
  - `src/badcase_store.py` —— 它用**独立的 `feedback.db`**（不与 qc.db 同库，
    便于诊断包单独导出/精调管线消费），不是"同库双轨"；
  - `src/backup.py` —— 用 `sqlite3` 做 `VACUUM INTO` 与完整性校验，属**文件级**
    操作，天然不属于 ORM 语义。
  这两处的 sqlite3 是有意为之；`src/samplelib.py` 的裸 sqlite3 已并轨 ORM。

## 9. Git LFS：只给训练权重用，OCR 模型（13MB）现在是普通文件

- **`assets/ocr_models/*.onnx` 不进 LFS**（2026-09-30 起）：约 13MB，作为普通
  二进制直接入库。原因见 `.gitattributes` 注释——曾标为 LFS 后，仓库里变成
  131~133 字节的指针文本，任何没装 git-lfs 的克隆/CI/Docker 构建都会把指针
  打进 exe，装机后「屏幕区域 OCR」静默失效。**不要**再给 `*.onnx` 加 LFS 规则。
- `adapters/**/*.safetensors`、`saves/**/*.safetensors`（几百 MB）**仍走 LFS**，
  那才是 LFS 的正确用法；没有 git-lfs 的克隆拿到的是指针，属预期。
- 打包校验必须按**字节数**核对 OCR 模型（`2432880 / 10690752 / 585532`），
  只判断文件名会把指针文本当成"模型齐全"（校验已是阻断式）。
- 若在 CI 里额外 `git lfs pull`，请限定 `--include`，别把 319MB 的 adapters
  全拉下来（会吃掉 GitHub LFS 免费配额 1GB/月）。

## 10. 测试隔离与本地库

- 用临时库跑测试：`QC_DB_OVERRIDE`（samplelib 认）+ `set_test_db()`（ORM 认）。
  只设 `QC_DB_OVERRIDE` 而不调 `set_test_db()` 时，ORM 仍会落到真实开发库。
- `server/main.py` 在 **import 时**就执行 `db.init_db()`，所以「先 import 再
  set_test_db」的顺序会先碰一次真实库；新写 API 测试请照 `tests/test_api_guard.py`
  的写法（模块级先设环境变量，再 import）。
- 沙箱/受限环境下有**一个**已知环境性失败：`tests/test_accounts.py::TestAccounts::test_session`
  （session 文件写在 `user_data_dir()`，沙箱禁止写工作区外 → 断言失败），与代码无关。
  > 2026-09-30 更新：原先还有 `test_api_guard::TestRegionsValidation`（写 `~/.config`）；
  拆 S3 时把 OCR 配置路径统一到 `paths.ocr_config_path()`（→ `user_data_dir()`）后该用例已通过。


## 11. 隐私与授权：几处有意为之的约束

- **错误/信息日志（`src/error_reporter.py`）**：context 只接受白名单键（未知键丢弃），
  字符串值统一去标识擦洗。擦洗是"降低风险"不是"保证无 PHI"——**不要把报告正文塞进
  context**。`QC_ERROR_REPORT_URL` **未实现**（本模块不联网），`get_stats()` 会标明
  `remote_upload: not_implemented`。
- **诊断包（`src/log_utils.py::export_diagnostic_bundle`）**：默认**不含** `feedback.db`
  （里面有完整报告正文）；要打包须显式 `--include-patient-data`，包内会附
  `README_诊断包.txt` 说明所含内容与隐私提示。
- **试用起点（`src/license_utils.py`）**：除 `license.dat` 外还冗余写在同目录
  `.license_trial_anchor`（+ Windows 注册表 + 用户数据目录），取**最早**的有效日期 ——
  这样 `rm license.dat` 不能重置试用。新增锚点位置请沿用 `_trial_sign/_trial_verify` 签名。
- **鉴权/令牌只有 `server/security.py` 一份**：`server/deps.py` 只负责再导出
  （`tests/test_single_implementation.py` 断言两边是同一个对象）。默认密钥必须是
  `_load_or_create_secret()` 生成的随机值，**禁止**再写 `change-me-in-prod` 这类可猜默认值。

## 12. 数据库变更：走迁移框架，不要再往 init_db 里堆临时函数

- 新 schema 变更一律在 `server/migrations.py` 加一条 `@migration("NNNN_slug", "说明")`，
  用 `add_column_if_missing()` 等幂等助手；**不要**再写"每次启动无条件跑"的临时函数。
- 迁移前会自动快照（`<db>.premigrate-<版本>-<时间>.bak`，保留 3 份），失败**不写版本号**、
  下次启动重试；**已发布的迁移不要修改**（别人已经跑过）。
- ⚠️ **SQLAlchemy 2.0 不许 `conn.execute("字符串")`**：必须 `exec_driver_sql(...)` 或 `text(...)`。
  本仓曾因此静默失效两个迁移（`queue.report_hash` 从未补列、`settings` 复合唯一从未建立）——
  因为异常被函数自己的 `except` 吞掉，表现只是"功能不生效"。
- ⚠️ **SQLite 不能 DROP 由 UNIQUE 约束隐式创建的 `sqlite_autoindex_*`**（迁移 `0003` 的教训），
  要改约束只能**整表重建**（改名旧表 → 按模型定义建新表 → 列交集 INSERT OR IGNORE → 删旧表），
  且必须放在同一个事务里。
- 改动数据层后至少跑：`tests/test_migrations.py`、`tests/test_backup_upgrade_drill.py`
  （跨版本：旧库备份 → 新版恢复 → 启动自动升级）、`tests/test_data_layer_unified.py`。

## 13. 测试里改库绑定：必须"用完还原"

仓库的库绑定是**全局单例**（`server/db.py` 的 `engine`/`SessionLocal`），
`set_test_db()` 还会写 `QC_DB_OVERRIDE`/`DATABASE_URL`/`QC_APPDATA`。历史上一个测试模块
在 import 或用例里改绑而不还原，会连锁搞挂另外 6 个毫不相干的用例。

- **不要在 import 时改绑**（pytest 会先把所有模块 import 完）。
- 用具 `conftest.temp_db(...)`（上下文管理器，自动还原对象 + 环境变量），
  或在 fixture 里用 `snapshot_binding()` / `restore_binding()`。
- `tests/conftest.py` 已加**每个用例自动快照/还原**的全局夹具 + `env↔engine` 归一化，
  但新测试仍请遵守上面两条，别依赖它兜底。
- 改共享模块的属性（如 `backup._resolve_known = ...`）必须用 `monkeypatch.setattr` **并恢复** ——
  直接赋值会永久污染其它用例（实测让 4 个用例连带失败）。

## 14. 门禁与"静默失效"

- 提交前跑 `bash scripts/check_all.sh`（可选 `WITH_E2E=1` / `WITH_COVERAGE=1`）。
- 新增 `except` 分支必须留痕：`logger.warning(...)` 或 `log_quiet(__name__)`
  （后者已自动带异常类型/消息/调用位置）。确实无害时写 `# silent-except-ok: <原因>`；
  `scripts/audit_silent_except.py` 会拦住"净新增静默吞异常"。
- 覆盖率基线 60.1%，只允许上升（`scripts/coverage_gate.py`）。
- 前端改动：改动 `modules/*.js` 后必须 `python3 tools/build_bundle.py`，否则
  `tests/test_frontend_bundle.py` 与 CI 的 e2e 都会红。

## 15. 许可：非商业免费 + 商业需授权（不要动）

- 本仓许可是 **PolyForm Noncommercial License 1.0.0**（`LICENSE`），**不是 MIT**（MIT 仅适用于
  变更点之前的版本，文本存档在 `LICENSE-MIT`）。**不要**把它改回 MIT / Apache 等宽松许可，
  也**不要**删除 `NOTICE.md`、`LICENSE-MIT`、`LICENSE-HISTORY.md`。
- **随副本传递是许可的硬性义务**：`LICENSE` 的 Notices 章节要求任何分发者一并提供许可条款
  （或其 URL，见 `NOTICE.md` 顶部）——所以 CI 打包必须把 `LICENSE`、`NOTICE.md`、
  `docs/DISCLAIMER.md` 复制进 `dist/报告质控软件/`（绿色版与安装包都从这里取文件）。
- 涉及"免费/开源/可再分发"的对外文案改动前，先读 `LICENSE-HISTORY.md` 与
  `docs/LICENSE_OPTIONS.md`；非商业免费 ≠ 可自由商用。
- 商业许可（订购协议）的正式条款仍需律师定稿（见 `docs/OPEN_DECISIONS.md` D2 备注）。
