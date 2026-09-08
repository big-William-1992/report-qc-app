"""
route_qc.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Depends, Query, Request
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any, List
from server.schemas import CheckReq, BatchReq, BatchItem, QcReportExportReq, LearnTypoReq, TypoItemReq, TypoBatchImportReq
from server.deps import SECRET, _envelope, _run_qc, require_emp_local, require_emp, log_audit
import engine, samplelib


router = APIRouter(tags=['qc'])

# ----------------------------- 质控计算（无状态） -----------------------------
@router.post("/api/v1/qc/check")
def qc_check(req: CheckReq, emp: str = Depends(require_emp_local)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    return _envelope(True, "OK", _run_qc(req.report, req.meta, req.auto_fix))


@router.post("/api/v1/qc/batch")
def qc_batch(req: BatchReq, emp: str = Depends(require_emp_local)):
    if len(req.items) > 50:
        raise HTTPException(400, "单次最多 50 条")
    results = []
    for it in req.items:
        if not it.report.strip():
            results.append({"ok": False, "error": "report 不能为空"})
            continue
        results.append({"ok": True, **_run_qc(it.report, it.meta, it.auto_fix)})
    return _envelope(True, "OK", {"results": results})


@router.get("/api/v1/qc/rules")
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


@router.put("/api/v1/qc/rules")
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

@router.post("/api/v1/qc/export-report")
def qc_report_export(req: QcReportExportReq, emp: str = Depends(require_emp_local)):
    """把当前质控结果直接导出为质控报告单（PDF/Word），无需先入库。"""
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    fmt = (req.fmt or "docx").lower()
    if fmt not in ("docx", "pdf"):
        raise HTTPException(400, "fmt 仅支持 docx/pdf")
    try:
        path = samplelib.export_qc_report(
            req.report, req.meta, req.findings or [],
            req.scores or {}, fmt=fmt)
        return _envelope(True, "OK", {"path": path, "fmt": fmt})
    except Exception as exc:
        raise HTTPException(500, str(exc))
# ----------------------------- 规则配置（供规则维护页编辑） -----------------------------
@router.get("/api/v1/qc/rules/config")
def qc_rules_config_get(emp: str = Depends(require_emp_local)):
    """返回可编辑的规则配置：R8 错别字表 / R9 矛盾对 / 忽略词 / 模板规范。"""
    return _envelope(True, "OK", engine.load_rules_config())


@router.put("/api/v1/qc/rules/config")
def qc_rules_config_put(cfg: Dict[str, Any], request: Request,
                         emp: str = Depends(require_emp_local)):
    """覆盖保存规则配置（typos/conflicts/ignores/template）。"""
    try:
        engine.save_rules_config(cfg)
        log_audit(emp, "rules_config_saved",
                  {"keys": list(cfg.keys())},
                  request.client.host if request.client else "")
        return _envelope(True, "OK", engine.load_rules_config(), "规则配置已保存")
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/api/v1/qc/rules/config/reset")
def qc_rules_config_reset(request: Request, emp: str = Depends(require_emp_local)):
    """恢复出厂默认规则库（覆盖用户自定义）。"""
    try:
        cfg = engine.default_rules_config()
        engine.save_rules_config(cfg)
        log_audit(emp, "rules_config_reset", None,
                  request.client.host if request.client else "")
        return _envelope(True, "OK", engine.load_rules_config(), "已恢复默认规则库")
    except Exception as exc:
        raise HTTPException(500, str(exc))




@router.post("/api/v1/qc/rules/learn-typo")
def qc_rules_learn_typo(req: LearnTypoReq, request: Request,
                         emp: str = Depends(require_emp_local)):
    """P0 修正反馈闭环：用户确认的「错词→正确词」写入规则库，下次自动识别。"""
    ok = engine.learn_typo(req.wrong, req.correct)
    if not ok:
        raise HTTPException(400, "无效的错字对（为空或已存在反向冲突）")
    log_audit(emp, "typo_learned", {"wrong": req.wrong, "correct": req.correct},
              request.client.host if request.client else "")
    return _envelope(True, "OK", None, f"已学习错字对：{req.wrong}→{req.correct}")


# ----------------------------- 错别字词库可视化维护（P0：增删改/批量导入/启停单条） -----------------------------




@router.post("/api/v1/qc/rules/typos")
def qc_rules_typo_add(req: TypoItemReq, request: Request,
                       emp: str = Depends(require_emp_local)):
    """新增单条错字对（若已存在则更新正确词并自动启用）。"""
    ok = engine.learn_typo(req.wrong, req.correct)
    if not ok:
        raise HTTPException(400, "无效的错字对（为空、过长或含非中文）")
    # 新增时自动从停用列表移除（新维护的词默认启用）
    cfg = engine.load_rules_config()
    disabled = cfg.get("disabled_typos") or []
    if req.wrong.strip() in disabled:
        cfg["disabled_typos"] = [d for d in disabled if d != req.wrong.strip()]
        engine.save_rules_config(cfg)
    log_audit(emp, "typo_added", {"wrong": req.wrong, "correct": req.correct},
              request.client.host if request.client else "")
    return _envelope(True, "OK", None, f"已新增错字对：{req.wrong}→{req.correct}")


@router.post("/api/v1/qc/rules/typos/toggle")
def qc_rules_typo_toggle(req: TypoItemReq, request: Request,
                          emp: str = Depends(require_emp_local)):
    """启用/停用单条错字：enabled=false 时把错词加入 disabled_typos，true 时移出。"""
    wrong = (req.wrong or "").strip()
    if not wrong:
        raise HTTPException(400, "缺少错词")
    enabled = (req.correct or "").lower() not in ("0", "false", "off", "停用")
    cfg = engine.load_rules_config()
    disabled = set(cfg.get("disabled_typos") or [])
    if enabled:
        disabled.discard(wrong)
        msg = "已启用"
    else:
        disabled.add(wrong)
        msg = "已停用"
    cfg["disabled_typos"] = sorted(disabled)
    engine.save_rules_config(cfg)
    log_audit(emp, "typo_toggled", {"wrong": wrong, "enabled": enabled},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {"wrong": wrong, "enabled": enabled}, f"{msg}错字词条「{wrong}」")


@router.post("/api/v1/qc/rules/typos/delete")
def qc_rules_typo_delete(req: TypoItemReq, request: Request,
                          emp: str = Depends(require_emp_local)):
    """删除单条错字（同时从停用列表移除）。"""
    wrong = (req.wrong or "").strip()
    if not wrong:
        raise HTTPException(400, "缺少错词")
    cfg = engine.load_rules_config()
    typos = cfg.get("typos") or {}
    if wrong not in typos:
        raise HTTPException(404, f"错词「{wrong}」不存在")
    typos.pop(wrong, None)
    cfg["typos"] = typos
    cfg["disabled_typos"] = [d for d in (cfg.get("disabled_typos") or []) if d != wrong]
    engine.save_rules_config(cfg)
    log_audit(emp, "typo_deleted", {"wrong": wrong},
              request.client.host if request.client else "")
    return _envelope(True, "OK", None, f"已删除错字词条「{wrong}」")


@router.post("/api/v1/qc/rules/typos/batch-import")
def qc_rules_typo_batch_import(req: TypoBatchImportReq, request: Request,
                                emp: str = Depends(require_emp_local)):
    """批量导入错字对：自动跳过无效/反向冲突项，返回成功与失败数。"""
    cfg = engine.load_rules_config()
    typos = cfg.get("typos") or {}
    disabled = set(cfg.get("disabled_typos") or [])
    ok_n = bad_n = 0
    bad_items = []
    for it in (req.items or []):
        if isinstance(it, dict):
            wrong, correct = (it.get("wrong") or "").strip(), (it.get("correct") or "").strip()
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            wrong, correct = str(it[0]).strip(), str(it[1]).strip()
        else:
            bad_n += 1
            continue
        if not wrong or not correct or wrong == correct or len(wrong) > 10 or len(correct) > 10:
            bad_n += 1
            bad_items.append([wrong, correct])
            continue
        if typos.get(correct) == wrong:   # 反向冲突保护
            bad_n += 1
            bad_items.append([wrong, correct])
            continue
        typos[wrong] = correct
        disabled.discard(wrong)
        ok_n += 1
    cfg["typos"] = typos
    cfg["disabled_typos"] = sorted(disabled)
    engine.save_rules_config(cfg)
    log_audit(emp, "typo_batch_imported", {"ok": ok_n, "bad": bad_n},
              request.client.host if request.client else "")
    return _envelope(True, "OK", {"ok": ok_n, "bad": bad_n, "bad_items": bad_items[:20]},
                     f"批量导入完成：成功 {ok_n} 条，跳过 {bad_n} 条")


@router.post("/api/v1/qc/rules/scan-reports")
def qc_rules_scan_reports(emp: str = Depends(require_emp_local)):
    """P0 历史报告词频学习：扫描样本库，自动发现候选错字对，供一键采纳。"""
    try:
        cands = engine.scan_reports_for_typos()
        return _envelope(True, "OK", {"candidates": cands}, f"发现 {len(cands)} 个候选错字")
    except Exception as exc:
        raise HTTPException(500, str(exc))
