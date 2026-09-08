"""server/deps.py — 共享依赖（从 server/main.py 抽离，勿手动编辑）。"""

import os
import sys
import io
import time
import hmac
import hashlib
import base64
import json
import threading
import uuid as _uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse

import engine
import ris
import accounts
import samplelib
from server import db

# LLM 引擎模式（环境变量控制）
QC_ENGINE = os.environ.get("QC_ENGINE", "rule")  # rule | api | local


SECRET = os.environ.get("QC_API_SECRET", "change-me-in-prod")
TOKEN_TTL = int(os.environ.get("QC_API_TTL", "86400"))  # 默认 24h
def make_token(emp_id: str, ttl: int = TOKEN_TTL) -> str:
    exp = int(time.time()) + ttl
    payload = f"{emp_id}.{exp}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()
def verify_token(tok: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        emp_id, exp = payload.rsplit(".", 1)
        if int(exp) < time.time():
            return None
        expect = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expect, sig):
            return emp_id
    except Exception:
        return None
    return None
def _emp_from_auth(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    tok = authorization
    if tok.lower().startswith("bearer "):
        tok = tok[7:]
    return verify_token(tok)
def require_emp(authorization: Optional[str] = Header(None),
                 x_emp_id: Optional[str] = Header(None)) -> str:
    """写操作鉴权：Bearer token 或内网 X-Emp-Id 头，二选一。
    回退到 X-Emp-Id 时必须校验工号真实存在，防止远程任意填头冒充他人。"""
    emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token> 或 X-Emp-Id 头")
    if not _emp_from_auth(authorization):
        # 仅当来自 X-Emp-Id 头（非 Bearer token）时校验工号存在性
        try:
            import src.accounts as _acct
            if not _acct.account_exists(emp):
                raise HTTPException(401, "X-Emp-Id 工号不存在")
        except HTTPException:
            raise
        except Exception:
            pass
    return emp
def require_emp_local(request: Request,
                      authorization: Optional[str] = Header(None),
                      x_emp_id: Optional[str] = Header(None)) -> str:
    """写操作鉴权（本地优先）：

    - 来自 127.0.0.1/::1 的调用（桌面端 WebView、浏览器同源 localhost）自动放行，
      避免 SPA 必须携带鉴权头，保持本地"双击即用"体验；
    - 公网/远程部署仍强制 Bearer token 或 X-Emp-Id，保持责任到人追溯。
    """
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        return (x_emp_id or "local").strip() or "local"
    emp = _emp_from_auth(authorization) or (x_emp_id or "").strip()
    if not emp:
        raise HTTPException(401, "缺少鉴权：Authorization: Bearer <token> 或 X-Emp-Id 头")
    return emp
def _envelope(ok: bool, code: str, data: Any, message: str = ""):
    return {"ok": ok, "code": code, "data": data, "message": message}
_SCORE_EN = {"准确性": "accuracy", "完整性": "completeness",
             "规范性": "normalization", "及时性": "timeliness"}
def _eng_scores(cn: dict) -> dict:
    """把引擎中文维度键映射为前端期望的英文键；未知键透传。"""
    out = {}
    for k, v in (cn or {}).items():
        out[_SCORE_EN.get(k, k)] = v
    return out
_SEV_MAP = {"high": "critical", "medium": "warning", "low": "info"}
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}
def _worst_sev(findings: list) -> str:
    """从 findings 推导最严重级别（high>medium>low）；无 findings 视为 low→info。"""
    worst = "low"
    for f in (findings or []):
        sv = f.get("severity", "low") if isinstance(f, dict) else getattr(f, "severity", "low")
        if _SEV_RANK.get(sv, 0) > _SEV_RANK.get(worst, 0):
            worst = sv
    return _SEV_MAP.get(worst, "info")
