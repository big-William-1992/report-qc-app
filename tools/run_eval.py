#!/usr/bin/env python3
"""回归评测基准: 规则引擎(可选+LLM) 在 qc_sft_eval.jsonl 上的检出率/误报率。

用途:
  每次改规则、换模型、调 prompt 后跑一遍, 与 benchmarks/ 下历史基线对比,
  回答「这次改动是变好还是变坏」。

用法:
  python3 tools/run_eval.py                    # 规则引擎, 对比上次基线
  python3 tools/run_eval.py --llm              # 加跑 LLM(用当前 llm_config)
  python3 tools/run_eval.py --save             # 结果存为新基线
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EVAL_FILE = ROOT / "data" / "qc_sft_eval.jsonl"
BENCH_DIR = ROOT / "benchmarks"
BASELINE = BENCH_DIR / "eval_baseline.json"

# 标注 error_type → 引擎 rule_id 的等价类(用于匹配)
_EQUIV = {
    "R8-TYPO": {"R8-TYPO"},
    "R19-HOMOPHONE": {"R19-HOMOPHONE", "R8-TYPO"},  # 同音错字引擎可能归到 R8
}


def load_cases():
    cases = []
    with open(EVAL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            try:
                expect = json.loads(d["output"])
                assert isinstance(expect, list)
            except (json.JSONDecodeError, AssertionError):
                continue
            cases.append({"report": d["input"], "expect": expect})
    return cases


def run_rules(cases):
    from engine import RuleEngine, extract_meta
    eng = RuleEngine()
    results = []
    for c in cases:
        findings = eng.run(c["report"], extract_meta(c["report"]))
        items = []
        for f in findings:
            if isinstance(f, dict):
                rid, text = f.get("rule_id", ""), (f.get("snippet") or f.get("message") or "")
            else:  # Finding dataclass
                rid = getattr(f, "rule_id", "")
                text = getattr(f, "snippet", "") or getattr(f, "message", "")
            items.append({"rule_id": rid, "text": text})
        results.append(items)
    return results


def match(expect_item, found_list):
    """标注错误是否被引擎命中: rule_id 等价 且 文本包含标注 location。"""
    et = expect_item.get("error_type", "")
    loc = (expect_item.get("location") or "").strip()
    ok_ids = _EQUIV.get(et, {et})
    for fd in found_list:
        rid = fd["rule_id"].split("-")[0]
        rid_full = fd["rule_id"]
        id_ok = any(rid_full == i or rid_full.startswith(i.split("-")[0]) and et.split("-")[0] in rid_full
                    for i in ok_ids) or any(i in rid_full or et in rid_full for i in ok_ids)
        text_ok = loc in fd["text"] if loc else True
        if (id_ok or et in rid_full) and text_ok:
            return True
    return False


def evaluate(cases, preds, tag):
    n_err = n_err_hit = 0          # 错误样本数 / 其中被正确检出的
    n_norm = n_norm_clean = 0      # 正常样本数 / 其中零误报的
    misses, falses = [], []

    for c, pred in zip(cases, preds):
        expect = c["expect"]
        if not expect:  # 正常样本
            n_norm += 1
            if not pred:
                n_norm_clean += 1
            else:
                falses.append({"report": c["report"][:60], "false_findings": pred[:2]})
        else:
            n_err += 1
            hit = all(match(e, pred) for e in expect)
            if hit:
                n_err_hit += 1
            else:
                misses.append({
                    "report": c["report"][:60],
                    "expect": [e.get("location") for e in expect],
                    "got": [p["rule_id"] for p in pred][:3],
                })

    recall = n_err_hit * 100.0 / max(n_err, 1)
    spec = n_norm_clean * 100.0 / max(n_norm, 1)
    print(f"\n===== [{tag}] 评测结果 =====")
    print(f"错误样本检出率(recall):   {n_err_hit}/{n_err} = {recall:.1f}%")
    print(f"正常样本零误报率(specificity): {n_norm_clean}/{n_norm} = {spec:.1f}%")
    if misses:
        print(f"漏检示例(前5):")
        for m in misses[:5]:
            print(f"  期望{m['expect']} → 得到{m['got']} | {m['report']}")
    if falses:
        print(f"误报示例(前3):")
        for x in falses[:3]:
            print(f"  {x['false_findings']} | {x['report']}")
    return {"tag": tag, "recall": round(recall, 1), "specificity": round(spec, 1),
            "n_err": n_err, "n_norm": n_norm, "misses": misses[:20], "falses": falses[:10]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="加跑 LLM 链路")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 条(快速验证用)")
    ap.add_argument("--save", action="store_true", help="存为新基线")
    args = ap.parse_args()

    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]
    print(f"评测集: {len(cases)} 条 (来自 data/qc_sft_eval.jsonl)")

    t0 = time.time()
    rule_preds = run_rules(cases)
    report = {"date": datetime.now().isoformat(timespec="seconds"),
              "engine": evaluate(cases, rule_preds, f"规则引擎 {time.time()-t0:.0f}s")}
    report["engine"]["tag"] = "rules"

    if args.llm:
        t0 = time.time()
        from llm_qc import run_llm_qc
        llm_preds = []
        ok = True
        for c in cases:
            r = run_llm_qc(c["report"])
            if not r.get("available"):
                ok = False
                break
            llm_preds.append([
                {"rule_id": f.get("rule_id", ""), "text": (f.get("location") or "") + (f.get("rationale") or "")}
                for f in r.get("findings", [])
            ])
        if ok:
            report["llm"] = evaluate(cases, llm_preds, f"规则+LLM {time.time()-t0:.0f}s")
            report["llm"]["tag"] = "llm"
        else:
            print("\n⚠️ LLM 不可用, 跳过 LLM 评测")

    # 与基线对比
    if BASELINE.exists() and not args.save:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        print("\n===== 基线对比 =====")
        for key in ("engine", "llm"):
            if key in report and key in base:
                b, n = base[key], report[key]
                dr = n["recall"] - b["recall"]
                ds = n["specificity"] - b["specificity"]
                arrow = lambda v: ("↑+" if v > 0 else v < 0 and "↓" or "=") + f"{v:+.1f}" if v != 0 else "= 持平"
                print(f"[{key}] recall {b['recall']}%→{n['recall']}% ({arrow(dr)}) | "
                      f"误报率 {b['specificity']}%→{n['specificity']}% ({arrow(ds)})")

    if args.save:
        BENCH_DIR.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✅ 已保存新基线 → {BASELINE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
