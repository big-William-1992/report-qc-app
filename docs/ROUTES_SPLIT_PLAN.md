# server/main.py 拆分计划（89 个端点 → server/routes/）

> 状态：**S1–S5 已完成（2026-09-30）**；S6 待执行。
> 执行按"一次一个切片"推进 —— 半拆状态比不拆更危险。

## 已完成：S1（前置改造 + queue + stats）

| 内容 | 结果 |
|---|---|
| 跨域助手 `_scope_user_id` 下沉 | → `server/core.py`（原在 main.py；queue/stats/samples 多处使用，留在 main 会造成循环依赖） |
| 授权门 `require_license_active` 下沉 | → `server/security.py`（与 `require_emp_local`/`require_admin` 同类；路由模块不再反向 import main） |
| 拆出队列路由 | `server/routes/route_queue.py`（4 端点：GET/POST/DELETE /api/v1/queue[/{qid}]） |
| 拆出统计路由 | `server/routes/route_stats.py`（3 端点：/api/v1/stats/{error-types,trend,report}） |
| main.py | 删除上述 7 个端点的原实现（**必须删**，否则同路径两份处理器） |
| 注册 | `app.include_router` 两个新模块；main.py 保留 `_scope_user_id`/`require_license_active` 的再导出，既有引用不变 |
| main.py 体量 | 2567 → 2468 行 |
| 验证 | 全量 pytest 绿（除 2 项已知沙箱环境失败）、引擎基线 100%/100% 持平、Playwright e2e 2/2、OpenAPI 78 条路径无重复 |

### S1 顺带修好的一个**门禁本身失效**（重要）

`tests/test_single_implementation.py::test_no_duplicate_registered_routes` 原先只遍历
`app.routes`。但本仓 FastAPI 版本里 `include_router()` **不把子路由摊平**，而是插入一个
`_IncludedRouter` 包装对象（无 `.path`/`.methods`/`.routes`，只有 `original_router`）——
于是守卫对"拆分到 `routes/` 的端点被重复注册"这一**正是它要防的场景完全失明**
（实测：push/queue/stats 共 8 个端点不可见，等于门禁空跑）。

- 修复：收集器递归跟进 `routes` 与 `_IncludedRouter.original_router`；
- 新增**反证测试** `test_route_collector_sees_included_routers`：断言收集器确实能看到
  `include_router` 注册的端点（防止将来又变回"空跑"）；
- 反向验证：故意在 `route_stats.py` 里重复注册 `/api/v1/stats/trend` → 守卫**报错**；
  修复前该场景会静默通过。

## 已完成：S2（license 5 端点）

| 内容 | 结果 |
|---|---|
| 拆出授权路由 | `server/routes/route_license.py`：`/api/v1/license/{status,disclaimer,machine-code,activate}`（5 端点，**公开端点**——登录/激活本身是闸门流程，要求先登录会死锁） |
| main.py | 删除原实现；注册新模块；2568 → 2428 行 |
| 顺带改进 | `license_status_get` 合并扩展信息的 `except Exception: pass` 改为 `log_quiet`（按"降级必须留痕"规约；静默会让"授权信息显示不全"无从排查） |
| 验证 | 5 个端点冒烟全 200（无效激活码返回 `ok=false` 而非 500）、全量 pytest 绿、e2e 2/2、OpenAPI 78 路径无重复 |

### S2 的取舍：为什么 `screen` 没跟着做

原计划 S2 是 `license`(5) + `screen`(4)。动手前查依赖发现 **screen 与 ocr 共用一个锁**：
`_OCR_LOCK` 同时保护 `/api/v1/screen/ocr` 与 `/api/v1/ocr/base64`（2026-08-18 加固：
RapidOCR 单次峰值约 610MB，并发推理会让医院低配桌面 OOM）。
此外 screen 还依赖模块级状态 `_SHOT`（原图缓存）/`_OCR_CACHE`/`_SHOT_MAX_W` 与
`_grab_fullscreen()`。

**结论：screen 不是"小且独立"的切片**。强行只搬 screen 会把共享锁与状态留成跨文件引用，
反而制造新的耦合。正确做法是 **screen + ocr 合并为一片**，并抽出一个
`server/ocr_runtime.py` 存放共享锁与截图/缓存状态。→ 已调整为 **S3 = screen + ocr**。

