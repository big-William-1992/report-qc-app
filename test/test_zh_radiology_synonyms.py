"""中文放射同义词归一表单元测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import zh_radiology_synonyms as zs


def test_normalize_signs_basic():
    signs = zs.normalize_signs("右肺上叶见磨玻璃密度影，伴斑片状影")
    assert "磨玻璃影" in signs
    assert "斑片影" in signs
    assert len(signs) == 2


def test_normalize_signs_longest_first():
    # 「磨玻璃密度影」应归一为「磨玻璃影」，而非被短词「磨玻璃」误处理两次
    signs = zs.normalize_signs("磨玻璃密度影")
    assert signs == {"磨玻璃影"}


def test_normalize_text_preserves_organ_and_side():
    # 只替换口语征象，方位/器官 token 不动（纯增益预通道）
    out = zs.normalize_text("左肺上叶见磨玻璃密度影，右肾见钙化灶")
    assert "左肺上叶" in out
    assert "右肾" in out
    assert "磨玻璃影" in out
    assert "钙化" in out
    # 原口语词不应残留
    assert "磨玻璃密度影" not in out


def test_normalize_text_degree():
    out = zs.normalize_text("两肺少许斑片影")
    assert "轻度" in out
    assert "斑片影" in out


def test_extract_followup_with_timeframe():
    r = zs.extract_followup("建议3个月后复查")
    assert r["has_followup"] is True
    assert r["timeframe_months"] == 3
    assert r["raw_timeframe"] == "3个月后"


def test_extract_followup_half_year():
    r = zs.extract_followup("半年后随访")
    assert r["has_followup"] is True
    assert r["timeframe_months"] == 6


def test_extract_followup_one_year():
    r = zs.extract_followup("一年后复查")
    assert r["timeframe_months"] == 12


def test_extract_followup_vague_no_timeframe():
    r = zs.extract_followup("建议定期复查")
    assert r["has_followup"] is True
    assert r["timeframe_months"] is None


def test_extract_followup_absent():
    r = zs.extract_followup("未见明显异常")
    assert r["has_followup"] is False


def test_canonical_sign():
    assert zs.canonical_sign("增殖灶") == "增殖灶"
    assert zs.canonical_sign("斑片") == "斑片影"
    assert zs.canonical_sign("不是征象") is None


def test_sign_canonical_set_consistency():
    # 所有 synonym 的值都应是已知规范 token（无悬空规范词）
    for v in zs.SIGN_SYNONYMS.values():
        assert v in zs.SIGN_CANONICAL
