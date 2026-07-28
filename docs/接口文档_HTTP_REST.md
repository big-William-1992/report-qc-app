# 星衍放射质控软件 — HTTP/REST 接口规范

> 适用版本：`v2.5.0`
> 目的：将《接口文档_程序化API.md》中的本地引擎封装为**远程可调用的 REST 服务**，供星衍其他系统（放射质控 Web 端 / 病历系统 / 影像云）或第三方科室系统调用。
> 性质：**本文为接口规范草案，不含实现**。文末附 FastAPI 参考骨架，可直接落地。

---

## 1. 设计原则

1. **离线合规优先**：OCR 模型与规则库均在服务端本地，报告文本不出域；若部署在院内服务器，建议在隔离网段运行。
2. **责任到人**：所有写入类接口（`/qc/check` 持久化、`/samples`、`/accounts`）必须携带操作员工号（`X-Emp-Id` 头或 `user_id` 字段），与 `accounts` 模块一致。
3. **无状态质检**：`/qc/check`、`/qc/batch`、`/ocr` 为纯计算，不落库、可水平扩展；落库由 `/samples` 显式触发。
4. **错误明确**：统一错误包络 + 机器可读 `code`，便于前端分类处理。

---

## 2. 通用约定

### 2.1 基础路径
```
BASE = http(s)://<host>:<port>/api/v1
```
### 2.2 请求头
| Header | 必填 | 说明 |
|--------|------|------|
| `Content-Type` | 是 | `application/json`（JSON 接口） |
| `Authorization` | 条件 | `Bearer <token>`；登录/创建账号接口除外 |
| `X-Emp-Id` | 写操作必填 | 操作员工号（责任到人）；与 `accounts` 一致 |

### 2.3 统一响应包络
```json
{ "ok": true, "code": "OK", "data": { }, "message": "" }
```
失败时：
```json
{ "ok": false, "code": "BAD_REQUEST", "data": null, "message": "report 不能为空" }
```

### 2.4 错误码表
| code | HTTP | 含义 |
|------|------|------|
| `OK` | 200 | 成功 |
| `BAD_REQUEST` | 400 | 参数缺失/格式错误 |
| `UNAUTHORIZED` | 401 | 未登录 / token 失效 |
| `FORBIDDEN` | 403 | 工号无权限（如非责任人不许删他人样本） |
| `NOT_FOUND` | 404 | 资源不存在 |
| `UNSUPPORTED_MEDIA` | 415 | Content-Type 不支持 |
| `OCR_UNAVAILABLE` | 503 | OCR 依赖未就绪（未装 RapidOCR） |
| `INTERNAL` | 500 | 服务端异常 |

### 2.5 数据模型（JSON）

**Finding**
```json
{
  "rule_id": "R14-NATURE",
  "error_type": "前后文逻辑错误-定性矛盾",
  "severity": "high",
  "message": "描述称恶性倾向，但结论称良性倾向，前后定性矛盾",
  "snippet": "右肺上叶结节，符合良性",
  "span": [20, 28],
  "suggestion": ""
}
```
**Meta**（全部可选字符串）
```json
{ "patient": "张某", "exam_no": "CT20260728", "gender": "男", "age": "56",
  "modality": "CT", "applied_site": "胸部", "laterality": "右" }
```
**ScoreSummary**
```json
{ "准确性": 60, "完整性": 100, "规范性": 90, "及时性": 100 }
```

---

## 3. 接口清单

### 3.1 质控计算（无状态）

#### `POST /qc/check`
提交一份报告文本 + 元信息，返回错误列表与评分。**不落库**。

请求：
```json
{
  "report": "影像描述：右肺上叶见结节，边缘毛刺。\n影像结论：右肺上叶结节，符合良性。",
  "meta": { "gender": "男", "modality": "CT", "applied_site": "胸部" },
  "auto_fix": false
}
```
响应 `200`：
```json
{
  "ok": true, "code": "OK",
  "data": {
    "findings": [ { "rule_id": "R14-NATURE", "severity": "high", "...": "..." } ],
    "score": { "准确性": 70, "完整性": 100, "规范性": 90, "及时性": 100 },
    "error_counts": { "前后文逻辑错误-定性矛盾": 1 },
    "fixed": null
  }
}
```
字段说明：
- `auto_fix=true` 时 `data.fixed` 返回 `{fixed_text, n_fixed, n_manual, details}`，同 `engine.auto_fix`（仅错别字被安全替换）。

#### `POST /qc/batch`
批量质检（数组，上限建议 50 条/批），返回每一条的结果数组。语义同 `/qc/check`。
```json
{ "items": [ { "report": "...", "meta": {...}, "auto_fix": false }, ... ] }
```

#### `GET /qc/rules`
返回当前生效的规则配置（`rules_config.json` 内容：错别字词典/冲突词/忽略项/模板）。

#### `PUT /qc/rules`
更新规则配置（需管理员工号）。Body 同 `GET` 返回结构。`accounts` 工号权限由调用方约定（如 `X-Emp-Id` 在白名单）。成功后引擎 `reload_rules()`。

---

### 3.2 OCR（可选能力）

#### `POST /ocr`
上传图片（multipart/form-data，字段 `file`）或传 base64（`{ "image_base64": "..." }`），返回识别文本。
- 成功 `200`：`{ "data": { "text": "..." } }`
- OCR 未就绪 `503`：`{ "code": "OCR_UNAVAILABLE", ... }`

