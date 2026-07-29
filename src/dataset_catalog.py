"""公共影像数据集与术语本体目录（知识库）。

数据来源
--------
- 用户调研汇总（2026-07-29 起）：NIH Chest X-Ray / CheXpert / MIMIC-CXR /
  CheXnet / OpenI / ROCOv2 / PadChest / IU-Xray / TCIA，以及术语本体
  RadLex / UMLS / SNOMED CT。
- 数据集元数据（机构、样本量、标注类别、获取方式）以用户提供的清单为准，
  字段含义见各条目注释。

合规与回归测试边界
------------------
- 标注为 label-only 的数据集（NIH / CheXnet）原始发布**不含自由文本报告**，
  仅含图像 + 分类标签；其回归样例为合成文本，仅模拟结论风格，**非原始数据**。
- 含自由文本报告的数据集（MIMIC-CXR / CheXpert / IU-Xray / PadChest / OpenI）
  报告均需授权（PhysioNet / AWS / Kaggle / BIMCV），回归集为**合成样例**。
- ROCOv2 为图文（caption）数据集，多模态，用于多器官描述风格覆盖。
- 术语本体（RadLex / UMLS / SNOMED CT）仅作术语映射参考，原始术语库需授权下载。

本模块是「单一事实来源」：质控引擎、回归测试、文档（README / docs）均从
此处取数，避免多处硬编码导致不一致。
"""

from __future__ import annotations

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 公开影像数据集
# ---------------------------------------------------------------------------
# 字段说明
#   name          数据集名称
#   institution   发布机构
#   size          样本量（图像或报告数，整数）
#   scope         标注范围 / 疾病类别
#   labels        标注类别数（None 表示非固定分类 / 自由文本）
#   modality      模态
#   access        获取方式
#   lang          报告语言（en / zh / multi）
#   has_report    是否含自由文本报告（决定能否做文本级质控回归）
#   license       许可（公开可查，具体以官方为准）
#   notes         补充说明
DATASETS: Dict[str, Dict] = {
    "nih_cxr": {
        "name": "NIH Chest X-Ray",
        "institution": "NIH Clinical Center",
        "size": 112120,
        "scope": "14 种胸部疾病（Atelectasis, Effusion, Infiltration, Mass, "
                 "Nodule, Pneumonia, Pneumothorax, Consolidation, Edema, "
                 "Emphysema, Fibrosis, Pleural Thickening, Hernia, No Finding）",
        "labels": 14,
        "modality": "Chest X-Ray",
        "access": "Box / Kaggle",
        "lang": "en",
        "has_report": False,
        "license": "CC BY 4.0（图像）；标签公开",
        "notes": "原始发布为图像 + 14 分类标签，无自由文本报告；BBox 子集后增。",
    },
    "chexpert": {
        "name": "CheXpert",
        "institution": "Stanford Medicine",
        "size": 52000,
        "scope": "胸部观察标签（含 No Finding / Cardiomegaly / Edema / "
                 "Consolidation / Pneumonia / Atelectasis / Pneumothorax / "
                 "Pleural Effusion 等，常配合不确定性标注 likely/uncertain）",
        "labels": 10,
        "modality": "Chest X-Ray",
        "access": "AWS S3",
        "lang": "en",
        "has_report": True,
        "license": "Research use（需签署协议）",
        "notes": "含自由文本报告；以不确定性标注（uncertain/negative/positive）著称。",
    },
    "mimic_cxr": {
        "name": "MIMIC-CXR",
        "institution": "MIT Lab (PhysioNet)",
        "size": 377000,
        "scope": "全科室（以胸部为主）放射报告 + 图像，含 14 种常见标签子集",
        "labels": 14,
        "modality": "Chest X-Ray + Radiology Report",
        "access": "PhysioNet（需认证 + 数据使用协议）",
        "lang": "en",
        "has_report": True,
        "license": "PhysioNet Credentialed Health Data License",
        "notes": "英文自由文本报告标杆；需授权。已在 test_regression_real_reports.py 覆盖。",
    },
    "chexnet": {
        "name": "CheXnet",
        "institution": "UCSD AI Lab",
        "size": 108000,
        "scope": "14 种胸部疾病（基于 NIH 子集训练）",
        "labels": 14,
        "modality": "Chest X-Ray",
        "access": "Kaggle / 官方权重",
        "lang": "en",
        "has_report": False,
        "license": "模型权重公开；底层 NIH 数据遵循 NIH 许可",
        "notes": "为模型/权重发布，非独立报告语料；标签体系同 NIH。",
    },
    "openi": {
        "name": "OpenI",
        "institution": "Duke University (NIH NLM)",
        "size": 7470,
        "scope": "胸部影像 + 结构化放射报告（约 10 类常见发现）",
        "labels": 10,
        "modality": "Chest X-Ray / CT",
        "access": "Kaggle / NIH Open-i",
        "lang": "en",
        "has_report": True,
        "license": "Open-i 公开检索；使用遵循 NLM 条款",
        "notes": "含结构化英文报告，适合分段器风格覆盖。",
    },
    "rocov2": {
        "name": "ROCOv2",
        "institution": "German Cancer Research Center (DKFZ)",
        "size": 79789,
        "scope": "多器官、多模态图文（radiology figure captions + image）",
        "labels": None,
        "modality": "Multi-modal (X-Ray / CT / MRI / PET / US)",
        "access": "官网申请",
        "lang": "en",
        "has_report": True,
        "license": "研究用途；需官网申请",
        "notes": "图文描述风格，用于多器官侧别描述覆盖（非纯胸片）。",
    },
    # —— 此前已纳入回归集的数据集，统一归档于此 ——
    "padchest": {
        "name": "PadChest",
        "institution": "Universidad de Alicante / BIMCV",
        "size": 160000,
        "scope": "174 findings + 19 dx + 104 解剖部位（→ UMLS CUI）",
        "labels": None,
        "modality": "Chest X-Ray + Bilingual Report",
        "access": "BIMCV（需授权申请）",
        "lang": "en/es",
        "has_report": True,
        "license": "BIMCV 数据使用协议",
        "notes": "104 解剖部位→UMLS 映射已落库（src/anatomy_lexicon.py）。详见论文 "
                 "Bustos et al., Medical Image Analysis 2020, doi:10.1016/j.media.2020.101797。",
    },
    "iu_xray": {
        "name": "IU-Xray",
        "institution": "Indiana University",
        "size": 7470,
        "scope": "胸部影像 + 英文自由文本报告（含 MeSH 标签）",
        "labels": None,
        "modality": "Chest X-Ray + Radiology Report",
        "access": "Kaggle / 官方",
        "lang": "en",
        "has_report": True,
        "license": "Open（需引用）",
        "notes": "英文报告标杆之一；已在 test_regression_real_reports.py 覆盖。",
    },
    "tcia": {
        "name": "TCIA (The Cancer Imaging Archive)",
        "institution": "NIH Cancer Imaging Archive",
        "size": 20000000,
        "scope": "多癌种、多模态影像集合（含 LIDC-IDRI 等胸部子集）",
        "labels": None,
        "modality": "CT / MR / PET / X-Ray 等",
        "access": "官网开放下载",
        "lang": "en",
        "has_report": False,
        "license": "CC BY 3.0（多数集合）",
        "notes": "影像为主，报告稀疏；用于模态/解剖广度认知，非文本质控回归。",
    },
}

