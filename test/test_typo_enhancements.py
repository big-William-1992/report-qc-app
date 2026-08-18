"""错别字功能增强回归测试（2026-08-16）：
覆盖 4 个增强方向——放射专业形近字、多音字语境优先、R8 新错词表、数字/单位错字与 R19 空格容忍。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import engine
from engine import RuleEngine


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _ids(findings, rule):
    return [f for f in findings if f.rule_id == rule]


# ---------------------------------------------------------------------------
# 1. 放射专业形近字增强
# ---------------------------------------------------------------------------
class TestShapeEnhancement:
    def test_shape_ge_sep(self):
        # 膈↔隔：『纵膈』（错）应被检出为『纵隔』（可经 R8 词表或 R19 形近路径）
        f = _run("检查所见：纵膈未见明显异常。\n诊断印象：纵隔未见异常。\n")
        ids = [x.rule_id for x in f]
        sugg = [x.suggestion for x in f]
        assert "R8-TYPO" in ids or any("纵隔" in s for s in sugg), [x.message for x in f]

    def test_shape_lei_xiong(self):
        # 肋↔胸 形近字组已建立：验证正确书写『肋骨』『胸骨』不被误报为彼此
        f = _run("检查所见：肋骨未见骨折。\n诊断印象：胸廓未见异常。\n")
        # 正常报告不应误报 R19 形近（肋骨↔胸骨虽形近，但都是正确词）
        for x in f:
            if x.rule_id == "R19-HOMOPHONE":
                assert "胸骨" not in x.suggestion or x.suggestion == "肋骨", x.message


# ---------------------------------------------------------------------------
# 2. 多音字语境优先读音
# ---------------------------------------------------------------------------
class TestPolyphoneEnhancement:
    def test_word_pinyin_internal(self):
        from highfreq_lexicon import _word_pinyin
        # 放射语境优先读音
        assert _word_pinyin("内脏") == "neizang"   # 脏读 zàng
        assert _word_pinyin("咽部") == "yanbu"     # 咽读 yān
        assert _word_pinyin("膈肌") == "geji"      # 膈读 gé


# ---------------------------------------------------------------------------
# 3. R8 新错词表（rules_config.json typos）
# ---------------------------------------------------------------------------
class TestR8NewTypos:
    def test_r8_geji(self):
        # 隔肌（错）→ 膈肌（R8 精确匹配）
        f = _run("检查所见：左侧隔肌抬高。\n诊断印象：隔肌抬高。\n")
        hits = _ids(f, "R8-TYPO")
        assert any("膈肌" in h.suggestion for h in hits), [x.message for x in f]

    def test_r8_yingxiang(self):
        # 影相（错）→ 影像
        f = _run("检查所见：右肺影相正常。\n诊断印象：影像学未见异常。\n")
        hits = _ids(f, "R8-TYPO")
        assert any("影像" in h.suggestion for h in hits), [x.message for x in f]


# ---------------------------------------------------------------------------
# 4. 数字/单位错字（R22-UNIT）
# ---------------------------------------------------------------------------
class TestUnitEnhancement:
    def test_r22_unit_mm_as_cm(self):
        # 结节写 30cm（实为 30mm 误写）→ 触发 R22-UNIT 单位疑似误写
        f = _run("检查所见：右肺上叶见一结节，直径约30cm。\n诊断印象：右肺结节。\n")
        hits = _ids(f, "R22-UNIT")
        assert hits, [x.message for x in f]

    def test_r22_no_false_positive_normal(self):
        # 正常尺寸（8mm 结节）不应触发单位误写
        f = _run("检查所见：右肺上叶见一结节，直径约8mm。\n诊断印象：右肺结节。\n")
        assert not _ids(f, "R22-UNIT"), [x.message for x in f]


# ---------------------------------------------------------------------------
# 5. R19 空格/标点容忍
# ---------------------------------------------------------------------------
class TestSpaceTolerance:
    def test_r19_norm_re(self):
        # 循环归一化：『磨 玻 璃』连续去空格后应为『磨玻璃』
        from engine import _R19_NORM_RE
        s = "磨 玻 璃"
        while True:
            _n = _R19_NORM_RE.sub(lambda m: m.group(1) + m.group(2), s)
            if _n == s:
                break
            s = _n
        assert s == "磨玻璃", s

    def test_r19_space_homophone(self):
        # 带空格的错字『磨 玻 离』（OCR/录入带空格）应被归一化后识别为『磨玻璃』
        f = _run("检查所见：右肺上叶见磨 玻 离影。\n诊断印象：建议定期复查。\n")
        hits = [x for x in f if x.rule_id == "R19-HOMOPHONE" and "磨玻璃" in x.suggestion]
        assert hits, [x.message for x in f]

    def test_r19_space_homophone(self):
        # 带空格的错字『磨 玻 离』（OCR/录入带空格）应被归一化后识别为『磨玻璃』
        f = _run("检查所见：右肺上叶见磨 玻 离影。\n诊断印象：建议定期复查。\n")
        hits = [x for x in f if x.rule_id == "R19-HOMOPHONE" and "磨玻璃" in x.suggestion]
        assert hits, [x.message for x in f]

    def test_r19_autofix_span_maps_to_orig(self):
        # R19 span 基于去空格/标点的 norm_text，auto_fix 必须还原到原文坐标，
        # 否则『双肺 见膜玻璃』这类含空格报告自动修正会切错位损坏原文（2026-08-18 修复）
        t = "检查所见：双肺 见膜玻璃样密度影，考虑间质性改变。\n诊断印象：间质性改变。"
        f = _run(t)
        r19 = [x for x in f if x.rule_id == "R19-HOMOPHONE" and "磨玻璃" in x.suggestion]
        assert r19, [x.message for x in f]
        fixed, n_fix, manual, fixes = RuleEngine().auto_fix(t, f)
        assert n_fix == 1, (n_fix, fixes)
        for fx in fixes:
            assert t[fx["start"]:fx["end"]] == fx["wrong"], \
                f"span 错位: 原文[{fx['start']}:{fx['end']}]={t[fx['start']:fx['end']]!r} != {fx['wrong']!r}"
            assert fx["correct"] == "磨玻璃", fx
        assert "磨玻璃" in fixed and "膜玻璃" not in fixed, fixed
        assert "双肺 " in fixed, "空格应保留，未被替换破坏"
