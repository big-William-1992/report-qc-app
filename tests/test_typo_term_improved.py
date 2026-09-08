"""错别字/术语规则增强回归测试（2026-08-22）：
覆盖 R8 词典扩容+上下文安全闸、R19 同音安全词降误报、R22 定性-尺寸矛盾。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine  # noqa: E402

eng = RuleEngine()


def _ids(text, meta=None):
    return [f.rule_id for f in eng.run(text, meta or {})]


def _has(text, rule, meta=None):
    return rule in _ids(text, meta)


# ---------------------------------------------------------------------------
# A. R8 错别字词典扩容 + 上下文安全闸
# ---------------------------------------------------------------------------
class TestR8Expansion:
    def test_r8_zuofei_left_lung(self):
        # 坐肺（错）→ 左肺，放射语境内应检出
        assert _has("检查所见：坐肺上叶见结节。\n诊断印象：左肺结节。", "R8-TYPO")

    def test_r8_context_guard_no_fp(self):
        # 坐火车：错写本身是常见字组合，无放射语境 → 不误报 R8
        ids = _ids("患者坐火车前往医院。\n诊断印象：未见异常。")
        assert "R8-TYPO" not in ids

    def test_r8_zongge_mediastinum(self):
        # 纵膈（错）→ 纵隔
        assert _has("检查所见：纵膈未见异常。\n诊断印象：纵隔正常。", "R8-TYPO")

    def test_r8_yingxiang_image(self):
        # 影象（错）→ 影像
        assert _has("检查所见：右肺影象正常。\n诊断印象：影像未见异常。", "R8-TYPO")

    def test_r8_feiyan_pneumonia(self):
        # 费炎（错）→ 肺炎，放射语境内应检出
        assert _has("检查所见：右费炎。\n诊断印象：右肺炎。", "R8-TYPO")


# ---------------------------------------------------------------------------
# B. R19 同音安全词降误报
# ---------------------------------------------------------------------------
class TestR19SafeWords:
    def test_r19_safe_yinxiang(self):
        # 「印象」与「影像」同音，属常见合法词 → 不报 R19
        assert not _has("检查所见：右肺见结节。\n诊断印象：右肺结节。", "R19-HOMOPHONE")

    def test_r19_safe_qiguan(self):
        # 「器官」与「气管」同音，常见词 → 不报 R19
        assert not _has("检查所见：腹部实质器官未见异常。\n诊断印象：未见异常。", "R19-HOMOPHONE")

    def test_r19_real_typo_still_fires(self):
        # 真错字「膜玻璃」→ 磨玻璃 仍应检出（near/shape 不受安全词影响）
        assert _has("检查所见：右肺上叶见膜玻璃影。\n诊断印象：建议复查。", "R19-HOMOPHONE")


# ---------------------------------------------------------------------------
# C. R22 定性-尺寸矛盾
# ---------------------------------------------------------------------------
class TestR22Qualitative:
    def test_r22_qual_huge_nodule_small(self):
        # 巨大结节 但 <1cm → R22-QUAL
        assert _has("检查所见：右肺上叶见一巨大结节，直径约0.6cm。\n诊断印象：右肺结节。", "R22-QUAL")

    def test_r22_qual_tiny_mass_large(self):
        # 微小占位 但 >3cm → R22-QUAL
        assert _has("检查所见：肝左叶见一微小占位，大小约4.2×3.1cm。\n诊断印象：肝占位。", "R22-QUAL")

    def test_r22_qual_clean_no_fp(self):
        # 巨大肿块 5cm，描述与尺寸一致 → 不误报 R22-QUAL
        assert not _has("检查所见：右肺上叶见一巨大肿块，大小约5.0cm。\n诊断印象：右肺肿块。", "R22-QUAL")