def _run_qc(report: str, meta: dict, auto_fix: bool) -> dict:
    if QC_ENGINE == "rule":
        eng = engine.RuleEngine()
    else:
        # LLM 模式（api=云端通义千问 / local=本地 ollama）
        try:
            import llm_engine
            eng = llm_engine.LLMEngine()
        except Exception:
            # LLM 引擎加载失败，降级到规则引擎
            eng = engine.RuleEngine()
    findings = eng.run(report, meta)
    score = engine.score_summary(engine.score(findings))
    data = {
        "findings": [f.__dict__ for f in findings],
        "score": score,
        "error_counts": engine.error_type_counts(findings),
        "fixed": None,
    }
    if auto_fix:
        fixed_text, n_fixed, n_manual, details = eng.auto_fix(report, findings)
        data["fixed"] = {"fixed_text": fixed_text, "n_fixed": n_fixed,
                         "n_manual": n_manual, "details": details}
    return data
_OCR_MAX_BYTES = int(os.environ.get("QC_OCR_MAX_BYTES", str(20 * 1024 * 1024)))
_POLL_PATH = None
def _poll_path() -> str:
    global _POLL_PATH
    if _POLL_PATH is None:
        _POLL_PATH = os.path.join(_appdata_dir(), "ris_poll.json")
    return _POLL_PATH
_POLL_DEFAULT = {
    "enabled": False,          # 轮询总开关
    "interval_min": 30,        # 拉取间隔（分钟）
    "limit": 50,               # 每次最多拉取条数
    "auto_qc": True,           # 拉取后自动质控入库
    "auto_enqueue": True,      # 同时进待质控队列（医师复核）
    "last_run": "",            # 上次成功运行时间（ISO）
    "last_count": 0,           # 上次新增数量
    "last_error": "",          # 最近一次错误信息
    "seen": [],                # 已处理报告正文 MD5 指纹（去重）
}
def _poll_config() -> dict:
    cfg = dict(_POLL_DEFAULT)
    cfg["seen"] = list(cfg["seen"])
    try:
        with open(_poll_path(), encoding="utf-8") as fh:
            data = json.load(fh) or {}
        for k in _POLL_DEFAULT:
            if k in data:
                cfg[k] = data[k]
    except Exception:
        pass
    return cfg
