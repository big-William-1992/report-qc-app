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
- 模块源文件（`modules/*.js`）保留作代码组织参考，不直接加载
- 用 `split_modules.py` 做一次提取后，集成到 bundle 中

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

## 9. Git LFS：模型文件是 LFS 指针，别把指针打进安装包

- `assets/ocr_models/*.onnx`、`adapters/**/*.safetensors` 等由 `.gitattributes`
  标记为 LFS。**没有 git-lfs 的克隆/checkout 拿到的是 131~133 字节的指针文本**。
- CI 里 `actions/checkout` 默认 `lfs: false`，所以需要模型的 job 必须显式拉取：
  ```yaml
  - run: git lfs pull --include="assets/ocr_models/*.onnx"   # 只拉 13MB，别拉 319MB 的 adapters
  ```
- 打包校验必须按**字节数**核对模型（`2432880 / 10690752 / 585532`），
  只判断文件名会把指针文本当成"模型齐全"。
- 提交前留意 `git status`：若 `*.onnx` 显示 modified 而你没动过它，那是 LFS
  过滤器在比对（HEAD 里是真实二进制、工作区过 clean 后是指针），**不要提交**。

## 10. 测试隔离与本地库

- 用临时库跑测试：`QC_DB_OVERRIDE`（samplelib 认）+ `set_test_db()`（ORM 认）。
  只设 `QC_DB_OVERRIDE` 而不调 `set_test_db()` 时，ORM 仍会落到真实开发库。
- `server/main.py` 在 **import 时**就执行 `db.init_db()`，所以「先 import 再
  set_test_db」的顺序会先碰一次真实库；新写 API 测试请照 `tests/test_api_guard.py`
  的写法（模块级先设环境变量，再 import）。
- 沙箱/受限环境下会有两个已知的环境性失败（写 `~/.config`、session 断言），
  与代码无关；判断回归要看「除这 2 个之外是否全绿」。

