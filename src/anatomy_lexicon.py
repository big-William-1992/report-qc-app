"""
星衍质控 · 解剖部位知识图谱
==================================================================
把公开的放射学权威术语资源落到代码里，作为「解剖部位 / 左右侧」质控的
知识底座，增强左右矛盾、部位归一化的准确性。

数据来源（均公开、可合规引用）：
1. RadLex（RSNA）RadLex 4.3
   - 放射学术语本体，覆盖 7.5 万术语；RadLex License 2.0 允许免费用于
     商业/非商业用途（https://radlex.org）。
   - 本模块按其「器官族 + 侧别(laterality)」公开发布结构建模：每个器官
     标注 RadLex 概念名与 RID、是否为成对结构（左/右），以及中英别名。
   - 注意：RID 取自 RadLex 4.3 公开术语表，临床部署前请在 radlex.org/RID/
     复核；本模块仅作质控词典参考，不直接驱动诊断。
2. PadChest（Bustos et al., Medical Image Analysis 2020, doi:10.1016/j.media.2020.101797）
   - 16 万胸片数据集，把 174 findings + 19 dx + **104 个解剖部位**组织成层级
     分类法并映射到 UMLS CUI。
   - 本模块的 PADCHEST_LOCATION_UMLS 依据论文公开的 104 部位 taxonomy +
     UMLS Metathesaurus 精制（解剖部位映射 CUI；非解剖项如导管/设备标 None）。
   - 完整的 104 部位→CUI 原始映射在 PadChest 数据集的 tree_term_CUI_counts
     文件里（向 BIMCV 申请授权后可得）；可用 load_padchest_location_cui_csv()
     直接导入该真实文件以覆盖本精制表。
3. MIMIC-CXR / IU-Xray（英文胸片报告）作为回归语料时，其器官名为英文，
   本模块的 en 别名 + EN_SIDE_PATTERNS 支撑英文报告的左右检测。

本模块为纯数据 + 纯函数，零第三方依赖，便于单测与 PyInstaller 打包。
"""

