"""
route_stats.py — 质控统计（2026-09-30 路由拆分第一片）

从 `server/main.py` 拆出，端点实现**逐字搬迁**（不做行为改动）：

  GET /api/v1/stats/error-types  错误类型分布
  GET /api/v1/stats/trend        按日期趋势
  GET /api/v1/stats/report       质控问题分类统计报表（时间段 + TOP 榜 + 排行榜）

数据隔离：`_scope_user_id(emp)` —— admin/本机看全部，普通医生仅看本人（2026-08-18）。
该助手已下沉到 `server/core.py`，故本模块无需 import main。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from server.core import _envelope, _scope_user_id
from server.security import require_emp_local

import samplelib

router = APIRouter(tags=["stats"])


@router.get("/api/v1/stats/error-types")
def stats_error_types(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_error_type(
        user_id=_scope_user_id(emp)))


@router.get("/api/v1/stats/trend")
def stats_trend(emp: str = Depends(require_emp_local)):
    return _envelope(True, "OK", samplelib.stats_by_date(
        user_id=_scope_user_id(emp)))


@router.get("/api/v1/stats/report")
def stats_report(start: Optional[str] = None, end: Optional[str] = None,
                 emp: str = Depends(require_emp_local)):
    """质控问题分类统计报表（时间段筛选 + 问题类型 TOP 榜 + 科室/医生排行榜）。

    start / end 格式 YYYY-MM-DD，缺省不限。
    """
    try:
        data = samplelib.stats_report(start=start, end=end,
                                      user_id=_scope_user_id(emp))
        return _envelope(True, "OK", data)
    except Exception as exc:
        raise HTTPException(500, type(exc).__name__)
