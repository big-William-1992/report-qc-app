"""
report_qc_app/server/main.py
星衍放射质控软件 — HTTP/REST 服务（对应《接口文档_HTTP_REST.md》规范，v3.0）

设计要点（与桌面端一致）：
- 完全离线优先：/qc/* 与 /samples/* 仅依赖标准库引擎（engine/accounts/samplelib）；
  /ocr 为可选能力，懒加载，缺 RapidOCR 依赖时返回 503（不影响 /qc 主流程）。
- 责任到人：所有写入类接口必须携带操作员工号——内网用 `X-Emp-Id` 头，
  公网用 `Authorization: Bearer <token>`（由 /accounts/login 签发，HMAC 签名，零额外依赖）。
- OCR 与质检解耦：/qc/check 纯 CPU 标准库，可多副本水平扩展；/ocr 仅在具备
  显示/模型环境的节点启用。

启动：
    cd report_qc_app
    pip install -r server/requirements.txt
    uvicorn server.main:app --host 0.0.0.0 --port 8000
"""
import os
import sys
import io
import time
import hmac
import hashlib
import base64
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

# 把 src 加入 sys.path，便于 import engine / accounts / samplelib
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import engine
import ris
import accounts
import samplelib

# ----------------------------- 鉴权（stdlib HMAC 签名 token） -----------------------------
SECRET = os.environ.get("QC_API_SECRET", "change-me-in-prod")
TOKEN_TTL = int(os.environ.get("QC_API_TTL", "86400"))  # 默认 24h


def make_token(emp_id: str, ttl: int = TOKEN_TTL) -> str:
    exp = int(time.time()) + ttl
    payload = f"{emp_id}.{exp}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def verify_token(tok: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        emp_id, exp = payload.rsplit(".", 1)
        if int(exp) < time.time():
            return None
        expect = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expect, sig):
            return emp_id
    except Exception:
        return None
    return None