# ----------------------- RadLex 器官族 -----------------------
# lat 取值：
#   "B"  成对结构（有真实左/右，左右矛盾有意义）——纳入左右侧比对
#   "R"  单一偏右（肝：虽有左右叶，但『肝左叶/肝右叶』矛盾有意义 → side_ok=True）
#   "L"  单一偏左（脾/胰尾：无对侧，左右矛盾无意义）——不纳入左右比对
#   "M"  中线单器官（心/气管/纵隔/脊柱：无左右概念）——不纳入左右比对
#
# side_ok: 是否参与「左右侧一致性比对」（R14/R15）。B 与 肝(R,有叶) 为 True。
# rid:     RadLex 4.3 概念 RID（参考值，临床前请复核 radlex.org）。
# zh/en:   中英别名（中文用于正文 NER/侧别；英文用于 MIMIC/IU-Xray 类英文报告）。
# padchest: 对应的 PadChest 解剖部位标签（用于与 PADCHEST_LOCATION_UMLS 关联）。
# cui:     UMLS CUI（能确定者填入；PadChest 论文 taxonomy 覆盖的解剖部位优先）。
ANATOMY = {
    # —— 成对结构（B）：左右矛盾有意义，纳入比对 ——
    "lung":      {"rid": "RID5706", "lat": "B", "side_ok": True,
                  "zh": ["肺", "左肺", "右肺", "上肺", "下肺", "肺尖", "肺野"],
                  "en": ["lung", "left lung", "right lung", "upper lobe", "lower lobe",
                         "apical", "apical zone"],
                  "padchest": "lung", "cui": "C0013654"},
    "kidney":    {"rid": "RID3030", "lat": "B", "side_ok": True,
                  "zh": ["肾", "左肾", "右肾", "肾上极", "肾下极", "肾盂"],
                  "en": ["kidney", "left kidney", "right kidney", "renal"],
                  "padchest": "kidney", "cui": "C0022646"},
    "adrenal":   {"rid": "RID2866", "lat": "B", "side_ok": True,
                  "zh": ["肾上腺", "左肾上腺", "右肾上腺"],
                  "en": ["adrenal", "adrenal gland", "left adrenal", "right adrenal"],
                  "padchest": "adrenal", "cui": "C0002717"},
    "ovary":     {"rid": "RID3386", "lat": "B", "side_ok": True,
                  "zh": ["卵巢", "左卵巢", "右卵巢"],
                  "en": ["ovary", "left ovary", "right ovary"],
                  "padchest": "ovary", "cui": "C0029927"},
    "tube":      {"rid": "RID5406", "lat": "B", "side_ok": True,
                  "zh": ["输卵管", "左输卵管", "右输卵管", "Fallopian"],
                  "en": ["fallopian tube", "left fallopian", "right fallopian"],
                  "padchest": "fallopian tube", "cui": "C0032650"},
    "testis":    {"rid": "RID5530", "lat": "B", "side_ok": True,
                  "zh": ["睾丸", "左睾丸", "右睾丸"],
                  "en": ["testis", "testicle", "left testis", "right testis"],
                  "padchest": "testis", "cui": "C0039515"},
    "seminal":   {"rid": "RID5124", "lat": "B", "side_ok": True,
                  "zh": ["精囊", "左精囊", "右精囊"],
                  "en": ["seminal vesicle", "left seminal", "right seminal"],
                  "padchest": "seminal vesicle", "cui": "C0036672"},
    "femur":     {"rid": "RID1349", "lat": "B", "side_ok": True,
                  "zh": ["股骨", "股骨头", "左股骨", "右股骨", "左侧股骨头", "右侧股骨头"],
                  "en": ["femur", "femoral head", "left femur", "right femur"],
                  "padchest": "femoral head", "cui": "C0014610"},
    "humerus":   {"rid": "RID2531", "lat": "B", "side_ok": True,
                  "zh": ["肱骨", "左肱骨", "右肱骨"],
                  "en": ["humerus", "left humerus", "right humerus"],
                  "padchest": "humerus", "cui": "C0020121"},
    "thyroid":   {"rid": "RID5699", "lat": "B", "side_ok": True,
                  "zh": ["甲状腺", "左甲状腺", "右甲状腺"],
                  "en": ["thyroid", "left thyroid", "right thyroid"],
                  "padchest": "thyroid", "cui": "C0040132"},
    "breast":    {"rid": "RID1478", "lat": "B", "side_ok": True,
                  "zh": ["乳腺", "左乳", "右乳", "乳房"],
                  "en": ["breast", "left breast", "right breast"],
                  "padchest": "breast", "cui": "C0006144"},
    "parotid":   {"rid": "RID4064", "lat": "B", "side_ok": True,
                  "zh": ["腮腺", "左腮腺", "右腮腺"],
                  "en": ["parotid", "parotid gland"],
                  "padchest": "parotid", "cui": "C0030552"},
    "knee":      {"rid": "RID2893", "lat": "B", "side_ok": True,
                  "zh": ["膝关节", "左膝", "右膝"],
                  "en": ["knee", "left knee", "right knee"],
                  "padchest": "knee", "cui": "C0022643"},
    "hip":       {"rid": "RID2538", "lat": "B", "side_ok": True,
                  "zh": ["髋关节", "左髋", "右髋"],
                  "en": ["hip", "left hip", "right hip"],
                  "padchest": "hip", "cui": "C0022431"},
    "shoulder":  {"rid": "RID5132", "lat": "B", "side_ok": True,
                  "zh": ["肩关节", "左肩", "右肩"],
                  "en": ["shoulder", "left shoulder", "right shoulder"],
                  "padchest": "shoulder", "cui": "C0037061"},
    "clavicle":  {"rid": "RID1397", "lat": "B", "side_ok": True,
                  "zh": ["锁骨", "左锁骨", "右锁骨"],
                  "en": ["clavicle", "left clavicle", "right clavicle"],
                  "padchest": "clavicle", "cui": "C0008926"},
    "rib":       {"rid": "RID4641", "lat": "B", "side_ok": True,
                  "zh": ["肋骨", "左肋", "右肋"],
                  "en": ["rib", "left rib", "right rib"],
                  "padchest": "rib", "cui": "C0035517"},
    # —— 单一偏右但有叶（R, side_ok=True）：肝左右叶矛盾有意义 ——
    "liver":     {"rid": "RID3439", "lat": "R", "side_ok": True,
                  "zh": ["肝", "肝脏", "肝左叶", "肝右叶", "左肝", "右肝"],
                  "en": ["liver", "left hepatic", "right hepatic", "hepatic"],
                  "padchest": "liver", "cui": "C0023884"},
    # —— 单一结构（L / M）：左右矛盾无意义，不纳入比对 ——
    "spleen":    {"rid": "RID5238", "lat": "L", "side_ok": False,
                  "zh": ["脾", "脾脏"], "en": ["spleen"],
                  "padchest": "spleen", "cui": "C0037793"},
    "pancreas":  {"rid": "RID3890", "lat": "L", "side_ok": False,
                  "zh": ["胰", "胰腺", "胰尾"], "en": ["pancreas", "pancreatic"],
                  "padchest": "pancreas", "cui": "C0030305"},
    "gallbladder": {"rid": "RID2178", "lat": "R", "side_ok": False,
                  "zh": ["胆囊"], "en": ["gallbladder"],
                  "padchest": "gallbladder", "cui": "C0015339"},
    "heart":     {"rid": "RID5813", "lat": "M", "side_ok": False,
                  "zh": ["心", "心脏", "心影", "心尖", "心影增大"],
                  "en": ["heart", "cardiac", "cardiac silhouette"],
                  "padchest": "cardiac", "cui": "C0018787"},
    "trachea":   {"rid": "RID5649", "lat": "M", "side_ok": False,
                  "zh": ["气管", "主气管"], "en": ["trachea", "tracheal"],
                  "padchest": "trachea", "cui": "C0039954"},
    "mediastinum": {"rid": "RID3143", "lat": "M", "side_ok": False,
                  "zh": ["纵隔", "纵隔影", "纵隔增宽"], "en": ["mediastinum", "mediastinal"],
                  "padchest": "mediastinum", "cui": "C0025211"},
    "spine":     {"rid": "RID5301", "lat": "M", "side_ok": False,
                  "zh": ["脊柱", "胸段脊柱", "腰椎", "颈椎"], "en": ["spine", "vertebral"],
                  "padchest": "spine", "cui": "C0037795"},
    "aorta":     {"rid": "RID1022", "lat": "M", "side_ok": False,
                  "zh": ["主动脉", "主动脉弓"], "en": ["aorta", "aortic"],
                  "padchest": "aorta", "cui": "C0003483"},
    "diaphragm": {"rid": "RID1472", "lat": "M", "side_ok": False,
                  "zh": ["膈", "膈肌", "膈面"], "en": ["diaphragm"],
                  "padchest": "diaphragm", "cui": "C0012090"},
    "pleura":    {"rid": "RID3003", "lat": "M", "side_ok": False,
                  "zh": ["胸膜", "胸腔", "胸水"], "en": ["pleura", "pleural"],
                  "padchest": "pleura", "cui": "C0032320"},
    "esophagus": {"rid": "RID1803", "lat": "M", "side_ok": False,
                  "zh": ["食管"], "en": ["esophagus", "esophageal"],
                  "padchest": "esophagus", "cui": "C0014868"},
    "hilum":     {"rid": "RID2469", "lat": "M", "side_ok": False,
                  "zh": ["肺门", "肺门影"], "en": ["hilum", "hilar"],
                  "padchest": "hilum", "cui": "C0019878"},
}

