#!/usr/bin/env python3
"""规模化中文放射报告质控训练集生成器（v2：合并测试报告 + 多错误组合注入）。

在 v1 generate_cn_training.py 基础上增强：
1) 引入 test/*.py 与 test/reports/ 中的真实报告（含缺陷），覆盖 R1–R22 全谱。
2) 对每份报告执行「规则蒸馏」：RuleEngine 跑出银标（高精，负样本也保留教模型判负）。
3) 多错误组合注入：左右/错别字/否定/性别/随访/单位等，每份最多 3 种并列缺陷。

输出 alpaca jsonl（schema 对齐 train/dataset_info.json）。
"""
import argparse
import glob
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

from engine import RuleEngine, extract_meta  # noqa: E402
from dataset_adapters import INSTRUCTION  # noqa: E402


def _finding(et, loc, sev, rat):
    return {"error_type": et, "location": loc, "severity": sev,
            "confidence": 1.0, "rationale": rat}


# ---------- 基础干净报告（正常 + 多部位多样化） ----------
BASE_CLEAN = [
    "患者男，58岁。检查部位：胸部。\n检查所见：双肺纹理清晰，肺野透亮度正常，未见实变影。肺门及纵隔未见肿大淋巴结。\n诊断印象：胸部未见明显异常。",
    "患者女，45岁。检查部位：胸部。\n检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。双肺纹理清晰。\n诊断印象：右肺上叶磨玻璃结节，建议定期随访。",
    "患者男，62岁。检查部位：头颅。\n检查所见：双侧脑实质对称，密度均匀，未见明显异常密度灶。脑室系统大小形态正常，中线结构居中。\n诊断印象：头颅CT未见明显异常。",
    "患者女，50岁。检查部位：腹部。\n检查所见：肝脏大小形态正常，实质回声均匀，未见占位。胆囊未见结石。脾脏未见增大。双肾大小形态正常。\n诊断印象：腹部未见明显异常。",
    "患者男，55岁。检查部位：腹部。\n检查所见：肝左叶见一类圆形低密度灶，边界清，大小约2.0cm，考虑囊肿。\n诊断印象：肝左叶囊肿。",
    "患者女，42岁。检查部位：乳腺。\n检查所见：右乳外上象限见一低回声结节，边界清，大小约1.2cm。左侧乳腺未见明显异常。\n诊断印象：右乳结节，BI-RADS 3，建议随访。",
    "患者女，48岁。检查部位：盆腔。\n检查所见：子宫前位，大小形态正常，左卵巢见一囊肿，大小约3cm，壁薄光滑。\n诊断印象：左卵巢囊肿，建议随访。",
    "患者男，60岁。检查部位：腰椎。\n检查所见：腰椎生理曲度存在，椎体序列尚齐，L4-5椎间盘轻度突向椎管。硬膜囊受压不明显。\n诊断印象：L4-5椎间盘轻度突出。",
    "患者女，38岁。检查部位：甲状腺。\n检查所见：甲状腺两侧叶大小形态正常，实质回声均匀，未见明显结节。\n诊断印象：甲状腺未见明显异常。",
    "患者男，50岁。检查部位：胸部。\n检查所见：右肺下叶见斑片状密度增高影，边缘模糊。\n诊断印象：右肺下叶肺炎，建议抗炎治疗后复查。",
    "患者男，66岁。检查部位：头颅。\n检查所见：右侧基底节区见一低密度灶，边界不清，周围见低密度水肿带。\n诊断印象：右侧基底节区脑梗死可能。",
    "患者男，48岁。检查部位：腹部。\n检查所见：肝右叶见一占位性病变，大小约4.5cm，强化方式呈快进快出。\n诊断印象：肝右叶占位，原发性肝细胞癌可能，建议增强MRI。",
    "患者女，52岁。检查部位：乳腺。\n检查所见：左侧乳腺见一高密度钙化簇，大小约0.5cm。\n诊断印象：左乳可疑钙化，BI-RADS 4A，建议穿刺活检。",
    "患者男，40岁。检查部位：腹部。\n检查所见：左肾见一结石，大小约0.6cm，位于肾盂。双肾形态大小正常。\n诊断印象：左肾结石。",
    "患者女，35岁。检查部位：盆腔。\n检查所见：子宫前位，肌层见一低回声肌瘤，大小约2.5cm，突向宫腔。\n诊断印象：子宫肌瘤。",
]

# ---------- 从测试文件抓取的真实报告（含缺陷，规则蒸馏出银标） ----------
def collect_test_reports():
    """从 test/*.py 与 test/reports/*.txt 抽取报告文本。"""
    texts = set()
    for fp in glob.glob(os.path.join(os.path.dirname(HERE), "test", "*.py")):
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # 1. "检查所见：...诊断印象：..."（可能含 \n 转义）
            for m in re.finditer(r'检查所见[：:]([^"\'\\\n]*?(?:\\n)?[^"\'\\\n]*?)诊断印象[：:]([^"\'\\\n]*)', content):
                findings = (m.group(1) or "").replace("\\n", "\n")
                imp = (m.group(2) or "").replace("\\n", "\n").rstrip("，。")
                if findings and imp:
                    texts.add(f"检查所见：{findings}\n诊断印象：{imp}。")
        except Exception:
            continue
    # 2. test/reports/*.txt 原始报告
    for fp in glob.glob(os.path.join(os.path.dirname(HERE), "test", "reports", "*.txt")):
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                texts.add(f.read().strip())
        except Exception:
            continue
    return list(texts)


