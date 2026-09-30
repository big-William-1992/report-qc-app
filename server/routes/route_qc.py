"""
route_qc.py — 质控计算与规则/错字表管理（2026-09-30 路由拆分 S5）

从 `server/main.py` 迁出，端点实现**逐字搬迁**（共 16 个）：

质控计算  POST /api/v1/qc/check · /batch · /llm · /full · /export-report
规则读写  GET/PUT /api/v1/qc/rules（保存后刷新引擎单例）
          GET/PUT /api/v1/qc/rules/config · POST /api/v1/qc/rules/config/reset
错字表    POST /api/v1/qc/rules/learn-typo
（管理员）POST /api/v1/qc/rules/typos · /typos/toggle · /typos/delete
          POST /api/v1/qc/rules/typos/batch-import · /rules/scan-reports

共享运行时（引擎单例 / IP 限流 / _run_qc）在 `server/qc_runtime.py` ——
**引擎单例与限流表必须只有一份**，否则会出现"改了规则只有部分端点生效"、限流形同虚设。
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from server.core import _envelope
from server.qc_runtime import (_get_engine, _qc_rate_ok, _reload_engine_rules,  # noqa: F401
                               _run_qc)
from server.security import require_admin, require_emp_local, require_license_active
from server.schemas import (BatchReq, CheckReq, LearnTypoReq, QcReportExportReq,
                            TypoBatchImportReq, TypoItemReq)

import engine
import samplelib

router = APIRouter(tags=["qc"])

@router.post("/api/v1/qc/check")
def qc_check(req: CheckReq, request: Request,
             emp: str = Depends(require_emp_local),
             _lic: bool = Depends(require_license_active)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    if len(req.report) > 20000:
        raise HTTPException(413, "报告文本过长（上限 20000 字符）")
    ip = request.client.host if request.client else ""
    if not _qc_rate_ok(ip):
        raise HTTPException(429, "质控请求过于频繁，请稍后重试")
    return _envelope(True, "OK", _run_qc(req.report, req.meta, req.auto_fix))


@router.post("/api/v1/qc/batch")
def qc_batch(req: BatchReq, request: Request,
             emp: str = Depends(require_emp_local),
             _lic: bool = Depends(require_license_active)):
    if len(req.items) > 50:
        raise HTTPException(400, "单次最多 50 条")
    ip = request.client.host if request.client else ""
    if not _qc_rate_ok(ip):
        raise HTTPException(429, "质控请求过于频繁，请稍后重试")
    results = []
    for it in req.items:
        if not it.report.strip():
            results.append({"ok": False, "error": "report 不能为空"})
            continue
        if len(it.report) > 20000:
            results.append({"ok": False, "error": "report 过长（上限 20000 字符）"})
            continue
        results.append({"ok": True, **_run_qc(it.report, it.meta, it.auto_fix)})
    return _envelope(True, "OK", {"results": results})


# ----------------------------- LLM 语义质控（异步/可降级） -----------------------------
# 大模型的语义发现作为「第二阅片人」，不单独定错：结果经 llm_fusion 门控
# （高置信建议 / 待确认 / 人工复核）。本地 Ollama/vLLM 未启动时优雅降级（available=False），
# 不影响 /qc/check 主流程。耗时较长，前端建议异步调用并在 UI 分色展示。
@router.post("/api/v1/qc/llm")
def qc_llm(req: CheckReq, request: Request,
           emp: str = Depends(require_emp_local),
           _lic: bool = Depends(require_license_active)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    if len(req.report) > 20000:
        raise HTTPException(413, "报告文本过长（上限 20000 字符）")
    from llm_qc import run_full_qc
    result = run_full_qc(req.report, req.meta, run_rules=False, run_llm=True)
    return _envelope(True, "OK", {
        "available": result.get("llm_available", False),
        "error": result.get("llm_error"),
        "model": result.get("llm_model"),
        "llm_findings": result.get("llm_findings", []),
        "counts": result.get("counts", {}),
    })


# 融合质控：规则 + LLM 一次性返回（规则即时、LLM 同步等待）。超时由 llm_client 控制。
@router.post("/api/v1/qc/full")
def qc_full(req: CheckReq, request: Request,
            emp: str = Depends(require_emp_local),
            _lic: bool = Depends(require_license_active)):
    if not req.report.strip():
        raise HTTPException(400, "report 不能为空")
    if len(req.report) > 20000:
        raise HTTPException(413, "报告文本过长（上限 20000 字符）")
    from llm_qc import run_full_qc
    result = run_full_qc(req.report, req.meta, run_rules=True, run_llm=True)
    return _envelope(True, "OK", {
        "rule_findings": result.get("rule_findings", []),
        "llm_findings": result.get("llm_findings", []),
        "fused": result.get("fused", []),
        "counts": result.get("counts", {}),
        "llm_available": result.get("llm_available", False),
        "llm_error": result.get("llm_error"),
    })




@router.get("/api/v1/qc/rules")
def qc_rules_get():
    """返回规则元信息列表（供前端规则维护页展示；更新配置走 PUT）。
    ⚠️ 此表与引擎产出需人工同步，缺失会导致 /api/v1/qc/rules 元信息不全。
    2026-08-18 同步：清单改为规则合并后的实际产出 rule_id，severity 与引擎口径
    （high/medium/low）一致；R7/R11/R13/R20 已合并或预留，不再单列。"""
    rule_meta = [
        {"rule_id": "R1-GENDER",      "name": "性别矛盾",         "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R2-LATERALITY",  "name": "左右侧混淆",       "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R3-SCORE",       "name": "评分缺失",         "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R4-UNIT",        "name": "计量单位错误",     "category": "准确性", "severity": "low",     "enabled": True},
        {"rule_id": "R5-CONSISTENCY", "name": "描述-结论矛盾",    "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R6-SITE",        "name": "登记部位不符",     "category": "完整性", "severity": "high",    "enabled": True},
        {"rule_id": "R8-TYPO",        "name": "同音错别字",       "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R9-CONFLICT",    "name": "自定义互斥冲突",   "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R10-TEMPLATE",   "name": "模板合规",         "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R12-SENTENCE",   "name": "句内自相矛盾",     "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R14-NATURE",     "name": "良恶性定性矛盾",   "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R14-COUNT",      "name": "病灶数量矛盾",     "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R15-NORMAL",     "name": "段首正常段内阳性", "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R15-PRESENCE",   "name": "先见后无",         "category": "准确性", "severity": "medium",  "enabled": True},
        {"rule_id": "R16-FOLLOWUP",   "name": "随访时限缺失",     "category": "及时性", "severity": "low",     "enabled": False},
        {"rule_id": "R17-PERREGION",  "name": "逐部位描述-结论矛盾", "category": "准确性", "severity": "high", "enabled": True},
        {"rule_id": "R18-COVERAGE",   "name": "部位器官漏写",     "category": "完整性", "severity": "medium",  "enabled": True},
        {"rule_id": "R19-HOMOPHONE",  "name": "形近错字",         "category": "准确性", "severity": "low",     "enabled": True},
        {"rule_id": "R21-GENDER-SITE","name": "性别-部位联动",    "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R22-SIZE",       "name": "尺寸-术语一致性",  "category": "规范性", "severity": "medium",  "enabled": True},
        {"rule_id": "R22-UNIT",       "name": "尺寸单位规范",     "category": "规范性", "severity": "low",     "enabled": True},
        # R22-SIZE-MISSING 默认关（engine require_lesion_size 缺省 False），与引擎口径一致
        {"rule_id": "R22-SIZE-MISSING", "name": "病灶缺尺寸",     "category": "完整性", "severity": "low",     "enabled": False},
        {"rule_id": "R23-TRADITIONAL",  "name": "繁体字提示",     "category": "规范性", "severity": "low",     "enabled": True},
        {"rule_id": "R24-ADVICE",       "name": "建议强度矛盾",   "category": "准确性", "severity": "high",    "enabled": True},
        {"rule_id": "R25-TEMPORAL",     "name": "时序方向矛盾",   "category": "准确性", "severity": "medium",  "enabled": True},
    ]
    return _envelope(True, "OK", rule_meta)


@router.put("/api/v1/qc/rules")
def qc_rules_put(cfg: Dict[str, Any], emp: str = Depends(require_admin)):
    # 读→合并→写回（2026-08-18 M3 修复）：只覆盖客户端提交的已知键，不再整表覆盖
    # （此前部分 PUT 会静默丢弃 enable_r19/r19_sensitivity/disabled_typos）。
    # 注意：typos 必须**增量合并**（dict.update）而非整体替换——客户端通常只传
    # 1~2 条增改，整体替换会把规则库其余几百条用户词条清空。
    cur = engine.load_rules_config()
    for k in ("conflicts", "ignores", "template"):
        if k in cfg:
            cur[k] = cfg[k]
    if isinstance(cfg.get("typos"), dict):
        merged = dict(cur.get("typos") or {})
        merged.update(cfg["typos"])   # 新增/修改；不删除既有词条
        cur["typos"] = merged
    engine.save_rules_config(cur)
    _reload_engine_rules()
    return _envelope(True, "OK", {k: cur.get(k) for k in ("typos", "conflicts", "ignores", "template")},
                    "规则已更新，下次请求自动生效")


# ----------------------------- OCR（可选） -----------------------------
# 图片 OCR 端点（POST /api/v1/ocr、/api/v1/ocr/base64）已于 2026-09-30 拆分至
# server/routes/route_ocr.py；推理锁与大小上限见 server/ocr_runtime.py。

# RIS/PACS 端点（/api/v1/ris/*）已于 2026-09-30 拆分至 server/routes/route_ris.py，
# 并经 app.include_router 注册（见文件末尾）。轮询状态见 server/ris_runtime.py。



@router.post("/api/v1/qc/export-report")
def qc_report_export(req: QcReportExportReq, emp: str = Depends(require_emp_local), _lic: bool = Depends(require_license_active)):
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
        raise HTTPException(500, type(exc).__name__)




@router.get("/api/v1/qc/rules/config")
def qc_rules_config_get(emp: str = Depends(require_emp_local)):
    """返回可编辑的规则配置：R8 错别字表 / R9 矛盾对 / 忽略词 / 模板规范。"""
    return _envelope(True, "OK", engine.load_rules_config())


@router.put("/api/v1/qc/rules/config")
def qc_rules_config_put(cfg: Dict[str, Any], emp: str = Depends(require_admin)):
    """合并保存规则配置（读→合并→写回；2026-08-18 M3 修复：保留未提交的
    enable_r19/r19_sensitivity/disabled_typos 等键，避免半量覆盖丢数据）。"""
    try:
        cur = engine.load_rules_config()
        for k, v in cfg.items():
            cur[k] = v
        engine.save_rules_config(cur)
        _reload_engine_rules()
        return _envelope(True, "OK", engine.load_rules_config(), "规则配置已保存")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)


@router.post("/api/v1/qc/rules/config/reset")
def qc_rules_config_reset(emp: str = Depends(require_admin)):
    """恢复出厂默认规则库（覆盖用户自定义）。"""
    try:
        cfg = engine.default_rules_config()
        engine.save_rules_config(cfg)
        return _envelope(True, "OK", engine.load_rules_config(), "已恢复默认规则库")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)




@router.post("/api/v1/qc/rules/learn-typo")
def qc_rules_learn_typo(req: LearnTypoReq, emp: str = Depends(require_admin)):
    """P0 修正反馈闭环：用户确认的「错词→正确词」写入规则库，下次自动识别。"""
    ok = engine.learn_typo(req.wrong, req.correct)
    if not ok:
        raise HTTPException(400, "无效的错字对（为空或已存在反向冲突）")
    return _envelope(True, "OK", None, f"已学习错字对：{req.wrong}→{req.correct}")


# ----------------------------- 错别字词库可视化维护（P0：增删改/批量导入/启停单条） -----------------------------




@router.post("/api/v1/qc/rules/typos")
def qc_rules_typo_add(req: TypoItemReq, emp: str = Depends(require_admin)):
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
    return _envelope(True, "OK", None, f"已新增错字对：{req.wrong}→{req.correct}")


@router.post("/api/v1/qc/rules/typos/toggle")
def qc_rules_typo_toggle(req: TypoItemReq, emp: str = Depends(require_admin)):
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
    return _envelope(True, "OK", {"wrong": wrong, "enabled": enabled}, f"{msg}错字词条「{wrong}」")


@router.post("/api/v1/qc/rules/typos/delete")
def qc_rules_typo_delete(req: TypoItemReq, emp: str = Depends(require_admin)):
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
    return _envelope(True, "OK", None, f"已删除错字词条「{wrong}」")


@router.post("/api/v1/qc/rules/typos/batch-import")
def qc_rules_typo_batch_import(req: TypoBatchImportReq, emp: str = Depends(require_admin)):
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
    return _envelope(True, "OK", {"ok": ok_n, "bad": bad_n, "bad_items": bad_items[:20]},
                     f"批量导入完成：成功 {ok_n} 条，跳过 {bad_n} 条")


@router.post("/api/v1/qc/rules/scan-reports")
def qc_rules_scan_reports(emp: str = Depends(require_admin)):
    """P0 历史报告词频学习：扫描样本库，自动发现候选错字对，供一键采纳。"""
    try:
        cands = engine.scan_reports_for_typos()
        return _envelope(True, "OK", {"candidates": cands}, f"发现 {len(cands)} 个候选错字")
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)
