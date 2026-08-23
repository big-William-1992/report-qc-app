"""将 alpaca 训练集（instruction/input/output）转为百炼 ChatML SFT 格式。

百炼 SFT 要求每条为 {"messages":[{role:system},{role:user},{role:assistant}]}。
我们的：instruction→system，input→user，output→assistant。
用法：
  python3 tools/convert_to_bailian.py --in data/qc_sft.jsonl --out data/qc_sft_bailian.jsonl
合规：当前 qc_sft.jsonl 为 gen_reports 纯合成数据（零 PHI），转格式不涉及出网。
"""
import argparse
import json
import os
import sys


def convert(rec):
    instruction = rec.get("instruction", "")
    inp = rec.get("input", "")
    out = rec.get("output", "")
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": inp},
        {"role": "assistant", "content": out},
    ]
    return {"messages": messages}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/qc_sft.jsonl")
    ap.add_argument("--out", default="data/qc_sft_bailian.jsonl")
    args = ap.parse_args()
    with open(args.inp, encoding="utf-8") as fh:
        lines = [l for l in fh if l.strip()]
    out = [convert(json.loads(l)) for l in lines]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已转换 {len(out)} 条 -> {args.out}（百炼 ChatML SFT 格式）")


if __name__ == "__main__":
    main()