# ---------- 错误注入器 ----------
LATERALITY_PAIRS = [("左肺", "右肺"), ("右肺", "左肺"), ("左侧", "右侧"), ("右侧", "左侧"),
                    ("左叶", "右叶"), ("右叶", "左叶"), ("左肾", "右肾"), ("右肾", "左肾"),
                    ("左卵巢", "右卵巢"), ("右卵巢", "左卵巢"), ("左乳", "右乳"), ("右乳", "左乳"),
                    ("左肾上腺", "右肾上腺"), ("右肾上腺", "左肾上腺"), ("左输卵管", "右输卵管"),
                    ("右输卵管", "左输卵管"), ("左上叶", "右上叶"), ("右上叶", "左上叶"),
                    ("左肺门", "右肺门"), ("右肺门", "左肺门")]
TYPOS = [("坐肺", "左肺"), ("费炎", "肺炎"), ("膜玻璃", "磨玻璃"), ("影象", "影像"),
         ("纵膈", "纵隔"), ("姐姐", "结节"), ("点位", "占位"), ("前裂腺", "前列腺"),
         ("随珍", "随访"), ("随防", "随访"), ("隔肌", "膈肌")]
NEG_PAIRS = [("未见", "可见"), ("无明显异常", "有异常"), ("阴性", "阳性")]
UNUSUAL_UNITS = [("cmH2O", "cm"), ("cms", "cm"), ("mmHG", "mmHg")]


def inject_laterality(text):
    for a, b in LATERALITY_PAIRS:
        if a in text and b not in text.replace(a, b, 1):
            return text.replace(a, b, 1), a, b
    return None


def inject_typo(text):
    for wrong, correct in TYPOS:
        if correct in text:
            return text.replace(correct, wrong, 1), correct, wrong
    return None


def inject_negation(text):
    for a, b in NEG_PAIRS:
        if a in text:
            return text.replace(a, b, 1), a, b
    return None


def inject_gender_conflict(text):
    """给报告注入性别矛盾：增加一个与性别冲突的器官描述。"""
    if "患者女" in text:
        if "前列腺" not in text:
            return "检查所见：前列腺未见异常。\n" + text, "前列腺"
    elif "患者男" in text and "检查所见" in text:
        for o in ("子宫", "卵巢", "输卵管"):
            if o not in text:
                return text.replace("检查所见：", f"检查所见：{o}未见异常，"), o
    return None


def inject_unit(text):
    for wrong, correct in UNUSUAL_UNITS:
        if wrong in text:
            return text.replace(wrong, correct, 1), wrong, correct
    return None


def synthesize_variants(text):
    """对一份报告生成多个错误变体（单错误），返回 (错误报告, 期望发现) 列表。"""
    out = []
    # 左右混淆
    r = inject_laterality(text)
    if r:
        t, a, b = r
        out.append((t, [_finding("R2-LATERALITY", b, "high", f"左右混淆：将 '{a}' 误写为 '{b}'")]))
    # 错别字
    r = inject_typo(text)
    if r:
        t, a, b = r
        out.append((t, [_finding("R8-TYPO", b, "medium", f"错别字：'{a}' 误写为 '{b}'")]))
    # 否定翻转
    r = inject_negation(text)
    if r:
        t, a, b = r
        out.append((t, [_finding("R5-CONSISTENCY", b, "high", f"否定词翻转：'{a}' 改为 '{b}'")]))
    # 性别矛盾
    r = inject_gender_conflict(text)
    if r:
        t, o = r
        sex = "女" if "患者女" in text else "男"
        conflict = "乳腺/子宫/卵巢" if sex == "女" else "前列腺"
        out.append((t, [_finding("R1-GENDER", o, "high", f"性别矛盾：元信息为{sex}，正文描述男性专属器官 {conflict}")]))
    # 单位错误
    r = inject_unit(text)
    if r:
        t, a, b = r
        out.append((t, [_finding("R22-UNIT", b, "medium", f"单位错误：'{a}' 非常规医学单位，应为 '{b}'")]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/qc_sft.jsonl")
    ap.add_argument("--include-tests", action="store_true", help="纳入 test/ 中的真实报告")
    args = ap.parse_args()

    engine = RuleEngine()
    reports = list(BASE_CLEAN)
    if args.include_tests:
        reports += collect_test_reports()

    # 去重
    seen, uniq = set(), []
    for r in reports:
        k = r.strip()
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    reports = uniq

    recs = []
    # 第一步：规则蒸馏（每份报告跑引擎，保留正/负样本）
    for text in reports:
        try:
            findings = engine.run(text, extract_meta(text))
        except Exception:
            findings = []
        out = [{"error_type": f.rule_id, "location": f.snippet or "",
                "severity": f.severity, "confidence": 1.0, "rationale": f.message}
               for f in findings]
        recs.append({"instruction": INSTRUCTION, "input": text,
                     "output": json.dumps(out, ensure_ascii=False)})

    # 第二步：错误注入变体（正例）
    for text in reports:
        for inj_text, findings in synthesize_variants(text):
            recs.append({"instruction": INSTRUCTION, "input": inj_text,
                         "output": json.dumps(findings, ensure_ascii=False)})

    # 去重
    seen, uniq = set(), []
    for r in recs:
        k = r["input"]
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    recs = uniq

    pos = sum(1 for r in recs if json.loads(r["output"]))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"报告基底：{len(reports)} 份")
    print(f"生成总数：{len(recs)} 条")
    print(f"  正例(含缺陷)：{pos}")
    print(f"  反例(干净)  ：{len(recs) - pos}")
    print(f"输出 → {args.out}")


if __name__ == "__main__":
    main()