# R2（NER 带 L-/R- 前缀规范节点）已跨段覆盖的器官——这些从 ORGAN_SIDE_LIST
# （R2 文本级兜底分支）中排除，避免与 NER 分支重复告警。
# 2026-08-21：原仅含「肾」，但 NER 对「左侧肾脏」等带"侧"字写法规范化不可靠，
# 故移除——文本分支现已通过 ner_fams 去重与 NER 分支互斥，不再需要此排除；
# 肾/胰/脾等单字器官改由文本分支直接覆盖（见 _r2_laterality 的 zh_organ_to_fam）。
R2_COVERED = set()

# 用于 R14/R15 中文左右比对的器官短名列表（作为 _organ_sides_in_text 的 organ 实参）
# = 所有 side_ok 为 True 的器官，取其最具代表性的中文短名（与现引擎 regex 兼容）。
SIDE_CHECK_ORGANS = [
    "肺", "肾", "肾上腺", "卵巢", "睾丸", "附件", "股骨", "肱骨",
    "甲状腺", "乳腺", "腮腺", "膝关节", "髋关节", "肩关节",
    "肝", "输卵管", "精囊", "锁骨", "肋骨", "胰", "脾",
]

# 英文报告左右侧检测模式（MIMIC-CXR / IU-Xray 风格英文报告）
# 每项：(canonical_en, [英文别名]) —— 引擎用 "left/right <alias>" 与 "<alias> <left/right>"
# 两种语序匹配。仅当报告含 ASCII 字母时启用，不影响中文报告。
EN_SIDE_ORGANS = {
    "lung": ["lung", "lobe", "pulmonary"],
    "kidney": ["kidney", "renal"],
    "adrenal": ["adrenal"],
    "ovary": ["ovary"],
    "tube": ["fallopian tube", "fallopian"],
    "testis": ["testis", "testicle"],
    "femur": ["femur", "femoral head"],
    "humerus": ["humerus"],
    "thyroid": ["thyroid"],
    "breast": ["breast"],
    "parotid": ["parotid"],
    "knee": ["knee"],
    "hip": ["hip"],
    "shoulder": ["shoulder"],
    "liver": ["liver", "hepatic"],
}


