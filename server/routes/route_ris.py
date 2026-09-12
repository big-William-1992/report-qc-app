"""
route_ris.py — 星衍放射质控 API 路由：RIS 直连（遗留兼容）

保留 RIS 数据库直连能力（驱动检测、连接测试、手动拉取）供调试/兼容使用。
主线数据获取已切换为 PACS 推送模式（见 route_push.py）。
"""

from fastapi import APIRouter, Depends, Query
from server.schemas import RisConfigReq
from server.deps import _envelope, require_emp_local
import ris

router = APIRouter(tags=['ris'])

@router.get("/api/v1/ris/drivers")
def ris_drivers(emp: str = Depends(require_emp_local)):
    """返回支持的数据库驱动列表及可用性。"""
    drivers = []
    for dtype in ("sqlserver", "oracle", "mysql", "postgresql"):
        ok, mod, msg = ris.driver_available(dtype)
        drivers.append({"type": dtype, "available": ok, "module": mod or "", "message": msg})
    return _envelope(True, "OK", drivers)


@router.post("/api/v1/ris/test-connection")
def ris_test_connection(req: RisConfigReq, emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query_sql": req.query_sql,
    }
    ok, msg = ris.test_connection(config)
    return _envelope(True, "OK", {"ok": ok, "message": msg})


@router.post("/api/v1/ris/fetch-reports")
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