## 已完成：S3（screen 4 + ocr 2，唯一需要真重构的一片）

| 内容 | 结果 |
|---|---|
| 抽出共享运行时 | 新增 `server/ocr_runtime.py`：`OCR_LOCK`（推理串行锁）、`SHOT`（整屏原图缓存）、`OCR_CACHE`、`OCR_MAX_BYTES`、`grab_fullscreen()`、`ocr_config_path()`、配置读写助手 |
| 拆出屏幕路由 | `server/routes/route_screen.py`：capture / ocr / regions GET+PUT（4 端点） |
| 拆出 OCR 路由 | `server/routes/route_ocr.py`：`/api/v1/ocr`、`/api/v1/ocr/base64`、`/api/v1/ocr/meta`（3 端点） |
| main.py | 删除这 7 个端点 + 共享状态定义 + `_grab_fullscreen` + `_ocr_config_path`；2428 → **2171 行** |
| 验证 | 全量 pytest 514 passed（仅 1 项已知沙箱环境失败）、e2e 2/2、OpenAPI 78 路径无重复、screen/ocr 端点冒烟通过 |

### S3 顺手修掉的两个真问题

1. **Web 与桌面读写的是两个不同的 OCR 配置文件**（回归缺陷）
   `main.py` 里另有一份 `_ocr_config_path()`，用 `%APPDATA%/MedicalReportQC` 或
   `~/.config/MedicalReportQC`；而本轮早先把 `paths.ocr_config_path()` 统一到了
   `user_data_dir()` → **两者指向不同文件**，但那份实现的 docstring 却写着
   "实现桌面/Web 区域配置互通"（即注释与事实不符）。现统一委托 `paths.ocr_config_path()`
   （单一来源）。副作用：原本因"写 `~/.config` 被沙箱拒绝"而失败的
   `test_api_guard::TestRegionsValidation`（3 例）现在**通过**了。
2. **抓屏/识别失败没有服务端日志**：`except Exception: return JSONResponse(503, ...)`
   只把错误名返给前端；医生只会说"点了没反应/识别不了"，服务端无痕迹可查
   （macOS 屏幕录制权限、远程桌面黑屏都在此抛错）。已补 `log_quiet` 留痕。
   净效果：静默吞异常总数 171 → **169**（迁移本身让它下降了）。

## 已完成：S4（ris 8 端点 + 轮询运行时）

| 内容 | 结果 |
|---|---|
| 抽出轮询运行时 | `server/ris_runtime.py`：轮询配置持久化（`ris_poll.json`）、`RIS_POLL_LOCK`、`ris_poll_once/locked/loop`、`start_poll_thread()` |
| 拆出 RIS 路由 | `server/routes/route_ris.py`：config/drivers/test-connection/fetch-reports/poll-status/poll-config/poll-now（8 端点） |
| main.py | 1868 → 删除 RIS 段后 **1872**（S4 起点 2171） |
| 验证 | 516 passed（仅 1 项已知沙箱失败）、e2e 2/2、OpenAPI 78 路径无重复、RIS 端点冒烟通过、引擎基线持平 |

### ⚠️ S4 最重要的产出：发现并修好「S3 把 `/ris/poll-now` 弄坏了」

S3 用"文本标记区间"搬代码时，**连带删掉了 `_RIS_POLL_LOCK` 的定义**，而
`/api/v1/ris/poll-now` 仍引用它 → 调用返回
`{'ok': False, 'code': 'POLL_ERR', 'data': {'error': 'NameError'}}`。

**而 514 个测试 + 前端 e2e 全绿都没发现**，因为：
① 该端点把自己的异常包装成业务响应（不是 500）；② 当时没有任何测试打到它。

修复与加固：
- `_RIS_POLL_LOCK` 随 S4 迁入 `ris_runtime.RIS_POLL_LOCK`（唯一一份，路由模块从中导入）；
- 新增 `tests/test_no_dangling_endpoint_names.py`：
  用 `dis` 取 `LOAD_GLOBAL` 的 opcode（**不能用 `co_names`**，它含属性名与函数内局部 import，
  会产生海量误报——首版即因此报废），检查每个已注册端点引用的全局名是否存在，
  并附 `/ris/poll-now` 的回归用例。这类"没人调的端点"正是此前测试的盲区。

### S4 顺带修好的另一个真缺陷：`/api/v1/export/data` 100% 不可用

