"""
route_license.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from server.schemas import ActivateReq
from server.deps import _envelope, _appdata_dir
from server import license_web
import accounts

router = APIRouter(tags=['license'])

@router.get("/api/v1/license/status")
def license_status_get():
    """前端闸门用：免责/激活/试用剩余天数/机器码/账号数。"""
    return _envelope(True, "OK",
                      license_web.license_status(_appdata_dir(), accounts.count_accounts()))


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
                         license_web.license_status(_appdata_dir(), accounts.count_accounts()),
                         "激活码无效，请检查后重试")
    return _envelope(True, "OK",
                      license_web.license_status(_appdata_dir(), accounts.count_accounts()),
                      "激活成功")