def _save_poll_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_poll_path()), exist_ok=True)
        with open(_poll_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
def _ris_poll_once(manual: bool = False) -> dict:
    """执行一次轮询：拉取 RIS → 质控 → 入库 + 入队。返回统计。"""
    cfg = _poll_config()
    config = ris.load_config()
    if not config.get("host"):
        raise RuntimeError("RIS 连接未配置，请在 RIS 直连页填写并测试连接")
    reports = ris.fetch_reports(config, limit=int(cfg.get("limit", 50)))
    new_reports = []
    if not reports:
        new_count = 0
    else:
        seen = set(cfg.get("seen") or [])
        new_reports = []
        for r in reports:
            norm = "".join((r.get("report_text") or "").split())
            if not norm:
                continue
            h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            new_reports.append(r)
        cfg["seen"] = list(seen)[-5000:]   # 仅保留最近 5000 指纹，防无限膨胀
        new_count = len(new_reports)
        emp_id = "ris-poll"
        if cfg.get("auto_qc"):
            for r in new_reports:
                try:
                    qc = _run_qc(r.get("report_text") or "", {
                        "patient": r.get("patient", ""), "gender": r.get("gender", ""),
                        "age": r.get("age", ""), "modality": r.get("modality", ""),
                        "applied_site": r.get("applied_site", ""),
                    }, False)
                    findings = []
                    for f in qc.get("findings") or []:
                        findings.append(engine.Finding(
                            rule_id=f.get("rule_id", ""), error_type=f.get("error_type", ""),
                            severity=f.get("severity", "low"), message=f.get("message", ""),
                            snippet=f.get("snippet", ""),
                            span=tuple(f.get("span", (-1, -1))),
                            suggestion=f.get("suggestion", "")))
                    samplelib.save_sample(
                        r.get("report_text") or "",
                        {"patient": r.get("patient", ""), "gender": r.get("gender", ""),
                         "age": r.get("age", ""), "modality": r.get("modality", ""),
                         "applied_site": r.get("applied_site", "")},
                        findings, qc.get("score") or {},
                        anonymize=False, user_id=emp_id)
                except Exception:
                    continue
        if cfg.get("auto_enqueue"):
            for r in new_reports:
                try:
                    _queue_add_text(r.get("report_text") or "",
                                    {"patient": r.get("patient", ""),
                                     "gender": r.get("gender", ""),
                                     "age": r.get("age", ""),
                                     "modality": r.get("modality", ""),
                                     "applied_site": r.get("applied_site", "")},
                                    source="RIS轮询")
                except Exception:
                    continue
    cfg["last_run"] = datetime_now_iso()
    cfg["last_count"] = new_count
    cfg["last_error"] = ""
    _save_poll_config(cfg)
    return {"count": new_count, "total_seen": len(cfg.get("seen") or []),
            "last_run": cfg["last_run"], "new_reports": new_reports[:5]}
def datetime_now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")
def _queue_add_text(text: str, meta: dict, source: str = "RIS轮询"):
    """复用 queue 去重逻辑（正文 MD5）。返回条目 id 或 None。"""
    norm = "".join((text or "").split())
    if not norm:
        return None
    h = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
    items = _load_queue()
    for it in items:
        if it.get("hash") == h:
            return it.get("id")
    m = meta or {}
    item = {
        "id": _uuid.uuid4().hex[:8], "hash": h,
        "patient": (m.get("patient", "") or "").strip(),
        "site": (m.get("applied_site", "") or "").strip(),
        "findings_desc": (m.get("findings_desc", "") or "").strip(),
        "diagnosis": (m.get("diagnosis", "") or "").strip(),
        "text": text, "source": source,
        "ts": time.strftime("%Y-%m-%d %H:%M"), "meta": m,
    }
    items.append(item)
    _save_queue(items)
    return item["id"]
def require_admin(authorization: Optional[str] = Header(None)) -> str:
    """管理员操作依赖：强制 Bearer token（不享受 localhost 放行，防止伪造 X-Emp-Id 提权）。"""
    emp = _emp_from_auth(authorization)
    if not emp:
        raise HTTPException(401, "管理员操作需登录（Bearer token）")
    if accounts.get_role(emp) != "admin":
        raise HTTPException(403, "需要管理员权限")
    return emp
def _appdata_dir() -> str:
    """跨平台数据目录（统一由 paths.user_data_dir 解析，实现队列互通）。

    QC_APPDATA 环境变量可覆盖数据目录（E2E 测试隔离用，生产不设置则用默认路径）。"""
    import paths
    return paths.user_data_dir()
def _queue_path() -> str:
    return os.path.join(_appdata_dir(), "qc_queue.json")
def _load_queue() -> list:
    try:
        with open(_queue_path(), encoding="utf-8") as fh:
            return json.load(fh) or []
    except Exception:
        return []
def _save_queue(items: list) -> None:
    with open(_queue_path(), "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
_SHOT: Dict[str, Any] = {"img": None, "w": 0, "h": 0, "ts": 0.0}
_SHOT_MAX_W = 1600          # 传给前端的缩略图最大宽度（省带宽，不影响识别精度）
_OCR_CACHE: Dict[str, Any] = {}     # region_key -> {"sig": tuple, "text": str}
_OCR_CACHE_MAX = 12
_OCR_LOCK = threading.Lock()
def _grab_fullscreen():
    from PIL import ImageGrab
    img = ImageGrab.grab()
    if img is None:
        raise RuntimeError("截屏返回空图")
    # macOS 未授予「屏幕录制」权限时会返回纯黑图（不抛异常），这里做启发式检测
    try:
        ext = img.convert("L").getextrema()
        if ext == (0, 0):
            raise RuntimeError(
                "截屏结果全黑：macOS 需在『系统设置 → 隐私与安全性 → 屏幕录制』"
                "中勾选本应用（终端/星衍质控），授权后需重启应用。")
    except RuntimeError:
        raise
    except Exception:
        pass
    return img
_FINDINGS_TITLES = ("检查所见", "影像所见", "影像描述", "所见", "检查描述", "描述")
_IMPRESSION_TITLES = ("诊断印象", "影像诊断", "诊断意见", "诊断结论", "印象", "结论", "诊断")
_BASIC_TITLES = ("患者", "病人", "姓名", "检查号", "影像号", "登记")
def _strip_title(line: str, pats) -> str:
    """剥离行首的段落标题词，保留正文。如『检查所见：双肺纹理增多』→『双肺纹理增多』。
    标题可能后接中文冒号/空格/顿点；也可能标题在行中（罕见），统一只剥行首。"""
    s = line.strip()
    for p in sorted(pats, key=len, reverse=True):
        if s.startswith(p):
            rest = s[len(p):].lstrip("：:：: .、\t")
            return rest.strip()
        # 兼容『所见：』『描述 :』等带空格的标题写法
        if s.startswith(p + " ") or s.startswith(p + "："):
            rest = s[len(p):].lstrip(" ：: .、\t")
            return rest.strip()
    return s
def _split_dynamic(full: str) -> (Dict[str, str], Dict[str, str]):
    texts = {"basic": "", "findings": "", "impression": ""}
    errors: Dict[str, str] = {}
    lines = [ln for ln in (full or "").splitlines() if ln.strip()]
    if not lines:
        return texts, errors
    # 找各段标题行下标（首个命中）
    def _first_idx(pats):
        for i, ln in enumerate(lines):
            for p in pats:
                if p in ln:
                    return i
        return -1
    f_idx = _first_idx(_FINDINGS_TITLES)
    i_idx = _first_idx(_IMPRESSION_TITLES)
    b_idx = _first_idx(_BASIC_TITLES)
    # 修正：诊断标题若出现在描述标题之前（PACS 常把「诊断」列在患者信息区），
    # 以描述标题为基准重排——取描述之后首个诊断标题。
    if f_idx >= 0 and i_idx >= 0 and i_idx < f_idx:
        for j in range(f_idx, len(lines)):
            if any(p in lines[j] for p in _IMPRESSION_TITLES):
                i_idx = j
                break
    # basic：起始（或患者标题）→ 描述标题（或诊断标题）
    # 注意：若「患者」标题出现在描述/诊断之后（部分 PACS 布局），b_start > end，
    # 直接取起始段即可（lines[0:end]），避免 basic 被截成空串。
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
    elif i_idx >= 0:
        end = i_idx
    else:
        end = len(lines)
    b_start = b_idx if 0 <= b_idx < end else 0
    texts["basic"] = "\n".join(lines[b_start:end]).strip()
    # findings：从描述标题行开始（含该行正文，标题词被剥掉）→ 诊断标题行前
    # 注意跳过中间的患者标题行（部分 PACS 布局把患者信息插在描述段里），
    # 避免「患者：张三」等 basic 内容混入描述正文。
    if f_idx >= 0:
        end = i_idx if i_idx > f_idx else len(lines)
        head = _strip_title(lines[f_idx], _FINDINGS_TITLES)
        body = [head]
        for ln in lines[f_idx + 1:end]:
            if b_idx >= 0 and b_idx != f_idx and any(p in ln for p in _BASIC_TITLES):
                continue
            body.append(ln)
        texts["findings"] = "\n".join(body).strip()
    # impression：从诊断标题行开始（含该行正文，标题词被剥掉）→ 末尾
    if i_idx >= 0:
        head = _strip_title(lines[i_idx], _IMPRESSION_TITLES)
        body = [head] + [ln for ln in lines[i_idx + 1:]]
        texts["impression"] = "\n".join(body).strip()
    return texts, errors
def _ocr_config_path() -> str:
    """OCR 区域配置路径（统一由 paths.ocr_config_path 解析，桌面/Web 区域配置互通）。"""
    import paths
    return paths.ocr_config_path()
_DEFAULT_SETTINGS = {
    "emp_id": "demo01",            # 默认工号（责任到人）
    "default_modality": "",        # 默认成像方式
    "auto_qc_on_ocr": True,        # OCR 回填后自动跑质控
    "auto_enqueue": True,          # 采集/RIS 拉取自动进待质控队列
    "ocr_min_score": 0.55,         # OCR 置信度阈值
    "screen_refresh_on_ocr": False,  # 识别前重新抓屏
    "ocr_dynamic": True,           # 动态语义识别（整屏OCR按标题切分）
    "ocr_silent": False,           # 静默质控：一键识别完成后不强制弹窗
    "anonymize": False,            # 入库脱敏
    "theme": "light",
    # ── 可配置快捷键（Windows 风 Ctrl+ 默认；设置页可逐条重绑，持久化到 web_settings.json）──
    # mods 取值: "ctrl" / "shift" / "alt" / "meta"；key 为 KeyboardEvent.key（大小写敏感）
    "shortcuts": {
        "run_qc":      {"mods": ["ctrl"], "key": "Enter"},   # 运行质控
        "save_sample": {"mods": ["ctrl"], "key": "s"},       # 存入样本库
        "ocr_capture": {"mods": ["ctrl", "shift"], "key": "o"},  # 识别并质控（框选OCR）
        "toggle_theme":{"mods": ["ctrl"], "key": "t"},       # 明暗主题切换
    },
}
def _settings_path() -> str:
    return os.path.join(_appdata_dir(), "web_settings.json")


# ── 登录频率限制（防爆破） ──────────────────────────────────────────────────
# 基于 IP 的 in-memory 限流：每个 IP 最多 N 次失败后锁定 M 秒。
# 重启清空，对本地单用户部署足够；多实例部署建议改为 Redis。
_LOGIN_FAIL_LIMIT = int(os.environ.get("QC_LOGIN_FAIL_LIMIT", "5"))   # 最大失败次数
_LOGIN_FAIL_WINDOW = int(os.environ.get("QC_LOGIN_FAIL_WINDOW", "300"))  # 窗口秒数（5 分钟）
_LOGIN_LOCK_DURATION = int(os.environ.get("QC_LOGIN_LOCK_SECONDS", "900"))  # 锁定时长（15 分钟）
_login_failures: Dict[str, List[float]] = {}  # ip -> [timestamp, ...]


def _check_login_rate(request: Request) -> None:
    """登录限流依赖：超过阈值抛出 429。"""
    client_ip = (request.client.host if request.client else "unknown")
    now = time.time()
    # 清理过期记录（窗口外的全部丢弃）
    attempts = [ts for ts in _login_failures.get(client_ip, []) if now - ts < _LOGIN_FAIL_WINDOW]
    if len(attempts) >= _LOGIN_FAIL_LIMIT:
        earliest = min(attempts)
        remaining = _LOGIN_FAIL_WINDOW - (now - earliest)
        raise HTTPException(429,
            f"登录尝试过于频繁，请 {int(max(remaining, 0))} 秒后重试")


def _record_login_failure(request: Request) -> None:
    """记录一次登录失败。"""
    client_ip = (request.client.host if request.client else "unknown")
    _login_failures.setdefault(client_ip, []).append(time.time())
    # 上限保护：防止内存无限膨胀
    if len(_login_failures) > 10000:
        _login_failures.clear()


def _clear_login_failures(request: Request) -> None:
    """登录成功后清除该 IP 的失败计数。"""
    client_ip = (request.client.host if request.client else "unknown")
    _login_failures.pop(client_ip, None)


# ── 审计日志 ───────────────────────────────────────────────────────────────
def log_audit(emp_id: str, action: str, detail: Any = None, ip: str = "") -> None:
    """写入一条审计记录到 SQLite。失败不抛异常（日志不应阻塞业务）。"""
    try:
        from server.models import AuditLog
        from server.db import SessionLocal
        sess = SessionLocal()
        try:
            sess.add(AuditLog(
                emp_id=(emp_id or "anonymous").strip()[:64],
                action=action.strip()[:64],
                detail=json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else str(detail or "")[:1024],
                ip=(ip or "").strip()[:64],
            ))
            sess.commit()
        finally:
            sess.close()
    except Exception:
        pass  # 审计写入失败不阻断业务
