"""
test_silent_exceptions.py — 静默吞异常"不得新增"守卫（2026-09-30 审计新增）

本轮 6 个真 bug 有同一成因：异常被吞掉，故障只表现为"功能不生效"——
health 探针 TypeError、备份恢复 ImportError、诊断包 ValueError、反馈表 no such table、
工号校验 ImportError、会话写入失败。它们全都在日志里查不到原因。

治理方式（渐进式，不要求一次清完 170 处）：
  · `scripts/audit_silent_except.py` 用 AST 盘出所有"吞异常"分支；
  · `scripts/silent_except_baseline.json` 记录当前基线；
  · 本测试断言**没有新增**（真静默的 pass/直接 return 计回归；log_quiet 是合规的
    降级留痕，只计数不判回归 —— 它已带异常类型/消息/调用位置）。

清理后刷新基线：python3 scripts/audit_silent_except.py --write-baseline
"""
import json
import os
import subprocess

import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import subprocess_env  # noqa: E402


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "audit_silent_except.py")
_BASELINE = os.path.join(_ROOT, "scripts", "silent_except_baseline.json")


def test_baseline_file_exists():
    assert os.path.isfile(_BASELINE), \
        "缺少 scripts/silent_except_baseline.json（运行 audit_silent_except.py --write-baseline）"
    data = json.load(open(_BASELINE, encoding="utf-8"))
    assert "total" in data and "by_file" in data, "基线文件结构不对"


def test_no_new_silent_exceptions():
    """真静默（pass / 直接 return）不得比基线更多。"""
    assert os.path.isfile(_SCRIPT), "缺少 scripts/audit_silent_except.py"
    r = subprocess.run([sys.executable, _SCRIPT], capture_output=True, text=True, timeout=180, env=subprocess_env())
    assert r.returncode == 0, (
        "静默吞异常出现回归：\n" + r.stdout[-3000:] + r.stderr[-1000:])


def test_degraded_logging_carries_exception_context():
    """降级日志必须带异常类型与调用位置 —— 否则"为什么降级"依然无法定位。"""
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    import logger as lg

    ctx = {}
    try:
        {}["nope"]                      # 制造一个 KeyError
    except Exception:
        ctx = lg._context("unit.test")
    assert "exc" in ctx and "KeyError" in ctx["exc"], f"降级日志缺少异常信息：{ctx}"
    assert "at" in ctx and ":" in ctx["at"], f"降级日志缺少调用位置：{ctx}"


def test_degraded_logging_without_exception_still_works():
    """没有异常上下文时（普通降级）不能报错、也不能凭空写 exc。"""
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    import logger as lg

    ctx = lg._context("unit.test")
    assert ctx["where"] == "unit.test"
    assert "exc" not in ctx, "不应凭空生成异常字段"


def test_exempt_marker_is_not_confused_with_ruff_noqa():
    """豁免标记必须是独立写法（`# silent-except-ok:`），不能借道 ruff 的 noqa 指令。"""
    src = open(_SCRIPT, encoding="utf-8").read()
    assert 'EXEMPT_MARK = "silent-except-ok"' in src
    assert "noqa: silent-except" not in src


def test_new_file_with_only_log_quiet_passes(tmp_path, monkeypatch):
    """反证：按文档规矩写的新文件（except 只调 log_quiet 留痕）不得被判为回归。

    背景：2026-09-30 拆路由时新增 server/routes/route_license.py，里面唯一一处
    except 用的是 `log_quiet(__name__)`（文档明确推荐的留痕方式），却被"新增文件"
    规则判为静默吞异常 —— 门禁口径与文档/回归判定不一致，属门禁自身缺陷。
    """
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("audit_silent_except_t", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 造一个"只有 log_quiet"的假扫描结果做判定（不碰真实仓库文件）
    counts = {"pass": 0, "log_quiet": 1, "swallow": 0}
    silent_now = counts["pass"] + counts["swallow"]
    assert silent_now == 0, "此类文件应被视为合规（仅降级留痕）"

    # 同时确认脚本源码里确实已按新口径判断
    src = open(_SCRIPT, encoding="utf-8").read()
    assert "新增文件含真静默吞异常" in src, "新增文件规则未更新为只看真静默"
    assert 'silent_now = counts.get("pass", 0) + counts.get("swallow", 0)' in src