> 也可扩展 `POST /ocr/region`：传 `bbox={x,y,w,h}` 由服务端截图识别（需服务端有显示环境，仅适合桌面/网关一体机）。

---

### 3.3 账号（责任到人）

> 与本地 `accounts` 模块语义一致：工号即登录名，密码 PBKDF2 哈希；服务端另签发 `token` 用于 `Authorization`。

#### `POST /accounts`
创建账号（首次部署用；生产应限制来源 IP / 仅内网）。
```json
{ "emp_id": "10086", "password": "rad12345", "name": "谢俊" }
```
响应 `200`：`{ "data": { "emp_id": "10086", "name": "谢俊" } }`
失败 `400`：`{ "code": "BAD_REQUEST", "message": "密码至少 6 位" }` 或 `工号已存在`。

#### `POST /accounts/login`
```json
{ "emp_id": "10086", "password": "rad12345" }
```
响应 `200`：`{ "data": { "token": "<jwt>", "emp_id": "10086", "name": "谢俊" } }`
失败 `401`：`{ "code": "UNAUTHORIZED", "message": "工号或密码错误" }`

#### `GET /accounts`
列出账号（管理用），返回 `[{emp_id, name}, ...]`。

---

### 3.4 样本库（持久化 + 统计）

#### `POST /samples`
将一份已质检报告入库（责任到人，需 `X-Emp-Id` 或 body `user_id`）。
```json
{
  "report": "...", "meta": { "...": "..." },
  "findings": [ <Finding>, ... ], "score": { "准确性": 70, "...": "..." },
  "anonymize": false, "user_id": "10086"
}
```
响应 `200`：`{ "data": { "id": 42 } }`

#### `GET /samples`
分页列出样本概览。
```
GET /samples?page=1&page_size=20&user_id=10086&error_type=错别字
```
响应 `200`：`{ "data": { "total": 132, "items": [ {id,ts,patient,gender,modality,applied_site,user_id}, ... ] } }`

#### `GET /samples/{id}`
返回单条全字段（含 `report_text`、`findings_json`、`scores_json`）。

#### `DELETE /samples/{id}`
删除样本（需操作者为该样本 `user_id` 或管理员，否则 `403`）。

#### `GET /stats/error-types`
返回 `{ "错别字": 12, "前后文逻辑错误-定性矛盾": 5, ... }`（同 `samplelib.stats_by_error_type`）。

#### `GET /stats/trend`
返回 `{ "2026-07-28": {"n":3,"avg_acc":88.3}, ... }`（同 `samplelib.stats_by_date`）。

---

## 4. 鉴权建议

- 内部可信网络（院内网段）：可用 `X-Emp-Id` 头 + 简单网关白名单，低成本落地。
- 跨域/公网：采用 `POST /accounts/login` 签发 **JWT（短时效 access + 长 refresh）**，`Authorization: Bearer <token>` 校验；`user_id` 从 token 解析，禁止客户端自填（防越权改责任归属）。
- 所有写操作审计日志：记录 `emp_id + 接口 + 参数摘要 + 时间`，满足等保三级追溯要求。

---

## 5. 参考实现骨架（FastAPI，落地用）

```python
# server.py —— 仅示意，缺依赖请 pip install fastapi uvicorn python-multipart
import sys
sys.path.insert(0, "/path/to/report_qc_app/src")
import engine, accounts, samplelib
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

app = FastAPI(title="星衍放射质控 API", version="2.5.0")

class CheckReq(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False

class Finding(BaseModel):
    rule_id: str
    error_type: str
    severity: str
    message: str
    snippet: str = ""
    span: tuple = (-1, -1)
    suggestion: str = ""

@app.post("/api/v1/qc/check")
def qc_check(req: CheckReq, x_emp_id: Optional[str] = Header(None)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    re = engine.RuleEngine()
    findings = re.run(req.report, req.meta)
    score = engine.score_summary(engine.score(findings))
    data: Dict[str, Any] = {
        "findings": [f.__dict__ for f in findings],
        "score": score,
        "error_counts": engine.error_type_counts(findings),
        "fixed": None,
    }
    if req.auto_fix:
        fixed_text, n_fixed, n_manual, details = re.auto_fix(req.report, findings)
        data["fixed"] = {"fixed_text": fixed_text, "n_fixed": n_fixed,
                         "n_manual": n_manual, "details": details}
    return {"ok": True, "code": "OK", "data": data, "message": ""}

# 其余端点（/qc/batch, /qc/rules, /ocr, /accounts/*, /samples/*, /stats/*）
# 按第 3 节规范逐个补全即可，内部直接调用对应 engine/accounts/samplelib 函数。

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

> 部署提示：OCR 依赖（`opencv/rapidocr`）较重，建议将 `/ocr` 与 `/qc/*` 拆分为独立服务，OCR 服务仅在具备 GPU/显示环境或桌面网关上启用；`/qc/check` 纯 CPU 标准库，可多副本扩展。

---

## 6. 版本与兼容

- 本文档接口绑定 `engine` v2.5.0 的程序化签名（`RuleEngine.run` / `auto_fix` / `score` / `extract_meta` / `accounts.*` / `samplelib.*`）。
- 新增规则（R14/R15 等）会自动体现在 `findings[].rule_id`，**不改变接口形状**，调用方无需改代码即可获得更强校验。
- 破坏性变更（如 `meta` 字段增删、`score` 结构变化）将随 `APP_VERSION` 主/次版本号提升，并在《程序化API文档》同步标注。
