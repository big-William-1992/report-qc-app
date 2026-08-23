"""按质控规则合成中文放射报告语料（生成 → 自带标签）。

两类样本：
  - clean ：结构/逻辑合规报告（gold=[]，负例）
  - error ：注入违反质控规则的错误；gold 由 RuleEngine 实际回检得出（规则蒸馏）

多样性：模板为参数化组合（部位 × 征象 × 尺寸/数量 × 印象措辞），非固定句式。
自过滤：clean 必须 gold 为空；error 的注入类型必须被引擎检出，否则丢弃。
用法：
  python3 tools/gen_reports.py --out data/qc_sft.jsonl --n 2000 [--seed 20260822]
"""
import argparse
import json
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from engine import RuleEngine, extract_meta  # noqa: E402
from dataset_adapters import INSTRUCTION  # noqa: E402

# ———— 参数化模板：slots 组合展开，乘法级多样 ————
TEMPLATES = [
    {"part": "胸部CT", "gender": None,
     "finding": "{side}见一{lesion}，边界清，大小约{size}cm。",
     "impression": "{side}{lesion}，{verdict}",
     "slots": {"side": ["右肺上叶", "左肺上叶", "右肺下叶", "左肺下叶"],
               "lesion": ["磨玻璃结节", "实性结节", "微小小结节"],
               "size": ["0.4", "0.6", "0.8", "1.0"],
               "verdict": ["考虑良性。", "多考虑良性病变。"]}},
    {"part": "胸部CT", "gender": None,
     "finding": "双肺纹理增多，{neg}实质性占位，纵隔居中。",
     "impression": "支气管炎，{imp_neg}占位。",
     "slots": {"neg": ["未见", "无明显"],
               "imp_neg": ["未见", "无"]}},
    {"part": "胸部CT", "gender": None,
     "finding": "{side}肺见少许斑片影，边缘模糊。",
     "impression": "{side}肺少许炎症。",
     "slots": {"side": ["右", "左"]}},
    {"part": "腹部超声", "gender": None,
     "finding": "肝{lobe}见一囊性灶，边界清，大小约{size}cm。",
     "impression": "肝{lobe}囊肿。",
     "slots": {"lobe": ["左叶", "右叶"], "size": ["1.5", "2.0", "2.6"]}},
    {"part": "腹部超声", "gender": None,
     "finding": "胆囊内见强回声伴声影，大小约{size}cm。",
     "impression": "胆囊结石。",
     "slots": {"size": ["0.8", "1.1", "1.4"]}},
    {"part": "腹部超声", "gender": None,
     "finding": "{kidney}见一{cyst}，边界清，大小约{size}cm。",
     "impression": "{kidney}{cyst}。",
     "slots": {"kidney": ["左肾", "右肾"], "cyst": ["囊肿", "囊性灶"],
               "size": ["1.2", "2.0", "3.0"]}},
    {"part": "头部MR", "gender": None,
     "finding": "{side}基底节区见点状缺血灶，脑室系统对称。",
     "impression": "{side}基底节区腔隙灶。",
     "slots": {"side": ["左侧", "右侧", "双侧"]}},
    {"part": "头部CT", "gender": None,
     "finding": "{side}额叶见一{density}灶，边界清，大小约{size}cm。",
     "impression": "{side}额叶{density}灶，考虑陈旧性。",
     "slots": {"side": ["左", "右"], "density": ["低密度", "稍高密度"],
               "size": ["0.9", "1.5"]}},
    {"part": "颈部超声", "gender": None,
     "finding": "甲状腺{side}叶内见一结节，边界清，大小约{size}cm。",
     "impression": "甲状腺{side}叶结节，{verdict}",
     "slots": {"side": ["左", "右"], "size": ["0.3", "0.5", "0.8"],
               "verdict": ["考虑良性。", "TI-RADS 3类。"]}},
    {"part": "泌尿系超声", "gender": None,
     "finding": "膀胱壁欠光滑，{organ}{abn}。",
     "impression": "{organ}{abn_imp}。",
     "slots": {"organ": ["前列腺", "膀胱"],
               "abn": ["轻度增大", "内见强回声"],
               "abn_imp": ["轻度增大", "结石可能"]}},
    {"part": "腰椎MR", "gender": None,
     "finding": "L{v}/L{v2}椎间盘{abn}，硬膜囊受压。",
     "impression": "L{v}/L{v2}椎间盘{abn_imp}。",
     "slots": {"v": ["4"], "v2": ["5"],
               "abn": ["膨出", "向左后突出", "向右后突出"],
               "abn_imp": ["膨出", "突出"]}},
    {"part": "乳腺超声", "gender": "女",
     "finding": "{side}乳腺{pos}见一低回声结节，大小约{size}cm。",
     "impression": "{side}乳腺结节，BI-RADS {cat}类。",
     "slots": {"side": ["左", "右"], "pos": ["内上象限", "外上象限"],
               "size": ["0.6", "0.9"], "cat": ["3", "4a"]}},
    {"part": "甲状腺超声", "gender": None,
     "finding": "甲状腺实质回声欠均匀，{side}叶内见数个{echo}结节，较大者约{size}cm。",
     "impression": "甲状腺{side}叶{echo}结节，考虑结节性甲状腺肿。",
     "slots": {"side": ["左", "右"], "echo": ["低回声", "混合回声"],
               "size": ["0.4", "0.7"]}},
    {"part": "胸部平片", "gender": None,
     "finding": "{side}肺纹理增多模糊，{side2}肋膈角锐利。",
     "impression": "{side}肺支气管炎改变。",
     "slots": {"side": ["双", "右", "左"], "side2": ["双侧", "右", "左"]}},
    {"part": "妇科超声", "gender": "女",
     "finding": "{side}卵巢内见一囊性暗区，大小约{size}cm，边界清。",
     "impression": "{side}卵巢囊肿，考虑生理性。",
     "slots": {"side": ["左", "右"], "size": ["1.8", "2.5"]}},
    {"part": "肝胆MR", "gender": None,
     "finding": "肝S{s}段见一{lesion}，直径约{size}cm，增强后{enhance}。",
     "impression": "肝S{s}段{lesion}，考虑{dx}。",
     "slots": {"s": ["4", "6", "7", "8"],
               "lesion": ["血管瘤", "囊肿"],
               "size": ["1.2", "2.2"],
               "enhance": ["边缘结节状强化", "无强化"],
               "dx": ["血管瘤", "囊肿"]}},
]

