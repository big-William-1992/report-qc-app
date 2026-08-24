#!/usr/bin/env python3
"""CPT + SFT 两阶段训练数据生成器。

阶段1 CPT：正确报告纯文本，让模型学会「正常报告长什么样」
阶段2 SFT：错误报告 + 标注，教模型输出结构化错误类型

输出：
  data/cpt_reports.jsonl   — CPT 数据（{"text":"报告内容"}）
  data/sft_qc.jsonl        — SFT 数据（ChatML 格式）
"""
import argparse
import json
import os
import random
import sys
from typing import List, Dict, Tuple

# ── 复用正确报告生成器 ──
sys.path.insert(0, os.path.dirname(__file__))
from gen_normal_first_dataset import (
    _chest_reports, _abdomen_reports, _head_reports,
    _pelvis_reports, _thyroid_reports, _breast_reports,
    _spine_joint_reports, inject_typo, inject_multi_typo,
    inject_contradiction, SYSTEM_PROMPT, TYPO_MAP, _finding,
)

# ── CPT: 报告组合模板 ──────────────────────────────
# 把多份正确报告拼成一段连续文本，让模型学习报告的「体感」
REPORT_CONNECTORS = [
    "\n\n---\n\n",
    "\n\n",
    "\n【下一份报告】\n\n",
    "\n【病例 {}】\n\n",
]

def build_cpt_record(texts: List[str]) -> Dict:
    """将多份正确报告拼成一条 CPT 记录。"""
    sep = random.choice(REPORT_CONNECTORS)
    combined = sep.join(texts)
    return {"text": combined}


def build_cpt_single(text: str) -> Dict:
    """单份报告作为 CPT 记录。"""
    return {"text": text}


# ── SFT: ChatML 格式 ──────────────────────────────
def build_sft_record(report_text: str, findings) -> Dict:
    if isinstance(findings, dict):
        findings = [findings]
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": report_text},
        {"role": "assistant", "content": json.dumps(findings, ensure_ascii=False)},
    ]}


# ── 更多正确报告变体生成器 ──────────────────────────
def augment_report(text: str) -> str:
    """对正确报告做微小变异（换数值、换措辞），扩充数据量。"""
    import re

    # 换年龄
    age_match = re.search(r'(\d{2,3})岁', text)
    if age_match:
        old_age = int(age_match.group(1))
        new_age = old_age + random.randint(-5, 5)
        new_age = max(25, min(80, new_age))
        text = text.replace(age_match.group(0), f"{new_age}岁", 1)

    # 换大小
    size_match = re.search(r'约(\d+\.\d+)cm', text)
    if size_match:
        old_size = float(size_match.group(1))
        delta = random.uniform(-0.2, 0.2)
        new_size = max(0.1, round(old_size + delta, 1))
        text = text.replace(size_match.group(0), f"约{new_size}cm", 1)

    # 换措辞
    replacements = [
        ("未见明显异常", random.choice(["未见明显异常", "未见异常", "未见确切异常"])),
        ("边界清", random.choice(["边界清晰", "边界清楚", "边界清"])),
        ("建议定期随访", random.choice(["建议定期随访", "建议复查", "建议随诊"])),
        ("大小形态正常", random.choice(["大小形态正常", "大小形态如常", "形态正常"])),
        ("密度均匀", random.choice(["密度均匀", "回声均匀", "信号均匀"])),
    ]
    for old, new in replacements:
        if old in text and old != new:
            text = text.replace(old, new, 1)
            break

    return text


