"""质控 SFT 数据集生成器（规则蒸馏 + 模型蒸馏）。

两种数据源，输出统一为 LlamaFactory 可用的 alpaca jsonl（instruction/input/output）：
  - mode=rule    ：用确定性 RuleEngine 在原始报告上产出「银标」，把规则行为蒸馏进小模型
  - mode=teacher ：调用本地微调 teacher（llm_client）产出标签，把大模型能力蒸馏进小模型
  - mode=both    ：teacher 优先，teacher 空时回退规则

输出 output 字段：JSON 数组字符串（error_type/location/severity/confidence/rationale），
无问题为 "[]"。schema 与推理端 llm_prompt / llm_qc 完全一致，训推对齐。

用法：
  python3 tools/build_qc_dataset.py --reports 报告目录 --out data/qc_sft.jsonl --mode rule
  python3 tools/build_qc_dataset.py --reports 报告目录 --out data/qc_sft.jsonl --mode teacher
  python3 tools/build_qc_dataset.py --reports 报告目录 --out data/qc_sft.jsonl --mode both --teacher-model qwen2.5:3b-qc
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine, extract_meta  # noqa: E402

INSTRUCTION = ("你是资深放射科质控专家，审查下面的放射诊断报告，"
               "找出语义/语境类错误（含规则覆盖的硬错误），"
               "仅输出一个 JSON 数组，每条含 error_type/location/severity/confidence/rationale；"
               "无问题输出 []。")


def _rule_to_output(findings):
    out = []
    for f in findings:
        out.append({
            "error_type": f.rule_id,
            "location": f.snippet or "",
            "severity": f.severity,
            "confidence": 1.0,
            "rationale": f.message,
        })
    return out


def _read_reports(directory):
    paths = []
    for ext in ("*.txt", "*.md", "*.text"):
        paths += glob.glob(os.path.join(directory, "**", ext), recursive=True)
    return sorted(set(paths))


def build(rule_eng, reports_dir, out_path, mode, teacher_cfg=None, limit=None):
    from llm_qc import run_llm_qc  # 延迟导入，rule 模式无需联网

    records = []
    pos = neg = 0
    paths = _read_reports(reports_dir)
    if limit:
        paths = paths[:limit]

    for p in paths:
        with open(p, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        meta = extract_meta(text)
        output_obj = []

        if mode in ("rule", "both"):
            output_obj = _rule_to_output(rule_eng.run(text, meta))

        if mode in ("teacher", "both"):
            res = run_llm_qc(text, meta, config=teacher_cfg)
            if res.get("available") and res.get("findings"):
                output_obj = [
                    {k: it.get(k) for k in
                     ("error_type", "location", "severity", "confidence", "rationale")}
                    for it in res["findings"]
                ]

        if output_obj:
            pos += 1
        else:
            neg += 1

        records.append({
            "instruction": INSTRUCTION,
            "input": text,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"已写出 {len(records)} 条 -> {out_path}  (正样本 {pos}, 负样本 {neg})")
    return len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", required=True, help="原始报告目录")
    ap.add_argument("--out", default="data/qc_sft.jsonl")
    ap.add_argument("--mode", choices=["rule", "teacher", "both"], default="rule")
    ap.add_argument("--teacher-model", default=None, help="teacher 模型名（覆盖 llm_config）")
    ap.add_argument("--limit", type=int, default=None, help="仅处理前 N 篇（调试用）")
    args = ap.parse_args()

    eng = RuleEngine()
    teacher_cfg = {"model": args.teacher_model} if args.teacher_model else None
    build(eng, args.reports, args.out, args.mode, teacher_cfg, args.limit)


if __name__ == "__main__":
    main()
