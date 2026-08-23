#!/usr/bin/env python3
"""规模化生成中文放射报告质控训练集（方案 C 落地）。

策略（参考 ReXErr 方法论，映射本项目 R1-R23）：
1) 从内置报告的「干净版」抽取 N 份多样化中文报告（覆盖多部位/多异常/正常）。
2) 对每份干净报告：
   a. 规则蒸馏：RuleEngine 跑出银标（该报告若自带缺陷则产出正例）。
   b. teacher 合成（确定性）：cn_error_synth.synthesize 注入错误 → 错误报告+期望发现。
   c. 组合注入：更丰富的错误类型（矛盾/侧别/错别字/性别/随访/部位覆盖）。
3) 汇总成 alpaca jsonl，schema 与 train/dataset_info.json 对齐。

用法：
  python3 tools/generate_cn_training.py --out data/qc_sft.jsonl [--variants N] [--seed S]
"""
import argparse
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


# ---------- 多样化中文报告基底（干净/含已知缺陷） ----------
BASE_REPORTS = [
    # 胸部 CT
    {"text": "检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。双肺纹理清晰。\n诊断印象：右肺上叶磨玻璃结节，建议定期随访。",
     "meta": {"applied_site": "胸部"}},
    {"text": "检查所见：双肺纹理清晰，肺野透亮度正常，未见实变影。肺门及纵隔未见肿大淋巴结。\n诊断印象：胸部未见明显异常。",
     "meta": {"applied_site": "胸部"}},
    {"text": "检查所见：右肺下叶见斑片状密度增高影，边缘模糊。左肺上叶见一结节影。\n诊断印象：右肺下叶肺炎，左肺上叶结节，建议复查。",
     "meta": {"applied_site": "胸部"}},
    {"text": "检查所见：左侧胸腔见游离液体影，右侧肺野透亮度正常。\n诊断印象：左侧胸腔积液。",
     "meta": {"applied_site": "胸部"}},
    # 头颅 CT
    {"text": "检查所见：双侧脑实质对称，密度均匀，未见明显异常密度灶。脑室系统大小形态正常，中线结构居中。\n诊断印象：头颅CT未见明显异常。",
     "meta": {"applied_site": "头颅"}},
    {"text": "检查所见：右侧基底节区见一低密度灶，边界不清，周围见低密度水肿带。\n诊断印象：右侧基底节区脑梗死可能。",
     "meta": {"applied_site": "头颅"}},
    # 腹部 CT / 超声
    {"text": "检查所见：肝脏大小形态正常，实质回声均匀，未见占位。胆囊未见结石。脾脏未见增大。双肾大小形态正常。\n诊断印象：腹部未见明显异常。",
     "meta": {"applied_site": "腹部"}},
    {"text": "检查所见：肝左叶见一类圆形低密度灶，边界清，大小约2.0cm，考虑囊肿。\n诊断印象：肝左叶囊肿。",
     "meta": {"applied_site": "腹部"}},
    {"text": "检查所见：肝右叶见一占位性病变，大小约4.5cm，强化方式呈快进快出。\n诊断印象：肝右叶占位，原发性肝细胞癌可能，建议增强MRI。",
     "meta": {"applied_site": "腹部"}},
    {"text": "检查所见：左肾见一结石，大小约0.6cm，位于肾盂。双肾形态大小正常。\n诊断印象：左肾结石。",
     "meta": {"applied_site": "腹部"}},
    # 乳腺
    {"text": "检查所见：右乳外上象限见一低回声结节，边界清，大小约1.2cm。左侧乳腺未见明显异常。\n诊断印象：右乳结节，BI-RADS 3，建议随访。",
     "meta": {"applied_site": "乳腺"}},
    {"text": "检查所见：左侧乳腺见一高密度钙化簇，大小约0.5cm。\n诊断印象：左乳可疑钙化，BI-RADS 4A，建议穿刺活检。",
     "meta": {"applied_site": "乳腺"}},
    # 盆腔
    {"text": "检查所见：子宫前位，大小形态正常，左卵巢见一囊肿，大小约3cm，壁薄光滑。\n诊断印象：左卵巢囊肿，建议随访。",
     "meta": {"applied_site": "盆腔", "gender": "女"}},
    {"text": "检查所见：子宫前位，肌层见一低回声肌瘤，大小约2.5cm，突向宫腔。\n诊断印象：子宫肌瘤。",
     "meta": {"applied_site": "盆腔", "gender": "女"}},
    # 腰椎
    {"text": "检查所见：腰椎生理曲度存在，椎体序列尚齐，L4-5椎间盘轻度突向椎管。硬膜囊受压不明显。\n诊断印象：L4-5椎间盘轻度突出。",
     "meta": {"applied_site": "腰椎"}},
    {"text": "检查所见：腰椎椎体骨质增生，L3-4椎间隙变窄，椎间盘信号减低。\n诊断印象：腰椎退行性改变。",
     "meta": {"applied_site": "腰椎"}},
    # 甲状腺
    {"text": "检查所见：甲状腺两侧叶大小形态正常，实质回声均匀，未见明显结节。\n诊断印象：甲状腺未见明显异常。",
     "meta": {"applied_site": "甲状腺"}},
    {"text": "检查所见：甲状腺右叶见一低回声结节，边界清晰，内见点状强回声。大小约0.8cm。\n诊断印象：甲状腺右叶结节，TI-RADS 3，建议随访。",
     "meta": {"applied_site": "甲状腺"}},
]

