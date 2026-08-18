"""公共影像数据集与术语本体目录（知识库）。

数据来源
--------
- 用户调研汇总（2026-07-29 起）：NIH Chest X-Ray / CheXpert / MIMIC-CXR /
  CheXnet / OpenI / ROCOv2 / PadChest / IU-Xray / TCIA，以及术语本体
  RadLex / UMLS / SNOMED CT。
- 中文专项资源（2026-07-31 增补）：CBLUE / CCKS / CMedKG / CHIP 系列 /
  中文放射征象同义词表（规划中），用于补足 catalog 全英文术语空白，
  详见 KNOWLEDGE_RESOURCES 中文专项小节。
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

【2026-08-18 更正】本模块是「数据集/术语资源目录」——仅作资源登记与测试引用，
并非引擎词表的单一事实来源（引擎词表实际在 _lexicons.py / anatomy_lexicon.py /
highfreq_lexicon.py / zh_radiology_synonyms.py，均已接入）。保留本文件供
数据集回归测试与文档引用，勿再依据本文件修改引擎词表。
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
# 知识图谱 / 实体关系 / 报告规范（B 类：增强 anatomy_lexicon、NER、模板校验 R10）
# ---------------------------------------------------------------------------
# 字段说明
#   name        资源名称
#   owner       维护机构
#   type        资源类型：knowledge_graph / nomenclature / common_data_element /
#               template_library / ontology / reporting_lexicon
#   scope       覆盖范围 / 规模
#   license     许可（公开可查，具体以官方为准）
#   access      获取地址
#   use_in_app  在质控软件中的应用落点
#   related     关联的数据集/本体 key（用于溯源，不强制存在）
#
# 说明：此类资源不是「报告语料」，而是术语/图谱/模板规范，用于把质控从
# 「器官族」升级到「实体 + 关系」「结构化字段」「标准模板」三个层次。
# RadLex 主体本体已在 ONTOLOGIES 登记；此处 RadLex Playbook 为其检查命名延伸。
KNOWLEDGE_RESOURCES: Dict[str, Dict] = {
    "radgraph": {
        "name": "RadGraph",
        "owner": "Stanford University (MMRL)",
        "type": "knowledge_graph",
        "scope": "报告实体-关系抽取图谱，建在 MIMIC-CXR/CheXpert 报告上并映射到 RadLex；"
                 "500 开发报告(14,579 实体/10,889 关系)+100 测试+220,763 份 MIMIC 报告推理输出",
        "license": "PhysioNet（免费，需认证）",
        "access": "https://physionet.org/content/radgraph/1.0.0/",
        "use_in_app": "将 anatomy_lexicon 从器官族升级到实体+关系（如 'opacity' 位于 "
                      "'right lower lobe'），增强左右/部位逻辑质控（R2-LATERALITY / R6-SITE）",
        "related": ["mimic_cxr", "chexpert", "radlex"],
    },
    "radlex_playbook": {
        "name": "RadLex Playbook",
        "owner": "RSNA / LOINC",
        "type": "nomenclature",
        "scope": "放射学检查项目命名本体（约 1,000 检查条目 RPID），已并入 LOINC",
        "license": "免费（RSNA + Regenstrief LOINC）",
        "access": "https://www.radlex.org/playbook/",
        "use_in_app": "检查名称规范化质控（R6 部位/检查名称一致性对照字段源）",
        "related": ["radlex", "loinc"],
    },
    "radelement": {
        "name": "RadElement",
        "owner": "RSNA / ACR",
        "type": "common_data_element",
        "scope": "标准报告通用数据元（CDE），含 RadLex 绑定，统一报告字段命名",
        "license": "免费（ACR + RSNA）",
        "access": "https://www.acr.org/Clinical-Research/RadElement",
        "use_in_app": "报告字段结构化对照，支撑 R10 模板字段校验的数据元来源",
        "related": ["radlex", "radreport"],
    },
    "radreport": {
        "name": "RadReport",
        "owner": "RSNA / ACR",
        "type": "template_library",
        "scope": "200+ 标准放射报告模板（RSNA/ACR），覆盖各系统",
        "license": "免费（ACR + RSNA）",
        "access": "https://www.rsna.org/quality-safety/RadReport",
        "use_in_app": "直接作为 R10 模板校验的标准模板库来源（对照『应有段落/字段』）",
        "related": ["radelement"],
    },
    "loinc": {
        "name": "LOINC",
        "owner": "Regenstrief Institute",
        "type": "ontology",
        "scope": "实验室与观测/影像结果编码本体，与 RadLex Playbook 融合补全检查+结果编码",
        "license": "LOINC License（免费，需注册）",
        "access": "https://loinc.org",
        "use_in_app": "检查结果/观察项编码映射，补全检查+结果编码对照",
        "related": ["radlex_playbook"],
    },
    "acr_reporting_lexicons": {
        "name": "ACR LI-RADS / TI-RADS / BI-RADS",
        "owner": "American College of Radiology (ACR)",
        "type": "reporting_lexicon",
        "scope": "各系统报告分级词典（肝 LI-RADS、甲状腺 TI-RADS、乳腺 BI-RADS 等）",
        "license": "免费（ACR 公开）",
        "access": "https://www.acr.org/Clinical-Research/Data-Science-Institute",
        "use_in_app": "分级术语规范化质控（如报告写 BI-RADS 类别须与征象描述一致）",
        "related": [],
    },
    # —— 中文专项资源（放射科中文 NLP / 知识图谱），2026-07-31 增补 ——
    # 背景：上述 B 类资源几乎全为英文；本产品面向中国放射科，中文术语归一与
    # 中文临床 NER 是最大知识库空白。下列资源用于中文实体识别、术语归一，
    # 对标英文 RadGraph 升级 anatomy_lexicon 到『实体+关系』层。均为研究/教学
    # 用途，具体许可与获取以各官方发布页为准；本 catalog 仅登记元数据。
    "cblue": {
        "name": "CBLUE (中文医疗语言理解基准)",
        "owner": "中国中文信息学会(CIPS)医疗健康信息处理专委会 + 腾讯天衍等 8 家单位",
        "type": "nlp_benchmark",
        "scope": "8 个中文医疗 NLP 任务：CMeEE 实体抽取、CMeIE 关系抽取、CMedQA "
                 "医疗问答、CHIP-CDN 疾病归一、CCL 临床术语链接等，覆盖疾病/症状/"
                 "部位/检查/药物等实体",
        "license": "研究用途，需签署数据使用协议（非商业）",
        "access": "https://github.com/CBLUEbenchmark/CBLUE",
        "use_in_app": "中文临床 NER / 归一基准语料，支撑放射报告中文实体（疾病/部位/"
                      "征象/程度）识别与术语归一，补足 catalog 全英文术语空白",
        "related": [],
    },
    "ccks": {
        "name": "CCKS (中国健康信息处理会议) 系列语料",
        "owner": "中国计算机学会(CCF) / 中国中文信息学会 医疗健康信息处理专委会",
        "type": "nlp_benchmark",
        "scope": "历年医疗语料：yidu-s4k（医疗实体识别）、cMedQA（医疗问答）、"
                 "CCKS 药品说明 / 诊断识别等，含实体、关系、归一任务",
        "license": "会议评测语料，研究用途，需申请",
        "access": "https://www.biendata.xyz（搜索 CCKS）/ http://www.cips-cl.org",
        "use_in_app": "中文医疗实体识别与问答语料参考，辅助构建中文放射征象同义词归一表",
        "related": [],
    },
    "cmedkg": {
        "name": "CMedKG (中文医疗知识图谱)",
        "owner": "复旦大学等高校自然语言处理实验室",
        "type": "knowledge_graph",
        "scope": "中文医疗知识图谱，含疾病/症状/药物/检查及相互关系，面向中文临床语义理解",
        "license": "研究 / 教学用途（具体以发布页为准）",
        "access": "https://github.com/king-yy/CMedKG",
        "use_in_app": "中文临床实体关系图谱来源，支撑中文报告『所见-印象』实体+关系级"
                      "质控（对标英文 RadGraph）",
        "related": [],
    },
    "chip": {
        "name": "CHIP 系列 (CHIP-CTC / CHIP-CDN)",
        "owner": "CCF 医疗健康信息处理专委会 / 同济、复旦等",
        "type": "nlp_benchmark",
        "scope": "CHIP-CTC 临床文本分类（疾病推断）、CHIP-CDN 临床疾病归一"
                 "（诊断名→标准码），均为高质量中文标注",
        "license": "会议评测语料，研究用途，需申请",
        "access": "https://www.biendata.xyz（搜索 CHIP）",
        "use_in_app": "中文诊断名归一（印象诊断→标准术语）与分类，支撑 R 系列"
                      "『印象诊断术语归一』质控",
        "related": [],
    },
    "zh_radiology_synonyms": {
        "name": "中文放射征象同义词表（项目自建，已实现）",
        "owner": "本项目自建（放射科医师维护）",
        "type": "nomenclature",
        "scope": "已实现：将『增殖灶/纤维灶/斑片影/索条影/磨玻璃密度影』等中文征象词"
                 "归一，补 RadLex 少量 zh 别名之不足",
        "license": "项目内部",
        "access": "src/zh_radiology_synonyms.py（已实现并接入引擎）",
        "use_in_app": "中文征象同义词归一，提升 R14 侧别 / R6 部位等规则的中文"
                      "子串匹配准确率，是填补中英文术语鸿沟的关键内部资源",
        "related": [],
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


def get_knowledge_resource(key: str) -> Optional[Dict]:
    """按 key 取单个知识资源（B 类）元数据；不存在返回 None。"""
    return KNOWLEDGE_RESOURCES.get(key)


def list_knowledge_resources() -> List[str]:
    """返回全部知识资源（B 类）key 列表。"""
    return list(KNOWLEDGE_RESOURCES.keys())


def knowledge_resources_by_type(rtype: str) -> List[str]:
    """按资源类型（knowledge_graph / nomenclature / template_library ...）取 key 列表。"""
    return [k for k, v in KNOWLEDGE_RESOURCES.items() if v.get("type") == rtype]


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
    # B 类：知识图谱 / 实体关系 / 报告规范
    lines += ["", "### 知识图谱 / 实体关系 / 报告规范（B 类）", ""]
    lines.append("| 资源 | 机构 | 类型 | 许可 | 在软件中的应用 |")
    lines.append("|------|------|------|------|----------------|")
    for v in KNOWLEDGE_RESOURCES.values():
        lines.append(
            f"| {v['name']} | {v['owner']} | {v['type']} | {v['license']} | "
            f"{v['use_in_app']} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(catalog_markdown())
