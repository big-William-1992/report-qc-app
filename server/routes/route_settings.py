"""
route_settings.py — 星衍放射质控 API 路由

"""

from fastapi import APIRouter, Depends
from typing import Dict, Any
from server.deps import _DEFAULT_SETTINGS, _envelope, require_emp_local, _settings_path
import json

router = APIRouter(tags=['settings'])

@router.get("/api/v1/settings")
def settings_get(emp: str = Depends(require_emp_local)):
    data = dict(_DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data.update(json.load(fh) or {})
    except Exception:
        pass
    return _envelope(True, "OK", data)


@router.put("/api/v1/settings")
def settings_put(cfg: Dict[str, Any], emp: str = Depends(require_emp_local)):
    data = dict(_DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data.update(json.load(fh) or {})
    except Exception:
        pass
    for k in _DEFAULT_SETTINGS:          # 只接受已知键，避免写入杂项
        if k == "shortcuts":
            continue                     # shortcuts 走下方逐条合并，避免整体覆盖
        if k in cfg:
            data[k] = cfg[k]
    # shortcuts 为嵌套字典：以「默认值+已持久化」为基线，逐条合并已知动作键，
    # 避免只重绑某一个动作时丢失其它动作（如只改 run_qc 却把 save_sample 清零）
    if isinstance(cfg.get("shortcuts"), dict):
        known = set((_DEFAULT_SETTINGS.get("shortcuts") or {}).keys())
        cur = dict(data.get("shortcuts") or {})
        for act, sc in cfg["shortcuts"].items():
            if act in known:
                cur[act] = sc
        data["shortcuts"] = cur
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return _envelope(True, "OK", data, "设置已保存")
