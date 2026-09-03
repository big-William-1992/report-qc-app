"""
report_qc_app/server/main.py
星衍放射质控软件 — HTTP/REST 服务（对应《接口文档_HTTP_REST.md》规范，v3.0）

设计要点（与桌面端一致）：
- 完全离线优先：/qc/* 与 /samples/* 仅依赖标准库引擎（engine/accounts/samplelib）；
  /ocr 为可选能力，懒加载，缺 RapidOCR 依赖时返回 503（不影响 /qc 主流程）。
- 责任到人：所有写入类接口必须携带操作员工号——内网用 `X-Emp-Id` 头，
  公网用 `Authorization: Bearer <token>`（由 /accounts/login 签发，HMAC 签名，零额外依赖）。
- OCR 与质检解耦：/qc/check 纯 CPU 标准库，可多副本水平扩展；/ocr 仅在具备
  显示/模型环境的节点启用。

启动：
    cd report_qc_app
    pip install -r server/requirements.txt
    uvicorn server.main:app --host 0.0.0.0 --port 8000
"""
import os
import sys
import io
import time
import hmac
import hashlib
import base64
import json
import threading

from dotenv import load_dotenv
load_dotenv()  # 自动加载项目根目录 .env 文件
from pathlib import Path
from typing import Optional, List, Dict, Any

