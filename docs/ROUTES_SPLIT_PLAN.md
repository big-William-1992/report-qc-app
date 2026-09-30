# server/main.py 拆分计划（89 个端点 → server/routes/）

> 状态：**S1 已完成（2026-09-30）**；S2–S6 待执行。
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
