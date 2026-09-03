"""R1–R7 全错误测试报告 · 一键验证脚本

运行:
    cd report_qc_app/src
    python ../tests/run_r1_r7_test.py

验证两件事：
  1) 全错误报告应命中 7 条规则（R1-R7，R6 为独立纯头部用例——正文含肺叶会天然满足
     「申请胸部」的 R6 检查，故 R6 用专门的脑部报告验证）
  2) 多份正确报告不应产生任何误报（回归）

注：现代引擎共 22 条规则（R10 模板合规、R20 必查要素等为后续版本新增），
本脚本只关注 R1-R7 确定性错误，其余规则命中不计失败。
"""
import os
import sys

# 确保能导入 src/engine.py（独立脚本不经 pytest，需自行加路径）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # report_qc_app/
sys.path.insert(0, os.path.join(_ROOT, "src"))

from engine import RuleEngine

# R6 需正文不含申请部位对应器官（详见模块 docstring），单列纯头部用例
NEED = ["R1-GENDER", "R2-LATERALITY", "R3-SCORE", "R4-UNIT",
        "R5-CONSISTENCY", "R7-INTERNAL"]


def check(report_path, meta, expect_all=True):
    with open(report_path, encoding="utf-8") as fh:
        text = fh.read()
    # 去掉注释行（# 开头）再送引擎
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    fs = RuleEngine().run(text, meta)
    got = {f.rule_id for f in fs}
    miss = [r for r in NEED if r not in got]
    if expect_all:
        ok = not miss
        print(f"[{'PASS' if ok else 'FAIL'}] {os.path.basename(report_path)}: "
              f"命中 {sorted(got)}" + (f" | 缺失 {miss}" if miss else ""))
    else:
        noise = {f.rule_id for f in fs}
        ok = len(noise) == 0
        print(f"[{'PASS' if ok else 'FAIL'}] 正确报告应零误报: 实际 {sorted(noise) if noise else '无'}")
    return ok


def check_text(label, text, meta, expect_empty=True):
    fs = RuleEngine().run(text, meta)
    # 回归只看 R1-R7（本脚本定位）；R10 模板合规、R19-R22 等
    # 为其他规则的专项验证（tests/ 下有 pytest 覆盖），不计入本脚本失败
    got = {f.rule_id for f in fs if f.rule_id.split("-")[0] in
           ("R1", "R2", "R3", "R4", "R5", "R6", "R7")}
    if expect_empty:
        ok = len(got) == 0
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: " +
              (f"误报 {sorted(got)}" if got else "无"))
    else:
        ok = bool(got)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: " +
              (f"命中 {sorted(got)}" if got else "未命中"))
    return ok


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    report = os.path.join(base, "reports", "R1-R7_全错误测试报告.txt")
    meta = {"gender": "男", "age": "58", "modality": "乳腺", "applied_site": "胸部"}

    print("=== 1) 全错误报告：应命中 R1-R5、R7（R6 单列） ===")
    ok1 = check(report, meta, expect_all=True)

    print("\n=== 1b) R6 登记部位不符（纯头部报告，申请胸部） ===")
    # R5 需描述段肺叶占位 → 正文含「肺」= chest 族，R6 天然不报；
    # 故 R6 用不含胸部词的脑部报告专项验证
    ok6 = check_text(
        "R6登记部位不符",
        "检查所见：双侧脑实质对称，未见异常。\n诊断印象：脑部未见异常，建议随访。",
        {"applied_site": "胸部"},
        expect_empty=False,
    )

    print("\n=== 2) 回归：正确报告不应误报 ===")
    correct = [
        ("女+子宫一致", "检查所见：盆腔见子宫增大，膀胱充盈正常，直肠未见异常。\n诊断印象：子宫肌瘤，建议随访。",
         {"gender": "女"}),
        ("左右一致", "检查所见：左肾见一占位灶。\n诊断印象：左肾占位，考虑良性，建议复查。", {}),
        ("R5一致", "检查所见：左肺上叶见一占位灶。\n诊断印象：左肺上叶占位，考虑恶性，建议复查。", {}),
        ("R6一致", "检查所见：盆腔见子宫增大，膀胱充盈正常，直肠未见异常。\n诊断印象：子宫肌瘤，建议随访。",
         {"applied_site": "盆腔"}),
        ("R3一致", "检查所见：右乳腺见一结节，腺体致密，未见钙化。\n诊断印象：右乳腺结节，BI-RADS 4，建议随访。",
         {"modality": "乳腺"}),
        ("R4一致", "检查所见：左肾见一囊肿，范围约 2.5 cm。\n诊断印象：左肾囊肿，建议随访。", {}),
    ]
    ok2 = True
    for name, t, m in correct:
        ok2 = check_text(name, t, m, expect_empty=True) and ok2

    print("\n=== 结果 ===")
    print("全错误报告覆盖 R1-R5/R7:", "PASS" if ok1 else "FAIL")
    print("R6 登记部位不符专项:", "PASS" if ok6 else "FAIL")
    print("正确报告无假阳性:", "PASS" if ok2 else "FAIL")
    sys.exit(0 if (ok1 and ok6 and ok2) else 1)


if __name__ == "__main__":
    main()