# ---------- 组合错误注入器（比 cn_error_synth 更丰富，映射 R*） ----------
LATERALITY_PAIRS = [("左肺", "右肺"), ("右肺", "左肺"), ("左侧", "右侧"), ("右侧", "左侧"),
                    ("左叶", "右叶"), ("右叶", "左叶"), ("左肾", "右肾"), ("右肾", "左肾"),
                    ("左卵巢", "右卵巢"), ("右卵巢", "左卵巢"), ("左乳", "右乳"), ("右乳", "左乳")]

# 错别字（错→对），从引擎的 TYPO_MAP 提取常用项补充
TYPOS = [("坐肺", "左肺"), ("费炎", "肺炎"), ("膜玻璃", "磨玻璃"), ("影象", "影像"),
         ("纵膈", "纵隔"), ("姐姐", "结节"), ("点位", "占位"), ("前裂腺", "前列腺"),
         ("肝右业", "肝右叶"), ("肺门", "肺门"), ("结结", "结节"), ("炫晕", "眩晕")]

NEG_PAIRS = [("未见", "可见"), ("无异常", "有异常"), ("阴性", "阳性")]

FEMALE_ORGANS = ["子宫", "卵巢", "输卵管"]
MALE_ORGANS = ["前列腺", "睾丸"]


def inject_laterality(text):
    for a, b in LATERALITY_PAIRS:
        if a in text:
            return text.replace(a, b, 1), a, b
    return None


def inject_typo(text):
    for wrong, correct in TYPOS:
        if correct in text and wrong not in text:
            return text.replace(correct, wrong, 1), correct, wrong
    return None


def inject_gender_conflict(text, gender):
    """注入性别-器官矛盾：给定报告原文，往描述里加一个与元信息性别冲突的器官。"""
    if gender == "女":
        for o in MALE_ORGANS:
            return f"检查所见：{o}未见异常。\n" + text, o
    for o in FEMALE_ORGANS:
        return f"检查所见：{o}未见异常。\n" + text, o
    return None


def inject_negation(text):
    for a, b in NEG_PAIRS:
        if a in text:
            return text.replace(a, b, 1), a, b
    return None


def inject_followup_missing(text):
    """随访漏写：把报告里的『建议随访/复查』去掉。"""
    if "随访" in text or "复查" in text:
        clean = re.sub(r"[,，]?建议[^。\n]*", "", text)
        return clean, "随访建议缺失"
    return None


