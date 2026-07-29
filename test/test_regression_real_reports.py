"""真实公开数据集风格的质控回归集（合成样例，非原始患者数据）。

重要说明
--------
本文件中的报告均为**依据公开数据集报告 pattern 合成的回归样例**，并非原始患者数据：
- MIMIC-CXR / IU-Xray 需 PhysioNet / Kaggle 授权，无法在此拉取原始报告；
- PadChest 需向 BIMCV 申请授权，且其 104 部位→UMLS 已在 src/anatomy_lexicon.py 精制。

用合成样例建模这些数据集的典型错误形态（左右矛盾、性别矛盾、错别字、单位、模板缺失、
部位不符、英文报告左右矛盾等），作为 R1–R15 的回归护城河：任何规则改动都不应引入
漏报/误报。原始数据集的 CUI 文件可用 anatomy_lexicon.load_padchest_location_cui_csv()
导入以扩充对照表。

注意：模板合规（R10）目前面向中文结构化报告；英文报告用 'Findings:'/'Impression:'
标题可触发左右检测（R14/R11），但 R10 仍期望中文段标题，故英文样例不对其断言。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import engine
import anatomy_lexicon as ax
from engine import RuleEngine


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _has(findings, rid):
    return any(f.rule_id == rid for f in findings)


# ----------------------------- 英文报告（MIMIC-CXR / IU-Xray 风格） -----------------------------
def test_reg_mimic_normal_no_high_false_positive():
    # MIMIC-CXR 典型正常胸片报告（英文合成）
    text = ("Findings: The lungs are clear. No focal consolidation, effusion, "
            "or pneumothorax. Heart size is normal. Impression: No acute "
            "cardiopulmonary process.")
    f = _run(text)
    # 正常报告不应有左右矛盾 / 性别矛盾 / 左右侧混淆类高危误报
    assert not _has(f, "R14-SIDE")
    assert not _has(f, "R11-SIDE")
    assert not _has(f, "R1-GENDER")
    assert not _has(f, "R2-LATERALITY")


def test_reg_mimic_english_lower_lobe_contradiction():
    # MIMIC-CXR 风格：描述段右肺下叶、结论段左肺下叶 → 左右矛盾
    text = ("Findings: Opacity in the right lower lobe concerning for pneumonia.\n"
            "Impression: The left lower lobe shows consolidation.")
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_reg_iuxray_english_kidney_contradiction():
    # IU-Xray 风格：描述段左肾、结论段右肾 → 左右矛盾
    text = ("Findings: A nodule in the left kidney.\n"
            "Impression: The right kidney demonstrates a cystic lesion.")
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_reg_english_same_side_no_false_positive():
    # 同侧（右）不应误报
    text = ("Findings: Opacity in the right lower lobe.\n"
            "Impression: Right lower lobe pneumonia, recommend follow-up.")
    f = _run(text)
    assert not _has(f, "R14-SIDE")


# ----------------------------- 中文报告（PadChest 风格 / 临床常见） -----------------------------
def test_reg_padchest_style_lung_contradiction():
    # PadChest 风格胸片报告（中文）：左肺下叶 vs 右肺下叶
    text = "检查所见：左肺下叶见斑片状模糊影。\n诊断印象：右肺下叶炎症，建议抗炎后复查。"
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_reg_liver_lobe_contradiction():
    # 肝左右叶矛盾（肝为单一偏右但有功能左右叶，纳入比对）
    text = "检查所见：肝左叶见低密度灶。\n诊断印象：肝右叶占位，考虑血管瘤。"
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_reg_new_bilateral_oviduct_contradiction():
    # 新增成对器官（输卵管）左右矛盾：验证 lexicon 派生扩大了覆盖
    text = "检查所见：左输卵管增粗。\n诊断印象：右输卵管积水。"
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_reg_gender_contradiction_r1():
    # 信息框男 + 正文子宫 → 性别矛盾
    f = _run("检查所见：子宫增大，符合肌瘤。\n诊断印象：子宫肌瘤。", {"gender": "男"})
    assert _has(f, "R1-GENDER")


def test_reg_voice_typo_r8():
    # 语音录入错别字：坐肺→左肺，前裂腺→前列腺
    text = "检查所见：坐肺见结节。前裂腺增大。\n诊断印象：左肺结节，前列腺增生。"
    f = _run(text)
    assert _has(f, "R8-TYPO")
    wrongs = {fd.snippet for fd in f if fd.rule_id == "R8-TYPO"}
    assert "坐肺" in wrongs and "前裂腺" in wrongs


def test_reg_unit_error_r4():
    # 非常规单位 'cms'（VALID_UNITS 仅含 cm/mm）
    text = "检查所见：病灶长径约 3.5cms。\n诊断印象：病灶 3.5cms，建议复查。"
    f = _run(text)
    assert _has(f, "R4-UNIT")


def test_reg_template_missing_r10():
    # 缺结论段 → 模板缺失
    f = _run("检查所见：双肺纹理增多。")
    assert _has(f, "R10-TEMPLATE")


def test_reg_consistency_r5():
    # 描述段阳性征、结论段提及器官但无对应结论词 → 描述-结论矛盾
    text = "检查所见：右肺见结节影。\n诊断印象：右肺建议复查。"
    f = _run(text)
    assert _has(f, "R5-CONSISTENCY")


def test_reg_site_mismatch_r6():
    # 申请部位胸部 vs 报告主体为肾（腹部）→ 登记部位不符
    f = _run("检查所见：左肾见结石。\n诊断印象：左肾结石。", {"applied_site": "胸部"})
    assert _has(f, "R6-SITE")


def test_reg_info_laterality_r11():
    # 信息框侧别『左』但正文仅提右侧 → 上下文左右矛盾
    f = _run("检查所见：右肺见结节。\n诊断印象：右肺结节。", {"laterality": "左"})
    assert _has(f, "R11-SIDE")


def test_reg_normal_chinese_no_false_positive():
    # 一致性正常中文报告：不应有左右/性别/登记部位类误报
    text = "检查所见：双肺纹理清晰，未见异常。\n诊断印象：心肺未见异常，建议年度复查。"
    f = _run(text)
    assert not _has(f, "R14-SIDE")
    assert not _has(f, "R1-GENDER")
    assert not _has(f, "R6-SITE")
    assert not _has(f, "R2-LATERALITY")


# ----------------------------- 知识图谱（RadLex + PadChest）集成断言 -----------------------------
def test_lexicon_drives_side_lists():
    # ORGAN_SIDE_LIST / INTERNAL 由 anatomy_lexicon 派生，且保留了 R2 对肾/股骨头的跨段分离
    assert "输卵管" in engine.ORGAN_SIDE_LIST          # 新增成对器官已纳入
    assert "锁骨" in engine.ORGAN_SIDE_LIST
    assert "肾" not in engine.ORGAN_SIDE_LIST          # R2 已跨段覆盖 → 排除
    assert "股骨头" in engine.ORGAN_SIDE_LIST_INTERNAL  # R15 段内比对仍保留


def test_radlex_padchest_reference():
    # RadLex RID 与 PadChest→UMLS CUI 可查
    assert ax.radlex_rid("lung") == "RID5706"
    assert ax.cui_for_location("cardiac") == "C0018787"
    assert ax.cui_for_location("lung") == "C0013654"
    assert ax.is_bilateral("lung") is True
    assert ax.is_bilateral("heart") is False           # 中线单器官不参与左右比对
    # 加载真实 PadChest CUI 文件（如有）的接口存在且可调用（无文件时安全返回精制表）
    merged = ax.load_padchest_location_cui_csv("/nonexistent_path_xyz.csv")
    assert isinstance(merged, dict) and merged.get("lung") == "C0013654"
