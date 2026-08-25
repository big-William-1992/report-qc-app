#!/usr/bin/env python3
"""badcase 训练数据导出器 (P1-4 闭环出口, 2026-08-25)。

从 feedback.db 读取医生反馈, 产出两类物料:

1. false_positive → 规则调参清单 (CSV)
   每条 = 被质疑的规则 + 报告片段, 供人工复核后:
   - 加入忽略名单 / 调整阈值 / 收窄词典

2. missed → 半成品 SFT 样本 (JSONL)
   report + 医生备注已就位, **target 需人工补标**后并入 data/sft_qc_v2.jsonl
   增量精调流程:
     python3 tools/export_badcase_training.py --out-dir exports/
     → 人工补标 exports/missed_sft_todo.jsonl 的 target 字段
     → 合入训练集 → bl dataset upload → bl finetune text create

用法:
  python3 tools/export_badcase_training.py                 # 默认导出到 exports/<日期>/
  python3 tools/export_badcase_training.py --db path/fb.db
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="feedback.db 路径(默认自动定位)")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    import badcase_store as bs

    out_dir = Path(args.out_dir) if args.out_dir else (
        ROOT / "exports" / datetime.now().strftime("%Y%m%d"))
    out_dir.mkdir(parents=True, exist_ok=True)

    st = bs.stats(args.db)
    print(f"反馈库: 总计 {st['total']} 条 {st['by_type']} | 近7天 {st['last_7d']}")
    if not st["total"]:
        print("（库为空——等医生用起来再回来）")
        return

    # 1) 误报清单 → CSV
    fps = bs.list_recent(limit=10**9, feedback_type="false_positive", path=args.db)
    csv_path = out_dir / "false_positive_review.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "时间", "规则ID", "严重度", "引擎文案", "定位片段",
                    "报告摘要", "备注"])
        for r in fps:
            w.writerow([r["id"], r["ts"], r["rule_id"], r["severity"],
                        r["message"][:80], r["snippet"][:60],
                        r["report_text"][:120].replace("\n", " "),
                        r["user_note"][:80]])
    print(f"✅ 误报复核清单: {csv_path} ({len(fps)} 条)")

    # 2) 漏报样本 → SFT 半成品 JSONL
    misses = bs.list_recent(limit=10**9, feedback_type="missed", path=args.db)
    sft_path = out_dir / "missed_sft_todo.jsonl"
    with open(sft_path, "w", encoding="utf-8") as f:
        for r in misses:
            f.write(json.dumps({
                "_todo": "补标 target(JSON数组, 字段 error_type/location/severity/confidence/rationale)",
                "messages": [
                    {"role": "system",
                     "content": "你是资深放射科质控专家，负责审核放射诊断报告。"
                                "仅输出 JSON 数组，每条含 "
                                "error_type/location/severity/confidence/rationale；"
                                "无问题输出 []。"},
                    {"role": "user", "content": r["report_text"]},
                    {"role": "assistant", "content": None,
                     "_doctor_note": r["user_note"]},
                ],
            }, ensure_ascii=False) + "\n")
    print(f"✅ 漏报 SFT 半成品: {sft_path} ({len(misses)} 条, target 待人工补标)")

    # 3) 全量原始备份
    raw = out_dir / "raw_feedback.jsonl"
    n = bs.export_jsonl(str(raw), path=args.db)
    print(f"✅ 原始备份: {raw} ({n} 条)")


if __name__ == "__main__":
    main()