def _bundle_root() -> str:
    """资源（server/web/assets/src）在磁盘上的根目录。

    ⚠️ 引导期函数：此处尚不能 import paths（src 未进 sys.path），必须本地实现。
    逻辑与 src/paths.py 的 bundle_root() 完全一致，修改时两处必须同步。

    - 普通源码运行：本文件位于 <root>/server/main.py，root = 其上级目录。
    - PyInstaller 冻结（单目录/单文件）：资源随 exe 平铺在 exe 所在目录（单目录）
      或解压到 sys._MEIPASS（单文件），而非从 __file__ 回溯（冻结后 __file__ 指向
      PYZ 归档内的合成路径，回溯会得到不存在的目录，导致静态资源挂载失败）。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return str(Path(__file__).resolve().parent.parent)


# 把 src 加入 sys.path，便于 import engine / accounts / samplelib
_APP_ROOT = _bundle_root()
_SERVER = os.path.join(_APP_ROOT, "server") if getattr(sys, "frozen", False) \
    else str(Path(__file__).resolve().parent)
_SRC = os.path.join(_APP_ROOT, "src") if getattr(sys, "frozen", False) \
    else str(Path(__file__).resolve().parent.parent / "src")
# 项目根（server 的父目录 / 冻结后的 exe 所在目录）：用于 `from server import db`
# 这类「包内绝对导入」在源码与冻结两种模式下都能无歧义解析。
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 把 server 自身目录加入 path（双保险，便于裸 import 兜底）
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from server.schemas import (FindingOut, CheckReq, BatchItem, BatchReq, AccountCreate, LoginReq, SampleCreate, OCRB64, RisConfigReq, SampleExportReq, SampleImportReq, RisPollConfigReq, RoleReq, PwdReq, DeptReq, DeptCreateReq, ActivateReq, SampleReportExportReq, QcReportExportReq, QueueItemReq, ScreenRegion, ScreenOCRReq, OCRMetaReq, LearnTypoReq, TypoItemReq, TypoBatchImportReq)
from server.deps import SECRET, TOKEN_TTL, _BASIC_TITLES, _DEFAULT_SETTINGS, _FINDINGS_TITLES, _IMPRESSION_TITLES, _OCR_CACHE, _OCR_CACHE_MAX, _OCR_LOCK, _OCR_MAX_BYTES, _POLL_DEFAULT, _POLL_PATH, _SCORE_EN, _SEV_MAP, _SEV_RANK, _SHOT, _SHOT_MAX_W, _appdata_dir, _emp_from_auth, _eng_scores, _envelope, _grab_fullscreen, _load_queue, _ocr_config_path, _poll_config, _poll_path, _queue_add_text, _queue_path, _ris_poll_once, _run_qc, _save_poll_config, _save_queue, _settings_path, _split_dynamic, _strip_title, _worst_sev, datetime_now_iso, make_token, require_admin, require_emp, require_emp_local, verify_token

import engine
import ris
import accounts
import samplelib
from server import license_web
import ocr_provider
from version import APP_VERSION
from server import db  # SQLAlchemy 统一数据层（users/departments/queue/settings）

# E2E 测试隔离：QC_DB_OVERRIDE 指向临时 sqlite 路径时，账号/队列等全部落该临时库，
# 避免污染真实数据（accounts._DB_OVERRIDE 在每次建表前被消费）。
_qc_db_override = os.environ.get("QC_DB_OVERRIDE", "").strip()
if _qc_db_override:
    accounts._DB_OVERRIDE = os.path.abspath(_qc_db_override)

# 启动时确保表就绪（多用户/科室自托管核心表）
db.init_db()

# ----------------------------- 应用 -----------------------------
app = FastAPI(title="星衍放射质控 API", version=APP_VERSION)

# CORS 白名单：默认只放行桌面端 WebView 的本地源（127.0.0.1 / localhost，任意端口）。
# 禁止通配 "*"：否则任何网页（含恶意站点）都能向本机特权端点发请求（CSRF 面，见
# require_emp_local 的本地放行）。内网/远程部署需要放行其它源时，用环境变量追加：
#   QC_CORS_ORIGINS=http://192.168.1.10:8000,http://qc.example.com
_cors_origins = [o.strip() for o in os.environ.get("QC_CORS_ORIGINS", "").split(",") if o.strip()]
_LOCAL_ORIGIN_RE = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_LOCAL_ORIGIN_RE,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 引擎 score_summary 输出中文维度键；前端（app.js）样本列表直接读英文键。
# 在需要英文键的端点做一层翻译，qc/check 的实时结果由前端自行映射（幂等）。


# 引擎 Finding.severity 用 high/medium/low；看板 by_severity 用 critical/warning/info。


# ----------------------------- 静态前端托管（SPA） -----------------------------
# 单服务同时提供 REST API 与同一套 SPA 前端，桌面 WebView 壳与浏览器共用。
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os as _os

# 静态目录：冻结后用 _APP_ROOT（exe 所在目录），否则用 __file__ 回溯。
_STATIC_DIR = _os.path.join(_APP_ROOT, "web", "static") if getattr(sys, "frozen", False) \
    else _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "web", "static"))
# 静态前端每次版本更新都直接改文件；若浏览器/WebView 命中强缓存会一直加载旧版
# app.js（曾因此出现「已修复 bodypart 仍报错」的假象）。统一给前端资源加 no-cache：
# 每次仍重新校验（ETag/Last-Modified），文件变了浏览器必然拉到新版本，无需手动改 ?v=。
class _NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return resp


if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", _NoCacheStaticFiles(directory=_STATIC_DIR), name="static")

# ---- 注册路由子包 ----
from server.routes import route_qc
from server.routes import route_ocr
from server.routes import route_ris
from server.routes import route_account
from server.routes import route_license
from server.routes import route_sample
from server.routes import route_queue
from server.routes import route_screen
from server.routes import route_settings
from server.routes import route_push

app.include_router(route_qc.router)
app.include_router(route_ocr.router)
app.include_router(route_ris.router)
app.include_router(route_account.router)
app.include_router(route_license.router)
app.include_router(route_sample.router)
app.include_router(route_queue.router)
app.include_router(route_screen.router)
app.include_router(route_settings.router)
app.include_router(route_push.router)


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # SPA 路由兜底：非 API / static / 文档 的请求一律返回 index.html
    if full_path.startswith(("api/", "static/", "docs", "openapi", "redoc")):
        raise HTTPException(404, "Not Found")
    index = _os.path.join(_STATIC_DIR, "index.html")
    if _os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(404, "前端未找到")


if __name__ == "__main__":
    import argparse
    import uvicorn
    # 默认只绑定本机回环，与桌面壳(127.0.0.1)一致，避免源码启动即暴露到局域网。
    # 内网多机访问请显式：python server/main.py --host 0.0.0.0
    _p = argparse.ArgumentParser(description="星衍放射质控 API 服务")
    _p.add_argument("--host", default=os.environ.get("QC_HOST", "127.0.0.1"))
    _p.add_argument("--port", type=int, default=int(os.environ.get("QC_PORT", "8000")))
    _args = _p.parse_args()
    uvicorn.run(app, host=_args.host, port=_args.port)