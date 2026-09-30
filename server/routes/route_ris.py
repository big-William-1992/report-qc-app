"""
route_ris.py — RIS/PACS 直连与主动轮询（2026-09-30 路由拆分 S4）

从 `server/main.py` 迁出，端点实现**逐字搬迁**：

  GET  /api/v1/ris/config · /drivers · /poll-status
  PUT  /api/v1/ris/config · /poll-config
  POST /api/v1/ris/test-connection · /fetch-reports · /poll-now

轮询状态与引擎在 `server/ris_runtime.py`（**锁必须共用**，见该模块说明）。
"""

from fastapi import APIRouter, Depends, Query

from server.core import _envelope
from server.ris_runtime import (RIS_POLL_LOCK, poll_config, ris_poll_once,   # noqa: F401
                                save_poll_config)
from server.security import require_admin, require_emp_local
from server.schemas import RisConfigReq, RisPollConfigReq

import ris

def _lg(where: str) -> None:
    """降级留痕（局部导入，零模块级依赖）。"""
    try:
        try:
            from ..log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(where)
    except Exception:   # silent-except-ok: 观测层自身失败绝不能抛
        pass


router = APIRouter(tags=["ris"])

@router.get("/api/v1/ris/config")
def ris_config_get(emp: str = Depends(require_emp_local)):
    """读取已保存的 RIS 连接配置（password 脱敏不回传）。
    2026-08-18 新增：此前 save_config 仅被 Tkinter 版调用，SPA 配置后轮询线程读不到。"""
    cfg = dict(ris.load_config())
    if cfg.get("password"):
        cfg["password"] = "******"
    return _envelope(True, "OK", cfg)


@router.put("/api/v1/ris/config")
def ris_config_put(req: RisConfigReq, emp: str = Depends(require_admin)):
    """保存 RIS 连接配置（轮询/拉取复用；管理权限）。"""
    cfg = ris.load_config()
    cfg.update({
        "db_type": req.db_type or "sqlserver",
        "host": req.host or "",
        "port": req.port or "",
        "database": req.database or "",
        "user": req.user or "",
        "password": req.password or cfg.get("password", ""),  # 未填则保留原值
        "query": req.query_sql or cfg.get("query", ""),
    })
    ris.save_config(cfg)
    return _envelope(True, "OK", {}, "RIS 连接配置已保存")


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
        "password": req.password, "query": req.query_sql,
    }
    ok, msg = ris.test_connection(config)
    return _envelope(True, "OK", {"ok": ok, "message": msg})


@router.post("/api/v1/ris/fetch-reports")
def ris_fetch_reports(req: RisConfigReq, limit: int = Query(50, ge=1, le=200),
                      emp: str = Depends(require_emp_local)):
    config = {
        "db_type": req.db_type, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user,
        "password": req.password, "query": req.query_sql,
    }
    try:
        reports = ris.fetch_reports(config, limit=limit)
    except Exception as exc:
        # 统一错误封装（与 poll-now 失败路径对齐），避免 FastAPI 默认 500 破坏前端 data.ok 判定。
        # 2026-09-30：同时**服务端留痕** —— 前端只看到错误名，医院现场排查全靠日志。
        _lg(__name__)
        return _envelope(False, "RIS_ERR", {}, f"RIS 拉取失败：{type(exc).__name__}：{exc}")
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




@router.get("/api/v1/ris/poll-status")
def ris_poll_status(emp: str = Depends(require_emp_local)):
    cfg = poll_config()
    return _envelope(True, "OK", {
        "enabled": cfg.get("enabled", False),
        "interval_min": cfg.get("interval_min", 30),
        "limit": cfg.get("limit", 50),
        "auto_qc": cfg.get("auto_qc", True),
        "auto_enqueue": cfg.get("auto_enqueue", True),
        "last_run": cfg.get("last_run", ""),
        "last_count": cfg.get("last_count", 0),
        "last_error": cfg.get("last_error", ""),
        "seen_count": len(cfg.get("seen") or []),
    })


@router.put("/api/v1/ris/poll-config")
def ris_poll_config_put(req: RisPollConfigReq, emp: str = Depends(require_emp_local)):
    cfg = poll_config()
    if req.enabled is not None:
        cfg["enabled"] = bool(req.enabled)
    if req.interval_min is not None:
        cfg["interval_min"] = max(5, min(int(req.interval_min), 1440))
    if req.limit is not None:
        cfg["limit"] = max(5, min(int(req.limit), 200))
    if req.auto_qc is not None:
        cfg["auto_qc"] = bool(req.auto_qc)
    if req.auto_enqueue is not None:
        cfg["auto_enqueue"] = bool(req.auto_enqueue)
    save_poll_config(cfg)
    return _envelope(True, "OK", ris_poll_status(emp), "轮询配置已保存")


@router.post("/api/v1/ris/poll-now")
def ris_poll_now(emp: str = Depends(require_emp_local)):
    """手动立即触发一次轮询（不依赖 enabled 开关，便于配置后首跑验证）。"""
    try:
        result = ris_poll_once(manual=True)
        return _envelope(True, "OK", result)
    except Exception as exc:
        _lg(__name__)   # 手动轮询失败留痕（前端只看到 POLL_ERR 代码）
        return _envelope(False, "POLL_ERR", {"error": type(exc).__name__}, f"轮询失败：{exc}")


