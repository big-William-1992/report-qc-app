#!/usr/bin/env python3
"""v2 增强训练集：prompt 鲁棒性 + 正常报告负样本增强。

问题背景：
- v1 模型仅在训练时的完整 system prompt 下输出规范 JSON，
  换简化 prompt 会漂移成自由文本。
- 正常报告占比偏低时模型倾向过度审查。

方案：
1. Prompt 变体池：完整版 / 简化版 / 极简版 / 规则编号版，
   每条样本随机绑定一种变体，让模型学会「无论 prompt 怎么变都输出 JSON」。
2. 从 cpt_reports.jsonl 抽取真实正常报告作负样本（target = []），
   提升正常报告占比、抑制过度审查。
3. 原 sft_qc.jsonl 全部保留并重绑 prompt 变体。

输出：data/sft_qc_v2.jsonl（ChatML，可直接上传百炼）
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_typo_context_dataset import (
    inject_single_typo,
    inject_shape_typo,
    inject_multi_typo,
)

# ── Prompt 变体池 ──────────────────────────────────────
PROMPT_VARIANTS = [
    # 0: 完整版（与 v1 训练一致）
    (
        "你是资深放射科质控专家，负责审核放射诊断报告。\n"
        "你已学习了大量正确报告的模式。现在请审查以下报告：\n"
        "1. 检查错别字（同音字、形近字、输入法误输入）\n"
        "2. 检查上下文对应（描述与结论是否一致、同一器官是否自相矛盾）\n"
        "3. 检查是否符合该系统常见病的报告规范\n"
        "仅输出 JSON 数组，每条含 error_type/location/severity/confidence/rationale；无问题输出 []。\n"
        "error_type 允许：R8-TYPO / R19-HOMOPHONE / R5-CONSISTENCY / R17-PERREGION / R12-SENTENCE / L1-OTHER。"
    ),
    # 1: 简化版（生产环境可能被精简的形态）
    (
        "你是资深放射科质控专家，审查放射诊断报告中的错误。\n"
        "重点：错别字（同音/形近/输入法）、描述与结论一致性。\n"
        "仅输出 JSON 数组，每条含 error_type/location/severity/confidence/rationale；无问题输出 []。"
    ),
    # 2: 极简版
    (
        "审查以下放射诊断报告，找出错别字和前后矛盾之处。"
        "仅输出 JSON 数组（字段：error_type/location/severity/confidence/rationale），无问题输出 []。"
    ),
    # 3: 规则编号版（贴近规则引擎术语）
    (
        "放射报告质控。检测维度：R8-TYPO 错别字、R19-HOMOPHONE 同音字、"
        "R5-CONSISTENCY 描述-结论一致性、R17-PERREGION 分部位矛盾、R12-SENTENCE 句内矛盾。\n"
        "只输出 JSON 数组，每项含 error_type/location/severity/confidence/rationale；无错误输出 []。"
    ),
]

SYSTEM_ROLE_FALLBACK = (
    "你是资深放射科质控专家，负责审核放射诊断报告。"
    "仅输出 JSON 数组，每条含 error_type/location/severity/confidence/rationale；无问题输出 []。"
)


def load_jsonl(path: str):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def normalize_assistant(content: str) -> str:
    """把任意 assistant 输出规整为紧凑 JSON 数组字符串。"""
    text = content.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return json.dumps(parsed, ensure_ascii=False)
        if isinstance(parsed, dict) and "errors" in parsed:
            return json.dumps(parsed["errors"], ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    raise ValueError(f"非 JSON 输出，无法规整: {text[:80]}")


def make_record(system_prompt: str, report_text: str, answer_list) -> dict:
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": report_text},
            {"role": "assistant", "content": json.dumps(answer_list, ensure_ascii=False)},
        ]
    }


def main():
    parser = argparse.ArgumentParser(description="生成 v2 prompt 鲁棒性训练集")
    parser.add_argument("--sft", default="data/sft_qc.jsonl", help="v1 SFT 数据")
    parser.add_argument("--cpt", default="data/cpt_reports.jsonl", help="正常报告库")
    parser.add_argument("--output", default="data/sft_qc_v2.jsonl")
    parser.add_argument("--normal-extra", type=int, default=200,
                        help="从 CPT 库额外抽取的正常报告数")
    parser.add_argument("--typo-extra", type=int, default=120,
                        help="反向注入错别字生成的错误样本数")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    out_records = []

    # 1) v1 数据全保留，assistant 规整为标准数组，重绑 prompt 变体
    sft = load_jsonl(args.sft)
    kept, dropped = 0, 0
    for rec in sft:
        msgs = {m["role"]: m["content"] for m in rec["messages"]}
        user = msgs.get("user", "")
        try:
            answer = normalize_assistant(msgs.get("assistant", ""))
        except ValueError:
            dropped += 1
            continue
        variant = rng.choice(PROMPT_VARIANTS)
        out_records.append(make_record(variant, user, json.loads(answer)))
        kept += 1
    print(f"v1 样本: 保留 {kept} 条（格式异常丢弃 {dropped} 条），已绑定随机 prompt 变体")

    # 2) 从 CPT 库抽正常报告补负样本（排除已在 v1 中出现过的报告文本）
    seen_users = {
        next(m["content"] for m in rec["messages"] if m["role"] == "user").strip()
        for rec in out_records
    }
    cpt = load_jsonl(args.cpt)
    normals = []
    for rec in cpt:
        text = rec.get("text", "").strip()
        if text and text not in seen_users:
            normals.append(text)
            seen_users.add(text)
    rng.shuffle(normals)
    picked = normals[: args.normal_extra]
    for text in picked:
        variant = rng.choice(PROMPT_VARIANTS)
        out_records.append(make_record(variant, text, []))
    print(f"CPT 负样本: 新增 {len(picked)} 条正常报告（target=[]）")

    # 3) 从剩余正常报告反向注入错别字，扩充错误样本
    injectors = [
        lambda t: (lambda r: (r[0], [r[1]]) if r else None)(inject_single_typo(t)),
        lambda t: (lambda r: (r[0], [r[1]]) if r else None)(inject_shape_typo(t)),
        lambda t: inject_multi_typo(t, n=2),
    ]
    pool = normals[args.normal_extra:]
    rng.shuffle(pool)
    injected_count = 0
    for text in pool:
        if injected_count >= args.typo_extra:
            break
        result = rng.choice(injectors)(text)
        if not result:
            continue
        bad_text, findings = result
        if not findings or bad_text == text:
            continue
        variant = rng.choice(PROMPT_VARIANTS)
        out_records.append(make_record(variant, bad_text, findings))
        injected_count += 1
    print(f"错别字注入: 新增 {injected_count} 条错误样本")

    # 4) 打乱并写出
    rng.shuffle(out_records)
    Path(args.output).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_records) + "\n",
        encoding="utf-8",
    )

    # 统计
    n_err = sum(1 for r in out_records if len(r["messages"][2]["content"]) > 4)
    print(f"\n✅ 共 {len(out_records)} 条 → {args.output}")
    print(f"   错误样本 {n_err} 条 / 正常样本 {len(out_records)-n_err} 条")
    vc = {}
    for r in out_records:
        p = r["messages"][0]["content"]
        idx = PROMPT_VARIANTS.index(p) if p in PROMPT_VARIANTS else -1
        vc[f"variant{idx}"] = vc.get(f"variant{idx}", 0) + 1
    print(f"   prompt 变体分布: {vc}")


if __name__ == "__main__":
    main()
