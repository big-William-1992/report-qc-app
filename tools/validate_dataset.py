"""验证合成训练集：引擎能否复现标签（可复现性 = 训练目标一致性）。

指标：
  - 错误样本召回：gold 标签里有多少条能被 RuleEngine 实际检出
  - 干净样本误报率：gold 为空但引擎仍报错的比例（越低越好）
  - 分错误类型覆盖率
用法：
  python3 tools/validate_dataset.py --in data/synthetic_qc.jsonl
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from engine import RuleEngine, extract_meta  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/synthetic_qc.jsonl")
    args = ap.parse_args()

    eng = RuleEngine()
    gold_total = 0
    gold_covered = 0
    clean_total = 0
    clean_fp = 0
    per_type = {}

    with open(args.inp, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            gold = json.loads(r["output"])
            det = eng.run(r["input"], extract_meta(r["input"]))
            det_prefixes = {f.rule_id.split("-")[0] for f in det}

            if not gold:
                clean_total += 1
                if det:
                    clean_fp += 1
                continue

            for g in gold:
                gold_total += 1
                p = g["error_type"].split("-")[0]
                per_type.setdefault(p, [0, 0])
                per_type[p][1] += 1
                if p in det_prefixes:
                    gold_covered += 1
                    per_type[p][0] += 1

    recall = gold_covered / gold_total if gold_total else 0.0
    fp_rate = clean_fp / clean_total if clean_total else 0.0
    print(f"样本：错误 {gold_total} 条标签 / 干净 {clean_total} 份")
    print(f"错误召回（标签被引擎复现）：{gold_covered}/{gold_total} = {recall:.1%}")
    print(f"干净样本误报率：{clean_fp}/{clean_total} = {fp_rate:.1%}")
    print("分类型覆盖：")
    for p, (c, t) in sorted(per_type.items()):
        print(f"  {p:6s} {c}/{t} = {c/t:.0%}")


if __name__ == "__main__":
    main()
