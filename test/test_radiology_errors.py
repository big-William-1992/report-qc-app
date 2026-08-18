"""放射科常见错误规则测试：错别字(R8) / 上下文逻辑(R15) / 前后文逻辑(R14, R1-GENDER)。

覆盖：
- 错别字：器官/疾病名形近音近错字（R8-TYPO）及其 auto_fix 可修正
- 上下文逻辑错误（同一描述段内跨句）：段内自相矛盾 / 同器官左右自相矛盾 / 先见后无
- 前后文逻辑错误（描述段↔结论段）：描述正常结论异常 / 良恶性矛盾 / 数量不一致 / 同器官左右跨段矛盾
- 信息框性别 vs 正文性别矛盾（R1-GENDER）
- 一致性报告不误报（反例）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import engine
from engine import RuleEngine


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _ids(findings):
    return {f.rule_id for f in findings}


def _has(findings, rid):
    return any(f.rule_id == rid for f in findings)


# ----------------------------- 错别字 (R8) -----------------------------
def test_typo_organ_voice():
    # 前列腺误写为「前裂腺」
    f = _run("影像描述：前裂腺体积增大。\n影像结论：前裂腺增生。")
    assert _has(f, "R8-TYPO"), [x.message for x in f if x.rule_id == "R8-TYPO"]
    assert any("前裂腺" in x.snippet and x.suggestion == "前列腺" for x in f if x.rule_id == "R8-TYPO")


def test_typo_disease():
    # 肺炎误写为「费炎」、结核误写为「肺结合」
    f = _run("影像描述：右肺下叶费炎。\n影像结论：考虑肺结合。")
    assert _has(f, "R8-TYPO")


def test_typo_autofix():
    # auto_fix 能将确定性错别字替换为正确词
    text = "影像描述：左肺子官。\n影像结论：建议复查。"
    f = _run(text, {})
    fixed, n_fixed, manual, _ = RuleEngine().auto_fix(text, f)
    assert n_fixed >= 1
    assert "子宫" in fixed
    assert "子官" not in fixed


def test_typo_no_false_positive_on_correct():
    # 正确用词不应误报
    f = _run("影像描述：前列腺体积增大，考虑增生。\n影像结论：前列腺增生。")
    typos = [x for x in f if x.rule_id == "R8-TYPO"]
    assert not any(x.suggestion == "前列腺" for x in typos), "正确词『前列腺』不应被纠错"


# ----------------------------- 上下文逻辑错误 (R15) -----------------------------
def test_r15_normal_then_positive():
    # 段首称未见异常但段内描述阳性征
    f = _run("影像描述：检查所见未见明显异常。右肺见斑片影。\n影像结论：建议复查。")
    assert _has(f, "R15-NORMAL")


def test_r15_presence_then_absence():
    # 同一病灶先见后无
    f = _run("影像描述：左肺见结节。上述结节未见。\n影像结论：建议复查。")
    assert _has(f, "R15-PRESENCE")


# ----------------------------- 前后文逻辑错误 (R14/R17) -----------------------------
def test_r14_normal_findings_abnormal_impression():
    # 描述正常 → 结论异常。
    # R17 重构后按部位精确比对（肺），产生更精确的 R17-PERREGION（描述正常、结论异常），
    # 替代旧的整段级 R14-NORMAL 兜底；用例语义不变，断言同步到新规则。
    f = _run("影像描述：未见明显异常。\n影像结论：考虑肺癌。")
    assert _has(f, "R17-PERREGION")


def test_r14_nature_conflict():
    # 描述恶性倾向、结论良性倾向
    f = _run("影像描述：右肺上叶见结节，边缘毛刺，考虑恶性。\n"
             "影像结论：右肺上叶结节，符合良性，建议复查。")
    assert _has(f, "R14-NATURE")


def test_r14_count_mismatch():
    # 数量前后不一致
    f = _run("影像描述：右肺见两枚结节。\n影像结论：右肺一枚结节，建议复查。")
    assert _has(f, "R14-COUNT")


def test_r14_cross_side_conflict():
    # 同器官左右跨段矛盾（肝，非 R2 规范器官族）：现统一由 R2-LATERALITY 产出
    f = _run("影像描述：肝左叶见占位。\n影像结论：肝右叶占位，建议复查。")
    assert _has(f, "R2-LATERALITY")


def test_r14_cross_side_conflict_lung():
    # 肺的左右跨段矛盾：历史上 R2 因规范节点无连字符(LUL/RUL/LUUL/RUUL)而失效、
    # R14 又曾把「肺」排除在 ORGAN_SIDE_LIST 外，导致「谁都不抓」的漏报。
    # 现已将「肺」纳入 ORGAN_SIDE_LIST，跨段左右矛盾统一由 R2-LATERALITY 文本兜底覆盖。
    f = _run("影像描述：左肺上叶见磨玻璃结节。\n影像结论：右肺上叶占位，考虑恶性。")
    assert _has(f, "R2-LATERALITY"), [x.message for x in f if x.rule_id == "R2-LATERALITY"]
    # 反向：描述右肺、结论左肺，同样应命中
    f2 = _run("影像描述：右肺下叶见斑片影。\n影像结论：左肺下叶感染性病变。")
    assert _has(f2, "R2-LATERALITY"), [x.message for x in f2 if x.rule_id == "R2-LATERALITY"]


def test_r11_gender_meta_vs_text():
    # 信息框性别(男) vs 正文性别(女) 矛盾
    f = _run("女性，45岁。\n影像描述：子宫形态正常。\n影像结论：未见异常。",
             {"gender": "男"})
    assert _has(f, "R1-GENDER")


# ----------------------------- 反例：一致性报告不误报 -----------------------------
def test_clean_report_no_new_errors():
    text = ("影像描述：右肺上叶见一结节，边界清，考虑良性。\n"
            "影像结论：右肺上叶结节，良性可能，建议复查。")
    f = _run(text, {"gender": "女"})
    new_ids = {x.rule_id for x in f
               if x.rule_id.startswith(("R14", "R15"))}
    assert not new_ids, f"一致性报告不应产生 R14/R15 误报：{new_ids}"


def test_clean_report_no_typo_false_positive():
    text = ("影像描述：前列腺体积增大，考虑增生。\n"
            "影像结论：前列腺增生，建议随访。")
    f = _run(text, {"gender": "男"})
    assert not _has(f, "R8-TYPO")


# ----------------------------- 严重度分级（红/橙/蓝 高亮依据） -----------------------------
def test_severity_assignment_by_rule():
    # 严重(high)：左右侧混淆 / 信息框-正文左右矛盾
    f_lat = _run("影像描述：左肾见结石。\n影像结论：右肾结石。")
    assert _has(f_lat, "R2-LATERALITY")
    assert any(x.severity == "high" for x in f_lat if x.rule_id == "R2-LATERALITY")
    # 注意：R2 span 为 (-1,-1)，依赖 snippet 兜底才能高亮——此处确认对象存在且严重度为 high
    assert any(x.span == (-1, -1) for x in f_lat if x.rule_id == "R2-LATERALITY")

    # 左右矛盾统一由 R2（跨段）/ R17（逐部位）负责；R15-SIDE 已删除（2026-08-18）
    f_side = _run("影像描述：左肺结节。\n影像结论：右侧胸腔积液。", {"laterality": "左"})
    assert not _has(f_side, "R2-LATERALITY")
    assert not _has(f_side, "R17-PERREGION")

    # 提示(low)：模板缺失-随访建议（R10-TEMPLATE 默认 low）
    f_tpl = _run("影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节。")
    assert _has(f_tpl, "R10-TEMPLATE")
    assert any(x.severity == "low" for x in f_tpl if x.rule_id == "R10-TEMPLATE")

    # 警告(medium)：同音错别字
    assert _has(_run("影像描述：前裂腺增大。\n影像结论：前裂腺增生。"), "R8-TYPO")


def test_severity_weight_drives_score_degree():
    # 同数量错误下，严重度越高扣分越多——这是「按程度高亮」的评分基础
    high = RuleEngine().run("影像描述：左肾结石。\n影像结论：右肾结石，建议复查。", {})  # R2 high
    low = RuleEngine().run("影像描述：右肺上叶见结节。\n影像结论：右肺上叶结节。", {})  # R10 low
    from engine import score, SEVERITY_WEIGHT
    sh = score(high)
    sl = score(low)
    assert SEVERITY_WEIGHT["high"] > SEVERITY_WEIGHT["low"]
    assert sh["准确性"]["score"] < sl["准确性"]["score"], "严重错误应比提示错误扣更多分"


# ==================== 第七轮：规则边界修复回归（2026-08-18） ====================
def test_r4_hu_not_flagged():
    # CT 值标准单位 HU 此前必误报 R4（unit.lower() 与 VALID_UNITS 大写不匹配）
    f = _run("影像描述：右肺结节，CT值约35HU。\n影像诊断：右肺结节。")
    assert not _has(f, "R4-UNIT"), [x.message for x in f]


def test_r4_blood_pressure_not_flagged():
    # 血压『120/80mmHg』此前被吞成单位 /80mmhg 误报
    f = _run("影像描述：病史：血压120/80mmHg。双肺纹理清晰。\n影像诊断：未见异常。")
    assert not _has(f, "R4-UNIT"), [x.message for x in f]


def test_r14_negative_prefix_no_override():
    # 『不除外恶性』是阳性倾向措辞，不得被当否定跳过（描述恶性/结论良性应报）
    f = _run("影像描述：右肾见占位，不除外恶性。\n影像诊断：考虑良性，建议随访。")
    assert _has(f, "R5-CONSISTENCY") or _has(f, "R14-NATURE"), [x.message for x in f]


def test_r14_multi_vs_single_count():
    # 多发 vs 1 枚矛盾应报；多发 vs 2 枚不报
    f = _run("影像描述：双肺见多发结节。\n影像诊断：见 1 枚结节。")
    assert _has(f, "R14-COUNT"), [x.message for x in f]
    f2 = _run("影像描述：双肺见多发结节。\n影像诊断：共 2 枚结节。")
    assert not _has(f2, "R14-COUNT")


def test_r22_modifier_between_term_and_size():
    # 『结节较前增大，现约3.5cm』（术语与测量间夹修饰语）此前漏检
    f = _run("影像描述：右肺见结节，较前增大，现约3.5cm。\n影像诊断：右肺结节，建议复查。")
    assert _has(f, "R22-SIZE"), [x.message for x in f]


def test_r21_male_breast_ultrasound_ok():
    # 男性乳腺超声（乳房发育）合理：不得报性别不符
    f = _run("影像描述：双乳腺体组织未见异常。\n影像诊断：未见异常。",
             {"gender": "男", "applied_site": "乳腺", "modality": "超声"})
    assert not _has(f, "R21-GENDER-SITE")


def test_r12_same_side_seen_and_absent():
    # 『左肺见结节，左肺未见异常』同句同侧矛盾应报（此前 _REGION_NORMAL_RE 只匹配 X正常）
    f = _run("影像描述：左肺见结节，左肺未见异常。\n影像诊断：建议复查。")
    assert _has(f, "R12-SENTENCE"), [x.message for x in f]


def test_r3_negative_report_exempt():
    # 『乳腺未见异常』整体阴性不强制 BI-RADS（此前报 R3 并扣分）
    f = _run("影像描述：双侧乳腺未见异常。\n影像诊断：乳腺未见异常。",
             {"applied_site": "乳腺", "modality": "乳腺"})
    assert not _has(f, "R3-SCORE")


def test_r6_multi_region_applied():
    # 『胸部、上腹部』多区域申请 + 正文覆盖胸部 → 不报 R6（此前单族归一误报）
    f = _run("影像描述：双肺纹理清晰。\n影像诊断：未见异常。", {"applied_site": "胸部、上腹部"})
    assert not _has(f, "R6-SITE")
