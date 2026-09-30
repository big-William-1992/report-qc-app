"""
qc_runtime.py — 质控计算运行时（2026-09-30 路由拆分 S5 抽出）

从 `server/main.py` **逐字搬迁**：

  · `_get_engine()`          —— 进程级 RuleEngine 单例（避免每次质控重读 rules_config.json）
  · `_reload_engine_rules()` —— 规则变更后刷新单例（**唯一刷新入口**）
  · `_run_qc()`              —— 单份报告质控（含 auto_fix）
  · `_qc_rate_ok()`          —— 按 IP 的内存限流（CPU 密集接口的 DoS 防护）

## 为什么单独成模块

这些是"跨端点共享的进程级状态"：引擎单例必须**只有一份**（否则多份单例各自持有
rules_config 副本 → 改了规则某些端点生效、某些不生效），限流表同理（两份即形同虚设）。
qc 端点拆到 `routes/` 后，状态留在这里，路由模块只做参数校验与响应封装。

## 与其它模块的关系

⚠️ 注意：`server/deps.py` 里另有一份 `_run_qc`（会按 `QC_ENGINE` 环境变量选择
LLM 引擎），与本模块的 `_run_qc`（**始终用规则引擎**）**行为不同**，且分别被
`route_push`（用 deps 版）与 UI 路径（用本模块版）使用 —— 这是一个**已知的不一致**，
待产品决策（是否让 UI 也遵循 QC_ENGINE）。详见 docs/OPEN_DECISIONS.md。
"""
import os
import threading
import time
from typing import Dict

import engine

# ── 进程级引擎单例（2026-08-18 E2 修复）──────────────────────────────────
# 此前每次 /qc/check 新建 RuleEngine 并重读 rules_config.json（批量 50 条即 50 次磁盘读）。
# 规则变更后 _reload_engine_rules() 刷新；run() 只读 self.rules_config 引用，并发安全。
_ENGINE = None
_ENGINE_LOCK = threading.Lock()

def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = engine.RuleEngine()
    return _ENGINE


def _lg(where: str) -> None:
    """降级留痕（局部导入，零模块级依赖）。"""
    try:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(where)
    except Exception:       # silent-except-ok: 观测层自身失败绝不能抛
        pass


def _reload_engine_rules():
    try:
        _get_engine().reload_rules()
    except Exception:
        _lg(__name__)       # 规则刷新失败必须留痕，否则"改了规则没生效"无从排查


def _run_qc(report: str, meta: dict, auto_fix: bool) -> dict:
    eng = _get_engine()
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


# ----------------------------- 质控计算（无状态） -----------------------------
# 内存态简单限流（2026-08-18 H1c）：质控是 CPU 密集计算，远程部署时防止
# 无凭证/恶意调用打满 CPU（DoS）。每 IP 每分钟默认 60 次，可 QC_RATE_PER_MIN 调整。
_QC_RATE_MAX = int(os.environ.get("QC_RATE_PER_MIN", "60"))
_QC_RATE: Dict[str, list] = {}
_QC_RATE_LOCK = threading.Lock()


def _qc_rate_ok(ip: str) -> bool:
    now = time.time()
    with _QC_RATE_LOCK:
        ts = [t for t in _QC_RATE.get(ip, []) if now - t < 60]
        if len(ts) >= _QC_RATE_MAX:
            _QC_RATE[ip] = ts
            return False
        ts.append(now)
        _QC_RATE[ip] = ts
        # 2026-08-24 修复：定期清理过期 IP 条目，防止内存无限增长
        if len(_QC_RATE) > 1000:
            _QC_RATE.clear()
        return True
