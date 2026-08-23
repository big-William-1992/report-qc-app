"""微调效果评测：在独立 eval 集上对比「微调后 vs 底座」。

指标：
  - 样本级准确率：错误样本判出全部期望类型 / 干净样本判空
  - 类型级召回：gold 错误类型被命中的比例
  - 干净样例假阳性率
用法：
  python3 tools/eval_finetune.py --model qwen3:4b [--eval data/qc_sft_eval.jsonl] [--limit 50]
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from llm_qc import run_llm_qc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--provider", default="ollama", choices=["ollama", "mlx"])
    ap.add_argument("--adapter", default=None, help="mlx provider: LoRA adapter 目录")
    ap.add_argument("--eval", default="data/qc_sft_eval.jsonl")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只测前 N 条（快速冒烟）")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.eval, encoding="utf-8") if l.strip()]
    if args.limit > 0:
        # 保持正负均衡抽样
        pos = [r for r in rows if json.loads(r["output"])]
        neg = [r for r in rows if not json.loads(r["output"])]
        half = max(1, args.limit // 2)
        rows = pos[:half] + neg[:half]

    cfg = {"provider": args.provider, "model": args.model, "timeout": 300}
    if args.provider == "mlx":
        cfg["adapter_path"] = args.adapter
    n_ok = 0
    gold_hits = gold_total = 0
    fp = clean_n = 0
    lat = []
    t0 = time.time()
    for r in rows:
        gold = json.loads(r["output"])
        res = run_llm_qc(r["input"], None, config=cfg)
        if not res.get("available") or res.get("error"):
            continue
        lat.append(time.time() - t0 - sum(lat))
        det = {str(f.get("error_type") or "").split("-")[0]
               for f in res.get("findings", [])}
        if not gold:
            clean_n += 1
            if not res.get("findings"):
                n_ok += 1
            else:
                fp += 1
        else:
            hit = sum(1 for g in gold if g["error_type"].split("-")[0] in det)
            gold_hits += hit
            gold_total += len(gold)
            if hit == len(gold):
                n_ok += 1

    total = clean_n + (gold_total and len(rows) - clean_n or 0)
    print(f"模型={args.model}  样本={len(rows)}（干净{clean_n}/错误{len(rows)-clean_n}）"
          f"  总耗时={time.time()-t0:.0f}s  平均={sum(lat)/max(1,len(lat)):.1f}s")
    print(f"样本级准确率: {n_ok}/{len(rows)} = {n_ok/max(1,len(rows)):.0%}")
    print(f"类型级召回 : {gold_hits}/{gold_total} = {gold_hits/max(1,gold_total):.0%}")
    print(f"干净假阳性 : {fp}/{clean_n} = {fp/max(1,clean_n):.0%}")


if __name__ == "__main__":
    main()
