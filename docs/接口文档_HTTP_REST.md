# 星衍放射质控软件 — HTTP/REST 接口规范

> 版本：v3.0 ｜ 最后更新：2026-08-04（与 `origin/main` `cddc942` 对齐）
>
> **实时文档**：本服务的 FastAPI 自带 Swagger UI，启动后访问 `/docs` 即是最新接口（由代码生成，永远与实现一致）。本文档是给对接方/前端的离线参考。

---

## 1. 设计原则

- **单服务同源**：FastAPI 同时提供 `/api/v1/*` REST 与 `/static` SPA，桌面端 WebView 壳与浏览器共用同一后端。
- **统一包络**：所有接口返回 `{ ok, code, data, message }`，前端以 `ok` 判定成功、`data` 取业务数据。
- **本地优先鉴权**：来自 `127.0.0.1/::1` 的调用（桌面 WebView、同源 localhost 浏览器）自动放行；非本地调用需 `Authorization: Bearer <token>` 或 `X-Emp-Id` 头（见 §4）。
- **能力可选**：OCR 依赖 RapidOCR、RIS 依赖对应数据库驱动；缺失时端点返回 `ok:false` + 明确安装提示，不崩溃。

---

## 2. 通用约定

### 2.1 基础路径
```
BASE = http(s)://<host>:<port>/api/v1
```
默认端口：桌面端 `8500`，直接起 `server.main:app` 时 `8000`。

### 2.2 请求头
| 头 | 说明 |
|----|------|
| `Content-Type: application/json` | JSON 请求体 |
| `Authorization: Bearer <token>` | 非本地调用的鉴权令牌 |
| `X-Emp-Id: <emp_id>` | 内网/本地替代鉴权（工号直传） |

### 2.3 统一响应包络
```json
{ "ok": true, "code": "OK", "data": { }, "message": "" }
```
失败示例：
```json
{ "ok": false, "code": "OCR_UNAVAILABLE", "data": null, "message": "缺少驱动 rapidocr..." }
```

### 2.4 错误码表
| code | 含义 |
|------|------|
| `OK` | 成功 |
| `SCREEN_UNAVAILABLE` | 屏幕采集失败（未授权屏幕录制 / 全黑） |
| `OCR_UNAVAILABLE` | OCR 依赖缺失或推理失败 |
| `DB_DRIVER_MISSING` | RIS 数据库驱动未安装 |
| `INVALID_CREDENTIALS` | 账号/密码错误 |
| `FORBIDDEN` | 权限不足 |
| `NOT_FOUND` | 资源不存在 |

### 2.5 数据模型（JSON）
- **Finding**：`{ rule_id, error_type, severity(high|medium|low), message, snippet, span:[s,e], suggestion }`
- **Score**：引擎输出中文维度键，前端按 `_SCORE_EN` 映射英文键
  `准确性→accuracy / 完整性→completeness / 规范性→normalization / 及时性→timeliness`
- **Meta**：`{ patient, gender, age, modality, applied_site, ... }`（字符串字典）
- **严重度聚合**：看板 `by_severity` 用 `critical/warning/info`，由 `high→critical / medium→warning / low→info` 映射得到。

---

## 3. 接口清单

### 3.1 质控计算（无状态）
#### `POST /qc/check`
对单份报告跑质控引擎。
```jsonc
// 请求
{ "report": "检查所见：...。诊断印象：...。",
  "meta": { "patient":"张三","gender":"男","age":"45","modality":"CT" },
  "auto_fix": false }
// 响应 data
{ "findings": [ /* Finding[] */ ],
  "score": { "准确性":92,"完整性":88,"规范性":95,"及时性":100 },
  "passed": true, "summary": "..." }
```

#### `POST /qc/batch`
批量质控，请求 `{ "items": [ {report, meta, auto_fix} ] }`，返回逐项结果数组。

### 3.2 规则
#### `GET /qc/rules`
返回内置规则目录（R1–R11：性别矛盾 / 左右混淆 / 评分缺失 / 单位错误 / 描述-结论矛盾 / 登记部位不符 / 错别字 / 互斥冲突 / 忽略词 / 模板规范 / …），含分类与默认严重度。

#### `PUT /qc/rules`
覆盖保存内置规则开关：`{ "rules": [ { "id":"R8", "enabled": true }, ... ] }`。

#### `GET /qc/rules/config`
返回**可编辑词表**（前端「规则维护」页加载）：
```jsonc
{ "typos": { "肋膜":"胸膜" },          // R8 错别字表（错词→正词）
  "conflicts": [ {"a":"良性","b":"恶性","scope":"正文","severity":"medium","note":"..."} ], // R9 互斥对
  "ignores": ["请结合临床"],           // 白名单（命中不报）
  "template": { "required_sections":["findings","impression"], "require_followup": true, "severity":"low", "note":"..." } } // R10 模板
```

