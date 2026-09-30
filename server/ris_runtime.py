"""
ris_runtime.py — RIS 轮询运行时（2026-09-30 路由拆分 S4 抽出）

从 `server/main.py` **逐字搬迁**（仅改名去掉下划线前缀，行为不变）：

  · 轮询配置与去重指纹持久化（appdata/ris_poll.json）
  · `RIS_POLL_LOCK`、`ris_poll_once()`、`ris_poll_once_locked()`、`ris_poll_loop()`
  · `start_poll_thread()` —— 由 main.py 在 import 时调用（等价于原先模块加载即启动）

## 为什么单独成模块

`/api/v1/ris/poll-now`（手动触发）与后台守护线程**共用同一把锁和同一份配置**：
无锁时两路并发会把同一批报告各自当成"新报告"，重复入库/重复入队（2026-08-18 修复）。
把端点拆到 `routes/` 后，若状态留在 main 或两边各建一份，就会重演该类并发缺陷
（S3 曾因"区间删除"误删 `_RIS_POLL_LOCK` 定义，使 poll-now 返回 NameError，
 见 tests/test_no_dangling_endpoint_names.py）。

## 依赖方向

轮询引擎需要"跑质控"与"入队"，二者仍在 main/deps；为避免循环导入，在函数内按需局部导入。
"""
import hashlib
import json
import os
import sys
import threading
import time

from server.core import _JSON_IO_LOCK, _appdata_dir, _atomic_json_write, _log, queue_add_text


_POLL_PATH = None


def poll_path() -> str:
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


def poll_config() -> dict:
    cfg = dict(_POLL_DEFAULT)
    cfg["seen"] = list(cfg["seen"])
    try:
        with _JSON_IO_LOCK:
            with open(poll_path(), encoding="utf-8") as fh:
                data = json.load(fh) or {}
        for k in _POLL_DEFAULT:
            if k in data:
                cfg[k] = data[k]
    except FileNotFoundError:   # silent-except-ok: 首次运行没有轮询状态文件属正常，非降级
        pass
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)   # 文件存在但损坏 / 无权限：需要留痕
    return cfg


def save_poll_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(poll_path()), exist_ok=True)
        with _JSON_IO_LOCK:
            _atomic_json_write(poll_path(), cfg)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)






# ── 轮询互斥锁（2026-08-18）────────────────────────────────────────────────
# 后台守护线程与 /api/v1/ris/poll-now 手动触发共用 ris_poll_once；无锁时两路并发
# 会把同一批报告各自识别为"新报告"重复入库/入队。
# ⚠️ 这把锁**只能有一份**：路由模块一律 `from server.ris_runtime import RIS_POLL_LOCK`。
RIS_POLL_LOCK = threading.Lock()


def log_quiet(where: str) -> None:
    """降级留痕（局部导入，零模块级依赖，沿用本仓既有写法）。"""
    try:
        try:
            from .log_utils import log_quiet as _lq
        except ImportError:
            from log_utils import log_quiet as _lq
        _lq(where)
    except Exception:   # silent-except-ok: 观测层自身失败绝不能抛
        pass

def ris_poll_once(manual: bool = False) -> dict:
    """执行一次轮询：拉取 RIS → 质控 → 入库 + 入队。返回统计。

    互斥：非阻塞获取全局锁；若另一路（后台线程/手动）正在轮询则直接返回
    {"skipped": True}，避免并发拉取同一批报告重复入库/入队。
    """
    if not RIS_POLL_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "已有轮询在进行中"}
    try:
        return ris_poll_once_locked(manual)
    finally:
        RIS_POLL_LOCK.release()