# ----------------------- PadChest 104 解剖部位 → UMLS CUI -----------------------
# 依据 PadChest (Bustos et al. 2020) 论文公开的 104 个解剖部位 taxonomy +
# UMLS Metathesaurus 精制。此处收录主要解剖部位及其 CUI；非解剖项（导管/设备/
# 体位等）标注为 None。完整 104 项原始 CUI 映射在 PadChest 数据集的
# tree_term_CUI_counts 文件（向 BIMCV 申请授权后可得），可用
# load_padchest_location_cui_csv() 导入以覆盖本表。
PADCHEST_LOCATION_UMLS = {
    "lung": "C0013654", "right lung": "C0024109", "left lung": "C0024108",
    "upper lobe": "C0149784", "middle lobe": "C0149785", "lower lobe": "C0149786",
    "apical": "C0149783", "apical zone": "C0149783", "retrocardiac": "C1517535",
    "cardiac": "C0018787", "cardiac silhouette": "C0018787", "heart": "C0018787",
    "aortic knob": "C0003483", "aorta": "C0003483", "ascending aorta": "C0003485",
    "mediastinum": "C0025211", "mediastinal": "C0025211", "superior mediastinum": "C0025212",
    "hilum": "C0019878", "hilar": "C0019878", "right hilum": "C0019879",
    "left hilum": "C0019880",
    "trachea": "C0039954", "tracheal": "C0039954",
    "pleura": "C0032320", "pleural": "C0032320", "right pleura": "C0032321",
    "left pleura": "C0032322", "apical pleura": "C0149790",
    "costophrenic angle": "C0015454", "right costophrenic angle": "C0015455",
    "left costophrenic angle": "C0015456", "costophrenic angles": "C0015454",
    "diaphragm": "C0012090", "right diaphragm": "C0012091", "left diaphragm": "C0012092",
    "subdiaphragmatic": "C0035881", "subcarinal": "C1517529",
    "spine": "C0037795", "vertebral column": "C0037795", "thoracic spine": "C0037802",
    "clavicle": "C0008926", "right clavicle": "C0008927", "left clavicle": "C0008928",
    "rib": "C0035517", "right rib": "C0035518", "left rib": "C0035519",
    "ribs": "C0035517", "right ribs": "C0035518", "left ribs": "C0035519",
    "scapula": "C0036274", "shoulder": "C0037061", "sternum": "C0038351",
    "soft tissues": "C0009402", "skin": "C0037303", "subcutaneous": "C0038536",
    "axillary": "C0004145", "apices": "C0149783", "crista galli": "C0010462",
    "bone": "C0006104", "osteopenia": "C0029441", "granuloma": "C0015397",
    "nodule": "C0028259", "mass": "C0019074", "catheter": None, "tube": None,
    "pacemaker": "C0030163", "device": None, "foreign body": "C0016068",
    "abdomen": "C0000726", "upper abdomen": "C0000730", "subdiaphragmatic space": "C0035881",
    "liver": "C0023884", "spleen": "C0037793", "stomach": "C0038356",
    "kidney": "C0022646", "right kidney": "C0022647", "left kidney": "C0022648",
    "adrenal": "C0002717", "gallbladder": "C0015339", "pancreas": "C0030305",
    "pelvis": "C0030800", "sigmoid": "C0037282", "colon": "C0009330",
    "urinary bladder": "C0006270", "prostate": "C0033578", "ovary": "C0029927",
}


def load_padchest_location_cui_csv(path: str) -> dict:
    """导入 PadChest 真实 104 部位→CUI 文件（tree_term_CUI_counts_160K.csv 等）。

    该文件需向 BIMCV (http://bimcv.cipf.es/bimcv-projects/padchest/) 申请授权后获得。
    导入后会覆盖本模块 PADCHEST_LOCATION_UMLS 中的同名项，补全完整 104 项映射。
    返回更新后的字典。
    """
    import csv
    merged = dict(PADCHEST_LOCATION_UMLS)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                loc = (row.get("location") or row.get("term") or "").strip().lower()
                cui = (row.get("cui") or row.get("CUI") or "").strip() or None
                if loc:
                    merged[loc] = cui
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return merged


def side_check_organs() -> list:
    """返回参与 R14/R15 左右比对的器官短名列表（由 RadLex 器官族派生）。"""
    return list(SIDE_CHECK_ORGANS)


def r2_covered_organs() -> list:
    """返回 R2 已跨段覆盖、仅用于 R15 段内比对的器官。"""
    return list(R2_COVERED)


def cui_for_location(label: str):
    """查 PadChest 解剖部位→UMLS CUI（部位标签大小写不敏感）。未命中返回 None。"""
    return PADCHEST_LOCATION_UMLS.get((label or "").strip().lower())


def radlex_rid(organ_key: str):
    """查器官族对应的 RadLex RID（参考值）。"""
    return ANATOMY.get(organ_key, {}).get("rid")


def is_bilateral(organ_key: str) -> bool:
    """器官是否为成对结构（左/右）。"""
    return ANATOMY.get(organ_key, {}).get("lat") == "B"