# ---------------------------------------------------------------------------
# 术语本体 / 知识图谱（用于解剖部位映射与质控增强）
# ---------------------------------------------------------------------------
ONTOLOGIES: Dict[str, Dict] = {
    "radlex": {
        "name": "RadLex",
        "owner": "RSNA (Radiological Society of North America)",
        "scope": "放射学术语本体（约 7.5 万术语：解剖、发现、设备、流程）",
        "license": "RadLex License 2.0（允许免费商业/非商业用途）",
        "use_in_app": "器官族 RID + laterality 建模（src/anatomy_lexicon.py）",
        "access": "https://radlex.org",
    },
    "umls": {
        "name": "UMLS (Unified Medical Language System)",
        "owner": "NIH NLM",
        "scope": "多本体元词典，含 CUIs 跨术语系统映射",
        "license": "UMLS Metathesaurus License（需注册）",
        "use_in_app": "PadChest 解剖部位→CUI 映射（PADCHEST_LOCATION_UMLS）",
        "access": "https://uts.nlm.nih.gov",
    },
    "snomed_ct": {
        "name": "SNOMED CT",
        "owner": "SNOMED International",
        "scope": "临床术语标准（解剖、疾病、流程）",
        "license": "SNOMED CT Affiliate License（需成员国/订阅）",
        "use_in_app": "可作为 RadLex/UMLS 之外的备用术语映射源",
        "access": "https://www.snomed.org",
    },
}


# ---------------------------------------------------------------------------
# 查询辅助函数
# ---------------------------------------------------------------------------
def get_dataset(key: str) -> Optional[Dict]:
    """按 key 取单个数据集元数据；不存在返回 None。"""
    return DATASETS.get(key)


def list_datasets() -> List[str]:
    """返回全部数据集 key 列表。"""
    return list(DATASETS.keys())


def datasets_with_reports() -> List[str]:
    """返回含自由文本报告、可做文本级质控回归的数据集 key。"""
    return [k for k, v in DATASETS.items() if v.get("has_report")]


def datasets_by_access(access_hint: str) -> List[str]:
    """按获取方式关键字（如 'Kaggle' / 'PhysioNet'）模糊匹配数据集 key。"""
    hint = access_hint.lower()
    return [k for k, v in DATASETS.items() if hint in v.get("access", "").lower()]


def catalog_markdown() -> str:
    """生成 Markdown 表格，便于直接贴入 README / docs。"""
    lines = [
        "## 公共影像数据集与术语本体目录",
        "",
        "| 数据集 | 机构 | 样本量 | 标注范围 | 获取方式 | 语言 | 含报告 |",
        "|--------|------|--------|----------|----------|------|--------|",
    ]
    for v in DATASETS.values():
        lines.append(
            f"| {v['name']} | {v['institution']} | {v['size']:,} | "
            f"{v['scope'][:40]}… | {v['access']} | {v['lang']} | "
            f"{'是' if v.get('has_report') else '否'} |"
        )
    lines += ["", "### 术语本体", ""]
    lines.append("| 本体 | 机构 | 许可 | 在软件中的用途 |")
    lines.append("|------|------|------|----------------|")
    for v in ONTOLOGIES.values():
        lines.append(f"| {v['name']} | {v['owner']} | {v['license']} | {v['use_in_app']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(catalog_markdown())
