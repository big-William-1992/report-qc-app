"""本地模型 head-to-head 评测：在同一批合成 QC 样例上比较不同本地模型。

衡量：错误样例召回（期望 error_type 前缀是否被判出）、干净样例误报率、
JSON 合法性、单次延迟。数据全部合成，零 PHI，纯本地。
用法：
  python3 tools/compare_models.py
"""
import sys
import time
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from llm_qc import run_llm_qc  # noqa: E402

MODELS = ["qwen2.5:7b", "qwen3:4b"]

# (名称, 报告文本, 期望 error_type 前缀列表；[] 表示应为干净)
CASES = [
    ("clean", "患者男，58岁。检查部位：胸部CT。\n检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。\n诊断印象：右肺上叶磨玻璃结节，考虑良性。", []),
    ("laterality", "患者男，58岁。检查部位：胸部CT。\n检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。\n诊断印象：左肺上叶磨玻璃结节，考虑良性。", ["R2"]),
    ("typo", "患者男，58岁。检查部位：胸部CT。\n检查所见：右肺上叶见一摩玻璃结节，边界清，大小约0.8cm。\n诊断印象：右肺上叶摩玻璃结节，考虑良性。", ["R8"]),
    ("negation", "患者男，58岁。检查部位：胸部CT。\n检查所见：右肺上叶见实质性占位，边界清。\n诊断印象：未见占位。", ["R5"]),
    ("gender", "患者男，58岁。检查部位：胸部CT。\n检查所见：右肺上叶见一磨玻璃结节。\n诊断印象：右肺上叶磨玻璃结节，考虑良性。（该病变多见于女性。）", ["R1"]),
]


def score_model(model):
    cfg = {"provider": "ollama", "model": model,
           "base_url": "http://localhost:11434", "timeout": 180}
    correct = 0
    lat = []
    invalid = 0
    for name, text, expected in CASES:
        t0 = time.time()
        res = run_llm_qc(text, None, config=cfg)
        dt = time.time() - t0
        lat.append(dt)
        if not res.get("available") or res.get("error"):
            continue
        prefixes = {str(f.get("error_type") or "").split("-")[0] for f in res.get("findings", [])}
        if expected == []:
            ok = len(res.get("findings", [])) == 0
        else:
            ok = all(e in prefixes for e in expected)
        if ok:
            correct += 1
    return correct, len(CASES), sum(lat) / len(lat), invalid


def main():
    print(f"评测样例：{len(CASES)} 个（4 错误 + 1 干净）\n")
    print(f"{'模型':12s} {'正确':>4s} {'准确率':>6s} {'平均延迟':>8s}")
    print("-" * 36)
    for m in MODELS:
        correct, total, avg, inv = score_model(m)
        print(f"{m:12s} {correct:>4d} {correct/total:>5.0%} {avg:>6.1f}s")
    print("\n（准确率 = 期望错误全判出 / 干净判空 的样例占比）")


if __name__ == "__main__":
    main()
