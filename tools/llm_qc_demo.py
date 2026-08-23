"""LLM 质控层最小演示 / 冒烟测试。

用法：
  python3 tools/llm_qc_demo.py                 # 用内置样例报告
  python3 tools/llm_qc_demo.py 报告.txt         # 读文件
  python3 tools/llm_qc_demo.py --text "..."     # 直接给文本
  python3 tools/llm_qc_demo.py --rag            # 打印 RAG 召回片段
  python3 tools/llm_qc_demo.py --dry-run        # 不真实调用，展示 prompt + mock

真实调用需本地起 Ollama 并拉取模型（如微调后的 qwen2.5:3b-qc）：
  ollama serve && ollama pull qwen2.5:3b-qc
可用环境变量覆盖：OLLAMA_BASE_URL / OLLAMA_MODEL / LLM_PROVIDER
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_prompt import build_qc_prompt
from rag import get_retriever
from llm_qc import run_llm_qc

SAMPLE_REPORT = """检查所见：
右肺上叶见一磨玻璃密度结节，边界清，大小约0.8cm×0.6cm，余两肺纹理清晰，未见实变及胸腔积液。
诊断印象：
右肺上叶磨玻璃结节，考虑良性可能，建议抗炎治疗后复查。
"""

MOCK_RESPONSE = [
    {
        "error_type": "recommendation_mismatch",
        "location": "诊断印象：建议抗炎治疗后复查",
        "severity": "medium",
        "confidence": 0.7,
        "rationale": "磨玻璃结节 0.8cm 通常优先随访而非抗炎，'抗炎治疗后复查'与征象不符，建议改为3-6个月CT随访。",
    }
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", help="报告文本文件路径(.txt)")
    ap.add_argument("--text", help="直接给文本")
    ap.add_argument("--rag", action="store_true", help="打印 RAG 召回片段")
    ap.add_argument("--dry-run", action="store_true", help="不真实调用，展示 prompt + mock")
    args = ap.parse_args()

    if args.text:
        text = args.text
    elif args.report and os.path.exists(args.report):
        with open(args.report, encoding="utf-8") as f:
            text = f.read()
    else:
        text = SAMPLE_REPORT

    retriever = get_retriever("simple")
    ctx = retriever.retrieve(text, top_k=4)
    system, user = build_qc_prompt(text, rag_contexts=ctx)

    print("=" * 30, "SYSTEM PROMPT", "=" * 30)
    print(system)
    print("=" * 30, "USER PROMPT", "=" * 30)
    print(user)
    if args.rag:
        print("=" * 30, "RAG CONTEXTS", "=" * 30)
        print("\n".join(ctx) or "（空）")

    if args.dry_run:
        print("=" * 30, "MOCK LLM RESPONSE (dry-run)", "=" * 30)
        print(json.dumps(MOCK_RESPONSE, ensure_ascii=False, indent=2))
        return

    result = run_llm_qc(text, retriever=retriever)
    print("=" * 30, "LLM QC RESULT", "=" * 30)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
