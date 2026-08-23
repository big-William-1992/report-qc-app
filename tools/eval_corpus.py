"""星衍质控 · 错别字/术语规则评估脚本（2026-08-22）

用途：把你手上的「无标注原始报告」丢进一个目录，跑本脚本即可：
  1) 逐份调用 RuleEngine 跑全量规则；
  2) 汇总每个规则命中次数、按严重度分布；
  3) 输出每份报告的错别字/术语类命中（R8/R19/R22*）明细，供你人工抽检精度；
  4) 可选导出 JSON，便于后续统计召回/误报。

用法：
  # 评估一个目录下的 .txt/.md 报告
  python3 tools/eval_corpus.py /path/to/reports
  # 评估单个文件
  python3 tools/eval_corpus.py report.txt
  # 导出 JSON
  python3 tools/eval_corpus.py /path/to/reports --json out.json
  # 仅看错别字/术语类（R8/R19/R22）
  python3 tools/eval_corpus.py /path/to/reports --only-typo

依赖：python3 + 当前 src/ 下的引擎（自动把 src 加入 sys.path）。
注意：本脚本只读、不改写你的报告，也不写数据库。
"""
import os
import sys
import json
import argparse
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

from engine import RuleEngine, extract_meta  # noqa: E402

TYPO_RULES = {"R8-TYPO", "R19-HOMOPHONE", "R22-SIZE", "R22-UNIT", "R22-QUAL",
              "R22-SIZE-MISSING"}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception as exc:  # pragma: no cover
        print(f"  [warn] 读取失败 {path}: {exc}")
        return None


def eval_text(engine, text):
    meta = extract_meta(text)  # 自动抽性别/年龄/部位/侧别，提升 R1/R3/R6/R21 命中率
    findings = engine.run(text, meta)
    return findings


def main():
    ap = argparse.ArgumentParser(description="错别字/术语规则批量评估")
    ap.add_argument("path", help="报告目录或单个 .txt/.md 文件")
    ap.add_argument("--json", help="导出结果到 JSON 文件")
    ap.add_argument("--only-typo", action="store_true",
                    help="仅统计/展示错别字与术语类规则 (R8/R19/R22)")
    ap.add_argument("--llm", action="store_true",
                    help="同时调用本地 LLM 语义层，输出其发现供人工抽检精度")
    args = ap.parse_args()

    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "**", "*.txt"), recursive=True)
                       + glob.glob(os.path.join(args.path, "**", "*.md"), recursive=True))
    else:
        files = [args.path]

    if not files:
        print("未找到任何 .txt/.md 报告文件。")
        return

    engine = RuleEngine()
    rule_counter = {}
    sev_counter = {"high": 0, "medium": 0, "low": 0}
    per_file = []
    typo_only = args.only_typo
    llm_enabled = args.llm
    if llm_enabled:
        try:
            from llm_qc import run_llm_qc
        except Exception as exc:
            print(f"[warn] LLM 模块加载失败，跳过 --llm：{exc}")
            llm_enabled = False

    print(f"待评估报告：{len(files)} 份\n" + "=" * 60)
    for fp in files:
        text = _read(fp)
        if text is None:
            continue
        findings = eval_text(engine, text)
        if typo_only:
            findings = [f for f in findings if f.rule_id in TYPO_RULES]
        rec = {"file": os.path.relpath(fp, args.path if os.path.isdir(args.path) else "."),
               "n": len(findings),
               "findings": [{"rule": f.rule_id, "type": f.error_type,
                             "sev": f.severity, "msg": f.message} for f in findings]}
        # LLM 语义层（可选，独立于规则；不可用则跳过）
        if llm_enabled:
            try:
                lr = run_llm_qc(text, extract_meta(text))
                rec["llm"] = {
                    "available": lr.get("available", False),
                    "error": lr.get("error"),
                    "findings": lr.get("findings", []),
                }
                if lr.get("available") and lr.get("findings"):
                    print(f"\n[{rec['file']}]  LLM 语义发现 {len(lr['findings'])} 条")
                    for it in lr["findings"]:
                        print(f"   · {it.get('error_type')} ({it.get('severity')}, "
                              f"conf={it.get('confidence')}): {it.get('rationale', '')[:50]}")
            except Exception as exc:
                rec["llm"] = {"available": False, "error": str(exc), "findings": []}
        per_file.append(rec)
        for f in findings:
            rule_counter[f.rule_id] = rule_counter.get(f.rule_id, 0) + 1
            if f.severity in sev_counter:
                sev_counter[f.severity] += 1
        # 逐份打印错别字/术语命中（无论 --only-typo，默认就聚焦这类）
        typo_hits = [f for f in findings if f.rule_id in TYPO_RULES]
        if typo_hits:
            print(f"\n[{rec['file']}]  错别字/术语命中 {len(typo_hits)} 条")
            for f in typo_hits:
                print(f"   · {f.rule_id} ({f.severity}): {f.message[:60]}")

    print("\n" + "=" * 60)
    print("规则命中汇总：")
    for rule, n in sorted(rule_counter.items(), key=lambda kv: -kv[1]):
        print(f"  {rule:18s} {n}")
    print("\n严重度分布：", sev_counter)
    print(f"总报告数：{len(per_file)}，含错别字/术语命中的报告："
          f"{sum(1 for r in per_file if any(f['rule'] in TYPO_RULES for f in r['findings']))}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"rule_counter": rule_counter, "severity": sev_counter,
                       "files": per_file}, fh, ensure_ascii=False, indent=2)
        print(f"\n已导出 JSON → {args.json}")


if __name__ == "__main__":
    main()