def main():
    parser = argparse.ArgumentParser(description="CPT + SFT 两阶段训练数据")
    parser.add_argument("--cpt-out", default="data/cpt_reports.jsonl")
    parser.add_argument("--sft-out", default="data/sft_qc.jsonl")
    parser.add_argument("--cpt-augment", type=int, default=3,
                        help="每份正确报告生成几个变体（CPT用）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    # ══════════════════════════════════════════════
    #  阶段1: CPT 数据
    # ══════════════════════════════════════════════
    print("=" * 50)
    print("阶段1: 生成 CPT 数据（正确报告）")
    print("=" * 50)

    all_clean = []
    all_clean.extend(_chest_reports())
    all_clean.extend(_abdomen_reports())
    all_clean.extend(_head_reports())
    all_clean.extend(_pelvis_reports())
    all_clean.extend(_thyroid_reports())
    all_clean.extend(_breast_reports())
    all_clean.extend(_spine_joint_reports())

    # 去重
    seen, clean_reports = set(), []
    for r in all_clean:
        k = r.strip()
        if k not in seen:
            seen.add(k)
            clean_reports.append(k)

    print(f"原始正确报告：{len(clean_reports)} 份")

    cpt_records = []

    # 1. 单份报告
    for text in clean_reports:
        cpt_records.append(build_cpt_single(text))

    # 2. 变体扩充
    for text in clean_reports:
        for _ in range(args.cpt_augment):
            augmented = augment_report(text)
            if augmented != text:
                cpt_records.append(build_cpt_single(augmented))

    # 3. 多份报告组合（3-5份拼成一段）
    shuffled = list(clean_reports)
    random.shuffle(shuffled)
    for i in range(0, len(shuffled) - 2, 3):
        group = shuffled[i:i + random.randint(3, 5)]
        cpt_records.append(build_cpt_record(group))

    random.shuffle(cpt_records)

    os.makedirs(os.path.dirname(args.cpt_out) or ".", exist_ok=True)
    with open(args.cpt_out, "w", encoding="utf-8") as f:
        for rec in cpt_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"CPT 数据：{len(cpt_records)} 条")
    print(f"  ├─ 单份报告：{len(clean_reports)}")
    print(f"  ├─ 变体：{len(clean_reports) * args.cpt_augment}")
    print(f"  └─ 组合：{len(cpt_records) - len(clean_reports) - len(clean_reports) * args.cpt_augment}")

    # ══════════════════════════════════════════════
    #  阶段2: SFT 数据
    # ══════════════════════════════════════════════
    print()
    print("=" * 50)
    print("阶段2: 生成 SFT 数据（错误报告 + 标注）")
    print("=" * 50)

    sft_records = []
    seen_inputs = set()

    def _add(text, findings):
        key = text.strip()
        if key not in seen_inputs:
            seen_inputs.add(key)
            sft_records.append(build_sft_record(text, findings))

    # 正确报告 → []
    for text in clean_reports:
        _add(text, [])

    # 错别字注入
    error_pool = random.sample(clean_reports, min(len(clean_reports) // 3, 60))
    for text in error_pool:
        result = inject_typo(text)
        if result:
            _add(*result)

    for text in error_pool[:len(error_pool) // 2]:
        t, f = inject_multi_typo(text)
        if f:
            _add(t, f)

    # 上下文矛盾
    for text in error_pool[:len(error_pool) // 3]:
        t, f = inject_contradiction(text)
        if f:
            _add(t, f)

    random.shuffle(sft_records)

    os.makedirs(os.path.dirname(args.sft_out) or ".", exist_ok=True)
    with open(args.sft_out, "w", encoding="utf-8") as f:
        for rec in sft_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    pos = sum(1 for r in sft_records if json.loads(r["messages"][2]["content"]))
    print(f"SFT 数据：{len(sft_records)} 条")
    print(f"  ├─ 正确报告（反例）：{len(sft_records) - pos}")
    print(f"  └─ 错误报告（正例）：{pos}")

    # ══════════════════════════════════════════════
    #  执行命令
    # ══════════════════════════════════════════════
    print()
    print("=" * 50)
    print("执行命令")
    print("=" * 50)
    print(f"""
# ═══ 阶段1: CPT（让模型学会正确报告） ═══
bl dataset validate --file {args.cpt_out} --schema cpt
bl dataset upload --file {args.cpt_out}
# 拿到 file-cpt-xxx 后：
bl finetune text create \\
  --base-model qwen3-4b-instruct-2507 \\
  --datasets file-cpt-xxx \\
  --training-type cpt \\
  --model-name qc-cpt-stage1

# 等 CPT 完成
bl finetune watch --job-id ft-cpt-xxx --follow

# ═══ 阶段2: SFT（教模型标注错误） ═══
bl dataset validate --file {args.sft_out}
bl dataset upload --file {args.sft_out}
# 拿到 file-sft-xxx 后：
bl finetune text create \\
  --base-model qc-cpt-stage1 \\
  --datasets file-sft-xxx \\
  --training-type sft-lora \\
  --model-name qc-typo-ctx-v2

# 等 SFT 完成
bl finetune watch --job-id ft-sft-xxx --follow

# ═══ 部署 ═══
bl finetune export --job-id ft-sft-xxx --checkpoint ckpt-best --model-name qc-typo-ctx-v2
bl deploy text create --model-name qc-typo-ctx-v2 --display-name 质控模型
""")


if __name__ == "__main__":
    main()