该端点（商业化"数据可移植性"功能）有三处独立缺陷，且无任何测试覆盖：
1. `db.get_session()` —— `server/db.py` 里**没有这个函数**（只有 `SessionLocal` 与 `get_db` 生成器）→ AttributeError；
2. `_json.dumps(...)` —— `_json` 只在**另一个函数体内**被局部导入 → NameError；
3. `datetime.datetime.now()` / `datetime.now()` 混用 —— 该函数缺少局部 `import datetime` → NameError。

现已修复并冒烟验证（返回合法 JSON）。

> 澄清（避免夸大）：`main.py` 中另有多处 `datetime.now()`，但它们**所在函数都有局部
> `import datetime`**（如 `sample_dashboard`、`orders_*`），经逐个核查**并未失效**；
> 只有 `data_export` 漏了。此前一度误判为"5 个端点坏了"，已核实更正。

## 已完成：S5（qc 16 端点 + 质控运行时）

| 内容 | 结果 |
|---|---|
| 抽出质控运行时 | `server/qc_runtime.py`：`_get_engine()`（进程级 RuleEngine 单例）、`_reload_engine_rules()`、`_run_qc()`、`_qc_rate_ok()`（按 IP 限流） |
| 拆出质控路由 | `server/routes/route_qc.py`：16 端点（check/batch/llm/full/export-report + rules 读写 + rules/config + 错字表 5 个 + scan-reports） |
| main.py | 1872 → **1519 行**（自 S1 起累计 2567 → 1519） |
| 验证 | 516 passed（仅 1 项已知沙箱失败）、e2e 2/2、OpenAPI 78 路径无重复、qc 端点冒烟通过、引擎基线持平 |

### ⭐ 新增的"悬空引用"守卫**当场抓到了本片引入的回归**

把 `_run_qc` 迁到 `qc_runtime` 后，**`main.py` 里另一个域**（`POST /api/v1/samples` 的
"入库即质控"）仍在引用它 → 调用即 `NameError`。

`tests/test_no_dangling_endpoint_names.py`（S4 新增）**立刻把它标红**：
```
- POST /api/v1/samples (sample_create): ['_run_qc']
```
这正是该守卫存在的意义：跨域的悬空引用，靠"只测被改域"的行为测试很难发现
（本例中 `test_api_guard` 只覆盖了其中一条路径）。修复：main.py 从 `qc_runtime` 再导出
这些符号，供仍在内联的其它域使用。

### qc 拆分的两条注意

1. **引擎单例必须只有一份**：多份单例各自持有 `rules_config` 副本 → "改了规则只有部分端点
   生效"。故单例随 `qc_runtime` 走，`route_qc` 只导入。
2. **qc 端点与 feedback 端点曾交错排列**：285–468 区间里夹着 `/api/v1/feedback*`（3 个），
   拆分时必须按端点边界切，不能按"连续区间"整段搬（否则会把 feedback 一起搬走）。
   本次即因整段搬导致 `route_qc` 误含 feedback 端点，已按精确边界重做。
   → **教训：搬代码前先打印区间内的 `@app.` 清单核对**。

### S1 的经验（给后续切片）

1. **拆之前先看依赖方向**：跨域助手必须先下沉，否则路由模块只能 `import main`（循环）。
2. **拆完立刻用 OpenAPI 核对端点账**（`app.openapi()["paths"]`），别用 `app.routes`
   目测——本仓版本下后者看不到子路由（这一坑差点让我误判"拆分失败"）。
3. 每片都跑：全量 pytest + `WITH_E2E=1 bash scripts/check_all.sh` + 引擎基线。

---

## 1. 为什么要拆（而不是"重构好看"）

| 现象 | 影响 |
|---|---|
| `server/main.py` **2567 行 / 89 个端点** | 任何改动都要在巨型文件里定位；并行开发者/agent 极易互相踩（本审计 3 起事故均与此相关） |
| 端点按业务混排（qc/queue/samples/ris/license/admin…） | 无法按域设 CODEOWNERS、无法按域写测试边界 |
| 依赖靠文件内全局符号（`_envelope`/`_scope_user_id`/`samplelib`…） | 拆分必须先把"跨域助手"下沉到共享层，否则只能互相 import main（循环依赖） |

