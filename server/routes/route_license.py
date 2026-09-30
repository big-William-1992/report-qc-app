"""
route_license.py — 授权/试用/免责（2026-09-30 路由拆分 S2）

从 `server/main.py` 迁出，端点实现**逐字搬迁**（不做行为改动），仅一处按项目规约
补齐静默吞异常：

  GET  /api/v1/license/status        前端闸门用：免责/激活/试用剩余/机器码/账号数
  GET  /api/v1/license/disclaimer    免责声明文本
  POST /api/v1/license/disclaimer    接受免责声明
  GET  /api/v1/license/machine-code  机器码（用于离线激活）
  POST /api/v1/license/activate      激活码激活

**这些端点均为公开（无需登录）**：登录/激活本身就是闸门流程的一部分，
若要求先登录会形成死锁（新装用户无法激活）。

拆分约定见 docs/ROUTES_SPLIT_PLAN.md：
  1. 每个路由模块都必须被 main.py `include_router`（有守卫测试）；
  2. 拆出后必须删掉 main.py 里的原实现，避免同路径两份处理器；
  3. 鉴权一律来自 server.security。
"""
from fastapi import APIRouter

from server.core import _appdata_dir, _envelope
from server.schemas import ActivateReq

import accounts
import license_web

try:
    import license_utils as _lu
except ImportError:  # pragma: no cover - 包式导入兜底
    from src import license_utils as _lu  # type: ignore

router = APIRouter(tags=["license"])


@router.get("/api/v1/license/status")
def license_status_get():
    """前端闸门用：免责/激活/试用剩余天数/机器码/账号数 + 扩展信息。"""
    base = license_web.license_status(_appdata_dir(), accounts.count_accounts())
    # 合并扩展信息（试用告警/到期日期/授权类型）。
    # 2026-09-30：原为 `except Exception: pass`（静默吞掉）。按本仓"降级必须留痕"的
    # 规约改为 log_quiet —— 它是**降级**（扩展信息拿不到时前端少几个字段），
    # 不是无害分支；静默会让"授权信息显示不全"无从排查。
    try:
        ext = _lu.get_license_info()
        for k in ("trial_days_remaining", "trial_warning", "license_type",
                  "expires_at", "licensed_to", "seat_count"):
            if k not in base and k in ext:
                base[k] = ext[k]
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return _envelope(True, "OK", base)


@router.get("/api/v1/license/disclaimer")
def license_disclaimer_text():
    return _envelope(True, "OK", {"text": license_web.disclaimer_text()})


@router.post("/api/v1/license/disclaimer")
def license_disclaimer_accept():
    license_web.accept_disclaimer(_appdata_dir())
    return _envelope(True, "OK", {"disclaimer_accepted": True})


@router.get("/api/v1/license/machine-code")
def license_machine_code():
    return _envelope(True, "OK", {"machine_id": license_web.machine_id()})


@router.post("/api/v1/license/activate")
def license_activate(req: ActivateReq):
    ok = license_web.activate(_appdata_dir(), req.code)
    if not ok:
        return _envelope(False, "ERR",
                         license_web.license_status(_appdata_dir(),
                                                    accounts.count_accounts()),
                         "激活码无效，请检查后重试")
    return _envelope(True, "OK",
                     license_web.license_status(_appdata_dir(),
                                                accounts.count_accounts()),
                     "激活成功")
