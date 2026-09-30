#!/usr/bin/env python3
"""
scripts/audit_silent_except.py — 找出"静默吞异常"并守住不新增

为什么（2026-09-30 审计复盘）：
  本轮 6 个真 bug 有同一个成因——异常被静默吞掉，故障只剩"功能不生效"：
    · health 探针 `with get_db()` TypeError 被吞 → 探活永远"绿"
    · backup 恢复 `import src.accounts` ImportError 被吞 → "恢复成功"却不生效
    · 诊断包 feedback.db 写在 `with ZipFile` 块外 → ValueError 被吞 → 从未入包
    · badcase 反馈表未建 → `no such table` 被吞 → 医生反馈静默丢失
    · deps 的 require_emp 工号校验 ImportError 被吞 → 校验是死代码
    · set_session 写失败被吞 → 登录预填静默失效
  全仓曾有约 180 处吞掉异常的分支，其中真正静默（pass/直接 return）约占一半，
  另一半是 log_quiet 降级留痕（2026-09-30 起已带上异常类型/位置）。
  本脚本把它们盘出来，并让**新增**必须显式豁免，避免继续腐烂。

判定（AST，不是 grep）：
  - kind=pass      : `except ...:` 体里只有 `pass`
  - kind=log_quiet : `except ...:` 体里是 `log_quiet(...)` —— 合规的降级留痕（仅计数）
  - kind=swallow   : `except ...:` 体里只有 `continue`/`return None`/`return ""`（常见但风险较低，单独统计）

豁免：在 `except` 行尾加 `# silent-except-ok: <原因>` 即不计入（刻意不用 `# noqa:`
前缀，避免与 ruff 的 noqa 指令语法冲突）。

关于 `log_quiet`（重要澄清）：它**不是**静默——2026-09-12 起以 WARNING 级输出结构化
JSON；2026-09-30 又补上了异常类型/消息/调用位置（见 src/logger.py::_context）。
因此本脚本对 `log_quiet` 只做**计数展示**（降级点），回归判定只看真正静默的
`pass` 与 `swallow`。

用法：
    python3 scripts/audit_silent_except.py                 # 打印清单 + 与基线对比（CI/测试用，超基线则退出码 1）
    python3 scripts/audit_silent_except.py --list          # 只打印明细
    python3 scripts/audit_silent_except.py --write-baseline # 清理后刷新基线（需在提交信息里说明）
"""
import argparse
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "scripts", "silent_except_baseline.json")
SCAN_DIRS = ("src", "server")
EXEMPT_MARK = "silent-except-ok"


def _call_name(node):
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return ""


def _classify(handler: ast.ExceptHandler):
    """返回 kind 或 None（不视为静默）。"""
    # 有 raise（含 bare raise）→ 不是静默
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return None
    calls = {_call_name(n) for n in ast.walk(handler) if isinstance(n, ast.Call)}
    # 有 logger/ print 等可诊断输出 → 不算静默（warning/error 都算留痕）
    if calls & {"warning", "error", "exception", "critical", "print"}:
        return None
    if calls & {"log_quiet"}:
        return "log_quiet"
    body = [n for n in handler.body if not (isinstance(n, ast.Expr)
                                            and isinstance(n.value, ast.Constant)
                                            and isinstance(n.value.value, str))]
    if len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            return "pass"
        if isinstance(only, ast.Continue):
            return "swallow"
        if isinstance(only, ast.Return):
            return "swallow"
    return None


def _exempt(lines, lineno):
    line = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
    # 豁免注释可能写在 except 行或其后一行
    nxt = lines[lineno] if 0 <= lineno < len(lines) else ""
    return EXEMPT_MARK in line or EXEMPT_MARK in nxt


def scan():
    findings = []
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in ("__pycache__", "node_modules")]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT)
                try:
                    src = open(path, encoding="utf-8").read()
                    tree = ast.parse(src)
                except Exception:
                    continue
                lines = src.splitlines()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler):
                        kind = _classify(node)
                        if kind and not _exempt(lines, node.lineno):
                            findings.append({"file": rel, "line": node.lineno, "kind": kind})
    return findings


def summarize(findings):
    by_file = {}
    for f in findings:
        by_file.setdefault(f["file"], {"pass": 0, "log_quiet": 0, "swallow": 0})
        by_file[f["file"]][f["kind"]] += 1
    return {"total": len(findings), "by_file": by_file}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印明细")
    ap.add_argument("--write-baseline", action="store_true", help="刷新基线")
    args = ap.parse_args()

    findings = scan()
    summary = summarize(findings)

    if args.list:
        for f in sorted(findings, key=lambda x: (x["file"], x["line"])):
            print(f"  {f['file']}:{f['line']}  {f['kind']}")
        print(f"\n合计 {summary['total']} 处")
        return 0

    if args.write_baseline:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"已写入基线：{os.path.relpath(BASELINE, ROOT)}（合计 {summary['total']} 处）")
        return 0

    if not os.path.isfile(BASELINE):
        print(f"缺少基线文件 {os.path.relpath(BASELINE, ROOT)}；"
              "先运行 python3 scripts/audit_silent_except.py --write-baseline")
        return 1

    with open(BASELINE, encoding="utf-8") as fh:
        base = json.load(fh)

    problems = []
    for rel, counts in summary["by_file"].items():
        old = base.get("by_file", {}).get(rel)
        # 新增文件：只看**真静默**（pass/swallow）。
        # 2026-09-30 修正：原规则把 `log_quiet` 也算作回归，而 log_quiet 正是本仓
        # 文档推荐的做法（"新增 except 分支必须留痕：logger.warning 或 log_quiet"）
        # —— 于是"按规矩写的新文件"反而过不了门禁。这与回归判定口径（忽略 log_quiet）
        # 也不一致，属于门禁自身的缺陷。
        if old is None:
            silent_now = counts.get("pass", 0) + counts.get("swallow", 0)
            if silent_now:
                problems.append(
                    f"新增文件含真静默吞异常：{rel}（pass={counts.get('pass',0)}, "
                    f"swallow={counts.get('swallow',0)}）")
            continue
        # 只看真静默的两类；log_quiet 是合规的降级留痕，不参与回归判定
        for kind in ("pass", "swallow"):
            if counts.get(kind, 0) > old.get(kind, 0):
                problems.append(f"{rel} 的 {kind} 从 {old.get(kind,0)} 增到 {counts.get(kind,0)}")

    if problems:
        print("✗ 静默吞异常出现回归：")
        for p in problems:
            print("   - " + p)
        print("\n  修复方式：让 except 分支至少留一条可诊断日志（logger.warning(..., exc)），")
        print("  或在确实无害时于 except 行尾写 `# silent-except-ok: <原因>`。")
        print("  清理后刷新基线：python3 scripts/audit_silent_except.py --write-baseline")
        return 1

    print(f"✓ 静默吞异常未新增（当前合计 {summary['total']} 处，"
          f"基线 {base.get('total')} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
