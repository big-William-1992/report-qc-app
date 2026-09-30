#!/usr/bin/env python3
"""
tools/semantic_eval.py — 语义评测集脚手架（2026-09-30 新增）

## 为什么需要它

现有评测集 `data/qc_sft_eval.jsonl`（200 例）的标签是**由规则引擎自身蒸馏/注入**
得到的银标 → 规则链路在它上面 100%/100% 属**自证**；而 LLM 层在同一集上只有
9.9% 召回，因为该集几乎全是确定性错误，**测不出 LLM 的目标价值**
（它要抓的是"随访建议缺失、良恶性与处置不匹配"这类语义漏报）。

本工具把"人工语义评测集"这条路固化下来（标注本身必须由医生做）：

    # ① 从医生反馈/报告池生成**待标注模板**（label 留空）
    python3 tools/semantic_eval.py template --from-feedback --out data/semantic_eval/candidates.jsonl
    python3 tools/semantic_eval.py template --from-jsonl data/reports.jsonl --limit 300 \\
            --out data/semantic_eval/pool.jsonl

    # ② 医生按 docs/EVAL_SET_GUIDE.md 填写 label；然后校验
    python3 tools/semantic_eval.py validate data/semantic_eval/labeled.jsonl

    # ③ 打分：研究 A（规则 vs 人工金标准）与研究 B（LLM 增量）**分开报**
    python3 tools/semantic_eval.py score data/semantic_eval/labeled.jsonl --llm --json

## 两条硬规则（不遵守就没有证据价值）
1. `label_source` 必须是 `"human"`。银标（规则自蒸馏）一律拒收 —— 否则又是自证。
2. **禁止把"规则+LLM"合成一个指标**。研究 A 看规则的 Se/Sp；研究 B 只看
   规则漏报区上的 Δrecall 与新增误报 ΔFP（见 docs/CLINICAL_VALIDATION_PLAN.md）。
"""
import argparse
import json
import os
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "tools"), os.path.join(ROOT, "src"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_eval  # noqa: E402  复用既有的 run_rules/match/evaluate（单一实现）

DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "semantic_eval")


def _rel(path: str) -> str:
    """仓库内显示相对路径，仓库外直接给绝对路径（避免 ../../../../tmp 这种噪声）。"""
    ap = os.path.abspath(path)
    return os.path.relpath(ap, ROOT) if ap.startswith(ROOT + os.sep) else ap


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _write_jsonl(path: str, rows: List[dict]) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _blank_label() -> dict:
    return {
        "is_true_error": None,      # true/false —— 必填，人工判定
        "error_type": "",           # 如 R19-HOMOPHONE / SEM-FOLLOWUP（语义类见指南）
        "location": "",             # 错误定位片段（能被 match 用来核对）
        "severity": "",             # high/medium/low
        "note": "",                 # 判定理由（尤其"为什么规则没发现"）
    }


