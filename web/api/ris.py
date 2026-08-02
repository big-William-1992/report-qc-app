"""
/api/ris/* — RIS/PACS 数据库直连 API
将 ris.py 的连接/拉取能力封装为 REST 接口。
"""

import json
from flask import Blueprint, request, jsonify

import ris

ris_bp = Blueprint("ris", __name__)


@ris_bp.route("/drivers", methods=["GET"])
def list_drivers():
    """返回支持的数据库驱动列表及可用性。"""
    drivers = []
    for dtype in ("sqlserver", "oracle", "mysql", "postgresql"):
        ok, mod, msg = ris.driver_available(dtype)
        drivers.append({"type": dtype, "available": ok, "module": mod or "", "message": msg})
    return jsonify({"success": True, "data": drivers})


@ris_bp.route("/test-connection", methods=["POST"])
def test_connection():
    """测试 RIS 数据库连接。

    POST body: { "db_type": "sqlserver", "host": "...", "port": 1433,
                 "database": "...", "user": "...", "password": "..." }
    """
    data = request.get_json(force=True)
    try:
        config = ris.RisConfig(
            db_type=data.get("db_type", "sqlserver"),
            host=data.get("host", ""),
            port=data.get("port", 0),
            database=data.get("database", ""),
            user=data.get("user", ""),
            password=data.get("password", ""),
            query_sql=data.get("query_sql", ""),
        )
        ok, msg = ris.test_connection(config)
        return jsonify({"success": True, "data": {"ok": ok, "message": msg}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@ris_bp.route("/fetch-reports", methods=["POST"])
def fetch_reports():
    """从 RIS 拉取报告列表。

    POST body: { 同 test_connection + "limit": 50 }
    """
    data = request.get_json(force=True)
    limit = data.get("limit", 50)
    try:
        config = ris.RisConfig(
            db_type=data.get("db_type", "sqlserver"),
            host=data.get("host", ""),
            port=data.get("port", 0),
            database=data.get("database", ""),
            user=data.get("user", ""),
            password=data.get("password", ""),
            query_sql=data.get("query_sql", ""),
        )
        reports = ris.fetch_reports(config, limit=limit)
        items = []
        for r in (reports or []):
            items.append({
                "report_text": r.get("report_text", "")[:500],
                "patient": r.get("patient", ""),
                "gender": r.get("gender", ""),
                "age": r.get("age", ""),
                "modality": r.get("modality", ""),
                "applied_site": r.get("applied_site", ""),
                "ts": r.get("ts", ""),
            })
        return jsonify({"success": True, "data": {"items": items, "count": len(items)}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
