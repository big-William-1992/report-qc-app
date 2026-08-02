"""
/api/samples/* — 样本库管理 API
将 samplelib.py 的导出/导入/列表能力封装为 REST 接口。
"""

import os
import sys
import json
from flask import Blueprint, request, jsonify, send_file

import samplelib

samples_bp = Blueprint("samples", __name__)


@samples_bp.route("/list", methods=["GET"])
def list_samples():
    """返回样本库明细（支持分页 / 筛选）。

    GET params: page, per_page, modality, severity
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    modality = request.args.get("modality", "")
    severity = request.args.get("severity", "")

    db_path = samplelib._default_db_path()
    rows = samplelib.list_samples_full(db_path)

    # 服务端简单筛选
    if modality:
        rows = [r for r in rows if r.get("modality") == modality]
    if severity:
        rows = [r for r in rows if (r.get("scores_json") or "").find(severity) >= 0]

    total = len(rows)
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = rows[start:end]

    items = []
    for r in page_rows:
        scores = json.loads(r.get("scores_json") or "{}")
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

    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@samples_bp.route("/export", methods=["POST"])
def export_samples():
    """导出样本库为 CSV 或 JSON。

    POST body: { "fmt": "csv" | "json", "path": "/tmp/export.csv" }
    """
    data = request.get_json(force=True)
    fmt = data.get("fmt", "csv")
    path = data.get("path", "")

    if not path:
        # 自动生成路径
        base = os.path.expanduser("~") if sys.platform != "win32" else os.environ.get(
            "LOCALAPPDATA", os.path.expanduser("~")
        )
        path = os.path.join(base or "/tmp", f"星衍质控_样本导出.{fmt}")

    try:
        result_path = samplelib.export_samples(path=path, fmt=fmt)
        return jsonify({"success": True, "data": {"path": result_path}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@samples_bp.route("/import", methods=["POST"])
def import_samples():
    """导入样本库文件。

    POST body: { "path": "/tmp/import.csv" }
    """
    data = request.get_json(force=True)
    path = data.get("path", "")

    try:
        inserted, skipped = samplelib.import_samples(path)
        return jsonify({
            "success": True,
            "data": {"inserted": inserted, "skipped": skipped},
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@samples_bp.route("/stats/dashboard", methods=["GET"])
def dashboard_stats():
    """返回看板页所需的聚合统计。"""
    db_path = samplelib._default_db_path()
    rows = samplelib.list_samples_full(db_path)

    total = len(rows)
    by_modality = {}
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    today = 0
    this_week = 0

    from datetime import datetime, timedelta
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    today_str = now.strftime("%Y-%m-%d")

    for row in rows:
        mod = row.get("modality", "未知")
        by_modality[mod] = by_modality.get(mod, 0) + 1

        scores = json.loads(row.get("scores_json") or "{}")
        worst = scores.get("worst_severity", "info")
        if worst not in by_severity:
            worst = "info"
        by_severity[worst] = by_severity.get(worst, 0) + 1

        ts = row.get("ts", "")
        if ts and ts.startswith(today_str):
            today += 1
        if ts and ts > week_ago.strftime("%Y-%m-%d"):
            this_week += 1

    return jsonify({
        "success": True,
        "data": {
            "total": total,
            "today": today,
            "this_week": this_week,
            "by_modality": by_modality,
            "by_severity": by_severity,
        },
    })
