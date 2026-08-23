"""中文放射报告质控训练集构造流水线（三步走）。

把「英文错误集做方法种子 + 确定性引擎做规则蒸馏 + 本地 teacher 合成中文错例」
三者合并为 LlamaFactory 可用的 alpaca jsonl（schema 见 train/dataset_info.json）。

步骤：
  1) 英文种子：用 dataset_adapters 把 CorBenchX/ReXErr/llm4proofreading 对齐成训练样本
     （生产环境建议先机翻为中文，或仅作 few-shot 种子；demo 中直接使用以验证链路）
  2) 规则蒸馏：用确定性 RuleEngine 在中文干净报告上产出银标（高精）
  3) teacher 合成：对中文干净报告注入错误 → 训练对
     - --teacher synth ：本仓库确定性合成器（cn_error_synth，无需 GPU/模型，可验证）
     - --teacher llm  ：本地 Ollama/vLLM 微调 teacher（需先起服务并改 llm_config.json）

用法：
  python3 tools/build_cn_dataset.py --chinese-reports 报告目录 --out data/qc_sft.jsonl --teacher synth
  python3 tools/build_cn_dataset.py --demo --out /tmp/qc_cn_demo.jsonl
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

from engine import RuleEngine, extract_meta  # noqa: E402
from dataset_adapters import (corbenchx_records, rexerr_records,  # noqa: E402
                              llm4proofreading_records, INSTRUCTION)
from cn_error_synth import synthesize, to_record  # noqa: E402
from llm_inject import inject_errors, verify_injection, to_record as inject_to_record  # noqa: E402
from llm_client import get_llm_client  # noqa: E402


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception:
        return None


def step_rule_distill(reports):
    eng = RuleEngine()
    recs = []
    for text in reports:
        findings = eng.run(text, extract_meta(text))
        out = [{"error_type": f.rule_id, "location": f.snippet or "",
                "severity": f.severity, "confidence": 1.0, "rationale": f.message}
               for f in findings]
        recs.append({"instruction": INSTRUCTION, "input": text,
                     "output": json.dumps(out, ensure_ascii=False)})
    return recs


def step_teacher_synth(reports, teacher):
    recs = []
    if teacher == "synth":
        for text in reports:
            inj, findings = synthesize(text)
            if findings:
                recs.append(to_record(inj, findings))
        return recs
    # teacher == llm
    from llm_qc import run_llm_qc
    for text in reports:
        res = run_llm_qc(text, extract_meta(text))
        if res.get("available") and res.get("findings"):
            out = [{"error_type": it.get("error_type"), "location": it.get("location") or "",
                    "severity": it.get("severity", "low"),
                    "confidence": it.get("confidence"), "rationale": it.get("rationale")}
                   for it in res["findings"]]
            recs.append({"instruction": INSTRUCTION, "input": text,
                         "output": json.dumps(out, ensure_ascii=False)})
    return recs


def step_llm_inject(reports, client, eng, n=1):
    """ReXErr 式注入：LLM 往干净报告注错 → 回检 → 保留通过样本。"""
    recs = []
    kept = 0
    for text in reports:
        res = inject_errors(client, text, n=n)
        if not res:
            continue
        inj, errs = res
        ok, _, missing = verify_injection(inj, errs, eng)
        if ok and errs:
            recs.append(inject_to_record(inj, errs))
            kept += 1
        else:
            print(f"  [inject] 回检未通过，丢弃：{missing}")
    return recs, kept


def step_self_test_inject(reports, eng):
    """离线模拟 LLM 注入（用确定性合成器产出结构化结果），验证 inject→回检→record 全链路。"""
    from llm_inject import FakeInjectionClient
    from cn_error_synth import synthesize
    recs = []
    for text in reports:
        inj, findings = synthesize(text, kinds=("laterality", "typo"))
        if not findings:
            continue
        client = FakeInjectionClient({"injected_report": inj, "errors": findings})
        res = inject_errors(client, text)
        if not res:
            continue
        ok, _, missing = verify_injection(inj, findings, eng)
        if ok:
            recs.append(inject_to_record(inj, findings))
        else:
            print(f"  [self-test-inject] 回检未通过：{missing}")
    return recs


def step_english_seeds(en_dir):
    recs = []
    if not en_dir:
        return recs
    for fp in glob.glob(os.path.join(en_dir, "**", "*"), recursive=True):
        if not os.path.isfile(fp):
            continue
        name = os.path.basename(fp).lower()
        try:
            if "corbench" in name:
                import json as _j
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    recs += corbenchx_records(_j.load(fh))
            elif "rexerr" in name:
                import csv
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    recs += rexerr_records(list(csv.DictReader(fh)))
            elif "proofread" in name or "llm4" in name:
                import json as _j
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    recs += llm4proofreading_records(_j.load(fh))
        except Exception as e:
            print(f"  [warn] 适配器解析失败 {fp}: {e}")
    return recs


DEMO_CN = [
    "患者男，58岁。检查部位：胸部。\n检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。\n诊断印象：右肺上叶磨玻璃结节，考虑良性可能。",
    "患者女，45岁。检查部位：腹部。\n检查所见：肝左叶见一囊性灶，边界清，大小约2.0cm。\n诊断印象：肝左叶囊肿。",
    "患者男，62岁。检查部位：胸部。\n检查所见：纵隔居中，未见明显占位。\n诊断印象：纵隔未见异常。",
]

DEMO_EN_SEED = [
    {"input_report": "FINDINGS: Consolodation at the left base is noted. IMPRESSION: ",
     "error_type": "Spelling Error",
     "error_description": "Misspelling 'Consolidation' as 'Consolodation'."},
    {"input_report": "FINDINGS: The tube is placed in the left main bronchus. IMPRESSION: ",
     "error_type": "Side Confusion",
     "error_description": "Left/right confusion in tube placement."},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chinese-reports", help="中文干净报告目录")
    ap.add_argument("--english-seeds", help="英文错误集目录（CorBenchX/ReXErr/llm4proofreading）")
    ap.add_argument("--out", default="data/qc_sft.jsonl")
    ap.add_argument("--teacher", choices=["synth", "llm"], default="synth")
    ap.add_argument("--inject", action="store_true",
                    help="启用 ReXErr 式 LLM 注入（需可用 LLM：本地/院内或脱敏云）")
    ap.add_argument("--self-test-inject", action="store_true",
                    help="离线模拟 LLM 注入，验证 inject→回检→record 全链路（合成数据，无需网络）")
    ap.add_argument("--inject-n", type=int, default=1, help="每份报告注入的错误数")
    ap.add_argument("--demo", action="store_true", help="使用内置样例验证整条链路（无需外部数据）")
    args = ap.parse_args()

    cn_reports = []
    if args.demo:
        cn_reports = DEMO_CN
        en_recs = corbenchx_records(DEMO_EN_SEED)
    else:
        if args.chinese_reports:
            for ext in ("*.txt", "*.md"):
                cn_reports += [p for p in glob.glob(
                    os.path.join(args.chinese_reports, "**", ext), recursive=True)]
            cn_reports = [_read(p) for p in cn_reports if _read(p)]
        en_recs = step_english_seeds(args.english_seeds or "")

    print(f"中文报告：{len(cn_reports)} 份；英文种子：{len(en_recs)} 条")

    rule_recs = step_rule_distill(cn_reports) if cn_reports else []
    synth_recs = step_teacher_synth(cn_reports, args.teacher) if cn_reports else []
    inject_recs = []
    if args.self_test_inject and cn_reports:
        inject_recs = step_self_test_inject(cn_reports, RuleEngine())
        print(f"  [self-test-inject] 模拟 LLM 注入通过 {len(inject_recs)} 条")
    elif args.inject and cn_reports:
        client = get_llm_client()
        if client.available():
            inject_recs, kept = step_llm_inject(cn_reports, client, RuleEngine(), n=args.inject_n)
            print(f"  [inject] LLM 注入通过 {kept} 条")
        else:
            print("  [inject] LLM 不可用（检查 src/llm_config.json 的 provider/endpoint），跳过")
    all_recs = en_recs + rule_recs + synth_recs + inject_recs

    pos = sum(1 for r in all_recs if json.loads(r["output"]))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in all_recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已写出 {len(all_recs)} 条 -> {args.out}")
    print(f"  英文种子 {len(en_recs)} | 规则蒸馏 {len(rule_recs)} | teacher合成 {len(synth_recs)} | LLM注入 {len(inject_recs)}")
    print(f"  含正例（非空 output）{pos} 条")


if __name__ == "__main__":
    main()