# ———— 注入器：作用于整份文本；返回 (错误文本 or None, 期望规则前缀) ————


def _segments(text):
    lines = text.split("\n")
    f_idx = next((i for i, l in enumerate(lines) if l.startswith("检查所见")), 1)
    i_idx = next((i for i, l in enumerate(lines) if l.startswith("诊断印象")), len(lines) - 1)
    return lines, f_idx, i_idx


def _break_laterality(text, gender_cn=None, rng=None):
    """印象段首个 左↔右 对调 → 跨段左右矛盾（R2）"""
    lines, _, ii = _segments(text)
    seg = lines[ii]
    for ch, other in (("左", "右"), ("右", "左")):
        pos = seg.find(ch)
        if pos != -1:
            lines[ii] = seg[:pos] + other + seg[pos + 1:]
            return "\n".join(lines), "R2"
    return None, "R2"


def _break_typo(text, gender_cn=None, rng=None):
    """TYPO_MAP 正确词→错写，全文随机命中一处（R8/R19）"""
    from cn_error_synth import _CORRECT_TO_WRONG
    items = list(_CORRECT_TO_WRONG.items())
    if rng is not None:
        rng.shuffle(items)
    for correct, wrong in items:
        if correct in text and wrong not in text:
            return text.replace(correct, wrong, 1), "R8"
    return None, "R8"