#### `PUT /qc/rules/config`
保存词表。**注意 `conflicts` 必须是 dict 列表 `[{a,b,scope,severity}]`，不能是 `[[a,b]]`（后者会让 qc/check 崩溃）**。
```jsonc
{ "typos": { "建义":"建议" },
  "conflicts": [ {"a":"未见明显异常","b":"结节"} ],
  "ignores": ["请结合临床"],
  "template": { "require_followup": true } }
```

### 3.3 OCR
#### `POST /ocr`（multipart 文件上传）
单图 OCR，表单字段 `file` → `{ "text": "..." }`。

#### `POST /ocr/base64`
```jsonc
{ "image_base64": "<PNG/JPG base64>" }  →  { "text": "..." }
```

#### `POST /screen/capture`
截取全屏（PIL.ImageGrab），原图缓存服务端，仅返回缩略图：
```jsonc
→ { "image_base64":"<缩略图>", "width":3840, "height":2160,
     "thumb_width":1600, "thumb_height":900, "ts": 169... }
```
> macOS 需「系统设置 → 隐私与安全性 → 屏幕录制」授权本应用；未授权返回 `SCREEN_UNAVAILABLE`（全黑检测）。

#### `POST /screen/ocr`
对缓存整屏按**比例框**高精度裁剪并 OCR（三区：basic=病人基础信息 / findings=影像描述 / impression=影像诊断）：
```jsonc
// 请求：regions 坐标为 0~1 比例
{ "regions": {
    "basic":      { "x":0.03, "y":0.02, "w":0.94, "h":0.26 },
    "findings":   { "x":0.03, "y":0.30, "w":0.94, "h":0.32 },
    "impression": { "x":0.03, "y":0.64, "w":0.94, "h":0.33 } },
  "refresh": false }   // true=识别前重新抓屏（画面已变动）
// 响应 data
{ "texts": { "basic":"...", "findings":"...", "impression":"..." },
  "meta": { "patient":"张三","gender":"男","age":"45","modality":"CT" },
  "errors": {} }
```

#### `GET /screen/regions` ｜ `PUT /screen/regions`
读取/保存 SPA 侧记住的框位（持久化到 `ocr_config.json`，下次进模态自动复原）。GET 返回 `{ "web_regions": { basic/findings/impression: {x,y,w,h} } }`；PUT 接收同样的 regions 对象。

### 3.4 待质控队列（与桌面端 `qc_queue.json` 互通，MD5 去重）
#### `GET /queue` → `{ items:[{id,hash,patient,site,text,source,ts,meta}], count }`
#### `POST /queue` → `{ id, duplicated, count }`
```jsonc
{ "text":"报告正文", "patient":"张三", "site":"胸部", "source":"手动", "meta":{} }
```
#### `DELETE /queue` → 清空，返回 `{ count:0 }`
#### `DELETE /queue/{qid}` → 移出单条，返回 `{ count }`

### 3.5 样本库（持久化 + 统计）
#### `POST /samples`（入库即质控）
```jsonc
{ "report":"...", "meta":{...}, "findings":[...], "score":{...},
  "anonymize": false, "user_id":"demo01" }  →  { "id": 1 }
```
#### `GET /samples?page=1&page_size=20&q=张三` → `{ items, total, page }`
#### `GET /samples/{sid}` → 样本详情（报告全文 + 发现 + 评分）
#### `DELETE /samples/{sid}` → `{ ok:true }`
#### `POST /samples/export` `{ path, fmt:"csv" }` → 导出文件
#### `POST /samples/import` `{ path }` → `{ inserted, skipped }`（服务端路径导入）
#### `POST /samples/import/upload`（multipart）`file` → `{ inserted, skipped }`（浏览器上传 CSV/JSON 导入）
#### `GET /samples/stats/dashboard` →
```jsonc
{ "total":120, "today":5, "this_week":30,
  "by_modality": { "CT":80, "DR":40 },
  "by_severity": { "critical":2, "warning":10, "info":108 } }
```

### 3.6 统计
#### `GET /stats/error-types` → `{ "模板缺失":12, "错别字":8, ... }`（错误类型分布）
#### `GET /stats/trend` → `[ { "date":"2026-08-01", "count":5, "avg_score":91 }, ... ]`（近 30 天趋势）

