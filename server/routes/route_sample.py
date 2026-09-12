"""
route_sample.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query, Request
from typing import Optional
from server.schemas import SampleCreate, SampleExportReq, SampleReportExportReq, SampleImportReq
from server.deps import _envelope, _run_qc, _eng_scores, _worst_sev, require_emp_local, require_emp, log_audit
import engine, samplelib, accounts, json, os
from version import APP_VERSION

router = APIRouter(tags=['samples'])

@router.post("/api/v1/samples")
def sample_create(req: SampleCreate, request: Request,
                  emp: str = Depends(require_emp_local)):
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
    log_audit(emp, "sample_created", {"id": sid, "user_id": user_id},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {"id": sid})


@router.get("/api/v1/samples")
def sample_list(page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                user_id: Optional[str] = None,
                error_type: Optional[str] = None,
                emp: str = Depends(require_emp)):
    role = accounts.get_role(emp)
    rows = samplelib.list_samples_full()
    if role != "admin":
        # 普通医生仅看自己导入的样本（历史无归属样本归管理员管辖）
        rows = [r for r in rows if (r.get("user_id") or "") == emp]
    elif user_id:
        # 管理员可按工号筛选
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


@router.get("/api/v1/samples/{sid}")
def sample_get(sid: int, emp: str = Depends(require_emp_local)):
    """样本详情（含患者信息与报告全文）：本地 WebView 放行；
    远程（如内网 --host 0.0.0.0）强制凭证，避免无鉴权读取患者隐私。"""
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    return _envelope(True, "OK", s)


@router.delete("/api/v1/samples/{sid}")
def sample_delete(sid: int, request: Request, emp: str = Depends(require_emp_local)):
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    # 归属校验：本地（require_emp_local 返回 "local"）是桌面单机唯一使用者，
    # 允许删除；远程访问则强制责任到人（只能删自己导入的样本）。
    if s.get("user_id") and emp != "local" and s.get("user_id") != emp:
        raise HTTPException(403, "无权删除他人样本")
    samplelib.delete_sample(sid)
    log_audit(emp, "sample_deleted", {"id": sid},
              request.client.host if request.client else "")
    return _envelope(True, "OK", None, "已删除")
@router.post("/api/v1/samples/export")
def sample_export(req: SampleExportReq, request: Request,
                   emp: str = Depends(require_emp_local)):
    """导出样本库为 CSV / JSON / DOCX / PDF（修正 Flask 版把输出路径误传为库路径参数的问题）。"""
    try:
        fmt = (req.fmt or "csv").lower()
        if fmt not in ("csv", "json", "docx", "pdf"):
            raise HTTPException(400, "fmt 仅支持 csv/json/docx/pdf")
        result_path = samplelib.export_samples(out_path=req.path or None, fmt=fmt)
        log_audit(emp, "sample_exported", {"fmt": fmt, "path": result_path},
                  request.client.host if request.client else "")
        return _envelope(True, "OK", {"path": result_path, "fmt": fmt})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
@router.post("/api/v1/samples/{sid}/export-report")
def sample_report_export(sid: int, req: SampleReportExportReq,
                         emp: str = Depends(require_emp_local)):
    """导出单份样本的质控报告单（PDF/Word）：标题、检查部位、原报告、质控发现、建议修正。"""
    s = samplelib.get_sample(sid)
    if not s:
        raise HTTPException(404, "样本不存在")
    fmt = (req.fmt or "docx").lower()
    try:
        if fmt == "pdf":
            path = samplelib.export_report_pdf(s)
        else:
            path = samplelib.export_report_docx(s)
        return _envelope(True, "OK", {"path": path, "fmt": fmt})
    except Exception as exc:
        raise HTTPException(500, str(exc))
@router.get("/api/v1/files/download")
def file_download(file: str = Query(...), emp: str = Depends(require_emp_local)):
    """下载服务端生成的导出文件（按 basename 限定到导出目录，防任意路径读取）。

    前端把导出接口返回的 path 的 basename 传回即可拿到文件流。
    """
    from fastapi.responses import FileResponse
    name = os.path.basename(file or "")
    if not name or name in (".", ".."):
        raise HTTPException(400, "无效的文件名")
    # 限定在样本库所在目录 / 临时导出目录，防路径穿越
    export_dir = os.path.dirname(samplelib.db_path())
    full = os.path.join(export_dir, name)
    if not os.path.exists(full):
        raise HTTPException(404, "文件不存在或已被清理")
    return FileResponse(full, filename=name)
@router.post("/api/v1/samples/import")
def sample_import(req: SampleImportReq, request: Request,
                  emp: str = Depends(require_emp_local)):
    """导入样本库文件（按服务端路径）。"""
    try:
        inserted, skipped = samplelib.import_samples(req.path)
        log_audit(emp, "sample_imported", {"inserted": inserted, "skipped": skipped,
                                            "source": "path"},
                  request.client.host if request.client else "")
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/api/v1/samples/import/upload")
async def sample_import_upload(request: Request,
                               file: UploadFile = File(...),
                               emp: str = Depends(require_emp_local)):
    """浏览器端上传 CSV/JSON 文件导入样本库。"""
    import tempfile
    suffix = os.path.splitext(file.filename or "import.csv")[1] or ".csv"
    # 上传体积上限（与 OCR 上传一致），防止超大文件一次性读入内存造成 DoS
    _MAX_IMPORT_BYTES = 20 * 1024 * 1024
    data = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, f"导入文件超过 {_MAX_IMPORT_BYTES // (1024 * 1024)}MB 上限")
    tf = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
    try:
        tf.write(data)
        tf.close()
        inserted, skipped = samplelib.import_samples(tf.name)
        log_audit(emp, "sample_imported", {"inserted": inserted, "skipped": skipped,
                                            "source": "upload",
                                            "filename": file.filename or ""},
                  request.client.host if request.client else "")
        return _envelope(True, "OK", {"inserted": inserted, "skipped": skipped})
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        try:
            os.remove(tf.name)
        except Exception:
            pass
# ----------------------------- 统计 -----------------------------
@router.get("/api/v1/stats/error-types")
def stats_error_types(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_error_type())


@router.get("/api/v1/stats/trend")
def stats_trend(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_date())


@router.get("/api/v1/stats/report")
def stats_report(start: Optional[str] = None, end: Optional[str] = None,
                 emp: str = Depends(require_emp_local)):
    """质控问题分类统计报表（时间段筛选 + 问题类型 TOP 榜 + 科室/医生排行榜）。

    start / end 格式 YYYY-MM-DD，缺省不限。
    """
    try:
        data = samplelib.stats_report(start=start, end=end)
        return _envelope(True, "OK", data)
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/api/v1/samples/stats/dashboard")
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


@router.get("/api/v1/health")
def health():
    return _envelope(True, "OK", {"status": "up", "version": APP_VERSION})
