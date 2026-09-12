"""server/static_spa.py — SPA 静态资源托管与前端兜底。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def _bundle_root() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return str(Path(__file__).resolve().parent.parent)


_APP_ROOT = _bundle_root()
_STATIC_DIR = (
    os.path.join(_APP_ROOT, "web", "static")
    if getattr(sys, "frozen", False)
    else os.path.abspath(os.path.join(_APP_ROOT, "web", "static"))
)


class NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return resp


def mount_static_spa(app) -> Optional[str]:
    """挂载静态资源和 SPA 兜底；返回静态目录路径，目录缺失时返回 None。"""
    if not os.path.isdir(_STATIC_DIR):
        return None

    app.mount("/static", NoCacheStaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if not full_path.startswith(("api/", "docs", "openapi", "redoc")):
            candidate = os.path.normpath(os.path.join(_STATIC_DIR, full_path))
            if candidate.startswith(_STATIC_DIR) and os.path.isfile(candidate):
                return FileResponse(candidate, headers={
                    "Cache-Control": "no-cache, max-age=0, must-revalidate"
                })
        if full_path.startswith(("api/", "static/", "docs", "openapi", "redoc")):
            raise HTTPException(404, "Not Found")
        index = os.path.join(_STATIC_DIR, "index.html")
        if os.path.exists(index):
            return FileResponse(index)
        raise HTTPException(404, "前端未找到")

    return _STATIC_DIR