def _emp_from_auth(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    tok = authorization
    if tok.lower().startswith("bearer "):
        tok = tok[7:]
    return verify_token(tok)


def require_emp(authorization: Optional[str] = Header(None),
                 x_emp_id: Optional[str] = Header(None)) -> str:
    """写操作鉴权：Bearer token 或内网 X-Emp-Id 头，二选一。"""
    emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token> 或 X-Emp-Id 头")
    return emp


def require_emp_local(request: Request,
                      authorization: Optional[str] = Header(None),
                      x_emp_id: Optional[str] = Header(None)) -> str:
    """写操作鉴权（本地优先）：

    - 来自 127.0.0.1/::1 的调用（桌面端 WebView、浏览器同源 localhost）自动放行，
      避免 SPA 必须携带鉴权头，保持本地"双击即用"体验；
    - 公网/远程部署仍强制 Bearer token 或 X-Emp-Id，保持责任到人追溯。
    """
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        return (x_emp_id or "local").strip() or "local"
    emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token> 或 X-Emp-Id 头")
    return emp


# ----------------------------- 应用 -----------------------------
app = FastAPI(title="星衍放射质控 API", version="3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------- 请求模型 -----------------------------
class FindingOut(BaseModel):
    rule_id: str
    error_type: str
    severity: str
    message: str
    snippet: str = ""
    span: list = [-1, -1]
    suggestion: str = ""


class CheckReq(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class BatchItem(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class BatchReq(BaseModel):
    items: List[BatchItem]


class AccountCreate(BaseModel):
    emp_id: str
    password: str
    name: str = ""


class LoginReq(BaseModel):
    emp_id: str
    password: str


class SampleCreate(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    findings: List[dict] = []
    score: Dict[str, Any] = {}
    anonymize: bool = False
    user_id: Optional[str] = None


class OCRB64(BaseModel):
    image_base64: str


# ----------------------------- 请求模型（Phase1 补充） -----------------------------
class RisConfigReq(BaseModel):
    db_type: str = "sqlserver"
    host: str = ""
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    query_sql: str = ""


class SampleExportReq(BaseModel):
    path: str = ""
    fmt: str = "csv"


class SampleImportReq(BaseModel):
    path: str = ""


# ----------------------------- 辅助 -----------------------------
def _envelope(ok: bool, code: str, data: Any, message: str = ""):
    return {"ok": ok, "code": code, "data": data, "message": message}


# 引擎 score_summary 输出中文维度键；前端（app.js）样本列表直接读英文键。
# 在需要英文键的端点做一层翻译，qc/check 的实时结果由前端自行映射（幂等）。
_SCORE_EN = {"准确性": "accuracy", "完整性": "completeness",
             "规范性": "normalization", "及时性": "timeliness"}


def _eng_scores(cn: dict) -> dict:
    """把引擎中文维度键映射为前端期望的英文键；未知键透传。"""
    out = {}
    for k, v in (cn or {}).items():
        out[_SCORE_EN.get(k, k)] = v
    return out


# 引擎 Finding.severity 用 high/medium/low；看板 by_severity 用 critical/warning/info。
_SEV_MAP = {"high": "critical", "medium": "warning", "low": "info"}
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def _worst_sev(findings: list) -> str:
    """从 findings 推导最严重级别（high>medium>low）；无 findings 视为 low→info。"""
    worst = "low"
    for f in (findings or []):
        sv = f.get("severity", "low") if isinstance(f, dict) else getattr(f, "severity", "low")
        if _SEV_RANK.get(sv, 0) > _SEV_RANK.get(worst, 0):
            worst = sv
    return _SEV_MAP.get(worst, "info")


def _run_qc(report: str, meta: dict, auto_fix: bool) -> dict:
    eng = engine.RuleEngine()
    findings = eng.run(report, meta)
    score = engine.score_summary(engine.score(findings))
    data = {
        "findings": [f.__dict__ for f in findings],
        "score": score,
        "error_counts": engine.error_type_counts(findings),
        "fixed": None,
    }
    if auto_fix:
        fixed_text, n_fixed, n_manual, details = eng.auto_fix(report, findings)
        data["fixed"] = {"fixed_text": fixed_text, "n_fixed": n_fixed,
                         "n_manual": n_manual, "details": details}
    return data


# ----------------------------- 质控计算（无状态） -----------------------------
@app.post("/api/v1/qc/check")
def qc_check(req: CheckReq):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    return _envelope(True, "OK", _run_qc(req.report, req.meta, req.auto_fix))


@app.post("/api/v1/qc/batch")
def qc_batch(req: BatchReq):
    if len(req.items) > 50:
        raise HTTPException(400, "单次最多 50 条")
    results = []
    for it in req.items:
        if not it.report.strip():
            results.append({"ok": False, "error": "report 不能为空"})
            continue
        results.append({"ok": True, **_run_qc(it.report, it.meta, it.auto_fix)})
    return _envelope(True, "OK", {"results": results})


@app.get("/api/v1/qc/rules")
def qc_rules_get():
    """返回规则元信息列表（供前端规则维护页展示；更新配置走 PUT）。"""
    rule_meta = [
        {"rule_id": "R1",  "name": "性别一致性检查",   "category": "完整性", "severity": "warning",   "enabled": True},
        {"rule_id": "R2",  "name": "侧别标注检查",     "category": "规范性", "severity": "warning",   "enabled": True},
        {"rule_id": "R3",  "name": "评分单位规范",     "category": "规范性", "severity": "info",      "enabled": True},
        {"rule_id": "R4",  "name": "单位实体识别",     "category": "准确性", "severity": "warning",   "enabled": True},
        {"rule_id": "R5",  "name": "描述与结论一致性", "category": "准确性", "severity": "critical",  "enabled": True},
        {"rule_id": "R6",  "name": "检查部位完整性",   "category": "完整性", "severity": "warning",   "enabled": True},
        {"rule_id": "R7",  "name": "内部结构完整性",   "category": "完整性", "severity": "info",      "enabled": True},
        {"rule_id": "R8",  "name": "错别字检测",       "category": "准确性", "severity": "warning",   "enabled": True},
        {"rule_id": "R9",  "name": "矛盾信息检测",     "category": "准确性", "severity": "critical",  "enabled": True},
        {"rule_id": "R10", "name": "模板符合度检查",   "category": "规范性", "severity": "warning",   "enabled": True},
        {"rule_id": "R11", "name": "上下文合理性",     "category": "准确性", "severity": "warning",   "enabled": True},
        {"rule_id": "R12", "name": "句子级质量评估",   "category": "规范性", "severity": "info",      "enabled": True},
        {"rule_id": "R14", "name": "跨区域交叉验证",   "category": "准确性", "severity": "warning",   "enabled": True},
        {"rule_id": "R15", "name": "内部术语规范化",   "category": "规范性", "severity": "info",      "enabled": True},
        {"rule_id": "R16", "name": "随访时限缺失",     "category": "及时性", "severity": "info",      "enabled": False},
    ]
    return _envelope(True, "OK", rule_meta)


@app.put("/api/v1/qc/rules")
def qc_rules_put(cfg: Dict[str, Any], emp: str = Depends(require_emp_local)):
    # 仅持久化已知键，避免客户端写入杂项
    clean = {
        "typos": cfg.get("typos", {}),
        "conflicts": cfg.get("conflicts", []),
        "ignores": cfg.get("ignores", []),
        "template": cfg.get("template", {}),
    }
    engine.save_rules_config(clean)
    return _envelope(True, "OK", clean, "规则已更新，下次请求自动生效")


# ----------------------------- OCR（可选） -----------------------------
@app.post("/api/v1/ocr")
async def ocr_upload(file: UploadFile = File(...)):
    from PIL import Image
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    data = await file.read()
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


@app.post("/api/v1/ocr/base64")
def ocr_base64(req: OCRB64):
    from PIL import Image
    import ocr_provider
    import base64 as _b64
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    try:
        raw = _b64.b64decode(req.image_base64)
        img = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"图片解析失败：{e}")
    text = ocr_provider.ocr_image(img)
    return _envelope(True, "OK", {"text": text})


# ----------------------------- RIS / PACS 直连（迁移自 web/api/ris.py） -----------------------------
@app.get("/api/v1/ris/drivers")
def ris_drivers(emp: str = Depends(require_emp_local)):
    """返回支持的数据库驱动列表及可用性。"""
    drivers = []
    for dtype in ("sqlserver", "oracle", "mysql", "postgresql"):
        ok, mod, msg = ris.driver_available(dtype)
        drivers.append({"type": dtype, "available": ok, "module": mod or "", "message": msg})
    return _envelope(True, "OK", drivers)


@app.post("/api/v1/ris/test-connection")
def ris_test_connection(req: RisConfigReq, emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query_sql": req.query_sql,
    }
    ok, msg = ris.test_connection(config)
    return _envelope(True, "OK", {"ok": ok, "message": msg})


@app.post("/api/v1/ris/fetch-reports")
def ris_fetch_reports(req: RisConfigReq, limit: int = Query(50, ge=1, le=200),
                      emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query_sql": req.query_sql,
    }
    reports = ris.fetch_reports(config, limit=limit)
    items = [{
        "report_text": (r.get("report_text", "") or "")[:500],
        "patient": r.get("patient", ""),
        "gender": r.get("gender", ""),
        "age": r.get("age", ""),
        "modality": r.get("modality", ""),
        "applied_site": r.get("applied_site", ""),
        "ts": r.get("ts", ""),
    } for r in (reports or [])]
    return _envelope(True, "OK", {"items": items, "count": len(items)})


# ----------------------------- 账号（责任到人） -----------------------------
@app.post("/api/v1/accounts")
def account_create(req: AccountCreate,
                   authorization: Optional[str] = Header(None),
                   x_emp_id: Optional[str] = Header(None)):
    # 首个账号免鉴权引导（boot）；已存在账号则必须登录后才能创建（防滥用）
    if accounts.count_accounts() > 0:
        emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
        if not emp:
            raise HTTPException(401, "创建账号需登录：Authorization: Bearer <token> 或 X-Emp-Id 头")
    ok, msg = accounts.create_account(req.emp_id, req.password, req.name)
    if not ok:
        raise HTTPException(400, msg)
    return _envelope(True, "OK", {"emp_id": req.emp_id, "name": req.name}, msg)


@app.post("/api/v1/accounts/login")
def account_login(req: LoginReq):
    if not accounts.verify_account(req.emp_id, req.password):
        raise HTTPException(401, "工号或密码错误")
    token = make_token(req.emp_id)
    return _envelope(True, "OK",
                      {"token": token, "emp_id": req.emp_id, "name": accounts.get_name(req.emp_id)})


@app.get("/api/v1/accounts")
def account_list(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", [{"emp_id": e, "name": n} for e, n in accounts.list_accounts()])


# ----------------------------- 样本库（持久化 + 统计） -----------------------------
@app.post("/api/v1/samples")
def sample_create(req: SampleCreate, emp: str = Depends(require_emp_local)):
    user_id = (req.user_id or emp).strip()
    raw_findings = list(req.findings) if req.findings else []
    # 入库即质控：未显式提供 findings 时，自动跑引擎生成发现与评分
    if not raw_findings and req.report.strip():
        qc = _run_qc(req.report, req.meta, False)
        raw_findings = qc.get("findings") or []
        score = qc.get("score") or {}
    else:
        score = req.score or {}
    # findings 由 dict 还原为 Finding 对象（save_sample 会序列化其 __dict__）
    findings = []
    for f in raw_findings:
        try:
            findings.append(engine.Finding(
                rule_id=f.get("rule_id", ""),
                error_type=f.get("error_type", ""),
                severity=f.get("severity", "low"),
                message=f.get("message", ""),
                snippet=f.get("snippet", ""),
                span=tuple(f.get("span", (-1, -1))),
                suggestion=f.get("suggestion", ""),
            ))
        except Exception:
            continue
    sid = samplelib.save_sample(
        req.report, req.meta, findings, score,
        anonymize=req.anonymize, user_id=user_id)
    return _envelope(True, "OK", {"id": sid})


@app.get("/api/v1/samples")
def sample_list(page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                user_id: Optional[str] = None,
                error_type: Optional[str] = None):
    rows = samplelib.list_samples_full()
    if user_id:
        rows = [r for r in rows if r.get("user_id") == user_id]
    if error_type:
        kept = []
        for r in rows:
            fj = r.get("findings_json") or "[]"
            try:
                ets = {x.get("error_type") for x in json.loads(fj)}
            except Exception:
                ets = set()
            if error_type in ets:
                kept.append(r)
        rows = kept
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    items = []
    for r in page_rows:
        scores = _eng_scores(json.loads(r.get("scores_json") or "{}"))
        findings = json.loads(r.get("findings_json") or "[]")
        items.append({
            "id": r.get("id"),
            "ts": r.get("ts"),
            "patient": r.get("patient", ""),
            "gender": r.get("gender", ""),
            "age": r.get("age", ""),
            "modality": r.get("modality", ""),
            "applied_site": r.get("applied_site", ""),
            "laterality": r.get("laterality", ""),
            "report_text": (r.get("report_text") or "")[:200],
            "findings_count": len(findings),
            "scores": scores,
        })
    return _envelope(True, "OK", {
        "total": total, "items": items, "page": page,
        "page_size": page_size, "pages": (total + page_size - 1) // page_size,
    })


@app.get("/api/v1/samples/{sid}")
def sample_get(sid: int):
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    return _envelope(True, "OK", s)


@app.delete("/api/v1/samples/{sid}")
def sample_delete(sid: int, emp: str = Depends(require_emp_local)):
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    if s.get("user_id") and s.get("user_id") != emp:
        raise HTTPException(403, "无权删除他人样本")
    samplelib.delete_sample(sid)
    return _envelope(True, "OK", None, "已删除")


@app.post("/api/v1/samples/export")
def sample_export(req: SampleExportReq, emp: str = Depends(require_emp_local)):
    """导出样本库为 CSV / JSON（修正 Flask 版把输出路径误传为库路径参数的问题）。"""
    try:
        result_path = samplelib.export_samples(out_path=req.path or None, fmt=req.fmt)
        return _envelope(True, "OK", {"path": result_path})
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/v1/samples/import")
def sample_import(req: SampleImportReq, emp: str = Depends(require_emp_local)):
    """导入样本库文件（按服务端路径）。"""
    try:
        inserted, skipped = samplelib.import_samples(req.path)
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/v1/samples/import/upload")
async def sample_import_upload(file: UploadFile = File(...), emp: str = Depends(require_emp_local)):
    """浏览器端上传 CSV/JSON 文件导入样本库。"""
    import tempfile
    suffix = os.path.splitext(file.filename or "import.csv")[1] or ".csv"
    tf = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
    try:
        tf.write(await file.read())
        tf.close()
        inserted, skipped = samplelib.import_samples(tf.name)
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        try:
            os.remove(tf.name)
        except Exception:
            pass


# ----------------------------- 统计 -----------------------------
@app.get("/api/v1/stats/error-types")
def stats_error_types():
    return _envelope(True, "OK", samplelib.stats_by_error_type())


@app.get("/api/v1/stats/trend")
def stats_trend():
    return _envelope(True, "OK", samplelib.stats_by_date())


@app.get("/api/v1/samples/stats/dashboard")
def sample_dashboard(emp: str = Depends(require_emp_local)):
    """看板页聚合统计（迁移自 web/api/samples.py 的 dashboard_stats）。"""
    from datetime import datetime, timedelta
    rows = samplelib.list_samples_full()
    total = len(rows)
    by_modality = {}
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    today = 0
    this_week = 0
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    today_str = now.strftime("%Y-%m-%d")
    for row in rows:
        mod = row.get("modality", "未知")
        by_modality[mod] = by_modality.get(mod, 0) + 1
        findings = json.loads(row.get("findings_json") or "[]")
        by_severity[_worst_sev(findings)] = by_severity.get(_worst_sev(findings), 0) + 1
        ts = row.get("ts", "")
        if ts and ts.startswith(today_str):
            today += 1
        if ts and ts > week_ago.strftime("%Y-%m-%d"):
            this_week += 1
    return _envelope(True, "OK", {
        "total": total, "today": today, "this_week": this_week,
        "by_modality": by_modality, "by_severity": by_severity,
    })


@app.get("/api/v1/health")
def health():
    return _envelope(True, "OK", {"status": "up", "version": "3.0"})


# ============================================================================
# 以下为「SPA 功能追平桌面版」新增能力（P0/P1）
#   1) 待质控队列   —— 与 Tkinter 版共用 ~/.medical_report_qc/qc_queue.json
#   2) 屏幕采集 OCR —— 真·框选 PACS 屏幕三区（基础信息/影像描述/影像诊断）
#   3) 应用设置     —— SPA 侧设置面板的持久化
#   4) 规则配置读取 —— 供规则维护页编辑 R8 错别字 / R9 矛盾对 / 忽略词
# 注意：必须注册在文件末尾的 SPA catch-all 路由「之前」，否则 GET 会被兜底吞掉。
# ============================================================================
import uuid as _uuid


def _appdata_dir() -> str:
    """跨平台数据目录（与 src/app.py 的 _appdata_dir 保持完全一致，实现队列互通）。"""
    ap = os.path.expandvars("%APPDATA%")
    if ap and os.path.isabs(ap):
        base = os.path.join(ap, "MedicalReportQC")
    else:
        base = os.path.join(os.path.expanduser("~"), ".medical_report_qc")
    os.makedirs(base, exist_ok=True)
    return base


def _queue_path() -> str:
    return os.path.join(_appdata_dir(), "qc_queue.json")


def _load_queue() -> list:
    try:
        with open(_queue_path(), encoding="utf-8") as fh:
            return json.load(fh) or []
    except Exception:
        return []


def _save_queue(items: list) -> None:
    with open(_queue_path(), "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)


class QueueItemReq(BaseModel):
    text: str
    patient: str = ""
    site: str = ""
    source: str = "手动"
    meta: Dict[str, str] = {}


@app.get("/api/v1/queue")
def queue_list():
    items = _load_queue()
    return _envelope(True, "OK", {"items": items, "count": len(items)})


@app.post("/api/v1/queue")
def queue_add(req: QueueItemReq, emp: str = Depends(require_emp_local)):
    """加入待质控队列；按正文 MD5 去重（与桌面版同一去重口径）。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    norm = "".join(text.split())
    h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
    items = _load_queue()
    for it in items:
        if it.get("hash") == h:
            return _envelope(True, "OK", {"id": it["id"], "duplicated": True}, "该报告已在队列中")
    item = {
        "id": _uuid.uuid4().hex[:8],
        "hash": h,
        "patient": (req.patient or req.meta.get("patient", "")).strip(),
        "site": (req.site or req.meta.get("applied_site", "")).strip(),
        "text": text,
        "source": req.source or "手动",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "meta": req.meta or {},
    }
    items.append(item)
    _save_queue(items)
    return _envelope(True, "OK", {"id": item["id"], "duplicated": False, "count": len(items)})


@app.delete("/api/v1/queue")
def queue_clear(emp: str = Depends(require_emp_local)):
    _save_queue([])
    return _envelope(True, "OK", {"count": 0}, "队列已清空")


@app.delete("/api/v1/queue/{qid}")
def queue_remove(qid: str, emp: str = Depends(require_emp_local)):
    items = _load_queue()
    kept = [it for it in items if it.get("id") != qid]
    if len(kept) == len(items):
        raise HTTPException(404, "队列条目不存在")
    _save_queue(kept)
    return _envelope(True, "OK", {"count": len(kept)}, "已移出队列")


# ----------------------------- 屏幕采集（真·框选 PACS 屏幕） -----------------------------
# 用户诉求：不是上传报告图，而是在 PACS 软件窗口上框选三个区域
#   basic=病人基础信息 / findings=影像描述 / impression=影像诊断
# 流程：/screen/capture 抓全屏（原图缓存于内存）→ SPA 在缩略图上拖三个框
#      → /screen/ocr 传比例框 → 后端在「原始分辨率」截图上裁剪 → RapidOCR
# 在原图上裁剪（而非缩略图）可避免下采样导致的小字识别率骤降。
_SHOT: Dict[str, Any] = {"img": None, "w": 0, "h": 0, "ts": 0.0}
_SHOT_MAX_W = 1600          # 传给前端的缩略图最大宽度（省带宽，不影响识别精度）


class ScreenRegion(BaseModel):
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


class ScreenOCRReq(BaseModel):
    regions: Dict[str, ScreenRegion] = {}
    refresh: bool = False       # True=识别前重新抓屏（画面已变动时用）


def _grab_fullscreen():
    from PIL import ImageGrab
    img = ImageGrab.grab()
    if img is None:
        raise RuntimeError("截屏返回空图")
    # macOS 未授予「屏幕录制」权限时会返回纯黑图（不抛异常），这里做启发式检测
    try:
        ext = img.convert("L").getextrema()
        if ext == (0, 0):
            raise RuntimeError(
                "截屏结果全黑：macOS 需在『系统设置 → 隐私与安全性 → 屏幕录制』"
                "中勾选本应用（终端/星衍质控），授权后需重启应用。")
    except RuntimeError:
        raise
    except Exception:
        pass
    return img


@app.post("/api/v1/screen/capture")
def screen_capture(emp: str = Depends(require_emp_local)):
    """抓取整屏，返回缩略图 base64（原图缓存于服务端供后续高精度裁剪）。"""
    try:
        img = _grab_fullscreen()
    except Exception as exc:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "SCREEN_UNAVAILABLE", None, str(exc)))
    _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = img, img.width, img.height, time.time()
    thumb = img
    if img.width > _SHOT_MAX_W:
        ratio = _SHOT_MAX_W / float(img.width)
        thumb = img.resize((_SHOT_MAX_W, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    thumb.convert("RGB").save(buf, format="PNG")
    return _envelope(True, "OK", {
        "image_base64": base64.b64encode(buf.getvalue()).decode(),
        "width": img.width, "height": img.height,
        "thumb_width": thumb.width, "thumb_height": thumb.height,
        "ts": _SHOT["ts"],
    })


@app.post("/api/v1/screen/ocr")
def screen_ocr(req: ScreenOCRReq, emp: str = Depends(require_emp_local)):
    """按比例框在缓存的整屏原图上裁剪并 OCR，返回三区文本 + 结构化 meta。"""
    import ocr_provider
    ok, why = ocr_provider.availability()
    if not ok:
        return JSONResponse(status_code=503,
                            content=_envelope(False, "OCR_UNAVAILABLE", None, why))
    img = _SHOT.get("img")
    if req.refresh or img is None:
        try:
            img = _grab_fullscreen()
            _SHOT["img"], _SHOT["w"], _SHOT["h"], _SHOT["ts"] = \
                img, img.width, img.height, time.time()
        except Exception as exc:
            return JSONResponse(status_code=503,
                                content=_envelope(False, "SCREEN_UNAVAILABLE", None, str(exc)))
    W, H = img.width, img.height
    texts: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for role, r in (req.regions or {}).items():
        x0 = max(0, min(W - 1, int(r.x * W)))
        y0 = max(0, min(H - 1, int(r.y * H)))
        x1 = max(x0 + 1, min(W, int((r.x + r.w) * W)))
        y1 = max(y0 + 1, min(H, int((r.y + r.h) * H)))
        try:
            texts[role] = ocr_provider.ocr_image(img.crop((x0, y0, x1, y1))) or ""
        except Exception as exc:
            texts[role] = ""
            errors[role] = str(exc)
    meta = {}
    try:
        meta = engine.extract_meta_full(texts.get("basic", ""),
                                        texts.get("findings", ""),
                                        texts.get("impression", ""))
    except Exception:
        try:
            meta = engine.extract_meta(texts.get("basic", ""))
        except Exception:
            meta = {}
    return _envelope(True, "OK", {"texts": texts, "meta": meta, "errors": errors})


def _ocr_config_path() -> str:
    """与 src/app.py 的 _ocr_config_path 同路径，实现桌面/Web 区域配置互通。"""
    ap = os.path.expandvars("%APPDATA%")
    if ap and os.path.isabs(ap):
        d = os.path.join(ap, "MedicalReportQC")
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "MedicalReportQC")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "ocr_config.json")


@app.get("/api/v1/screen/regions")
def screen_regions_get():
    """读取 SPA 侧保存的比例框（web_regions）。"""
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    return _envelope(True, "OK", {"web_regions": cfg.get("web_regions") or {}})


@app.put("/api/v1/screen/regions")
def screen_regions_put(regions: Dict[str, Any], emp: str = Depends(require_emp_local)):
    try:
        with open(_ocr_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        cfg = {}
    cfg["web_regions"] = regions
    with open(_ocr_config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return _envelope(True, "OK", {"web_regions": regions}, "框选区域已保存")


# ----------------------------- 应用设置（SPA 设置面板） -----------------------------
_DEFAULT_SETTINGS = {
    "emp_id": "demo01",            # 默认工号（责任到人）
    "default_modality": "",        # 默认成像方式
    "auto_qc_on_ocr": True,        # OCR 回填后自动跑质控
    "auto_enqueue": True,          # 采集/RIS 拉取自动进待质控队列
    "ocr_min_score": 0.55,         # OCR 置信度阈值
    "screen_refresh_on_ocr": False,  # 识别前重新抓屏
    "anonymize": False,            # 入库脱敏
    "theme": "light",
    # ── 可配置快捷键（Windows 风 Ctrl+ 默认；设置页可逐条重绑，持久化到 web_settings.json）──
    # mods 取值: "ctrl" / "shift" / "alt" / "meta"；key 为 KeyboardEvent.key（大小写敏感）
    "shortcuts": {
        "run_qc":      {"mods": ["ctrl"], "key": "Enter"},   # 运行质控
        "save_sample": {"mods": ["ctrl"], "key": "s"},       # 存入样本库
        "ocr_capture": {"mods": ["ctrl", "shift"], "key": "o"},  # 识别并质控（框选OCR）
        "toggle_theme":{"mods": ["ctrl"], "key": "t"},       # 明暗主题切换
    },
}


def _settings_path() -> str:
    return os.path.join(_appdata_dir(), "web_settings.json")


@app.get("/api/v1/settings")
def settings_get():
    data = dict(_DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data.update(json.load(fh) or {})
    except Exception:
        pass
    return _envelope(True, "OK", data)


@app.put("/api/v1/settings")
def settings_put(cfg: Dict[str, Any], emp: str = Depends(require_emp_local)):
    data = dict(_DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data.update(json.load(fh) or {})
    except Exception:
        pass
    for k in _DEFAULT_SETTINGS:          # 只接受已知键，避免写入杂项
        if k == "shortcuts":
            continue                     # shortcuts 走下方逐条合并，避免整体覆盖
        if k in cfg:
            data[k] = cfg[k]
    # shortcuts 为嵌套字典：以「默认值+已持久化」为基线，逐条合并已知动作键，
    # 避免只重绑某一个动作时丢失其它动作（如只改 run_qc 却把 save_sample 清零）
    if isinstance(cfg.get("shortcuts"), dict):
        known = set((_DEFAULT_SETTINGS.get("shortcuts") or {}).keys())
        cur = dict(data.get("shortcuts") or {})
        for act, sc in cfg["shortcuts"].items():
            if act in known:
                cur[act] = sc
        data["shortcuts"] = cur
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return _envelope(True, "OK", data, "设置已保存")


# ----------------------------- 规则配置（供规则维护页编辑） -----------------------------
@app.get("/api/v1/qc/rules/config")
def qc_rules_config_get():
    """返回可编辑的规则配置：R8 错别字表 / R9 矛盾对 / 忽略词 / 模板规范。"""
    return _envelope(True, "OK", engine.load_rules_config())


@app.put("/api/v1/qc/rules/config")
def qc_rules_config_put(cfg: Dict[str, Any], emp: str = Depends(require_emp_local)):
    """覆盖保存规则配置（typos/conflicts/ignores/template）。"""
    try:
        engine.save_rules_config(cfg)
        return _envelope(True, "OK", engine.load_rules_config(), "规则配置已保存")
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ----------------------------- 静态前端托管（SPA） -----------------------------
# 单服务同时提供 REST API 与同一套 SPA 前端，桌面 WebView 壳与浏览器共用。
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os as _os

_STATIC_DIR = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "web", "static"))
if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # SPA 路由兜底：非 API / static / 文档 的请求一律返回 index.html
        if full_path.startswith(("api/", "static/", "docs", "openapi", "redoc")):
            raise HTTPException(404, "Not Found")
        index = _os.path.join(_STATIC_DIR, "index.html")
        if _os.path.exists(index):
            return FileResponse(index)
        raise HTTPException(404, "前端未找到")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
