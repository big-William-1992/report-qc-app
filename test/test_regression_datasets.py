"""公开数据集风格的质控回归集（合成样例，非原始患者数据）。

重要说明
--------
本文件中的报告均为**依据公开数据集报告 pattern 合成的回归样例**，并非原始患者数据：
- NIH Chest X-Ray / CheXnet 原始发布为图像 + 分类标签，无自由文本报告；
  此处合成样例仅模拟其结论风格（label-only）。
- CheXpert / OpenI / ROCOv2 含报告/图文，需 AWS / Kaggle / 官网申请授权，无法拉取原始数据。
- MIMIC-CXR / PadChest / IU-Xray 已在 test_regression_real_reports.py 覆盖。

用合成样例为每个数据集建模「正常报告不应误报」「典型左右矛盾应命中」两类护城河，
验证质控引擎对这些主流公开语料的鲁棒性。原始 CUI 可用
anatomy_lexicon.load_padchest_location_cui_csv() 导入扩充对照表。

注意：模板合规（R10）面向中文结构化报告；英文样例使用 'FINDINGS:'/'IMPRESSION:'
（大小写不敏感）触发左右检测，但 R10 不对其断言。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine
import dataset_catalog as dc


def _run(text, meta=None):
    return RuleEngine().run(text, meta or {})


def _has(findings, rid):
    return any(f.rule_id == rid for f in findings)


# ----------------------------- 各数据集「正常报告」不应误报 -----------------------------
# 合成的正常胸片/多器官报告，按各数据集典型英文风格；断言无左右/性别/侧别高危误报。
_DATASET_NORMAL = {
    # NIH：图像+14标签，无原文报告，合成普通阴性结论
    "nih_cxr": (
        "FINDINGS: The lungs are clear. No focal consolidation, effusion, "
        "or pneumothorax. Cardiomediastinal silhouette is normal. "
        "IMPRESSION: No finding."
    ),
    # CheXpert：含不确定性标注风格的阴性报告
    "chexpert": (
        "FINDINGS: No finding. The cardiomediastinal silhouette is normal. "
        "Lungs are clear. IMPRESSION: No acute cardiopulmonary process."
    ),
    # CheXnet：基于 NIH 的 14 类阴性
    "chexnet": (
        "FINDINGS: Lungs are well expanded and clear. No nodules or masses. "
        "Heart size within normal limits. IMPRESSION: No finding."
    ),
    # OpenI：结构化英文报告
    "openi": (
        "FINDINGS: The heart is normal in size. The lungs are clear without "
        "focal consolidation. No pleural effusion. IMPRESSION: No acute abnormality."
    ),
    # ROCOv2：多器官图文风格（以脑部 MRI caption 为例，验证多模态描述不误报）
    "rocov2": (
        "FINDINGS: MRI of the brain shows no acute intracranial abnormality. "
        "The ventricles are normal in size. IMPRESSION: No acute finding."
    ),
}


def test_dataset_normal_no_high_false_positive():
    # 5 个含报告/可模拟的数据集，正常样例不应触发左右/性别/侧别高危误报
    for key, text in _DATASET_NORMAL.items():
        f = _run(text)
        assert not _has(f, "R14-SIDE"), f"{key}: 正常报告误报 R14-SIDE"
        assert not _has(f, "R11-SIDE"), f"{key}: 正常报告误报 R11-SIDE"
        assert not _has(f, "R1-GENDER"), f"{key}: 正常报告误报 R1-GENDER"
        assert not _has(f, "R2-LATERALITY"), f"{key}: 正常报告误报 R2-LATERALITY"


# ----------------------------- 各数据集「典型左右矛盾」应命中 -----------------------------
def test_chexpert_english_lower_lobe_contradiction():
    # CheXpert 风格：描述段右肺下叶、结论段左肺下叶 → 左右矛盾
    text = (
        "FINDINGS: Opacity in the right lower lobe concerning for pneumonia. "
        "IMPRESSION: The left lower lobe shows consolidation."
    )
    f = _run(text)
    assert _has(f, "R14-SIDE")


def test_rocov2_multi_organ_side_contradiction():
    # ROCOv2 多器官图文：左肾囊肿 vs 右肾正常 → 左右矛盾
    text = (
        "FINDINGS: The left kidney demonstrates a simple cortical cyst. "
        "IMPRESSION: The right kidney is unremarkable."
    )
    f = _run(text)
    assert _has(f, "R14-SIDE")


# ----------------------------- 数据集目录完整性 -----------------------------
def test_catalog_structure_integrity():
    # 全部 6 个新数据集 + 既有数据集均在目录中，且字段完整、样本量为正整
    required = {"name", "institution", "size", "scope", "access", "lang", "has_report"}
    for key, meta in dc.DATASETS.items():
        missing = required - set(meta.keys())
        assert not missing, f"{key} 缺字段: {missing}"
        assert isinstance(meta["size"], int) and meta["size"] > 0, f"{key} size 非法"
        assert isinstance(meta["has_report"], bool)
    # 6 个本轮新增数据集 key 必须存在
    for key in ("nih_cxr", "chexpert", "mimic_cxr", "chexnet", "openi", "rocov2"):
        assert key in dc.DATASETS, f"目录缺少数据集 {key}"


def test_catalog_ontologies_present():
    # 术语本体（RadLex / UMLS / SNOMED CT）必须登记，供质控映射参考
    for key in ("radlex", "umls", "snomed_ct"):
        assert key in dc.ONTOLOGIES, f"目录缺少本体 {key}"
        assert "license" in dc.ONTOLOGIES[key]


def test_catalog_datasets_with_reports_covers_new():
    # 含报告的数据集应包含本轮新增的 chexpert / openi / rocov2
    with_reports = dc.datasets_with_reports()
    for key in ("chexpert", "openi", "rocov2", "mimic_cxr", "padchest", "iu_xray"):
        assert key in with_reports, f"{key} 应标记为含报告"
    # label-only 的 NIH / CheXnet 不应在含报告集合
    assert "nih_cxr" not in with_reports
    assert "chexnet" not in with_reports