def synthesize_variants(base, rng):
    """对一份基础报告生成多个错误变体，返回 (错误报告, 期望发现列表) 的列表。"""
    text = base["text"]
    meta = base.get("meta", {})
    variants = []

    # 1. 左右混淆
    r = inject_laterality(text)
    if r:
        t, a, b = r
        variants.append((t, [_finding("R2-LATERALITY", b, "high", f"左右混淆：将 '{a}' 误写为 '{b}'")]))

    # 2. 错别字
    r = inject_typo(text)
    if r:
        t, a, b = r
        variants.append((t, [_finding("R8-TYPO", b, "medium", f"错别字：'{a}' 误写为 '{b}'")]))

    # 3. 否定词翻转（描述-结论矛盾倾向）
    r = inject_negation(text)
    if r:
        t, a, b = r
        variants.append((t, [_finding("R5-CONSISTENCY", b, "high", f"否定词翻转：'{a}' 改为 '{b}'")]))

    # 4. 性别矛盾
    gender = meta.get("gender", "男" if "乳腺" not in text and "子宫" not in text and "卵巢" not in text else ("女" if "子" in text or "卵" in text else "男"))
    inj_sex = inject_gender_conflict(text, gender)
    if inj_sex:
        t, o = inj_sex
        variants.append((t, [_finding("R1-GENDER", o, "high", f"性别矛盾：报告元信息为{gender}，却描述男性/女性专属器官 {o}")]))

    # 5. 随访漏写
    r = inject_followup_missing(text)
    if r:
        t, _ = r
        variants.append((t, [_finding("R16-FOLLOWUP", "建议随访", "medium", "存在需随访的异常发现，但结论未给出随访建议")]))

    # 6. 描述-结论矛盾（改变结论中的部位）
    m = re.search(r"diagnosis|诊断印象[:：]\s*([^。]*)", text)
    if m:
        conclusion = m.group(1)
        for a, b in LATERALITY_PAIRS:
            if a in conclusion:
                new_conclusion = conclusion.replace(a, b, 1)
                t = text.replace(conclusion, new_conclusion, 1)
                variants.append((t, [_finding("R5-CONSISTENCY", b, "high", f"描述与结论部位矛盾：描述写 '{a}'，结论写 '{b}'")]))
                break

    return variants


def rule_distill(records, out):
    """规则蒸馏：用确定性 RuleEngine 在（含缺陷的）报告上产出银标正例。"""
    eng = RuleEngine()
    for rec in records:
        try:
            findings = eng.run(rec["input"], extract_meta(rec["input"]))
            if findings:
                out.append(rec)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/qc_sft.jsonl")
    ap.add_argument("--variants", type=int, default=8, help="每份基础报告生成的错误变体数")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    recs = []

    # 第一步：对每份基础报告做规約蒸馏（若报告自带缺陷）——产生高精银标
    rule_eng = RuleEngine()
    for base in BASE_REPORTS:
        text = base["text"]
        try:
            findings = rule_eng.run(text, extract_meta(text))
        except Exception:
            findings = []
        # 干净报告对 SFT 也有价值：output=[] 教模型"无问题"
        out = [{"error_type": f.rule_id, "location": f.snippet or "",
                "severity": f.severity, "confidence": 1.0, "rationale": f.message}
               for f in findings]
        recs.append({"instruction": INSTRUCTION, "input": text,
                     "output": json.dumps(out, ensure_ascii=False)})

    # 第二步：错误注入变体（正例）
    for base in BASE_REPORTS:
        variants = synthesize_variants(base, rng)
        for inj_text, findings in variants[:args.variants]:
            if findings:
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

    print(f"基础报告：{len(BASE_REPORTS)}")
    print(f"生成总数：{len(recs)} 条（去重后）")
    print(f"  正例（含缺陷）：{pos}")
    print(f"  反例（干净）   ：{len(recs) - pos}")
    print(f"输出 → {args.out}")


if __name__ == "__main__":
    main()