**已有的护栏（拆分不必从零开始）**：
- `tests/test_single_implementation.py`：每个 `server/routes/*.py` 必须被 `main.py` `include_router`；
  同"路径+方法"不得重复注册（历史事故：10 个路由模块从未注册、49 处重复端点，后注册的永久失效）。
- `tests/test_queue_ingest_paths.py` 等按 HTTP 入口验证行为，拆路由时**不需要改测试**（这正是拆分安全的前提）。
- `scripts/check_all.sh` 一条命令跑全部门禁。

---

## 2. 端点现状盘点（按前缀）

| 前缀 | 数量 | 建议归属模块 |
|---|---|---|
| `/api/v1/qc/*` | 16 | `routes/route_qc.py`（含队列操作，或拆 `route_queue.py`） |
| `/api/v1/admin/*` | 15 | `routes/route_admin.py`（订单/授权/管理，建议再拆 orders/license_admin） |
| `/api/v1/ris/*` | 8 | `routes/route_ris.py` |
| `/api/v1/samples/*` | 7 | `routes/route_samples.py` |
| `/api/v1/license/*` | 5 | `routes/route_license.py` |
| `/api/v1/accounts/*` | 5 | `routes/route_accounts.py` |
| `/api/v1/screen/*` | 4 | `routes/route_screen.py` |
| `/api/v1/stats/*` | 3 | `routes/route_stats.py` |
| `/api/v1/queue*` | 3 | `routes/route_queue.py` |
| `/api/v1/settings*`、`/api/v1/ocr*` | 4 | 归 `route_settings.py` / `route_ocr.py` |
| 其余（health/push/feedback/audit/report…） | 19 | 按域归入上述模块 |

---

## 3. 前置改造（必须先做，否则每个切片都要互相 import）

1. **把跨域助手下沉到 `server/core.py`**（当前在 main.py 内）：
   `_scope_user_id`、`_validate_period` 之类的纯函数、以及 `_envelope`（已在 core）。
2. **统一依赖来源**：路由模块一律 `from server.security import require_emp_local, require_admin`
   （禁止再出现"第二套鉴权"；`tests/test_single_implementation.py` 已断言 deps/security 同一对象）。
3. **`server/app_services.py`（可选）**：把 `_run_qc`、`samplelib` 等"业务动作"收口，
   路由只做参数校验 + 调用 + 包装响应。

---

## 4. 切片顺序（由小到大，每片独立可回滚）

| 切片 | 内容 | 预估 | 验收 |
|---|---|---|---|
| **S1** | 前置改造（§3）+ 拆 `stats`(3) + `queue`(3) | 半天 | 全量 pytest 绿；`/queue`、`/stats/*` 行为不变（现有测试覆盖） |
| S2 | `license`(5) + `screen`(4) | 半天 | 同上 + 授权相关测试绿 |
| S3 | `samples`(7) + `settings`/`ocr`(4) | 1 天 | `test_sample_user.py`、`test_export_import.py` 绿 |
| S4 | `ris`(8) | 1 天 | RIS 配置/轮询相关测试绿 |
| S5 | `qc`(16) | 1–2 天 | 最大且最核心，最后做；需人工过一遍关键路径 |
| S6 | `admin`(15) + `accounts`(5) | 1–2 天 | 管理端测试绿；注意订单/授权状态机 |

**每片的固定动作**：`git mv` 语义（复制代码 → 删除 main.py 中原文 → 在 routes 模块 `router = APIRouter(tags=[...])` →
main 中 `include_router`）→ 跑 `bash scripts/check_all.sh` → 跑 `WITH_E2E=1` → 提交一个 PR。

---

## 5. 拆分时的三个"必须"（历史教训）

1. **必须删掉 main.py 里的原实现**：否则同一路径出现两份处理器（FastAPI 只认先注册的，
   第二份是静默死代码 —— 本仓真实发生过，49 处重复）。
2. **必须同步更新 `ARCHITECTURE.md` 的模块表**：否则下一个人按旧架构图找代码。
3. **必须保持经典脚本前端不受影响**：拆路由只动后端；`web/static` 与 `app.bundle.js` 不参与。

---

## 6. 不做什么（避免过度设计）

- 不引入依赖注入框架、不改 FastAPI 应用结构、不引入蓝图/插件系统。
- 不为"分层好看"把简单的直读端点包成 service 层。
- 不追求一次拆完：**每个 PR 只动一个域**，保持"随时可回滚"。