def _break_qual(text, gender_cn=None, rng=None):
    """描述段尺寸放大到 ≥4cm、印象仍「良性」→ 定性-尺寸矛盾（R22）"""
    import re
    m = re.search(r"大小约(\d(?:\.\d)?)cm", text)
    if not m or float(m.group(1)) >= 4:
        return None, "R22"
    if "良性" not in text:
        return None, "R22"
    big = f"大小约5.{rng.randint(0, 9)}cm" if rng else "大小约5.0cm"
    return text.replace(m.group(0), big, 1), "R22"


def _break_omission(text, gender_cn=None, rng=None):
    """描述段阳性征 → 印象段整体换成无关结论（漏写该器官结论，R5）"""
    lines, _, ii = _segments(text)
    lines[ii] = "诊断印象：建议随访复查。"
    return "\n".join(lines), "R5"


def _break_gender(text, gender_cn=None, rng=None):
    """检查所见追加「另一性别」器官描述 → 性别矛盾（R1）"""
    organ = ("子宫及双侧附件区未见明确异常。" if (gender_cn or "男") == "男"
             else "前列腺未见明确异常。")
    lines, fi, _ = _segments(text)
    lines[fi] = lines[fi].rstrip("。") + "。" + organ
    return "\n".join(lines), "R1"


BREAKERS = [
    ("R2-LATERALITY", _break_laterality),
    ("R8-TYPO", _break_typo),
    ("R22-QUAL", _break_qual),
    ("R5-CONSISTENCY", _break_omission),
    ("R1-GENDER", _break_gender),
]


def gold_labels(text, eng):
    return [{"error_type": f.rule_id, "location": f.snippet or "",
             "severity": f.severity, "confidence": 1.0, "rationale": f.message}
            for f in eng.run(text, extract_meta(text))]


def _expand(tpl, rng):
    slots = {k: rng.choice(v) for k, v in tpl["slots"].items()}
    gender = tpl.get("gender") or rng.choice(["男", "女"])
    age = rng.randint(22, 78)
    part = tpl["part"]
    finding = tpl["finding"].format(**slots)
    impression = tpl["impression"].format(**slots)
    return (f"患者{gender}，{age}岁。检查部位：{part}。\n"
            f"检查所见：{finding}\n诊断印象：{impression}"), gender


def generate(n=40, seed=20260822):
    eng = RuleEngine()
    rng = random.Random(seed)
    recs, seen = [], set()
    bi = 0
    attempts = 0
    max_attempts = n * 60
    while len(recs) < n and attempts < max_attempts:
        attempts += 1
        tpl = TEMPLATES[rng.randrange(len(TEMPLATES))]
        clean, gender = _expand(tpl, rng)
        if clean in seen:
            continue
        # clean 负例：gold 必须为空
        if not gold_labels(clean, eng):
            seen.add(clean)
            recs.append({"instruction": INSTRUCTION, "input": clean, "output": "[]"})
            if len(recs) >= n:
                break
        # error 正例：轮流注入器，必须被引擎检出
        bname, bfunc = BREAKERS[bi % len(BREAKERS)]
        bi += 1
        err, _ = bfunc(clean, gender, rng)
        if err is None or err in seen:
            continue
        g = gold_labels(err, eng)
        if g and any(f["error_type"].startswith(bname.split("-")[0]) for f in g):
            seen.add(err)
            recs.append({"instruction": INSTRUCTION, "input": err,
                         "output": json.dumps(g, ensure_ascii=False)})
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/qc_sft.jsonl")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()
    recs = generate(args.n, args.seed)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    pos = sum(1 for r in recs if json.loads(r["output"]))
    types = Counter(g["error_type"].split("-")[0]
                    for r in recs for g in json.loads(r["output"]))
    print(f"已生成 {len(recs)} 条 -> {args.out}（正例 {pos} / 负例 {len(recs)-pos}）")
    print(f"唯一样本：{len({r['input'] for r in recs})}")
    print("类型分布：" + " ".join(f"{k}×{v}" for k, v in sorted(types.items())))


if __name__ == "__main__":
    main()
