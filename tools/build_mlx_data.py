"""构造 MLX 训练数据：prompt 与线上推理（run_llm_qc→build_qc_prompt）逐字节一致。

这是准确度的关键前提：微调时见到的 system/user 必须等于部署时 run_llm_qc 发出的
system/user，否则模型学到的是"另一道题"。RAG simple 模式恒为空，故 rag_contexts=None。
用法：
  python3 tools/build_mlx_data.py   # 读 qc_sft_train/eval.jsonl → data/mlx/{train,valid}.jsonl
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from engine import extract_meta          # noqa: E402
from llm_prompt import build_qc_prompt   # noqa: E402


def to_messages(record):
    meta = extract_meta(record["input"])
    system, user = build_qc_prompt(record["input"], meta, rag_contexts=None)
    return {"messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": record["output"]},
    ]}


def main():
    out_dir = os.path.join(HERE, "..", "data", "mlx")
    os.makedirs(out_dir, exist_ok=True)
    for src_name, dst_name in (("qc_sft_train.jsonl", "train.jsonl"),
                               ("qc_sft_eval.jsonl", "valid.jsonl")):
        src = os.path.join(HERE, "..", "data", src_name)
        rows = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
        with open(os.path.join(out_dir, dst_name), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(to_messages(r), ensure_ascii=False) + "\n")
        print(f"{src_name}: {len(rows)} 条 -> data/mlx/{dst_name}")


if __name__ == "__main__":
    main()
