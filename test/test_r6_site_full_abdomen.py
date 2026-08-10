"""R6 登记部位不符：带后缀登记写法（全腹部CT / 上腹部平扫 / 下腹部CT 等）。

覆盖：
- norm_site 子串最长匹配：『全腹部CT』应归一化到 abdomen（旧实现整串精确匹配返回 None，R6 直接跳过漏检）
- 全腹部登记 + 报告写肝脏 → 不误报
- 全腹部登记 + 报告写胸部 → 命中 R6
- 带『部』字写法（上腹部/下腹部/中腹部）在 SITE_NORM 与区域别名表中均可识别
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine, SITE_NORM, _extract_regions


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _has(findings, rid):
    return any(f.rule_id == rid for f in findings)


def test_norm_site_suffix_full_abdomen():
    eng = RuleEngine()
    assert eng.kg.norm_site("全腹部CT") == "abdomen"
    assert eng.kg.norm_site("全腹CT") == "abdomen"
    assert eng.kg.norm_site("上腹部平扫+增强") == "abdomen"
    assert eng.kg.norm_site("下腹部CT") == "abdomen"
    assert eng.kg.norm_site("胸部正侧位") == "chest"
    assert eng.kg.norm_site("头颅CT") == "head"


def test_full_abdomen_site_norm_has_dai_bu():
    # 带"部"字的常见登记写法应直接命中（精确匹配优先）
    assert SITE_NORM.get("全腹部") == "abdomen"
    assert SITE_NORM.get("上腹部") == "abdomen"
    assert SITE_NORM.get("下腹部") == "abdomen"
    assert SITE_NORM.get("中腹部") == "abdomen"


def test_extract_regions_full_abdomen():
    assert _extract_regions("全腹部CT") == ["全腹"]
    assert _extract_regions("全腹部") == ["全腹"]
    assert _extract_regions("上腹部CT") == ["上腹部"]
    assert _extract_regions("胸部、全腹部") == ["胸部", "全腹"]


def test_r6_full_abdomen_good_report():
    # 申请全腹部、报告写肝脏（正确场景，不应误报）
    text = "检查所见：肝脏大小形态正常，未见异常密度影，胆囊未见结石。\n诊断印象：未见明显异常。\n"
    f = _run(text, {"applied_site": "全腹部CT"})
    assert not _has(f, "R6-SITE"), [x.message for x in f]


def test_r6_full_abdomen_mismatch_chest():
    # 申请全腹部、报告却写胸部（应命中 R6）
    text = "检查所见：双肺纹理清晰，未见实变影。\n诊断印象：胸部未见明显异常。\n"
    f = _run(text, {"applied_site": "全腹部CT"})
    assert _has(f, "R6-SITE"), [x.message for x in f]