def cmd_template(args) -> int:
    rows: List[dict] = []
    if args.from_feedback:
        import badcase_store
        fb = badcase_store.list_recent(limit=10**9)
        seen = set()
        for r in fb:
            txt = (r.get("report_text") or "").strip()
            if not txt or txt in seen:
                continue
            seen.add(txt)
            rows.append({
                "id": f"fb-{r.get('id')}",
                "report_text": txt,
                "label_source": "",                     # 待医生填写 "human"
                "label": _blank_label(),
                "engine_at_label_time": {
                    "rule_id": r.get("rule_id") or "",
                    "message": r.get("message") or "",
                    "snippet": r.get("snippet") or "",
                    "suggestion": r.get("suggestion") or "",
                },
                "meta": {"origin": f"feedback:{(r.get('feedback_type') or 'other')}",
                         "doctor_note": r.get("user_note") or "",
                         "ts": r.get("ts") or ""},
            })
    elif args.from_jsonl:
        with open(args.from_jsonl, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                txt = (d.get("report_text") or d.get("report") or d.get("input") or "").strip()
                if not txt:
                    continue
                rows.append({
                    "id": f"pool-{i}",
                    "report_text": txt,
                    "label_source": "",
                    "label": _blank_label(),
                    "engine_at_label_time": {},
                    "meta": {"origin": "pool", "src": os.path.basename(args.from_jsonl)},
                })
    else:
        print("请指定 --from-feedback 或 --from-jsonl <文件>", file=sys.stderr)
        return 2

    if args.limit:
        rows = rows[: args.limit]
    out = args.out or os.path.join(DEFAULT_OUT_DIR, "candidates.jsonl")
    _write_jsonl(out, rows)
    print(f"已生成待标注模板 {len(rows)} 条 → {_rel(out)}")
    print("下一步：医生按 docs/EVAL_SET_GUIDE.md 填写 label 与 label_source=\"human\"，"
          "然后跑 validate。")
    return 0


def _load(path: str) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def cmd_validate(args) -> int:
    rows = _load(args.file)
    problems: List[str] = []
    silver = 0
    ids = set()
    n_true = n_false = 0
    for i, r in enumerate(rows, 1):
        rid = r.get("id") or f"#{i}"
        if rid in ids:
            problems.append(f"{rid}: id 重复")
        ids.add(rid)
        if not (r.get("report_text") or "").strip():
            problems.append(f"{rid}: report_text 为空")
        ls = (r.get("label_source") or "").strip()
        if ls != "human":
            silver += 1
            problems.append(f"{rid}: label_source={ls!r}（必须是 'human'；"
                            "银标/规则自蒸馏的标签不能作为效能证据）")
        lab = r.get("label") or {}
        val = lab.get("is_true_error")
        if not isinstance(val, bool):
            problems.append(f"{rid}: label.is_true_error 必须是 true/false（当前 {val!r}）")
        elif val:
            n_true += 1
            if not (lab.get("error_type") or "").strip():
                problems.append(f"{rid}: 判为有错但未填 error_type")
        else:
            n_false += 1

    print(f"文件: {_rel(args.file)}  共 {len(rows)} 条"
          f"（有错 {n_true} / 无错 {n_false}）")
    if silver:
        print(f"⚠️ 其中 {silver} 条不是人工标签")
    if problems:
        print(f"✗ 校验未通过，{len(problems)} 个问题：")
        for p in problems[:20]:
            print("   - " + p)
        return 1
    print("✓ 校验通过（全部为人工标签且字段完整）")
    print("提示：若集合主要来自医生反馈（富集了规则漏报），报告效能时需说明"
          "抽样偏倚，不能直接当作总体召回率。")
    return 0


def _by_origin(rows: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        o = ((r.get("meta") or {}).get("origin") or "unknown").split(":")[0]
        out[o] = out.get(o, 0) + 1
    return out


def cmd_score(args) -> int:
    rows = _load(args.file)
    rc = cmd_validate(argparse.Namespace(file=args.file))
    if rc != 0 and not args.force:
        print("（校验未通过；如仍要打分请加 --force）", file=sys.stderr)
        return rc

    cases = []
    for r in rows:
        lab = r.get("label") or {}
        expect = []
        if lab.get("is_true_error"):
            expect.append({"error_type": lab.get("error_type") or "",
                           "location": lab.get("location") or ""})
        cases.append({"report": r["report_text"], "expect": expect, "id": r.get("id")})

    rule_preds = run_eval.run_rules(cases)
    study_a = run_eval.evaluate(cases, rule_preds, "研究 A：规则引擎 vs 人工金标准")

    result = {
        "n": len(cases),
        "origins": _by_origin(rows),
        "study_a_rules": {k: study_a[k] for k in ("recall", "specificity", "n_err", "n_norm")},
        "study_b_llm_increment": None,
    }

    if args.llm:
        from llm_qc import run_llm_qc
        n_rule_miss = n_rule_miss_llm_found = 0
        n_clean = n_clean_llm_fp = 0
        for c, pred in zip(cases, rule_preds):
            has_err = bool(c["expect"])
            if has_err and not pred:                 # 规则漏报区 → 看 LLM 能否补上
                n_rule_miss += 1
                out = run_llm_qc(c["report"])
                if out.get("findings"):
                    n_rule_miss_llm_found += 1
            elif not has_err and not pred:           # 规则干净的正常样本 → 看 LLM 是否新增误报
                n_clean += 1
                out = run_llm_qc(c["report"])
                if out.get("findings"):
                    n_clean_llm_fp += 1
        delta_recall = (n_rule_miss_llm_found * 100.0 / n_rule_miss) if n_rule_miss else 0.0
        delta_fp = (n_clean_llm_fp * 100.0 / n_clean) if n_clean else 0.0
        result["study_b_llm_increment"] = {
            "rule_miss_total": n_rule_miss,
            "rule_miss_llm_found": n_rule_miss_llm_found,
            "delta_recall": round(delta_recall, 1),
            "clean_total": n_clean,
            "clean_llm_false_positive": n_clean_llm_fp,
            "delta_false_positive": round(delta_fp, 1),
        }
        print("\n===== 研究 B：LLM 在规则漏报区的增量（**不与研究 A 合并**）=====")
        print(f"  增量召回 Δrecall = {n_rule_miss_llm_found}/{n_rule_miss} = {delta_recall:.1f}%")
        print(f"  新增误报 ΔFP     = {n_clean_llm_fp}/{n_clean} = {delta_fp:.1f}%")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.save:
        out = args.save if os.path.isabs(args.save) else os.path.join(ROOT, args.save)
        _ensure_dir(out)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"\n结果已保存 → {_rel(out)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="语义评测集：模板/校验/打分")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="生成待人工标注的模板")
    t.add_argument("--from-feedback", action="store_true", help="取医生反馈（feedback.db）")
    t.add_argument("--from-jsonl", help="从 JSONL 报告池取（字段 report_text/report/input）")
    t.add_argument("--limit", type=int, default=0)
    t.add_argument("--out", default="")
    t.set_defaults(func=cmd_template)

    v = sub.add_parser("validate", help="校验人工标签文件")
    v.add_argument("file")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("score", help="打分（研究 A/B 分开报）")
    s.add_argument("file")
    s.add_argument("--llm", action="store_true", help="加跑 LLM 以计算增量（研究 B）")
    s.add_argument("--json", action="store_true", help="输出 JSON 结果")
    s.add_argument("--save", default="", help="把结果写到指定文件")
    s.add_argument("--force", action="store_true", help="校验不过也强行打分")
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
