"""
/api/qc/* — 报告质控引擎 API
将 engine.py / ocr_provider.py 的能力封装为 REST 接口。
"""

import json
from flask import Blueprint, request, jsonify

import engine
import samplelib

qc_bp = Blueprint("qc", __name__)


@qc_bp.route("/run", methods=["POST"])
def run_qc():
    """运行质控引擎。接收报告文本 + 元信息，返回 findings + 评分。

    POST body:
    {
        "report_text": "胸廓对称，气管居中...",
        "findings": "右肺上叶见斑片状密度增高影...",
        "impression": "右肺上叶炎性病变可能",
        "meta": { "patient": "...", "gender": "男", "age": "52", ... }
    }
    """
    data = request.get_json(force=True)
    report_text = data.get("report_text", "")
    findings = data.get("findings", "")
    impression = data.get("impression", "")
    meta = data.get("meta") or {}

    # 合并全文（与桌面版 app.py 行为一致）
    full_text = report_text
    if findings:
        full_text = f"{full_text}\n{findings}"
    if impression:
        full_text = f"{full_text}\n{impression}"

    try:
        e = engine.RuleEngine()
        result_findings = e.run(full_text, meta)
        sc = engine.score(result_findings)

        return jsonify({
            "success": True,
            "data": {
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "severity": f.severity,
                        "category": f.category,
                        "message": f.message,
                        "line": f.line,
                        "col": f.col,
                    }
                    for f in result_findings
                ],
                "scores": dict(sc),
                "report_text": full_text,
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@qc_bp.route("/rules", methods=["GET"])
def list_rules():
    """返回当前加载的规则列表（供规则维护页 / 前端展示用）。"""
    e = engine.RuleEngine()
    cfg = e.rules_config or {}
    # 从配置中提取规则元信息
    rule_meta = [
        {"rule_id": "R1",  "name": "性别一致性检查",       "category": "完整性",   "severity": "warning", "enabled": True},
        {"rule_id": "R2",  "name": "侧别标注检查",         "category": "规范性",   "severity": "warning", "enabled": True},
        {"rule_id": "R3",  "name": "评分单位规范",         "category": "规范性",   "severity": "info",    "enabled": True},
        {"rule_id": "R4",  "name": "单位实体识别",         "category": "准确性",   "severity": "warning", "enabled": True},
        {"rule_id": "R5",  "name": "描述与结论一致性",     "category": "准确性",   "severity": "critical","enabled": True},
        {"rule_id": "R6",  "name": "检查部位完整性",       "category": "完整性",   "severity": "warning", "enabled": True},
        {"rule_id": "R7",  "name": "内部结构完整性",       "category": "完整性",   "severity": "info",    "enabled": True},
        {"rule_id": "R8",  "name": "错别字检测",           "category": "准确性",   "severity": "warning", "enabled": True},
        {"rule_id": "R9",  "name": "矛盾信息检测",         "category": "准确性",   "severity": "critical","enabled": True},
        {"rule_id": "R10", "name": "模板符合度检查",       "category": "规范性",   "severity": "warning", "enabled": True},
        {"rule_id": "R11", "name": "上下文合理性",         "category": "准确性",   "severity": "warning", "enabled": True},
        {"rule_id": "R12", "name": "句子级质量评估",       "category": "规范性",   "severity": "info",    "enabled": True},
        {"rule_id": "R14", "name": "跨区域交叉验证",       "category": "准确性",   "severity": "warning", "enabled": True},
        {"rule_id": "R15", "name": "内部术语规范化",       "category": "规范性",   "severity": "info",    "enabled": True},
        {"rule_id": "R16", "name": "随访时限缺失",         "category": "及时性",   "severity": "info",    "enabled": cfg.get("enable_r16", False)},
    ]
    return jsonify({"success": True, "data": rule_meta})


@qc_bp.route("/score/stats", methods=["GET"])
def score_stats():
    """返回样本库中的评分统计（供看板页使用）。"""
    db_path = samplelib._default_db_path()
    rows = samplelib.list_samples_full(db_path)
    stats = {"total": len(rows), "by_severity": {}, "score_avg": {}}
    acc_sum, comp_sum, norm_sum, tim_sum = 0, 0, 0, 0
    for row in rows:
        scores = json.loads(row.get("scores_json") or "{}")
        acc_sum += scores.get("accuracy", 0)
        comp_sum += scores.get("completeness", 0)
        norm_sum += scores.get("normalization", 0)
        tim_sum += scores.get("timeliness", 0)
        sev = scores.get("worst_severity", "info")
        stats["by_severity"][sev] = stats["by_severity"].get(sev, 0) + 1
    n = len(rows) or 1
    stats["score_avg"] = {
        "accuracy": round(acc_sum / n, 1),
        "completeness": round(comp_sum / n, 1),
        "normalization": round(norm_sum / n, 1),
        "timeliness": round(tim_sum / n, 1),
    }
    return jsonify({"success": True, "data": stats})
