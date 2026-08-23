"""R1–R6 + R12 全错误测试报告 · 一键验证脚本

运行:
    cd report_qc_app/src
    python ../test/run_r1_r7_test.py

验证两件事：
  1) 全错误报告应命中全部规则（R1-R6 + R12；原 R7 男女器官混用已并入 R12-SENTENCE）
  2) 多份正确报告不应产生任何误报（回归）
"""
import os
import sys

# 确保能导入 src/engine.py
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # report_qc_app/
sys.path.insert(0, os.path.join(_ROOT, "src"))

from engine import RuleEngine

NEED = ["R1-GENDER", "R2-LATERALITY", "R3-SCORE", "R4-UNIT",
        "R5-CONSISTENCY", "R6-SITE", "R12-SENTENCE"]


def check(report_path, meta, expect_all=True):
    with open(report_path, encoding="utf-8") as fh:
        text = fh.read()
    # 去掉注释行（# 开头）再送引擎
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    fs = RuleEngine().run(text, meta)
    got = {f.rule_id for f in fs}
    miss = [r for r in NEED if r not in got]
    extra = [r for r in got if r not in NEED]
    if expect_all:
        ok = not miss
        print(f"[{'PASS' if ok else 'FAIL'}] {os.path.basename(report_path)}: "
              f"命中 {sorted(got)}" + (f" | 缺失 {miss}" if miss else "") +
              (f" | 多余 {extra}" if extra else ""))
    else:
        ok = len(fs) == 0
        print(f"[{'PASS' if ok else 'FAIL'}] 正确报告应零误报: 实际 {sorted(got) if got else '无'}")
    return ok


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    report = os.path.join(base, "reports", "R1-R7_全错误测试报告.txt")
    meta = {"gender": "男", "age": "58", "modality": "乳腺", "applied_site": "胸部"}

    print("=== 1) 全错误报告：应命中 R1–R6 + R12 ===")
    ok1 = check(report, meta, expect_all=True)

    print("\n=== 2) 回归：正确报告不应误报 ===")
    correct = [
        ("女+子宫一致", "检查所见：盆腔见子宫增大。诊断印象：子宫肌瘤。",
         {"gender": "女"}),
        ("左右一致", "检查所见：左肾见一占位灶。诊断印象：左肾占位，考虑良性。", {}),
        ("R5一致", "检查所见：左肺上叶见一占位灶。诊断印象：左肺上叶占位，考虑恶性。", {}),
        ("R6一致", "检查所见：盆腔见子宫增大。诊断印象：子宫肌瘤。",
         {"applied_site": "盆腔"}),
        ("R3一致", "检查所见：右乳腺见一结节。诊断印象：右乳腺结节，BI-RADS 4。",
         {"modality": "乳腺"}),
        ("R4一致", "检查所见：左肾见一囊肿，范围约 2.5 cm。诊断印象：左肾囊肿。", {}),
    ]
    ok2 = True
    for name, t, m in correct:
        fs = RuleEngine().run(t, m)
        ok = len(fs) == 0
        ok2 = ok2 and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: " +
              (f"误报 {sorted({f.rule_id for f in fs})}" if fs else "无"))

    print("\n=== 结果 ===")
    print("全错误报告覆盖 R1-R6+R12:", "PASS" if ok1 else "FAIL")
    print("正确报告无假阳性:", "PASS" if ok2 else "FAIL")
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