### 3.7 RIS 直连（对接医院 PACS/RIS 数据库）
#### `GET /ris/drivers`
返回各数据库驱动可用性（前端据此提示安装什么）：
```jsonc
[ { "type":"sqlserver", "available":false, "module":"pyodbc",
    "message":"缺少驱动 pyodbc，请执行：pip install pyodbc（并安装 ODBC Driver 18 for SQL Server）" },
  { "type":"oracle", "available":false, "module":"oracledb", "message":"..." },
  { "type":"mysql",  "available":false, "module":"pymysql", "message":"..." },
  { "type":"postgresql","available":false,"module":"psycopg2-binary","message":"..." } ]
```

#### `POST /ris/test-connection`
```jsonc
{ "db_type":"sqlserver","host":"192.168.1.100","port":1433,"database":"RIS_DB",
  "user":"readonly_user","password":"***","query_sql":"SELECT ..." }
→ { "ok": true }  /  { "ok": false, "message":"缺少驱动 pyodbc..." }
```

#### `POST /ris/fetch-reports`
按 `query_sql` 拉取报告（建议 `limit` 默认 50，前端可覆盖）：
```jsonc
→ { "items": [ { "patient":"张三","gender":"男","age":"45","modality":"CT",
                 "applied_site":"胸部","report_text":"检查所见：...。诊断印象：...。" } ],
    "count": 50 }
```
> **SQL 约定**：结果集**必须含 `report_text` 列**（质控正文）；可选 `patient / gender / age / modality / applied_site / ts` 列会被自动映射为 Meta。

### 3.8 应用设置（SPA 设置面板）
#### `GET /settings` ｜ `PUT /settings`
```jsonc
{ "emp_id":"demo01", "default_modality":"", "auto_qc_on_ocr": true,
  "auto_enqueue": true, "ocr_min_score": 0.55, "screen_refresh_on_ocr": false,
  "anonymize": false, "theme":"light" }
```

### 3.9 账号（责任到人）
#### `POST /accounts` `{ emp_id, password, name }` → 创建
#### `POST /accounts/login` `{ emp_id, password }` → `{ token }`
#### `GET /accounts` → 账号列表

### 3.10 健康检查
#### `GET /health` → `{ "status":"up", "version":"3.0" }`

---

## 4. 鉴权建议
- 本地（桌面端 WebView / `localhost`）：`require_emp_local` 自动放行，双击即用。
- 跨机/服务化部署：用 `POST /accounts/login` 取 token，后续请求带 `Authorization: Bearer <token>`；或内网网关注入 `X-Emp-Id` 头。
- 写操作（POST/PUT/DELETE）缺鉴权头且非本地时返回 `401`。

## 5. RIS 驱动依赖（重要）
RIS 直连**按目标库类型装对应驱动**（在 `server/requirements.txt` 中默认注释，避免无谓安装）：
| 数据库 | pip 包 | 额外系统依赖 |
|--------|--------|--------------|
| SQL Server | `pyodbc` | Microsoft ODBC Driver 18 for SQL Server |
| Oracle | `oracledb` | 纯 Python，免 Client |
| MySQL | `pymysql` | 无 |
| PostgreSQL | `psycopg2-binary` | 无 |

未安装驱动时 `test-connection` / `fetch-reports` 返回 `ok:false` 并给出安装命令，前端在「数据库连接配置」卡片的连通状态灯与 toast 中提示，不会崩溃。

## 6. 参考实现骨架（FastAPI，落地用）
```python
# server.py —— 仅示意，依赖请 pip install fastapi uvicorn python-multipart
import sys; sys.path.insert(0, "src")
import engine, accounts, samplelib
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

app = FastAPI(title="星衍放射质控 API", version="3.0")

@app.post("/api/v1/qc/check")
def qc_check(req: dict):               # 实际用 CheckReq(BaseModel)
    return {"ok": True, "code": "OK",
            "data": engine.RuleEngine().run(req["report"], req.get("meta", {})),
            "message": ""}
# 其余端点（/qc/batch, /qc/rules, /ocr*, /screen/*, /queue*, /samples*,
# /stats/*, /ris/*, /settings, /accounts/*）按第 3 节逐个补全，
# 内部直接调用对应 engine / accounts / samplelib 函数。
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## 7. 版本与兼容
- v3.0：统一 FastAPI 后端 + SPA，新增 `/screen/*`、`/queue*`、`/settings`、`/qc/rules/config`、`/samples/import/upload`、`/ris/*`。
- 评分维度键约定（§2.5）自 v2 起固定，前端映射层幂等；旧版纯 Tkinter 桌面端（`src/app.py`）共用 `src/engine.py`，逻辑一致。