def ris_poll_once_locked(manual: bool = False) -> dict:
    # 这些依赖仍在 main/deps：按需局部导入，避免循环依赖
    import ris
    import engine
    import samplelib
    import accounts
    from server.deps import _run_qc

    cfg = poll_config()
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
                        anonymize=False, user_id=emp_id,
                        dept_id=accounts.get_dept_id(emp_id))
                except Exception:
                    # 单份报告入库失败不应中断整批（原有行为），但必须留痕 ——
                    # 否则"整批都存不进去"时只表现为"拉取成功但样本没增加"。
                    log_quiet(__name__)
                    continue
        if cfg.get("auto_enqueue"):
            for r in new_reports:
                try:
                    queue_add_text(r.get("report_text") or "",
                                    {"patient": r.get("patient", ""),
                                     "gender": r.get("gender", ""),
                                     "age": r.get("age", ""),
                                     "modality": r.get("modality", ""),
                                     "applied_site": r.get("applied_site", "")},
                                    source="RIS轮询")
                except Exception:
                    log_quiet(__name__)   # 同上：单条入队失败留痕后继续
                    continue
    cfg["last_run"] = datetime_now_iso()
    cfg["last_count"] = new_count
    cfg["last_error"] = ""
    save_poll_config(cfg)
    return {"count": new_count, "total_seen": len(cfg.get("seen") or []),
            "last_run": cfg["last_run"], "new_reports": new_reports[:5]}


def datetime_now_iso() -> str:
    """当前时间 ISO 字符串（原在 main.py；随轮询运行时一并迁入，保持逐字一致）。"""
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


def ris_poll_loop(stop_event: threading.Event):
    """后台守护线程：按 interval_min 周期轮询。sleep 分片避免阻塞退出。
    2026-08-18 修复：此前固定每 60s 一轮，interval_min（默认 30 分钟）完全不参与调度，
    对医院 RIS 库造成 30 倍无谓查询压力。
    2026-08-24：连续失败指数退避——连续 N 次失败后间隔翻倍（上限 4h），
    连续 5 次失败自动禁用轮询 + 日志告警，避免对不可达服务器无谓探测。"""
    _consec_fails = 0
    _MAX_CONSEC_FAILS = 5  # 连续失败 N 次后自动禁用
    _BACKOFF_CAP = 4 * 3600  # 退避上限 4 小时
    while not stop_event.is_set():
        try:
            cfg = poll_config()
            if cfg.get("enabled"):
                ris_poll_once(manual=False)
                _consec_fails = 0  # 成功则重置计数
        except Exception:
            _consec_fails += 1
            try:
                _log("error", f"RIS 轮询异常 (连续第{_consec_fails}次): "
                     f"{type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}")
                cfg = poll_config()
                cfg["last_error"] = str(sys.exc_info()[1])[:300]
                cfg["last_run"] = datetime_now_iso()
                # 连续失败达阈值：自动禁用 + 写入告警标记
                if _consec_fails >= _MAX_CONSEC_FAILS:
                    cfg["enabled"] = False
                    cfg["last_error"] = (
                        f"[自动禁用] 连续 {_consec_fails} 次轮询失败，已暂停。"
                        f"请检查 RIS 数据库连接后手动重新启用。"
                        f" 最近错误: {str(sys.exc_info()[1])[:200]}")
                    try:
                        _log("warning",
                             f"RIS 轮询连续失败 {_consec_fails} 次，已自动禁用。"
                             "请检查 RIS 数据库连接配置后在设置中重新启用。")
                    except Exception:
                        try:
                            from .log_utils import log_quiet
                        except ImportError:
                            from log_utils import log_quiet
                        log_quiet(__name__)
                save_poll_config(cfg)
            except Exception:
                try:
                    from .log_utils import log_quiet
                except ImportError:
                    from log_utils import log_quiet
                log_quiet(__name__)
        # 分片 sleep：基础 interval + 指数退避（每次失败翻倍，上限 _BACKOFF_CAP）
        base_sec = max(15, int((poll_config().get("interval_min") or 30) * 60))
        if _consec_fails > 0:
            backoff = min(base_sec * (2 ** min(_consec_fails - 1, 6)), _BACKOFF_CAP)
        else:
            backoff = base_sec
        for _i in range(max(1, backoff // 5)):
            if stop_event.is_set():
                return
            time.sleep(5)




# ── 线程启动（原在 main.py 模块加载处）────────────────────────────────────────
POLL_STOP = threading.Event()
_poll_thread = None


def start_poll_thread() -> threading.Thread:
    """启动轮询守护线程（幂等；daemon，进程退出自动终止）。"""
    global _poll_thread
    if _poll_thread is not None and _poll_thread.is_alive():
        return _poll_thread
    th = threading.Thread(target=ris_poll_loop, args=(POLL_STOP,), daemon=True)
    th.name = "ris-poll-loop"
    th.start()
    _poll_thread = th
    return th


def stop_poll_thread() -> None:
    POLL_STOP.set()
