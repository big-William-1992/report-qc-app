"""R19 读音相似错字检测测试：高频词组锚定 + pypinyin 自动推导。

设计（对应业务需求「从放射科常用词组出发确定高频词组，其他相似读音的字/词
作为可能错误标记」）：
- R19 依赖 pypinyin 与高频词库 src/highfreq_lexicon.py；未安装 pypinyin 时
  自动降级（本文件用 skipIf 跳过，避免 CI 无该依赖时报错）。
- 只标记「读音与高频正确词相同/高度相似、但写法不同」的疑似错字，
  且跳过 R8 人工词典已覆盖的区间（互补不重复）。
- 正确的高频词组（磨玻璃影、定期复查、占位、渗出）不应被误报。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import engine
from engine import RuleEngine


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _r19(findings):
    return [f for f in findings if f.rule_id == "R19-HOMOPHONE"]


pypinyin = pytest.importorskip("pypinyin", reason="R19 需 pypinyin")


def test_r19_homophone_glass_ground_glass():
    # 磨玻离影 → 磨玻璃影（高频词库锚定，读音同音；R8 词典未覆盖，由 R19 推导）
    f = _run("检查所见：右肺上叶见磨玻离影，大小约8mm。\n诊断印象：建议定期复查。\n")
    hits = _r19(f)
    assert hits, [x.message for x in f]
    assert any("磨玻璃" in h.suggestion for h in hits), [h.message for h in hits]


def test_r19_homophone_followup_typo():
    # 高频锚定词：随诊/随访。『随珍』(suizhen) 与『随诊』(suizhen) 同音
    f = _run("检查所见：右肺下叶见条索影。\n诊断印象：建议随珍。\n")
    hits = _r19(f)
    assert any("随诊" in h.suggestion or "随访" in h.suggestion for h in hits), \
        [h.message for h in hits]


def test_r19_no_false_positive_on_correct():
    # 正确高频词组不应被误报
    f = _run("检查所见：右肺上叶见磨玻璃影，大小约8mm，边界清。\n"
             "诊断印象：建议定期复查，随访观察。\n")
    assert not _r19(f), [x.message for x in _r19(f)]


def test_r19_does_not_duplicate_r8():
    # R8 人工词典已覆盖的错字（随防→随访），R19 不应重复报
    f = _run("检查所见：右肺见条索影。\n诊断印象：建议随防。\n")
    r8 = [x for x in f if x.rule_id == "R8-TYPO"]
    r19 = _r19(f)
    assert r8, "随防 应被 R8 词典命中"
    # 随防 不应同时出现在 R19（区间已被 R8 占用）
    assert not any("随防" in x.snippet for x in r19), [x.message for x in r19]


def test_r19_can_be_disabled():
    # enable_r19=False 时关闭读音推导
    eng = RuleEngine()
    eng.rules_config["enable_r19"] = False
    f = eng.run("检查所见：右肺上叶见磨玻离影。\n诊断印象：建议复查。\n", {})
    assert not _r19(f)


def test_r19_no_false_positive_on_verb_combos():
    # 滑窗切词误判反例：『肺见』=肺+见(动词) 是合法组合，不应误报为『肺尖』
    f = _run("检查所见：右肺见磨玻离影，大小约8mm。\n诊断印象：建议复查。\n")
    hits = _r19(f)
    # 只应命中「磨玻离影」，不得把「肺见」误报为「肺尖」
    assert not any("肺见" in h.snippet for h in hits), [h.message for h in hits]
    assert any("磨玻璃" in h.suggestion for h in hits), [h.message for h in hits]


def test_r19_ignores_single_char_noise():
    # 单字/无意义切分不应产生误报：正确报告整段不应有任何 R19
    f = _run("检查所见：右肺上叶见磨玻璃影，大小约8mm，边界清，未见异常。\n"
             "诊断印象：建议定期复查，随访观察。\n")
    assert not _r19(f), [x.message for x in _r19(f)]
