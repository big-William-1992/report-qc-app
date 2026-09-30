#!/usr/bin/env python3
"""
scripts/coverage_gate.py — 覆盖率"不得下降"门禁（2026-09-30 新增）

为什么用"不得下降"而不是固定阈值：本仓真实覆盖率约 60%，直接卡 80% 只会逼人
写无意义测试或干脆关掉门禁。**防退化**才是这个阶段真正需要的：改动不得让覆盖
掉的语句变多。

背景（顺带修掉的一个坑）：`.coveragerc` 的 `exclude_lines` 里有一行
`if not self.rules_config.get("enable_` —— 未闭合的正则会让 coverage 直接抛
ConfigError，所以 **`--cov` 从来没有真正跑起来过**（谁也不知道覆盖率是多少）。
本脚本会先做一次配置自检，避免"门禁静默失效"。

用法：
    python3 scripts/coverage_gate.py                 # 跑 pytest+cov 并与基线比对
    python3 scripts/coverage_gate.py --update        # 更新基线（需在提交信息里说明）
    python3 scripts/coverage_gate.py --report        # 只报告，不判失败
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "benchmarks", "coverage_baseline.json")
TOLERANCE = 0.5          # 允许 0.5 个百分点内的抖动（不同机器/插件顺序略有差异）


def _run_pytest_cov() -> int:
    """跑全量测试并收集覆盖率；返回进程退出码（不用于判定，测试有已知环境性失败）。"""
    env = {**os.environ, "QC_APPDATA": os.environ.get("QC_APPDATA", "/tmp/qc_cov_appdata")}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--cov=src", "--cov=server",
         "--cov-report="],
        cwd=ROOT, env=env, capture_output=True, text=True)
    return r.returncode


def _totals() -> dict:
    """读取 coverage 数据（需要 coverage 包）。"""
    subprocess.run([sys.executable, "-m", "coverage", "json", "-o",
                    os.path.join(ROOT, ".coverage.json"), "-q"],
                   cwd=ROOT, capture_output=True, text=True)
    path = os.path.join(ROOT, ".coverage.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("totals", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="把当前值写为基线")
    ap.add_argument("--report", action="store_true", help="只报告不判失败")
    args = ap.parse_args()

    try:
        import coverage  # noqa: F401
    except ImportError:
        print("未安装 coverage/pytest-cov：pip install pytest-cov coverage")
        return 1

    print("跑全量测试并收集覆盖率（约 20–40s）…")
    _run_pytest_cov()
    totals = _totals()
    if not totals:
        print("✗ 未能生成覆盖率数据（coverage json 失败）")
        return 1

    pct = round(float(totals.get("percent_covered", 0.0)), 1)
    missed = int(totals.get("missing_lines", 0))
    stmts = int(totals.get("num_statements", 0))
    print(f"覆盖率 {pct}%（语句 {stmts}，未覆盖 {missed}）")

    base = {}
    if os.path.isfile(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            base = json.load(f)
    old_pct = float(base.get("percent_covered", 0.0)) if base else None

    if args.update:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump({"percent_covered": pct, "num_statements": stmts,
                       "missing_lines": missed,
                       "note": "由 scripts/coverage_gate.py --update 生成；只允许上升"},
                      f, ensure_ascii=False, indent=1)
        print(f"✓ 已更新基线 → {os.path.relpath(BASELINE, ROOT)}（{pct}%）")
        return 0

    if old_pct is None:
        print("⚠ 尚无基线；请先运行 python3 scripts/coverage_gate.py --update")
        return 0 if args.report else 1

    print(f"基线 {old_pct}%  当前 {pct}%  容差 {TOLERANCE}pt")
    if pct + TOLERANCE < old_pct:
        print(f"✗ 覆盖率下降 {old_pct - pct:.1f}pt（超过容差 {TOLERANCE}pt）")
        print("  新增代码请补测试；确因删除死代码导致分母变化时用 --update 刷新基线。")
        return 1
    print("✓ 覆盖率未下降")
    return 0


if __name__ == "__main__":
    sys.exit(main())
