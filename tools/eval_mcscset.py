"""
eval_mcscset.py — 用 MCSCSet 医学拼写纠错测试集评估星衍规则引擎的错字检出能力

指标（聚焦错别字类规则 R8-TYPO / R19-HOMOPHONE）：
- 句子级召回：真正有错的句子中，引擎至少检出一个错字的比例
- 字符级召回：黄金错误位置被引擎 span 覆盖的比例
- 精确率：引擎报告的错误中，命中了黄金错误位置的比例
- 对照组误报：对正确句子（correct）跑引擎，报告错别字的句子占比

用法：
  python tools/eval_mcscset.py [--limit 2000] [--full]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from engine import RuleEngine  # noqa: E402

TYPO_RULES = ("R8-TYPO", "R19-HOMOPHONE")


def golden_error_spans(wrong: str, correct: str):
    """用 difflib 对齐 wrong/correct，返回 wrong 文本中的错误区间列表 [(start,end)]。"""
    import difflib
    spans = []
    sm = difflib.SequenceMatcher(a=wrong, b=correct, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            spans.append((i1, i2))
        elif tag == "delete":
            spans.append((i1, i2))
        # insert 无法映射到 wrong 位置，忽略
    return spans


def hit_ratio(finding_spans, gold_spans):
    """finding 的 span 与黄金错误区间是否有重叠覆盖。"""
    if not gold_spans:
        return 0.0
    covered = [False] * len(finding_spans)
    for f_idx, (fs, fe) in enumerate(finding_spans):
        for gs, ge in gold_spans:
            if fs < ge and gs < fe:  # 有重叠
                covered[f_idx] = True
                break
    return sum(covered) / len(finding_spans)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    test_file = ROOT / "data/mcscset/data/mcsc_benchmark_dataset/test.txt"
    lines = [l.rstrip("\n") for l in test_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"加载测试集: {len(lines)} 条")

    # 统计用 wrong 句跑：句子级召回 / 字符级召回 / 精确率
    eng = RuleEngine()
    n_bad = 0            # 真实有错的句子（gold 非空）
    n_sent_recall = 0    # 检出了至少一个错字的有错句子
    gold_char_total = 0  # 黄金错误字符总数
    gold_char_hit = 0    # 被引擎 span 覆盖的黄金错误字符数
    n_finding = 0        # 引擎报告的错字 finding 总数
    n_finding_hit = 0    # 其中命中黄金错误的

    t0 = time.time()
    for idx, line in enumerate(lines[: args.limit] if not args.full else lines):
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        wrong, correct = parts[0], parts[1]
        gold = golden_error_spans(wrong, correct)
        if not gold:
            continue  # 该对没有可映射错误（极少），跳过
        n_bad += 1
        gold_char_total += sum(e - s for s, e in gold)

        findings = eng.run(wrong, {})
        typo_spans = [(f.span[0], f.span[1]) for f in findings
                      if f.rule_id in TYPO_RULES and f.span and f.span != (-1, -1)]
        if typo_spans:
            n_sent_recall += 1
            n_finding += len(typo_spans)
            # 字符级：计算每个被覆盖的黄金字符
            for gs, ge in gold:
                for fs, fe in typo_spans:
                    if fs < ge and gs < fe:
                        gold_char_hit += min(ge, fe) - max(gs, fs)
                        break
            for fs, fe in typo_spans:
                for gs, ge in gold:
                    if fs < ge and gs < fe:
                        n_finding_hit += 1
                        break

        if (idx + 1) % 500 == 0:
            print(f"  [{idx+1}/{min(len(lines), args.limit)}] {time.time()-t0:.1f}s")

    # 对照组：抽前 1000 条正确文本跑引擎，统计误报
    n_ctrl = 0
    n_ctrl_fp = 0
    for line in lines[:1000] if not args.full else lines[:1000]:
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        correct = parts[1]
        n_ctrl += 1
        findings = eng.run(correct, {})
        if any(f.rule_id in TYPO_RULES and f.span and f.span != (-1, -1) for f in findings):
            n_ctrl_fp += 1

    print("\n===== 评估结果 =====")
    print(f"有错句子数        : {n_bad}")
    print(f"句子级召回        : {n_sent_recall/n_bad:.1%}  ({n_sent_recall}/{n_bad})")
    print(f"字符级召回        : {gold_char_hit/gold_char_total:.1%}  ({gold_char_hit}/{gold_char_total} 字符)")
    print(f"finding 精确率    : {n_finding_hit/n_finding:.1%}  ({n_finding_hit}/{n_finding})" if n_finding else "finding 精确率    : N/A (0 findings)")
    print(f"对照组误报句占比  : {n_ctrl_fp/n_ctrl:.1%}  ({n_ctrl_fp}/{n_ctrl})")
    print(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
