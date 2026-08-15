# -*- coding: utf-8 -*-
"""在干净的 CI 环境里验证质控引擎能正常 import 并运行（抓逻辑层错误）。
用法： python build/smoke_test.py
"""
import sys

sys.path.insert(0, "src")


def main():
    import engine

    e = engine.RuleEngine()
    sample = "Chest CT: no acute findings."
    findings = e.run(sample, {})  # meta 用空字典即可，引擎内部全部走 .get()
    fixed, n_fix, n_manual, _ = e.auto_fix(sample, findings)
    print("ENGINE_OK findings=%d fixed_chars=%d manual=%d" % (len(findings), n_fix, n_manual))
    assert fixed is not None


if __name__ == "__main__":
    main()
