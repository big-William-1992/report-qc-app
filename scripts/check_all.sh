#!/usr/bin/env bash
# =============================================================================
# scripts/check_all.sh — 统一自检入口（人 / AI agent 都跑这一条）
#
# 为什么要有它（2026-09-30 事故复盘）：
#   本轮审计里，"前端 bundle 语法错误导致整个界面白屏""备份/恢复从未生效"
#   "PACS 推送进不了队列"这些问题**不是测试写不出来**，而是没人跑对的那几条命令。
#   把全部门禁收进一个脚本，让"跑过 CI"和"跑过 check_all"变成同一件事。
#
# 用法：
#   bash scripts/check_all.sh            # 常规门禁（约 20s）
#   WITH_E2E=1 bash scripts/check_all.sh # 额外跑浏览器端到端（约 +9s，需 playwright 浏览器）
#   WITH_COVERAGE=1 bash scripts/check_all.sh  # 额外跑覆盖率门禁（约 +30s）
#   QC_PY=/path/to/python bash scripts/check_all.sh
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${QC_PY:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

fail=0
step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '   \033[31m✗\033[0m %s\n' "$1"; fail=1; }

step "Python: $PY"

# 1) 全量单元测试（含数据层/单一实现/前端 bundle/队列/R19 性能等守卫）
if "$PY" -m pytest -q; then ok "pytest 全量通过"; else bad "pytest 失败"; fi

# 2) 静态检查（CI 里是阻断项）。支持三种定位方式：RUFF 变量 / python -m ruff / PATH 上的 ruff
RUFF_CMD=()
if [ -n "${RUFF:-}" ]; then RUFF_CMD=("$RUFF")
elif "$PY" -m ruff --version >/dev/null 2>&1; then RUFF_CMD=("$PY" -m ruff)
elif command -v ruff >/dev/null 2>&1; then RUFF_CMD=(ruff)
fi
if [ ${#RUFF_CMD[@]} -eq 0 ]; then
  bad "未安装 ruff（pip install ruff，或用 RUFF=/path/to/ruff 指定）"
elif "${RUFF_CMD[@]}" check . ; then ok "ruff 通过"
else bad "ruff 发现致命问题"; fi

# 3) 前端 bundle：必须是合法经典脚本且与 modules/ 同步
if "$PY" tools/build_bundle.py --check; then ok "app.bundle.js 同步且合法"; else
  bad "app.bundle.js 与 modules/ 不同步或含 import/export（运行 python3 tools/build_bundle.py）"
fi

# 4) 浏览器级解析判定（最接近真实加载；无 node 时跳过）
if command -v node >/dev/null 2>&1; then
  if node --check web/static/js/app.bundle.js >/dev/null 2>&1; then ok "node --check bundle 通过"
  else bad "bundle 不是合法经典脚本（浏览器会 SyntaxError → 界面白屏）"; fi
else
  printf '   ⚠ 未安装 node，跳过 bundle 解析检查\n'
fi

# 5) 引擎回归基线（防止规则改动悄悄改变检出结果）
if [ -f tools/run_eval.py ]; then
  if OUT=$("$PY" tools/run_eval.py 2>/dev/null | tail -3) && ! echo "$OUT" | grep -q "↓"; then
    ok "引擎评测基线未下降"
  else
    bad "引擎评测基线下降或有异常：$(echo "$OUT" | tail -2 | tr '\n' ' ')"
  fi
fi

# 6) 覆盖率门禁（可选：跑全量 + cov，约 30s；只判"不得下降"）
if [ "${WITH_COVERAGE:-0}" = "1" ]; then
  if "$PY" scripts/coverage_gate.py; then ok "覆盖率未下降"
  else bad "覆盖率下降（或未生成覆盖率数据）"; fi
fi

# 7) 浏览器端到端（可选；CI 的 web-e2e job 会跑）
if [ "${WITH_E2E:-0}" = "1" ]; then
  if [ -x node_modules/.bin/playwright ]; then
    if QC_PY="$PY" node_modules/.bin/playwright test; then ok "Playwright E2E 通过"
    else bad "Playwright E2E 失败"; fi
  else
    printf '   ⚠ 未安装 node_modules（npm ci），跳过 E2E\n'
  fi
fi

printf '\n'
if [ "$fail" = "0" ]; then
  printf '\033[32m全部通过。\033[0m\n'
else
  printf '\033[31m存在失败项，见上。\033[0m\n'
fi
exit "$fail"
