"""
report_qc_app/src/engine.py
第一代医学影像报告质控引擎（纯标准库，零依赖）
管线：报告文本 → 分段 → NER → 知识图谱约束 → 规则引擎 → 错误 + 多维评分

相对原型的升级：
- 新增 R6 登记部位不符（申请部位 vs 报告主体解剖部位，KG 归一化比对）
- 新增 R7 描述内部矛盾（同一描述段内左右/性别自相矛盾）
- 错误类型统计与多维评分集中在此模块，便于驾驶舱复用
"""

import re
import os
import sys
import json
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple

# 解剖部位知识图谱（RadLex 器官族 + PadChest 104 部位→UMLS）：驱动左右侧比对表
from anatomy_lexicon import SIDE_CHECK_ORGANS, R2_COVERED, EN_SIDE_ORGANS

# 中文放射同义词归一 + 中文临床 NER（项目自建，离线可用；详见 dataset_catalog ZH 资源）
# 以 try/except 降级：极端打包缺失时不影响既有规则。
try:
    from zh_radiology_synonyms import (
        normalize_text as _zh_norm_text,
        extract_followup as _zh_extract_followup,
    )
    from zh_ner import extract_entities as _zh_ner_entities
    _ZH_NLP_OK = True
except Exception:  # pragma: no cover - 仅防御性降级
    _ZH_NLP_OK = False

# 高频正确词组库 + 读音相似推导（R19）：白名单锚定 → 读音相似标记疑似错字。
# 依赖 pypinyin（运行时可选），未安装时 R19 自动降级为空（不影响既有 R8 词典）。
try:
    from highfreq_lexicon import (
        segment_candidates as _hf_segment_candidates,
        highfreq_words as _hf_highfreq_words,
        is_pinyin_available as _hf_pinyin_available,
    )
    _HF_OK = True
except Exception:  # pragma: no cover - 仅防御性降级
    _HF_OK = False

# zh_ner 中文标签 → 引擎内部英文标签（与 anatomy / laterality 等保持一致）
_ZH_ENT_LABEL_MAP = {"征象": "sign", "随访": "followup", "程度": "degree"}


# ----------------------------- 数据模型 -----------------------------
@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int
    section: str = ""
    canonical: Optional[str] = None


@dataclass
class Finding:
    rule_id: str
    error_type: str
    severity: str      # high / medium / low
    message: str
    snippet: str = ""
    span: tuple = (-1, -1)
    suggestion: str = ""   # 可自动修正的建议值（仅 R8 错别字填充）


# ----------------------------- 词典 / 知识图谱 -----------------------------
LATERALITY = {"左": "left", "右": "right", "双侧": "bilateral", "两边": "bilateral",
              "双": "bilateral", "两侧": "bilateral"}

GENDER_ORGANS = {
    "前列腺": "male", "睾丸": "male", "阴茎": "male", "精囊": "male",
    "子宫": "female", "卵巢": "female", "宫颈": "female", "阴道": "female",
    "乳腺": "female", "乳房": "female",
}

# 解剖部位同义词 → 规范节点（带左右前缀便于跨段比对）
ANATOMY_SYNONYMS = {
    "左肺上叶": "LUUL", "左上肺": "LUUL", "左肺上": "LUUL",
    "右肺上叶": "RUUL", "右上肺": "RUUL", "右肺上": "RUUL",
    "右肺": "RUL", "左肺": "LUL",
    "左侧股骨头": "L-femoral-head", "右侧股骨头": "R-femoral-head",
    "左肾": "L-kidney", "右肾": "R-kidney",
}

# 部位归一化（用于登记部位不符：申请部位词 → 规范部位族）
SITE_NORM = {
    # 头颈
    "头颅": "head", "颅脑": "head", "头部": "head", "脑": "head", "脑实质": "head",
    "颅内": "head", "眼眶": "head", "鼻咽": "head", "鼻窦": "head", "腮腺": "head",
    # 脊柱（颈/胸/腰/骶）
    "颈椎": "cspine", "胸椎": "tspine", "骶椎": "sacrum",
    "椎间盘": "spine", "脊柱": "spine", "腰椎": "lumbar",
    # 胸部
    "胸部": "chest", "双肺": "chest", "心肺": "chest", "肺": "chest", "纵隔": "chest",
    "心脏": "chest", "冠脉": "chest", "冠状动脉": "chest",
    # 腹部（含带"部"字的常见登记写法：全腹部/上腹部/下腹部/中腹部）
    "腹部": "abdomen", "全腹": "abdomen", "全腹部": "abdomen",
    "上腹": "abdomen", "上腹部": "abdomen", "下腹": "abdomen", "下腹部": "abdomen",
    "中腹": "abdomen", "中腹部": "abdomen",
    "肝胆": "abdomen", "胰": "abdomen", "脾": "abdomen", "肾上腺": "abdomen",
    "肾": "abdomen", "双肾": "abdomen", "腹膜": "abdomen", "胃肠": "abdomen",
    # 盆腔
    "盆腔": "pelvis", "骨盆": "pelvis", "前列腺": "pelvis", "子宫": "pelvis",
    "附件": "pelvis", "膀胱": "pelvis",
    # 四肢关节
    "左肩": "shoulder", "右肩": "shoulder", "肩关节": "shoulder",
    "左肘": "elbow", "右肘": "elbow", "左腕": "wrist", "右腕": "wrist",
    "左髋": "hip", "右髋": "hip", "髋关节": "hip", "股骨头": "hip",
    "左膝": "knee", "右膝": "knee", "膝关节": "knee",
    "左踝": "ankle", "右踝": "ankle", "四肢": "limb", "上肢": "limb", "下肢": "limb",
}

# 英文规范值 → 中文标准部位族名（回填界面用，统一显示 胸部/腹部/盆腔 等）
SITE_CANON = {
    "head": "头颅", "cspine": "颈椎", "tspine": "胸椎", "sacrum": "骶椎",
    "spine": "脊柱", "lumbar": "腰椎", "chest": "胸部", "abdomen": "腹部",
    "pelvis": "盆腔", "shoulder": "肩关节", "elbow": "肘关节", "wrist": "腕关节",
    "hip": "髋关节", "knee": "膝关节", "ankle": "踝关节", "limb": "四肢",
}

VALID_UNITS = {"cm", "mm", "HU", "mm/s", "ml", "°", "mmhg"}

MODALITY_SCORE = {"乳腺": "BI-RADS", "钼靶": "BI-RADS", "乳腺x线": "BI-RADS",
                  "前列腺": "PI-RADS", "盆腔": "PI-RADS"}

# 描述内部阳性/阴性征（用于一致性）
POSITIVE_MARKERS = ["结节", "占位", "阴影", "肿块", "异常信号", "斑片", "渗出", "骨折", "扩张", "增大"]
NEGATIVE_MARKERS = ["未见", "无", "阴性", "通畅", "清晰", "正常", "未见明显", "未见异常"]

# 强阳性征（用于『描述异常→结论正常』与『同一句话逻辑错误』判定，覆盖面更广）
# 2026-08-05 扩充：补软化灶/梗塞灶/低密度(高)密度灶(影)/萎缩/脱髓鞘/变性/缺如/膨出/
#   积气/闭塞/信号异常/占位效应/破坏/充盈缺损/间盘突出等常见阳性病灶词——否则
#   『描述正常→结论软化灶』『描述软化灶→结论正常』这类前后文逻辑矛盾会漏检。
POSITIVE_STRONG = ["结节", "占位", "肿块", "骨折", "扩张", "增大", "囊肿", "结石", "出血",
                   "水肿", "癌", "瘤", "病变", "异常信号", "斑片", "渗出", "增厚", "狭窄",
                   "积液", "缺血", "梗死", "钙化灶",
                   "软化灶", "梗塞灶", "低密度灶", "高密度灶", "低密度影", "高密度影",
                   "萎缩", "脱髓鞘", "变性", "缺如", "膨出", "积气", "闭塞",
                   "信号异常", "占位效应", "破坏", "充盈缺损", "间盘突出", "盘突出",
                   "增生", "炎症", "炎性", "肿胀", "瘘", "畸形", "囊变", "积血", "积脓"]
# 阳性征否定前缀：其后紧跟的阳性征词视为『否定/未见』，不算异常，避免『未见明显炎症』
# 『无增生』等被误判为阳性（配合 str.endswith 匹配，覆盖『未见/未见明显/无/不伴…』等）。
_NEG_PREFIXES = ("未见", "未见明显", "未见明确", "未见确切", "未见可疑",
                 "无明显", "未发现", "未示", "不伴", "不含", "排除", "否认", "未及", "无")

# 明确的『正常/未见异常』声明（用于矛盾判定；不把『正常』二字单独算，避免误伤『形态正常』等）
NORMAL_CLAIM = ["未见异常", "未见明显异常", "无明显异常", "无异常", "未见异常征象",
                "未见明显异常征象", "未见占位", "未见占位性病变", "未见明确异常", "未见异常改变"]

# 常见解剖部位/区域词（用于精准识别『部位+正常』的正常声明，避免『形态正常』『结构正常』误报）
# 仅当『正常』紧跟这些部位/区域词时才判为正常声明；与 NORMAL_CLAIM 互补覆盖
# 『小脑正常』『肝脏正常』『脑实质正常』等"部位声明正常"写法（此前被设计性排除『正常』单字而漏检）。
REGION_NORMAL_WORDS = [
    "小脑", "大脑", "脑干", "丘脑", "基底节", "脑室", "脑实质", "脑白质", "脑沟", "脑回",
    "蛛网膜下腔", "额叶", "颞叶", "顶叶", "枕叶", "垂体", "脊髓", "延髓",
    "肝脏", "肾脏", "脾脏", "胰腺", "胆囊", "心脏", "肺脏", "胃肠", "膀胱",
    "前列腺", "精囊", "子宫", "卵巢", "输卵管", "宫颈", "阴道", "乳腺", "甲状腺",
    "腮腺", "淋巴结", "骨髓", "椎间盘", "椎体", "椎管", "硬膜", "蛛网膜", "颅骨",
    "肋骨", "锁骨", "肩胛骨", "肱骨", "尺骨", "桡骨", "股骨", "髌骨", "胫骨",
    "腓骨", "半月板", "韧带", "肌腱", "关节腔", "滑膜", "主动脉", "下腔静脉",
    "胸膜", "肺门", "支气管", "气管", "食管", "胃", "肠", "胆管", "输尿管",
]
# 『部位+正常』正则（部位词后紧跟『正常』；兼容『双侧/左侧』等方位前缀写在部位前）
_REGION_NORMAL_RE = re.compile(
    "(?:" + "|".join(re.escape(w) for w in
        sorted(set(REGION_NORMAL_WORDS) | set(ANATOMY_SYNONYMS.keys()) | set(SITE_NORM.keys()),
               key=len, reverse=True)) + ")正常"
)

# ===== 逐部位精确比对（R17）解剖部位词表 =====
# 覆盖颅脑 / 胸 / 腹 / 盆 / 骨骼关节等常见放射部位，含左右侧与双侧写法。
# 用途：把『描述段』与『结论段』按 (器官, 侧别) 精确到同一部位比对正常 / 异常声明，
# 解决『左小脑正常 + 右小脑软化灶』（不同侧不矛盾）与『左小脑正常 + 左小脑软化灶』
# （同侧矛盾）的区分——段级规则（R11/R14）无法表达部位级矛盾。
# 注：anatomy_lexicon 的 RadLex 器官族未覆盖脑区（小脑/基底节等），本表专门补齐，
# 与 R2/R14 的 SIDE_CHECK_ORGANS 各司其职，不重复告警。
REGION_LEXICON = {
    # 颅脑
    "cerebellum":      {"base": "小脑", "side": True, "extra": ["小脑半球", "小脑蚓部"]},
    "cerebrum":        {"base": "大脑", "side": True, "extra": ["大脑半球"]},
    "brainstem":       {"base": "脑干", "side": False},
    "thalamus":        {"base": "丘脑", "side": True},
    "basal_ganglia":   {"base": "基底节", "side": True, "extra": ["基底节区"]},
    "ventricle":       {"base": "脑室", "side": True},
    "brain_parenchyma":{"base": "脑实质", "side": False, "extra": ["脑内", "颅内"]},
    "frontal_lobe":    {"base": "额叶", "side": True},
    "temporal_lobe":   {"base": "颞叶", "side": True},
    "parietal_lobe":   {"base": "顶叶", "side": True},
    "occipital_lobe":  {"base": "枕叶", "side": True},
    "pituitary":       {"base": "垂体", "side": False},
    "white_matter":    {"base": "脑白质", "side": False},
    "subarachnoid":    {"base": "蛛网膜下腔", "side": False},
    "spinal_cord":     {"base": "脊髓", "side": False},
    # 胸部
    "lung":            {"base": "肺", "side": True,
                        "extra": ["左肺上叶", "右肺上叶", "左肺下叶", "右肺下叶",
                                  "肺上叶", "肺下叶", "上肺", "下肺", "肺尖", "肺野",
                                  "两肺", "双肺"]},
    "hilum":           {"base": "肺门", "side": True},
    "pleura":          {"base": "胸膜", "side": False, "extra": ["胸腔", "胸水"]},
    "mediastinum":     {"base": "纵隔", "side": False, "extra": ["纵隔影"]},
    "heart":           {"base": "心脏", "side": False, "extra": ["心", "心影"]},
    "bronchus":        {"base": "支气管", "side": True},
    "trachea":         {"base": "气管", "side": False},
    # 腹部
    "liver":           {"base": "肝", "side": True, "extra": ["肝脏", "肝左叶", "肝右叶"]},
    "kidney":          {"base": "肾", "side": True, "extra": ["肾上腺", "肾盂", "双肾"]},
    "spleen":          {"base": "脾", "side": False, "extra": ["脾脏"]},
    "pancreas":        {"base": "胰", "side": False, "extra": ["胰腺"]},
    "gallbladder":     {"base": "胆囊", "side": False},
    "stomach":         {"base": "胃", "side": False},
    "intestine":       {"base": "肠", "side": False, "extra": ["小肠", "结肠", "直肠", "肠道"]},
    "bladder":         {"base": "膀胱", "side": False},
    # 盆腔
    "prostate":        {"base": "前列腺", "side": False},
    "uterus":          {"base": "子宫", "side": False},
    "ovary":           {"base": "卵巢", "side": True},
    "tube":            {"base": "输卵管", "side": True},
    "seminal":         {"base": "精囊", "side": True},
    # 骨骼关节
    "femur":           {"base": "股骨", "side": True, "extra": ["股骨头", "左侧股骨头", "右侧股骨头"]},
    "humerus":         {"base": "肱骨", "side": True},
    "clavicle":        {"base": "锁骨", "side": True},
    "rib":             {"base": "肋骨", "side": True, "extra": ["左肋", "右肋"]},
    "vertebra":        {"base": "椎体", "side": False,
                        "extra": ["颈椎", "胸椎", "腰椎", "骶椎", "椎间盘", "脊柱"]},
    "knee":            {"base": "膝关节", "side": True, "extra": ["左膝", "右膝"]},
    "hip":             {"base": "髋关节", "side": True, "extra": ["左髋", "右髋"]},
    "shoulder":        {"base": "肩关节", "side": True, "extra": ["左肩", "右肩"]},
    "meniscus":        {"base": "半月板", "side": True},
    "ligament":        {"base": "韧带", "side": True},
    # 其它
    "thyroid":         {"base": "甲状腺", "side": True},
    "parotid":         {"base": "腮腺", "side": True},
    "lymph_node":      {"base": "淋巴结", "side": False},
    "breast":          {"base": "乳腺", "side": True, "extra": ["乳房", "左乳", "右乳"]},
    "esophagus":       {"base": "食管", "side": False},
    "aorta":           {"base": "主动脉", "side": False, "extra": ["主动脉弓"]},
}


# ===== R18 区域 → 器官覆盖映射（检查部位声明了区域，但正文漏写该区域器官）=====
# key 为规范区域名；value 为该区域 CT/MR 应描述的器官词（取最常见表达）。
# 与 SITE_NORM 分工不同：SITE_NORM 是『登记部位词 → 粗粒度部位族』，用于 R6 错配检查；
# 本表是『规范区域 → 器官组』，用于 R18 逐区域覆盖校验（上腹部 → 肝/胆/胰/脾/肾…）。
# 粒度说明：只校验"该区域是否至少描述了一个器官"（临床报告通常只写异常器官），
# 不逐器官核对，避免"写了胰脾肾但漏肝"被误报（肝可能正常未描述）。
REGION_TO_ORGANS = {
    "胸部": ["肺", "肺野", "肺门", "胸膜", "纵隔", "心脏", "心影", "支气管", "气管",
             "胸水", "肺纹理", "肺实质"],
    "上腹部": ["肝", "肝脏", "胆囊", "胰腺", "胰", "脾脏", "脾", "肾", "双肾", "肾上腺",
               "胃", "十二指肠", "肝内", "肝左叶", "肝右叶"],
    "中腹部": ["小肠", "结肠", "肠", "肠道", "系膜", "网膜"],
    "下腹部": ["结肠", "直肠", "肠", "肠道", "阑尾", "盲肠", "回肠", "乙状结肠"],
    "全腹": ["肝", "胆囊", "胰腺", "脾", "肾", "肾上腺", "胃", "肠", "膀胱", "腹膜"],
    "盆腔": ["膀胱", "前列腺", "精囊", "子宫", "卵巢", "输卵管", "宫颈", "阴道",
             "直肠", "盆壁", "盆腔"],
    "头颅": ["脑实质", "小脑", "脑干", "丘脑", "基底节", "脑室", "垂体", "脑白质",
             "蛛网膜下腔", "脑沟", "脑回", "脑内", "颅内"],
    "颈椎": ["颈椎", "椎体", "椎间盘", "脊髓", "椎管"],
    "胸椎": ["胸椎", "椎体", "椎间盘", "脊髓", "椎管"],
    "腰椎": ["腰椎", "椎体", "椎间盘", "脊髓", "椎管"],
    "肩关节": ["肩", "肱骨头", "肩锁", "喙突", "肩峰"],
    "膝关节": ["股骨远端", "胫骨近端", "髌骨", "半月板", "交叉韧带", "髌上囊"],
}

# 检查类型 → 必查要素清单（R20 模板完整性校验）。
# 与 R18 的区别：R18 只查『登记区域是否提到至少一个器官』（粒度粗，防误报）；
# R20 按『检查类型』校验必查要素缺项（粒度细，抓漏写），如胸部 CT 应描述
# 肺纹理/纵隔/胸膜/骨性胸廓等。由登记部位/检查类型关键词自动匹配。
REPORT_TYPE_REQUIREMENTS = {
    "胸部CT": {
        "要素": ["肺纹理", "肺实质", "肺门", "纵隔", "胸膜", "胸腔", "心脏", "心影",
                "骨性胸廓", "胸壁", "气管", "支气管"],
        "提示": "胸部 CT 报告应描述肺野/肺纹理、肺门及纵隔、胸膜与胸腔、心脏大血管、骨性胸廓等",
    },
    "腹部CT": {
        "要素": ["肝", "胆囊", "胆管", "胰腺", "脾", "双肾", "肾", "肾上腺",
                "胃", "肠道", "肠", "腹腔", "腹膜", "淋巴结"],
        "提示": "腹部 CT 报告应描述肝/胆/胰/脾/双肾、胃肠及腹腔淋巴结等",
    },
    "头颅CT": {
        "要素": ["脑实质", "脑白质", "脑灰质", "小脑", "脑干", "脑室", "基底节",
                "中线", "脑沟", "颅骨", "蛛网膜下腔"],
        "提示": "头颅 CT 报告应描述脑实质、脑室系统、中线结构、颅骨等",
    },
    "腰椎": {
        "要素": ["腰椎", "椎体", "椎间盘", "硬膜囊", "神经根", "黄韧带", "椎管", "小关节"],
        "提示": "腰椎报告应描述椎体/附件、椎间盘、硬膜囊及神经根、椎管等",
    },
    "颈椎": {
        "要素": ["颈椎", "椎体", "椎间盘", "硬膜囊", "神经根", "椎管", "韧带"],
        "提示": "颈椎报告应描述椎体/附件、椎间盘、硬膜囊及神经根、椎管等",
    },
    "乳腺": {
        "要素": ["腺体", "肿块", "结节", "钙化", "腋窝", "皮肤", "乳头", "Cooper"],
        "提示": "乳腺报告应描述腺体类型、肿块/结节、钙化、腋窝淋巴结等",
    },
    "盆腔": {
        "要素": ["膀胱", "直肠", "盆壁", "盆腔"],
        "提示": "盆腔报告应描述膀胱、直肠、盆壁等结构",
    },
    "膝关节": {
        "要素": ["股骨", "胫骨", "髌骨", "半月板", "交叉韧带", "关节囊", "髌上囊"],
        "提示": "膝关节报告应描述股骨/胫骨/髌骨、半月板、交叉韧带、关节腔等",
    },
}

# 检查类型关键词 → 规范类型（从登记部位/检查方式匹配）
_TYPE_KEYWORDS = [
    ("胸部CT", ["胸部ct", "胸部平扫", "胸部增强", "肺部ct", "肺ct", "双肺ct", "胸ct", "胸部ct平扫"]),
    ("腹部CT", ["腹部ct", "全腹ct", "上腹ct", "下腹ct", "腹ct", "腹部平扫", "腹部增强"]),
    ("头颅CT", ["头颅ct", "头部ct", "颅脑ct", "脑ct", "头颅平扫", "颅脑平扫"]),
    ("腰椎", ["腰椎", "腰段", "l1", "l2", "l3", "l4", "l5", "腰1", "腰2", "腰3", "腰4", "腰5"]),
    ("颈椎", ["颈椎", "颈段", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "颈1", "颈2", "颈3"]),
    ("乳腺", ["乳腺", "乳房", "钼靶", "breast"]),
    ("盆腔", ["盆腔", "骨盆", "pelvis"]),
    ("膝关节", ["膝关节", "膝部", "knee"]),
]

# 区域关键词别名 → 规范区域名（用于从登记部位『胸部、上腹部』中逐区域提取，多区域互不牵连）
_REGION_ALIAS_TO_REGION = {
    "胸部": "胸部", "双肺": "胸部", "肺部": "胸部", "肺": "胸部",
    "上腹部": "上腹部", "上腹": "上腹部",
    "中腹部": "中腹部", "中腹": "中腹部",
    "下腹部": "下腹部", "下腹": "下腹部",
    "全腹": "全腹", "全腹部": "全腹", "腹部": "全腹",
    "盆腔": "盆腔", "骨盆": "盆腔",
    "头颅": "头颅", "颅脑": "头颅", "头部": "头颅", "脑": "头颅",
    "颈椎": "颈椎", "胸椎": "胸椎", "腰椎": "腰椎",
    "肩关节": "肩关节", "膝关节": "膝关节",
}
_REGION_KW_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_REGION_ALIAS_TO_REGION, key=len, reverse=True))
)


def _extract_regions(applied_site: str) -> List[str]:
    """从登记部位串中提取规范区域名列表（支持『胸部、上腹部』逗号/顿号分隔的多区域）。"""
    if not applied_site:
        return []
    return list(dict.fromkeys(
        _REGION_ALIAS_TO_REGION[m.group(0)] for m in _REGION_KW_RE.finditer(applied_site)
    ))


def _side_of_alias(a: str):
    """根据别名中的方位词判定侧别：双侧 / 左 / 右 / None。"""
    if "双" in a or "两" in a or "两侧" in a:
        return "bilateral"
    if "左" in a and "右" not in a:
        return "left"
    if "右" in a and "左" not in a:
        return "right"
    return None


# 扁平别名表：别名 -> (器官key, 侧别)；最长匹配优先，避免短词（『肺』）抢长词（『右肺上叶』）。
_REGION_ALIAS_LIST = []
for _key, _meta in REGION_LEXICON.items():
    _base = _meta["base"]
    _al = [_base]
    if _meta.get("side"):
        _al += ["左" + _base, "右" + _base, "左侧" + _base, "右侧" + _base]
    _al += _meta.get("extra", [])
    for _a in _al:
        _REGION_ALIAS_LIST.append((_a, _key, _side_of_alias(_a)))
# 去重（同别名取首个），按长度降序便于最长匹配
_SEEN_A = set()
_REGION_ALIAS_LIST_SORTED = []
for _a, _k, _s in sorted(_REGION_ALIAS_LIST, key=lambda x: -len(x[0])):
    if _a in _SEEN_A:
        continue
    _SEEN_A.add(_a)
    _REGION_ALIAS_LIST_SORTED.append((_a, _k, _s))
_REGION_ALIAS_RE = re.compile("|".join(re.escape(a) for a, _, _ in _REGION_ALIAS_LIST_SORTED))


def _region_spans_in_text(text: str):
    """返回文本中所有部位提及：list of (器官key, 侧别, start, end)。最长匹配、跳过被覆盖短词。"""
    if not text:
        return []
    spans = []
    covered = []
    for _a, _k, _s in _REGION_ALIAS_LIST_SORTED:
        for m in re.finditer(re.escape(_a), text):
            s, e = m.start(), m.end()
            if any((cs <= s < ce) or (cs < e <= ce) or (s < cs and e > ce) for cs, ce in covered):
                continue
            spans.append((_k, _s, s, e))
            covered.append((s, e))
    return spans


_SIDE_NAME_OVERRIDE = {
    "liver": {"left": "肝左叶", "right": "肝右叶", "bilateral": "肝脏"},
}


def _region_cn_name(key, side) -> str:
    """(器官key, 侧别) -> 中文部位名（用于精准告警展示）。"""
    meta = REGION_LEXICON.get(key)
    if not meta:
        return str(key)
    base = meta["base"]
    ov = _SIDE_NAME_OVERRIDE.get(key, {})
    if side in ov:
        return ov[side]
    if side == "left":
        return "左" + base
    if side == "right":
        return "右" + base
    if side == "bilateral":
        return "双侧" + base
    return base


def _region_assertions_in_section(text: str, spans):
    """按部位聚合该段的正常 / 异常声明：返回 {(key, side): set(['normal'/'positive'])}。

    断言判定要求『紧邻部位词』，避免跨部位误贴：
      - 正常：部位词后紧跟『正常』(≤2字) 或紧跟『未见异常/未见明显异常/未见占位/
        未见明确异常』（部位词后窗口）。
      - 异常：部位词后窗口(后16字)含阳性征词（如『右肺上叶见结节』→ 结节）。
    只向后看（不看部位词之前），避免向前跨句吞掉前一句的『未见异常信号』导致否定
    前缀被截断失效（如『脑实质内未见异常信号，脑室系统大小正常』误把『异常信号』
    算进『脑室』区域）。同一部位在同一段既称正常又报病灶 → 两者都记，交由 R12/R15 处理。"""
    out = {}
    for (_k, _s, _st, _en) in spans:
        after = text[_en: _en + 5]
        after_big = text[_en: _en + 16]
        is_normal = ("正常" in after[:2]) or ("未见异常" in after_big) \
                    or ("未见明显异常" in after_big) or ("未见占位" in after_big) \
                    or ("未见明确异常" in after_big)
        is_positive = _has_positive(after_big)
        bucket = out.setdefault((_k, _s), set())
        if is_normal:
            bucket.add("normal")
        if is_positive:
            bucket.add("positive")
    return out


def _is_segment_global_normal(text: str, spans):
    """段级『全局正常』判定：仅当该段无阳性征、含 NORMAL_CLAIM 短语、且【不含任何具体部位提及】
    时才视为覆盖全段（可扩展至所有部位）。若段内已点名某部位（如『小脑正常』『余两肺未见异常』），
    则该正常声明是『部位级/局部』的，不得外溢到其它部位，避免『左侧小脑正常』被误判为『右侧小脑也正常』。"""
    if not text or _has_positive(text):
        return False
    if not any(k in text for k in NORMAL_CLAIM):
        return False
    if spans:
        return False
    return True

# 良恶性定性标记（用于前后文定性矛盾 R14-NATURE）
MALIGNANT_MARKERS = ["恶性", "癌", "转移", "浸润", "侵犯", "恶变", "ca", "mt"]
BENIGN_MARKERS = ["良性", "炎性", "炎症", "符合良性", "考虑良性"]

# 描述段内「先见后无」检测的阳性/阴性动词
_PRESENCE_VERBS = ["见", "示", "可见", "探及", "发现", "查见", "考虑", "提示", "显示"]
_ABSENCE_VERBS = ["未见", "消失", "吸收", "已吸收", "消退"]
LESION_WORDS = ["结节", "占位", "肿块", "病灶", "囊肿", "结石", "骨折", "积液",
                "阴影", "斑片", "异常信号"]

# 需做左右侧一致性比对的成对解剖结构
# —— 跨段比对(R14)用此表：文本级兜底，覆盖 R2（NER 带 L-/R- 前缀规范节点）未能可靠覆盖的器官。
#    来源：src/anatomy_lexicon.py（RadLex 器官族 + 侧别建模）动态派生；保留原有的 R2 跨段覆盖分离，
#    排除 R2 已覆盖的「肾/股骨头」避免重复告警。新增 输卵管/精囊/锁骨/肋骨 等成对器官扩大覆盖。
ORGAN_SIDE_LIST = [o for o in SIDE_CHECK_ORGANS if o not in R2_COVERED]
# —— 段内跨句比对(R15)用此表：在 ORGAN_SIDE_LIST 基础上补 R2 已覆盖的「肾/股骨头」
#    （R15 仅查描述段内部，不与 R2 跨段检查重叠，故可并存）
ORGAN_SIDE_LIST_INTERNAL = ORGAN_SIDE_LIST + list(R2_COVERED)

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "双": 2, "单": 1}


def _norm_laterality(s):
    """把侧别写法归一化为 'left'/'right'/'bilateral'；无法识别返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    if s in LATERALITY:
        return LATERALITY[s]
    # 兼容口语/书面写法：左侧/右侧/双侧/两边/左右
    if "双" in s or "两" in s or "左右" in s:
        return "bilateral"
    if "左" in s and "右" not in s:
        return "left"
    if "右" in s and "左" not in s:
        return "right"
    return None


def _detect_side_in_text(text: str) -> set:
    """扫描文本中提及的方位集合（left/right/bilateral）。兼容中英文报告。"""
    if not text:
        return set()
    sides = set()
    if re.search(r"左\s*(侧|肺|肾|肝|乳|肾上腺|卵巢|睾丸|附件|股骨|肱骨|膝|髋|肩|肘|腕|踝|叶|上|下|腹|盆|位)", text):
        sides.add("left")
    if re.search(r"右\s*(侧|肺|肾|肝|乳|肾上腺|卵巢|睾丸|附件|股骨|肱骨|膝|髋|肩|肘|腕|踝|叶|上|下|腹|盆|位)", text):
        sides.add("right")
    if re.search(r"双侧|两侧|左右|两边", text):
        sides.add("bilateral")
    # 英文报告（MIMIC-CXR / IU-Xray 风格）：left/right/bilateral 关键词
    low = text.lower()
    if re.search(r"\bleft\b", low):
        sides.add("left")
    if re.search(r"\bright\b", low):
        sides.add("right")
    if re.search(r"\bbilateral\b", low):
        sides.add("bilateral")
    return sides


def _project_side(report: str, meta: dict):
    """派生『项目 / 检查部位』侧别（left/right/bilateral/None）。

    优先级：① 显式 meta.laterality（SPA 侧别下拉）>
    ② 报告内显式标签字段（如『检查项目：右膝』『检查部位：左肺』）>
    ③ meta.applied_site（检查部位自由文本，如『右膝关节』）。

    之前 R11-SIDE 只取 ①，用户常把侧别写在 项目 自由文本里而不填独立下拉框，
    导致 meta.laterality 为空、规则整条跳过，无法核对 项目 vs 描述/诊断 左右。
    ② ③ 兜底让规则在常见录入方式下都能正确启动。
    """
    s = _norm_laterality(meta.get("laterality"))
    if s:
        return s
    for label in ("检查项目", "检查部位", "检查名称", "申请部位", "扫描部位", "检查范围"):
        m = re.search(label + r"[:：]?\s*([^\n，,。；;：]+)", report or "")
        if m:
            ss = _norm_laterality(m.group(1))
            if ss:
                return ss
    ss = _norm_laterality(meta.get("applied_site"))
    if ss:
        return ss
    return None


def _organ_sides_en(text: str, aliases) -> set:
    """英文器官左右检测：返回文本中某器官的方位集合（left/right）。
    语序兼容 'left lung' 与 'lung left'，并允许侧别词与器官间有少量修饰词
    （如 'right lower lobe'）。仅在含英文的报告中使用，不影响中文。"""
    if not text:
        return set()
    sides = set()
    low = text.lower()
    for a in aliases:
        if (re.search(r"\bleft\b.{0,30}?\b" + re.escape(a), low)
                or re.search(r"\b" + re.escape(a) + r"\b.{0,30}?\bleft\b", low)):
            sides.add("left")
        if (re.search(r"\bright\b.{0,30}?\b" + re.escape(a), low)
                or re.search(r"\b" + re.escape(a) + r"\b.{0,30}?\bright\b", low)):
            sides.add("right")
    return sides


def _claims_normal(text: str) -> bool:
    """文本是否明确声明『未见异常/正常』（用于描述-结论矛盾）。
    注：中文无词边界，采用子串匹配；NORMAL_CLAIM 均为强特异性表述，误命中风险低。
    2026-08-05 起补充『部位+正常』精准识别：仅当『正常』紧跟解剖部位/区域词时
    才判为正常声明，避免『形态正常』『结构正常』『密度正常』『信号正常』等误报
    （这正是此前『小脑正常→结论小脑软化灶』漏检的根因）。"""
    if not text:
        return False
    if any(k in text for k in NORMAL_CLAIM):
        return True
    return bool(_REGION_NORMAL_RE.search(text))


def _has_positive(text: str) -> bool:
    """文本是否包含强阳性征（异常表现）。
    注：中文无词边界，采用子串匹配；POSITIVE_STRONG 均为强特异性词（占位/结节/癌…），
    误命中风险低。『未见/未见明显/无/不伴…』等否定前缀后的阳性征词不算异常，
    避免『未见明显炎症』『无增生』被误判（参考 _NEG_PREFIXES）。如需更严谨可改为句级判定。"""
    if not text:
        return False
    for k in POSITIVE_STRONG:
        idx = text.find(k)
        while idx != -1:
            pre = text[max(0, idx - 5): idx]
            if not any(pre.endswith(neg) for neg in _NEG_PREFIXES):
                return True
            idx = text.find(k, idx + 1)
    return False


def _split_sentences(text: str) -> list:
    """按中英文句末标点 + 换行切分为句子（保留标点）。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p and p.strip()]


def _cn_to_int(tok):
    """中文数字/阿拉伯数字 → int；无法解析返回 None。"""
    if tok is None:
        return None
    tok = str(tok).strip()
    if tok.isdigit():
        return int(tok)
    return _CN_NUM.get(tok)


def _extract_lesion_count(text: str):
    """抽取文本中明确的病灶计数（枚/个），无法判定（如『多发/数枚』）返回 None。"""
    if not text:
        return None
    m = re.search(r"([一二三四五六七八九十两双单\d])\s*枚", text)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"([一二三四五六七八九十两双单\d])\s*个\s*(?:结节|占位|肿块|病灶|囊肿|结石|骨折|积液)", text)
    if m:
        return _cn_to_int(m.group(1))
    return None


def _has_marker_unnegated(text: str, markers) -> bool:
    """文本中是否存在未受否定修饰的标记词（用于良恶性判定，避免『未见恶性』误判为恶性）。"""
    if not text:
        return False
    for sent in _split_sentences(text):
        s = sent.lower()
        if not any(k.lower() in s for k in markers):
            continue
        if re.search(r"未见|未示|未见明显|不除外|不考虑|除外|待排|未见\s*明确", sent):
            continue
        return True
    return False


# 复合器官（含短名），避免短名被复合词误命中（如「肾上腺」误判为「肾」的左右）。
# 注意：肝左叶/肝右叶 是真实的左右叶矛盾来源，必须保留比对，故不入此排除列表。
_ORGAN_COMPOUND = {
    "肾": ["肾上腺", "肾盂", "肾盏", "肾窦", "肾门"],
    "肝": ["肝胆"],
}


def _organ_sides_in_text(text: str, organ: str) -> set:
    """返回文本中提及某器官的方位集合（left/right）。兼容『左肺』与『肝左叶』两种语序。
    先剔除复合器官名（如肾上腺/肝胆），避免短名（肾/肝）被复合词误命中而产生假阳性左右矛盾。"""
    sides = set()
    tmp = text
    for comp in _ORGAN_COMPOUND.get(organ, []):
        tmp = tmp.replace(comp, "")
    if (re.search(r"左\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*左", tmp)):
        sides.add("left")
    if (re.search(r"右\s*" + re.escape(organ), tmp)
            or re.search(re.escape(organ) + r"\s*右", tmp)):
        sides.add("right")
    return sides

# 放射报告常见同音/近音错别字（多由语音录入产生）：错词 → 正确词
# 该词典现由 assets/rules_config.json 维护（用户可在 GUI 中增删）；此处为读取失败的兜底默认值。
TYPO_MAP_DEFAULT = {
    "姐姐": "结节", "结解": "结节",
    "战位": "占位", "占为": "占位",
    "改化": "钙化", "盖化": "钙化", "钙话": "钙化", "钙划": "钙化",
    "病造": "病灶", "病燥": "病灶",
    "增墙": "增强", "墙化": "强化",
    "迷漫": "弥漫", "弥慢": "弥漫",
    "摩玻璃": "磨玻璃", "磨破璃": "磨玻璃",
    "深出": "渗出", "胸模": "胸膜",
    "般片": "斑片", "斑偏": "斑片", "政象": "征象",
    "纵格": "纵隔", "临吧": "淋巴", "淋巴结解": "淋巴结节",
    "囊中": "囊肿", "水种": "水肿",
    # —— 器官名形近/音近错字（typed + voice）——
    "子官": "子宫", "字宫": "子宫",
    "前裂腺": "前列腺", "前例腺": "前列腺",
    "腮线": "腮腺",
    "骨拆": "骨折",
    "蜘蛛膜": "蛛网膜",
    "申状腺": "甲状腺",
    "食官": "食管",
    "兰尾": "阑尾",
    "纵膈": "纵隔",
    # —— 疾病/征象名错字 ——
    "肺结合": "肺结核", "费炎": "肺炎",
    "曾生": "增生", "积夜": "积液",
    "息内": "息肉", "精脉曲张": "静脉曲张",
    "动肪瘤": "动脉瘤", "哽死": "梗死",
    "坎影": "龛影", "憩事": "憩室",
    "溃殇": "溃疡", "浸闰": "浸润",
    "珍断": "诊断", "征像": "征象",
    "曾强": "增强", "造形": "造影",
    "覆查": "复查", "随防": "随访",
    "坐肺": "左肺",
}

# 规则配置文件路径（与 samples.db 同目录：assets/rules_config.json）
# 兼容 PyInstaller 打包：打包后资源位于 exe 同级的 assets/ 下。
def _assets_dir() -> str:
    if getattr(sys, "frozen", False):
        # 打包后：exe 所在目录 / assets
        return os.path.join(os.path.dirname(sys.executable), "assets")
    # 开发态：src/../assets
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def _rules_config_path() -> str:
    """定位规则配置：打包后优先用用户可写目录(%APPDATA%/MedicalReportQC)，
    不存在则从 exe 同级 assets 复制初始文件，避免安装到 Program Files 后只读。"""
    if getattr(sys, "frozen", False):
        user_dir = os.path.join(os.path.expandvars("%APPDATA%"), "MedicalReportQC")
        user_path = os.path.join(user_dir, "rules_config.json")
        if not os.path.exists(user_path):
            src = os.path.join(os.path.dirname(sys.executable), "assets", "rules_config.json")
            try:
                os.makedirs(user_dir, exist_ok=True)
                if os.path.exists(src):
                    shutil.copyfile(src, user_path)
            except Exception:
                return src
        return user_path
    return os.path.join(_assets_dir(), "rules_config.json")


RULES_CONFIG_PATH = _rules_config_path()


# 结构化报告模板默认规范（可在 rules_config.json 的 template 字段覆盖）
DEFAULT_TEMPLATE = {
    "required_sections": ["findings", "impression"],  # 必须含「检查所见」与「诊断印象/结论」段
    "require_followup": True,                          # 建议给出随访/复查建议
    "severity": "low",
    "note": "结构化报告建议含『检查所见』与『诊断印象/结论』段，并给出随访/复查建议",
}


def default_rules_config() -> dict:
    """出厂默认规则配置（恢复默认用）。"""
    return {"typos": dict(TYPO_MAP_DEFAULT), "conflicts": [],
            "ignores": [], "template": dict(DEFAULT_TEMPLATE),
            "enable_r19": True}


def load_rules_config(path: str = RULES_CONFIG_PATH) -> dict:
    """读取用户维护的规则配置。失败回退内置默认值，保证引擎始终可用。"""
    defaults = default_rules_config()
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.setdefault("typos", dict(TYPO_MAP_DEFAULT))
        cfg.setdefault("conflicts", [])
        cfg.setdefault("ignores", [])
        cfg.setdefault("template", dict(DEFAULT_TEMPLATE))
        cfg.setdefault("enable_r19", True)
        return cfg
    except Exception:
        return defaults


def save_rules_config(cfg: dict, path: str = RULES_CONFIG_PATH) -> None:
    """持久化规则配置到 JSON。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def learn_typo(wrong: str, correct: str, path: str = RULES_CONFIG_PATH) -> bool:
    """修正反馈闭环：把用户确认的「错词→正确词」写入规则库 typos。
    带 _source: "learned" 标记，后续自动生效（R8 直接命中），
    且与人工录入（无 _source）区分，便于审计与回滚。"""
    wrong = (wrong or "").strip()
    correct = (correct or "").strip()
    # 校验：非空、不等、长度受限（1~10 字）、仅含中文（防止标点/超长/异物写入规则库）
    if not wrong or not correct or wrong == correct:
        return False
    if len(wrong) > 10 or len(correct) > 10:
        return False
    _CN = re.compile(r"^[\u4e00-\u9fa5]+$")
    if not _CN.match(wrong) or not _CN.match(correct):
        return False
    cfg = load_rules_config(path)
    typos = cfg.setdefault("typos", {})
    # 反向冲突保护：若正确词本身在错词表里（如曾误学 结节→姐姐），跳过
    if typos.get(correct) == wrong:
        return False
    typos[wrong] = correct
    save_rules_config(cfg, path)
    return True


def scan_reports_for_typos(path: str = RULES_CONFIG_PATH, limit: int = 200) -> list:
    """历史报告词频学习：扫描样本库 report_text，自动发现「低频写法 → 高频标准写法」候选。
    返回候选列表 [{wrong, correct, count, category, similarity, reason}]，供前端一键采纳。

    算法（纯本地、无模型）：
    1. 从样本库读取最近 limit 份报告正文；
    2. 滑窗 2-4 字切词 + 高频词库锚定，统计每词出现频次；
    3. 对每个「不在白名单、出现次数少」的片段，用读音相似度与高频正确词比对；
    4. 相似度高（同音/近音）且明显低于锚点词频的，列为候选错字；
    5. 已存在于 typos / ignores 的自动排除。
    """
    try:
        import samplelib
        # SQL 层直接限制最近 limit 份（避免全量载入长文本报告）
        samples = samplelib.list_samples_full(limit=limit)
    except Exception:
        return []
    if not samples:
        return []
    # 词频统计（单报告处理长度设上限，防止极端超长文本拖垮接口）
    from collections import Counter
    _MAX_CHARS = 4000
    freq: Counter = Counter()
    for s in samples:
        text = (s.get("report_text") or "") if isinstance(s, dict) else getattr(s, "report_text", "") or ""
        if not text:
            continue
        text = text[:_MAX_CHARS]
        # 标点/空白替换为空格作为切词边界，避免跨标点把不相关的字拼成「伪词」
        # （如「结节。复查」被拼成「结节复查」误导统计）
        text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", text)
        # 只对连续中文片段滑窗，英文/数字单词整体计一次
        for seg in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
            n = len(seg)
            for i in range(n):
                for L in (4, 3, 2):
                    if i + L <= n:
                        freq[seg[i:i + L]] += 1
    if not freq:
        return []
    # 读取现有规则避免重复推荐
    cfg = load_rules_config(path)
    typos = set(cfg.get("typos", {}))
    ignores = set(cfg.get("ignores", []))
    # 高频锚点：取词库中出现次数 ≥ 2 的词作为「标准写法」
    anchors = {w for w, c in freq.items() if c >= 2 and len(w) >= 2}
    # 低频候选 + 读音比对
    candidates = []
    seen = set()
    try:
        from highfreq_lexicon import find_homophone_suggestions, highfreq_words
        hf = {w for w, _ in highfreq_words()}
    except Exception:
        hf = set()
    for w, c in sorted(freq.items(), key=lambda kv: -kv[1]):
        if c >= 3 or len(w) < 2:
            continue
        if w in typos or w in ignores or w in hf or w in seen:
            continue
        if w in anchors:
            continue
        cand = find_homophone_suggestions(w)
        if not cand:
            continue
        best, cat, sim = cand[0]
        if sim < 0.97:
            continue
        seen.add(w)
        candidates.append({
            "wrong": w, "correct": best, "count": c,
            "category": cat, "similarity": round(sim, 3),
            "reason": f"历史报告出现 {c} 次，读音与「{best}」相似，疑似错字",
        })
        if len(candidates) >= 20:
            break
    return candidates


# ----------------------------- NER -----------------------------
class ChineseRadiologyNER:
    SECTION_MAP = [
        (re.compile(r"检查所见|影像描述|表现|imaging findings|findings|radiographic findings", re.I), "findings"),
        (re.compile(r"诊断印象|印象|诊断意见|结论|impression", re.I), "impression"),
        (re.compile(r"患者信息|患者|性别|年龄|检查部位|申请"), "meta"),
    ]

    def _split_sections(self, text: str):
        spans = []
        for pat, sec in self.SECTION_MAP:
            for m in pat.finditer(text):
                spans.append((m.start(), sec))
        spans.sort()
        sections = []
        for i, (start, sec) in enumerate(spans):
            end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
            sections.append((sec, start, end))
        if not sections:
            sections = [("findings", 0, len(text))]
        return sections

    def _section_of(self, sections, pos: int) -> str:
        for sec, s, e in sections:
            if s <= pos < e:
                return sec
        return sections[-1][0] if sections else "findings"

    def extract(self, text: str) -> List[Entity]:
        sections = self._split_sections(text)
        ents: List[Entity] = []
        # 方位词 + 解剖同义词：统一按词长降序做最长匹配，避免短词（"左"）抢先占用
        # 长词（"双侧""左肾"）的区间，使短词（"左""右""双"）被跳过。
        matched = []  # 所有已抽取实体的字符区间，用于跳过被覆盖的短词

        def _add(word, label, canon):
            for m in re.finditer(re.escape(word), text):
                s, e = m.start(), m.end()
                if any((ms <= s < me) or (ms < e <= me) or (s < ms and e > me)
                       for ms, me in matched):
                    continue
                ents.append(Entity(word, label, s, e,
                                    self._section_of(sections, s), canon))
                matched.append((s, e))

        combined = [(w, "laterality", c) for w, c in LATERALITY.items()] + \
                   [(w, "anatomy", c) for w, c in ANATOMY_SYNONYMS.items()]
        for word, label, canon in sorted(combined, key=lambda kv: -len(kv[0])):
            _add(word, label, canon)
        for word, gender in GENDER_ORGANS.items():
            for m in re.finditer(re.escape(word), text):
                ents.append(Entity(word, "gender_organ", m.start(), m.end(),
                                    self._section_of(sections, m.start()), gender))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([A-Za-z°/][A-Za-z°/0-9]*|\u00b0)", text):
            unit = m.group(2).lower()
            label = "measurement" if unit in VALID_UNITS else "bad_unit"
            ents.append(Entity(m.group(0), label, m.start(), m.end(),
                                self._section_of(sections, m.start()), unit))
        # —— 中文征象 / 随访 / 程度 实体（项目自建 zh_ner，离线词典式）——
        # 仅补充引擎既有 NER 未覆盖的 sign/followup/degree 三类；anatomy/laterality
        # 仍由上方 ANATOMY_SYNONYMS / LATERALITY 负责，避免重复抽取。
        if _ZH_NLP_OK:
            for ze in _zh_ner_entities(text):
                eng_label = _ZH_ENT_LABEL_MAP.get(ze.label)
                if eng_label is None:
                    continue
                if any((ms <= ze.start < me) or (ms < ze.end <= me)
                       or (ze.start < ms and ze.end > me) for ms, me in matched):
                    continue
                sec = self._section_of(sections, ze.start)
                ents.append(Entity(ze.text, eng_label, ze.start, ze.end, sec, ze.canonical))
                matched.append((ze.start, ze.end))
        return ents


# ----------------------------- 规则引擎 -----------------------------
class RuleEngine:
    def __init__(self):
        self.kg = _KG()
        self.rules_config = load_rules_config()

    def reload_rules(self):
        """重新从 assets/rules_config.json 读取规则（用户维护后即时生效）。"""
        self.rules_config = load_rules_config()

    def run(self, text: str, meta: dict) -> List[Finding]:
        # 实际启用规则：R1–R12、R14、R15、R17；R16（随访时限缺失）为可选规则，
        # 由 rules_config.enable_r16 控制（默认关闭，避免对常规『定期复查』过度告警）。
        # R13 为预留编号（当前无对应规则），故不调用 _r13_*。
        # R17 为逐部位精确比对（描述段↔结论段按 器官+侧别 精确到同一部位，承接原 R11-2/R14-1 段级逻辑）。
        # R18 为检查部位器官漏写（登记区域声明 → 描述段应含该区域器官，承接"胸部、上腹部"漏写上腹部器官等场景）。
        ner = ChineseRadiologyNER()
        ents = ner.extract(text)
        secs = self._split_for_r5(text)
        return (self._r1_gender(text, ents, meta)
                + self._r2_laterality(text, ents)
                + self._r3_score(text, meta)
                + self._r4_unit(ents)
                + self._r5_consistency(text, ents)
                + self._r6_site(text, meta)
                + self._r7_internal(text, ents)
                + self._r8_typo(text)
                + self._r9_conflict(text)
                + self._r10_template(text)
                + self._r11_context(text, meta, secs)
                + self._r12_sentence(text, ents)
                + self._r14_cross(text, secs)
                + self._r15_internal(text)
                + self._r17_cross_region(text, secs)
                + self._r18_region_coverage(text, meta)
                + self._r20_template_completeness(text, meta)
                + self._r21_gender_site(text, meta)
                + (self._r19_homophone(text)
                   if self.rules_config.get("enable_r19", True) else [])
                + (self._r16_followup_timeframe(text)
                   if self.rules_config.get("enable_r16") else []))

    def auto_fix(self, text: str, findings: List[Finding]):
        """自动修正：仅确定性错别字(R8)可安全替换；矛盾/规范/缺失类错误无法判定正确值，不改文本。
        返回 (修正后文本, 已修正错别字数, 需人工确认的问题数, 改动明细列表)。
        改动明细每项：{start, end, wrong, correct, snippet, message} —— 供前端预览逐条确认。"""
        fixes = []
        manual = 0
        for fd in findings:
            if fd.rule_id == "R8-TYPO":
                s, e = fd.span
                sug = getattr(fd, "suggestion", "")
                if s >= 0 and e > s and sug:
                    wrong = text[s:e]
                    snippet = text[max(0, s - 12): min(len(text), e + 12)]
                    fixes.append({"start": s, "end": e, "wrong": wrong,
                                  "correct": sug, "snippet": snippet, "message": fd.message})
            else:
                # 非错别字类（性别矛盾/左右混淆/描述-结论矛盾/部位不符等）需人工判定，不自动改
                if fd.severity in ("high", "medium", "low"):
                    manual += 1
        # 区间已由 _r8_typo 去重，无重叠；从右往左替换以规避位置偏移
        fixes.sort(key=lambda x: x["start"], reverse=True)
        fixed = text
        for fx in fixes:
            s, e, correct = fx["start"], fx["end"], fx["correct"]
            fixed = fixed[:s] + correct + fixed[e:]
        return fixed, len(fixes), manual, fixes

    # R1 性别矛盾
    def _r1_gender(self, text, ents, meta) -> List[Finding]:
        out = []
        # 性别来源：优先元信息，其次从报告正文解析（契合剪贴板/无元信息场景）
        rg = _norm_gender(meta.get("gender"))
        src = "报告头"
        if not rg:
            rg = _parse_gender_from_text(text)
            src = "报告正文"
        if not rg:
            return out  # 无法确定性别，不做判断（避免臆断）
        seen = set()
        for e in [x for x in ents if x.label == "gender_organ"]:
            expect = self.kg.expected_gender_for_organ(e.text)  # "male"/"female"
            if expect and expect != rg and e.text not in seen:
                seen.add(e.text)
                box = {"findings": "影像描述段", "impression": "影像结论段"}.get(e.section, "报告正文")
                out.append(Finding("R1-GENDER", "性别矛盾", "high",
                    f"{src}性别为{_zh(rg)}，但{box}出现{_zh(expect)}性专属器官「{e.text}」"
                    f"（{'男性不应有子宫/卵巢等' if expect=='female' else '女性不应有前列腺/睾丸等'}）",
                    e.text, (e.start, e.end)))
        return out

    # R2 左右混淆（同一解剖族：描述段 vs 印象段方位互斥）
    def _r2_laterality(self, text, ents) -> List[Finding]:
        out = []
        # 取带左右前缀的解剖实体（左肾→L-kidney 等），按器官族归组
        fam_of = lambda c: (c or "").split("-", 1)[-1] if (c or "").startswith(("L-", "R-")) else None
        fam_sides = {}
        for e in ents:
            if e.label != "anatomy" or not e.canonical:
                continue
            if e.section not in ("findings", "impression"):  # 仅在描述段与印象段间比较
                continue
            fam = fam_of(e.canonical)
            if not fam:
                continue
            side = "left" if e.canonical.startswith("L-") else "right"
            fam_sides.setdefault(fam, {"findings": set(), "impression": set()})
            fam_sides[fam][e.section].add(side)
        for fam, sides in fam_sides.items():
            f, i = sides["findings"], sides["impression"]
            if f and i and f.isdisjoint(i):
                out.append(Finding("R2-LATERALITY", "左右侧混淆", "high",
                    f"同一解剖部位族「{fam}」在影像描述段为{f}侧、诊断印象段为{i}侧，方位矛盾",
                    "", (-1, -1)))
        return out

    # R3 评分缺失
    def _r3_score(self, text, meta) -> List[Finding]:
        out = []
        modality = meta.get("modality")
        if not modality:
            return out
        required = self.kg.required_score_for_modality(modality)
        if not required:
            return out
        pat = {"BI-RADS": r"BI-?RADS\s*[:：]?\s*\d", "PI-RADS": r"PI-?RADS\s*[:：]?\s*\d"}.get(required)
        if pat and not re.search(pat, text, re.I):
            out.append(Finding("R3-SCORE", "评分标准缺失", "medium",
                f"检查部位={modality} 要求包含 {required}，但报告未检出", "", (-1, -1)))
        return out

    # R4 单位错误
    def _r4_unit(self, ents) -> List[Finding]:
        out = []
        for e in [x for x in ents if x.label == "bad_unit"]:
            out.append(Finding("R4-UNIT", "计量单位错误", "low",
                f"检出非常规单位表示「{e.text}」（单位={e.canonical}）", e.text, (e.start, e.end)))
        return out

    # R5 描述-结论矛盾（按器官族核对：描述段某器官族出现阳性征，印象段未就该器官族给出对应结论）
    def _r5_consistency(self, text, ents) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt, i_txt = secs["findings"], secs["impression"]
        f0 = secs.get("findings_start", 0)
        # 按"规范器官族"归组：描述段出现阳性征的器官族
        fam_mk = {}  # 器官族 -> 展示名
        for e in ents:
            if e.label != "anatomy" or e.section != "findings" or not e.canonical:
                continue
            fam = e.canonical.split("-", 1)[-1]
            seg = f_txt[max(0, (e.start - f0) - 20): (e.end - f0) + 20]
            if any(k in seg for k in POSITIVE_MARKERS):
                fam_mk.setdefault(fam, e.text)
        for fam, name in fam_mk.items():
            # 印象段是否就该器官族给出结论（任一含该器官族同义词的实体 + 结论词）
            fam_organs = {w for w, c in ANATOMY_SYNONYMS.items() if c.split("-", 1)[-1] == fam}
            mentioned = any(o in i_txt for o in fam_organs)
            concluded = any(k in i_txt for k in
                            ["占位", "结节", "癌", "瘤", "恶性", "病变", "异常",
                             "增大", "扩张", "囊肿", "结石", "水肿", "出血"])
            if mentioned and concluded:
                continue
            out.append(Finding("R5-CONSISTENCY", "描述-结论矛盾", "medium",
                f"影像描述段器官「{name}」（族={fam}）提示阳性征，但诊断印象段未就该器官给出对应结论",
                name, (-1, -1)))
        return out

    @staticmethod
    def _split_for_r5(text: str) -> dict:
        """按段落标题切分描述/印象原文（与 NER 段落划分一致）。
        返回 dict 额外含 findings_start：findings 段在原始 text 中的起始偏移，
        供 R5 用相对偏移取实体附近窗口（避免用绝对偏移索引子串导致错位）。"""
        spans = []
        for pat, sec in [("(?i)检查所见|影像描述|表现|imaging findings|findings", "findings"),
                         ("(?i)影像诊断|诊断印象|印象|诊断意见|诊断结论|影像结论|结论|impression|diagnosis", "impression")]:
            for m in re.finditer(pat, text):
                spans.append((m.start(), sec))
        spans.sort()
        res = {"findings": text, "impression": "", "findings_start": 0}
        if spans:
            # 从第一个标题起往后切：findings 取首个 findings 起 ~ 下一个 impression；impression 取首个 impression 起
            f0 = next((s for s in spans if s[1] == "findings"), None)
            i0 = next((s for s in spans if s[1] == "impression"), None)
            if f0 and i0 and i0[0] > f0[0]:
                res["findings"] = text[f0[0]:i0[0]]
                res["impression"] = text[i0[0]:]
                res["findings_start"] = f0[0]
            elif i0:
                res["impression"] = text[i0[0]:]
                res["findings"] = text[:i0[0]]
                # findings 取全文前半，起点为 0
        return res

    # R6 登记部位不符（申请部位 vs 报告主体解剖）
    def _r6_site(self, text, meta) -> List[Finding]:
        out = []
        applied = meta.get("applied_site", "")
        if not applied:
            return out
        norm_applied = self.kg.norm_site(applied)
        if not norm_applied:
            return out
        # 仅扫描报告正文（检查所见 + 诊断印象），排除元信息头部，避免"申请部位"自身被计入
        secs = self._split_for_r5(text)
        body = secs["findings"] + "\n" + secs["impression"]
        found_families = set()
        for m in re.finditer(r"|".join(map(re.escape, SITE_NORM.keys())), body):
            fam = self.kg.norm_site(m.group(0))
            if fam:
                found_families.add(fam)
        if found_families and norm_applied not in found_families:
            out.append(Finding("R6-SITE", "登记部位不符", "high",
                f"申请部位归一化={norm_applied}，但报告内容涉及{found_families}", "", (-1, -1)))
        return out

    # R18 检查部位器官漏写（登记区域声明 → 影像描述段应含该区域器官）
    # 与 R6(登记部位错配) 互补：R6 抓『申请胸部却写腹部』，R18 抓『申请了上腹部但描述段
    # 对肝/胆/胰/脾/肾等上腹部器官一个都没提』。仅查检查所见段；整段"未见异常"整体声明
    # （不点名器官）视为已覆盖，避免"上腹部CT未见异常"被误报。多区域分别校验、互不牵连。
    def _r18_region_coverage(self, text, meta) -> List[Finding]:
        out = []
        applied = meta.get("applied_site", "")
        if not applied:
            return out
        regions = _extract_regions(applied)
        if not regions:
            return out
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        i_txt = secs["impression"]
        # 防误报：描述段整体为正常声明（含『未见异常』等且不带阳性征）→ 视为各区域已声明
        # 未见异常，不判漏写（避免『上腹部CT未见异常』因未点名器官被误报）
        if _claims_normal(f_txt) and not _has_positive(f_txt):
            return out
        for region in regions:
            orgs = REGION_TO_ORGANS.get(region, [])
            if not orgs:
                continue
            # 描述段或结论段任一覆盖该区域器官即视为已描述（结论段常只点名异常器官，
            # 描述段常写正常所见，两段互补避免漏报；两段都无才判漏写）。
            hit = any(org in f_txt for org in orgs) or any(org in i_txt for org in orgs)
            if not hit:
                sample = "、".join(orgs[:4])
                out.append(Finding("R18-COVERAGE", "检查部位器官漏写", "medium",
                    f"检查部位含「{region}」，但影像描述与影像诊断中均未描述{region}"
                    f"相关器官（如{sample}等），疑似漏写或检查部位登记有误",
                    region, (-1, -1)))
        return out

    # R20 模板完整性校验：按检查类型（从登记部位/检查方式推断）校验必查要素缺项。
    # 与 R18 互补：R18 查『区域是否有任意器官』，R20 查『该检查类型必查要素是否齐』。
    # 防误报策略：报告头/描述段整体"未见异常"声明不豁免（与 R18 不同）——
    # 胸部 CT 即使全部正常也应点名肺纹理/纵隔/胸膜等，故缺项仍提示（medium 级）。
    def _r20_template_completeness(self, text, meta) -> List[Finding]:
        out = []
        applied = (meta.get("applied_site") or "").strip().lower()
        modality = (meta.get("modality") or "").strip().lower()
        src = " | ".join([applied, modality]).lower()
        # 匹配检查类型
        matched = None
        for tname, kws in _TYPE_KEYWORDS:
            if any(kw in src for kw in kws):
                matched = tname
                break
        # 若登记部位无类型信息，尝试从报告正文头部推断（OCR 场景）
        if not matched:
            head = text[:200].lower()
            for tname, kws in _TYPE_KEYWORDS:
                if any(kw in head for kw in kws):
                    matched = tname
                    break
        if not matched:
            return out
        req = REPORT_TYPE_REQUIREMENTS[matched]
        elems = req["要素"]
        # 描述段+结论段合并检查（必查要素可能分布在两段）
        secs = self._split_for_r5(text)
        combined = secs["findings"] + "\n" + secs["impression"]
        # 数值/量词噪声过滤：单纯出现『肝』但实际是『肝区不适』之类不适主体？不做过度过滤，
        # 以子串匹配为准，但排除『未见异常』整体声明对单要素的豁免——正常报告也应点名结构。
        missing = [e for e in elems if e not in combined]
        if missing and not _claims_normal(combined):
            sample = "、".join(missing[:6])
            out.append(Finding("R20-TEMPLATE", "报告必查要素漏写", "medium",
                f"「{matched}」报告缺少必查要素：{sample}。{req['提示']}",
                matched, (-1, -1)))
        return out

    # R21 性别-部位联动（检查类型级）：男性检查乳腺/子宫/卵巢，女性检查前列腺/睾丸。
    # 与 R1(正文出现异性别器官) 互补：R1 抓正文描述，R21 抓『检查部位登记』层面——
    # 登记部位/检查方式与性别不匹配（如男性做钼靶、女性做前列腺 MR）。
    def _r21_gender_site(self, text, meta) -> List[Finding]:
        out = []
        rg = _norm_gender(meta.get("gender"))
        if not rg:
            rg = _parse_gender_from_text(text)
        if not rg:
            return out
        applied = (meta.get("applied_site") or "").strip().lower()
        modality = (meta.get("modality") or "").strip().lower()
        src = applied + " " + modality
        female_only = ["乳腺", "乳房", "钼靶", "子宫", "卵巢", "宫颈", "阴道", "输卵管"]
        male_only = ["前列腺", "睾丸", "阴茎", "精囊", "阴囊"]
        for kw in female_only:
            if kw in src and rg == "male":
                out.append(Finding("R21-GENDER-SITE", "检查部位与性别不符", "high",
                    f"登记检查部位含「{kw}」（女性专属检查），与{_zh(rg)}性别不符",
                    kw, (-1, -1)))
                break
        for kw in male_only:
            if kw in src and rg == "female":
                out.append(Finding("R21-GENDER-SITE", "检查部位与性别不符", "high",
                    f"登记检查部位含「{kw}」（男性专属检查），与{_zh(rg)}性别不符",
                    kw, (-1, -1)))
                break
        return out

    # R7 描述内部矛盾（同一描述段内出现男女专属器官混用 —— 真实自相矛盾）
    def _r7_internal(self, text, ents) -> List[Finding]:
        out = []
        findings_ents = [e for e in ents if e.section == "findings"]
        g = set()
        for e in findings_ents:
            if e.label == "gender_organ":
                g.add(self.kg.expected_gender_for_organ(e.text))
        if len(g) > 1:
            out.append(Finding("R7-INTERNAL", "描述内部矛盾", "medium",
                f"影像描述段内出现男女专属器官混用{g}（同一患者不可能同时存在）", "", (-1, -1)))
        return out

    # R8 同音/近音错别字（多由语音录入产生：词典由 rules_config.json 维护，可在 GUI 增删）
    def _r8_typo(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        typo_map = self.rules_config.get("typos", {}) or {}
        seen = set()
        # 按错词长度降序匹配，优先命中更长的错写（如"淋巴结解"先于"结解"），避免重复告警
        for wrong in sorted(typo_map.keys(), key=len, reverse=True):
            if wrong == typo_map.get(wrong):
                continue  # 自映射无意义项
            correct = typo_map[wrong]
            for m in re.finditer(re.escape(wrong), text):
                s, e = m.start(), m.end()
                # 跳过已被更长错词覆盖的区间
                if any(ms <= s < me or ms < e <= me for ms, me in seen):
                    continue
                seen.add((s, e))
                out.append(Finding("R8-TYPO", "同音错别字", "medium",
                    f"检出疑似错别字「{wrong}」，疑为「{correct}」（常见语音录入误写）",
                    wrong, (s, e), correct))
        return out

    # R19 读音相似错字（高频词组锚定 + pypinyin 自动推导）
    # 思路：放射科高频正确词组作为「白名单锚定」。对文本中每个中文词组片段，
    # 若其读音与某高频正确词完全相同（同音异字，语音录入最典型）或高度相似，
    # 且该片段本身不是已知正确词，则标记为「可能错误」，给出最可能的正确词。
    # 与 R8 的区别：R8 靠人工维护的错词表；R19 靠「高频词库 + 读音」自动推导，
    # 能发现词表外的、读音相近但写法错误的词组。pypinyin 不可用时本规则静默关闭。
    def _r19_homophone(self, text) -> List[Finding]:
        out = []
        if not _HF_OK or not _hf_pinyin_available():
            return out
        if not text:
            return out
        # R8 已标记区间：R19 不重复报（同音异字由 R19 补漏）
        r8_spans = set()
        typo_map = self.rules_config.get("typos", {}) or {}
        for wrong in typo_map:
            for m in re.finditer(re.escape(wrong), text):
                r8_spans.add((m.start(), m.end()))
        seen_spans: Set[Tuple[int, int]] = set(r8_spans)
        # 切词：先按高频白名单最长匹配切出已知正确词（跳过不查），
        # 剩余中文串用 2~4 字滑窗做读音比对。
        hf = sorted(_hf_highfreq_words(), key=lambda t: len(t[0]), reverse=True)
        # 标记白名单覆盖区间
        covered = []
        for w, _c in hf:
            for m in re.finditer(re.escape(w), text):
                covered.append((m.start(), m.end()))
        covered.sort()
        def _in_covered(s, e):
            return any(ms <= s and e <= me for ms, me in covered)
        def _in_seen(s, e):
            return any(ms <= s < me or ms < e <= me for ms, me in seen_spans)
        cjk = re.compile(r"[\u4e00-\u9fff]+")
        for m in cjk.finditer(text):
            s0 = m.start()
            run = m.group()
            # 对 run 内每个可能起点做滑窗
            for i in range(len(run)):
                for ln in (4, 3, 2):
                    if i + ln > len(run):
                        continue
                    s, e = s0 + i, s0 + i + ln
                    if _in_seen(s, e):
                        continue
                    seg = run[i:i + ln]
                    hit, cand = _hf_segment_candidates(seg)
                    if hit and cand:
                        best, cat, sim = cand[0]
                        if sim >= 0.98 and not _in_covered(s, e):
                            reason = ("同音" if sim == 1.0 else "近音")
                            out.append(Finding(
                                "R19-HOMOPHONE", "读音相似错字", "low",
                                f"「{seg}」读音与高频词「{best}」（{cat}）{reason}，"
                                f"疑为语音录入误写，请核对",
                                seg, (s, e), best))
                            seen_spans.add((s, e))
                            break
        return out

    # R9 用户自定义互斥冲突（由 rules_config.json 维护：词A 与 词B 不应在同一范围内共存）
    def _r9_conflict(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        conflicts = self.rules_config.get("conflicts", []) or []
        for rule in conflicts:
            a = (rule.get("a") or "").strip()
            b = (rule.get("b") or "").strip()
            if not a or not b or a == b:
                continue   # 自反矛盾对(A==B)或空值无效，跳过防误报
            scope = rule.get("scope", "正文")
            sev = rule.get("severity", "medium")
            # 范围扩展：
            #   正文    → 整篇报告
            #   描述段  → 仅 检查所见/影像描述 段
            #   结论段  → 仅 诊断印象/影像诊断/结论 段
            #   同一句  → 同一句内 A 与 B 同时出现才算冲突（"同一行前后错误"）
            #   描述vs结论 → A 出现在描述段 且 B 出现在结论段（跨段上下文错误）
            secs = self._split_for_r5(text)
            f_txt, i_txt = secs["findings"], secs["impression"]
            note = rule.get("note", "")
            hit = False
            if scope == "描述段":
                target = f_txt
                hit = bool(target) and a in target and b in target
            elif scope == "结论段":
                target = i_txt
                hit = bool(target) and a in target and b in target
            elif scope == "同一句":
                # 同一句内 A、B 同现（按句切分，逐句判断）
                for sent in _split_sentences(f_txt + "\n" + i_txt):
                    if a in sent and b in sent:
                        hit = True
                        break
            elif scope == "描述vs结论":
                # 跨段：描述含 A 且 结论含 B（或反向）——"描述/诊断错误"
                hit = (a in f_txt and b in i_txt) or (b in f_txt and a in i_txt)
            else:   # 正文（默认）
                hit = a in text and b in text
            if hit:
                msg = (f"检出互斥冲突：『{a}』与『{b}』在[{scope}]内同时出现，应互斥"
                       + (f"（{note}）" if note else ""))
                out.append(Finding("R9-CONFLICT", "自定义互斥冲突", sev, msg, a, (-1, -1)))
        return out

    # R10 结构化报告模板合规（必填段 + 随访建议；要点由 rules_config.json 的 template 维护）
    def _r10_template(self, text) -> List[Finding]:
        out = []
        cfg = (self.rules_config.get("template") or dict(DEFAULT_TEMPLATE))
        required = cfg.get("required_sections", ["findings", "impression"])
        sev = cfg.get("severity", "low")
        has_findings = bool(re.search(r"检查所见|影像描述|表现", text))
        has_impression = bool(re.search(r"诊断印象|印象|诊断意见|结论", text))
        if "findings" in required and not has_findings:
            out.append(Finding("R10-TEMPLATE", "模板缺失-描述段", sev,
                "报告缺少『检查所见/影像描述』段，不符合结构化报告规范", "", (-1, -1)))
        if "impression" in required and not has_impression:
            out.append(Finding("R10-TEMPLATE", "模板缺失-结论段", sev,
                "报告缺少『诊断印象/结论』段，不符合结构化报告规范", "", (-1, -1)))
        if cfg.get("require_followup") and not re.search(r"随访|建议|复查|随诊", text):
            out.append(Finding("R10-TEMPLATE", "模板缺失-随访建议", sev,
                "报告未给出随访/复查建议，建议补充", "", (-1, -1)))
        return out

    # R11 上下文逻辑错误（信息框 vs 描述框/结论框 跨框比对）
    def _r11_context(self, text, meta, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        # R11-1 左右一致性：项目/检查部位侧别 与 描述/结论提及的方位 比对
        # 侧别来源自动兜底：meta.laterality > 报告内『检查项目/检查部位』标签 > meta.applied_site
        # （用户常把左右写在 项目 自由文本、不填独立侧别框，此前规则因 laterality 空而整条跳过）
        info_side = _project_side(text, meta)
        if info_side and info_side != "bilateral":
            for label, seg in (("影像描述", f_txt), ("影像结论", i_txt)):
                sides = _detect_side_in_text(seg)
                if not sides or "bilateral" in sides:
                    continue
                if info_side == "left" and "right" in sides and "left" not in sides:
                    out.append(Finding("R11-SIDE", "上下文逻辑错误-左右矛盾", "high",
                        f"患者基础信息侧别为『左』，但{label}中仅提及『右侧』（未见左侧），方位不一致",
                        seg[:24], (-1, -1)))
                elif info_side == "right" and "left" in sides and "right" not in sides:
                    out.append(Finding("R11-SIDE", "上下文逻辑错误-左右矛盾", "high",
                        f"患者基础信息侧别为『右』，但{label}中仅提及『左侧』（未见右侧），方位不一致",
                        seg[:24], (-1, -1)))
        # R11-2 描述异常 → 结论正常 矛盾：已下放到 R17 逐部位精确比对
        # （按器官+侧别精确到同一部位；无法归属具体部位时由 R17 段级兜底保持原语义）。
        # R11-3 信息框性别 vs 正文解析性别 矛盾（上下文不一致）
        rg_meta = _norm_gender(meta.get("gender"))
        rg_text = _parse_gender_from_text(text)
        if rg_meta and rg_text and rg_meta != rg_text:
            out.append(Finding("R11-GENDER", "上下文逻辑错误-性别矛盾", "high",
                f"患者基础信息性别为『{_zh(rg_meta)}』，但报告正文解析出性别『{_zh(rg_text)}』，二者矛盾",
                "", (-1, -1)))
        return out

    # R12 同一句话逻辑错误（句级自相矛盾）
    def _r12_sentence(self, text, ents) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        if not f_txt:
            return out
        for sent in _split_sentences(f_txt):
            # 1) 同一句内男女专属器官混用（如『子宫…前列腺…』）
            s_genders = {g for organ, g in GENDER_ORGANS.items() if organ in sent}
            if len(s_genders) > 1:
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "high",
                    f"同一句话内同时出现男女专属器官（自相矛盾）：『{sent[:30]}…』",
                    sent[:30], (-1, -1)))
                continue
            # 2) 同一句内既称某部位正常又描述该部位阳性征（真正的自相矛盾）。
            #    仅当『正常』是『部位+正常』（region-specific）时才判矛盾，避免
            #    『右肺上叶见结节，余两肺未见异常』这类标准报告被误报（2026-08-05 加固）
            if _REGION_NORMAL_RE.search(sent) and _has_positive(sent):
                out.append(Finding("R12-SENTENCE", "同一句话逻辑错误", "high",
                    f"同一句话内既称『未见异常』又描述阳性征（自相矛盾）：『{sent[:30]}…』",
                    sent[:30], (-1, -1)))
        return out

    # R14 前后文逻辑错误（描述段 ↔ 结论段 一致性）
    def _r14_cross(self, text, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        if not f_txt or not i_txt:
            return out
        # R14-1 描述正常 → 结论异常：已下放到 R17 逐部位精确比对（同 R11-2 说明）。
        # R14-2 良恶性定性矛盾（描述段与结论段方向相反）
        f_mal = _has_marker_unnegated(f_txt, MALIGNANT_MARKERS)
        f_ben = _has_marker_unnegated(f_txt, BENIGN_MARKERS)
        i_mal = _has_marker_unnegated(i_txt, MALIGNANT_MARKERS)
        i_ben = _has_marker_unnegated(i_txt, BENIGN_MARKERS)
        if (f_mal and i_ben) or (f_ben and i_mal):
            out.append(Finding("R14-NATURE", "前后文逻辑错误-良恶性矛盾", "high",
                "影像描述与影像结论在病灶良恶性定性上相互矛盾（一称恶性倾向、一称良性倾向）",
                "", (-1, -1)))
        # R14-3 病灶数量前后不一致
        cf = _extract_lesion_count(f_txt)
        ci = _extract_lesion_count(i_txt)
        if cf is not None and ci is not None and cf != ci:
            out.append(Finding("R14-COUNT", "前后文逻辑错误-数量不一致", "medium",
                f"影像描述段提及病灶约 {cf} 枚/个，影像结论段提及约 {ci} 枚/个，数量前后不一致",
                "", (-1, -1)))
        # R14-4 同器官左右跨段矛盾（超越 R2 的有限规范器官族，覆盖文本级左右写法）
        for o in ORGAN_SIDE_LIST:
            fs = _organ_sides_in_text(f_txt, o)
            isd = _organ_sides_in_text(i_txt, o)
            if len(fs) == 1 and len(isd) == 1 and fs != isd:
                out.append(Finding("R14-SIDE", "前后文逻辑错误-左右矛盾", "high",
                    f"同一器官「{o}」在影像描述为『{'左' if 'left' in fs else '右'}』侧、"
                    f"影像结论为『{'左' if 'left' in isd else '右'}』侧，方位前后矛盾",
                    "", (-1, -1)))
                break
        # R14-4b 英文报告（MIMIC-CXR / IU-Xray 风格）左右跨段矛盾
        if not any(f.rule_id == "R14-SIDE" for f in out) and re.search(r"[A-Za-z]", f_txt + i_txt):
            for key, aliases in EN_SIDE_ORGANS.items():
                fs = _organ_sides_en(f_txt, aliases)
                isd = _organ_sides_en(i_txt, aliases)
                if len(fs) == 1 and len(isd) == 1 and fs != isd:
                    out.append(Finding("R14-SIDE", "前后文逻辑错误-左右矛盾", "high",
                        f"同一器官『{key}』在影像描述为『left』、影像结论为『right』，方位前后矛盾（英文报告）",
                        "", (-1, -1)))
                    break
        return out

    # R15 上下文逻辑错误（同一描述段内跨句一致性）
    def _r15_internal(self, text) -> List[Finding]:
        out = []
        secs = self._split_for_r5(text)
        f_txt = secs["findings"]
        if not f_txt:
            return out
        sents = _split_sentences(f_txt)
        # R15-1 段首称未见异常但段内描述阳性征
        # 段首句须为『纯正常声明』（本身不含阳性征），避免『右肺上叶见结节，余两肺未见异常』
        # 这类标准报告被误报（2026-08-05 加固）
        if sents and _claims_normal(sents[0]) and not _has_positive(sents[0]) and _has_positive(f_txt):
            out.append(Finding("R15-NORMAL", "上下文逻辑错误-段内自相矛盾", "high",
                "影像描述段开头称『未见异常/正常』，但段内又描述阳性征，前后矛盾",
                sents[0][:30], (-1, -1)))
        # R15-2 同器官在描述段内前后左右矛盾
        for o in ORGAN_SIDE_LIST_INTERNAL:
            per = [(sent, _organ_sides_in_text(sent, o)) for sent in sents
                   if len(_organ_sides_in_text(sent, o)) == 1]
            flagged = False
            for i in range(len(per)):
                for j in range(i + 1, len(per)):
                    if per[i][1] != per[j][1]:
                        out.append(Finding("R15-SIDE", "上下文逻辑错误-左右自相矛盾", "high",
                            f"影像描述段内同一器官「{o}」前后方位不一致："
                            f"『{per[i][0][:18]}…』与『{per[j][0][:18]}…』",
                            "", (-1, -1)))
                        flagged = True
                        break
                if flagged:
                    break
        # R15-3 同一病灶先见后无（描述段内跨句）
        for lw in LESION_WORDS:
            pres = absn = None
            for idx, sent in enumerate(sents):
                has_pres = (lw in sent
                            and any(re.search(re.escape(v), sent) for v in _PRESENCE_VERBS)
                            and not any(re.search(re.escape(v), sent) for v in _ABSENCE_VERBS))
                has_abs = lw in sent and any(re.search(re.escape(v), sent) for v in _ABSENCE_VERBS)
                if has_pres:
                    pres = idx
                if has_abs:
                    absn = idx
            if pres is not None and absn is not None and absn > pres:
                out.append(Finding("R15-PRESENCE", "上下文逻辑错误-先见后无", "medium",
                    f"影像描述段内对同一「{lw}」先描述存在、后又称未见/消失，前后矛盾",
                    "", (-1, -1)))
                break
        return out

    # R17 逐部位精确比对（描述段 ↔ 结论段，按 器官 + 侧别 精确到同一部位）
    def _r17_cross_region(self, text, secs) -> List[Finding]:
        out = []
        f_txt, i_txt = secs["findings"], secs["impression"]
        if not f_txt or not i_txt:
            return out
        f_spans = _region_spans_in_text(f_txt)
        i_spans = _region_spans_in_text(i_txt)
        f_assert = _region_assertions_in_section(f_txt, f_spans)
        i_assert = _region_assertions_in_section(i_txt, i_spans)
        # 整段级正常声明（无阳性征、含 NORMAL_CLAIM、且无具体部位提及）作为全局正常，
        # 覆盖该段所有提及部位；部位级正常（如『小脑正常』）不触发全局扩展。
        f_global_normal = _is_segment_global_normal(f_txt, f_spans)
        i_global_normal = _is_segment_global_normal(i_txt, i_spans)
        found = False
        for region in sorted(set(f_assert) | set(i_assert),
                             key=lambda r: (r[0], r[1] or "")):
            d = f_assert.get(region, set())
            c = i_assert.get(region, set())
            d_eff = set(d)
            c_eff = set(c)
            if f_global_normal:
                d_eff.add("normal")
            if i_global_normal:
                c_eff.add("normal")
            # 仅当某一侧声明『明确且唯一』时才报矛盾，避免同侧既正常又异常时的歧义双报
            d_only_normal = (d_eff == {"normal"})
            d_only_pos = (d_eff == {"positive"})
            c_only_normal = (c_eff == {"normal"})
            c_only_pos = (c_eff == {"positive"})
            name = _region_cn_name(*region)
            if d_only_normal and c_only_pos:
                out.append(Finding("R17-PERREGION", "前后文逻辑错误-描述正常结论异常", "high",
                    f"影像描述段「{name}」称正常/未见异常，但影像结论段对「{name}」给出阳性诊断，"
                    f"同一部位描述与结论矛盾（描述正常、结论异常）", name, (-1, -1)))
                found = True
            elif d_only_pos and c_only_normal:
                out.append(Finding("R17-PERREGION", "上下文逻辑错误-描述结论矛盾", "high",
                    f"影像描述段「{name}」提示阳性征（异常表现），但影像结论段称「{name}」正常/未见异常，"
                    f"同一部位描述与结论矛盾（描述异常、结论正常）", name, (-1, -1)))
                found = True
        # 段级兜底（无法归属到具体部位时，保持原 R11/R14 语义，避免漏检）。
        # 注意：必须用严格『段级全局正常』（_is_segment_global_normal），不可用 _claims_normal——
        # 后者会匹配『部位+正常』（如『小脑正常』）而误把局部正常当整段正常外溢。
        if not found:
            if _has_positive(f_txt) and i_global_normal:
                out.append(Finding("R11-ABNORMAL", "上下文逻辑错误-描述结论矛盾", "high",
                    "影像描述提示阳性征（异常表现），但影像结论称『未见异常/正常』，二者矛盾",
                    i_txt[:30], (-1, -1)))
            elif f_global_normal and _has_positive(i_txt):
                out.append(Finding("R14-NORMAL", "前后文逻辑错误-描述正常结论异常", "high",
                    "影像描述称『未见异常/正常』，但影像结论给出阳性诊断，结论与描述不符",
                    i_txt[:30], (-1, -1)))
        return out

    # R16 随访时限缺失（中文 NER 驱动；默认关闭，由 rules_config.enable_r16 开启）
    # 许多中文报告写『建议复查』却不给具体间隔，属模板合规瑕疵。医疗上默认关闭，
    # 避免对常规『定期复查』过度告警；需在真实样本上验证后再开启。
    def _r16_followup_timeframe(self, text) -> List[Finding]:
        out = []
        if not _ZH_NLP_OK:
            return out
        r = _zh_extract_followup(text)
        if r["has_followup"] and r["timeframe_months"] is None:
            out.append(Finding("R16-FOLLOWUP", "随访时限缺失", "low",
                "报告给出随访/复查建议但未明确时限（如『3个月后』），建议补充具体随访间隔",
                "", (-1, -1)))
        return out


class _KG:
    def expected_gender_for_organ(self, organ: str) -> Optional[str]:
        return GENDER_ORGANS.get(organ)

    def required_score_for_modality(self, modality: str) -> Optional[str]:
        return MODALITY_SCORE.get(modality)

    def norm_site(self, site: str) -> Optional[str]:
        """登记部位 → 部位族。先整串精确匹配；匹配不到则按子串最长匹配，
        以兼容『全腹部CT』『上腹部平扫』『胸部正侧位』等带检查类型后缀的登记写法。"""
        site = site.strip()
        if not site:
            return None
        exact = SITE_NORM.get(site)
        if exact:
            return exact
        # 按词长降序找包含的子串（长词优先，避免『肺』抢『双肺』等）
        for k in sorted(SITE_NORM, key=len, reverse=True):
            if k in site:
                return SITE_NORM[k]
        return None


# ----------------------------- 评分与统计 -----------------------------
SEVERITY_WEIGHT = {"high": 30, "medium": 15, "low": 5}


def score(findings: List[Finding]) -> Dict[str, dict]:
    """返回每维度评分明细：{维度: {"score": int, "deductions": [{"rule","delta","reason"}]}}。
    旧消费方如需简单 {维度:int} 请用 score_summary()。"""
    total_penalty = sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in findings)
    acc_ded = [{"rule": f.rule_id, "delta": -SEVERITY_WEIGHT.get(f.severity, 0),
                "reason": f.error_type} for f in findings]
    completeness, comp_ded = 100, []
    if any(f.rule_id == "R3-SCORE" for f in findings):
        completeness, comp_ded = 80, [{"rule": "R3-SCORE", "delta": -20, "reason": "评分标准缺失"}]
    norm = 90 if findings else 100
    norm_ded = [{"rule": "综合", "delta": -10, "reason": "存在质控问题"}] if findings else []
    return {
        "准确性": {"score": max(0, 100 - total_penalty), "deductions": acc_ded},
        "完整性": {"score": completeness, "deductions": comp_ded},
        "规范性": {"score": norm, "deductions": norm_ded},
        "及时性": {"score": 100, "deductions": []},
    }


def score_summary(scores: Dict[str, dict]) -> Dict[str, int]:
    """从 score() 的新结构提取 {维度: 分数(int)}，兼容旧消费方（驾驶舱/导出）。"""
    return {dim: (v.get("score", 100) if isinstance(v, dict) else v)
            for dim, v in scores.items()}


def error_type_counts(findings: List[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.error_type] = counts.get(f.error_type, 0) + 1
    return counts


def _zh(g: str) -> str:
    return {"male": "男", "female": "女"}.get(g, g)


def _norm_gender(g) -> Optional[str]:
    """把各种性别写法归一化为 'male'/'female'；无法识别返回 None。"""
    if not g:
        return None
    s = str(g).strip().lower()
    if s in ("male", "男", "男性", "m", "boy"):
        return "male"
    if s in ("female", "女", "女性", "f", "girl"):
        return "female"
    return None


def _parse_gender_from_text(text: str) -> Optional[str]:
    """从报告正文推断性别。策略：
      1) 优先匹配显式字段：性别：男 / 性别 女 / 男性 / 女性
      2) 其次匹配常见病史写法：男，45岁 / 女 32Y / M,45 / F 32
    仅在证据明确时返回，避免误判。OCR 形近字（另≈男、文/久≈女）一并归一。
    """
    if not text:
        return None
    _gn = {"男": "male", "另": "male", "女": "female", "文": "female", "久": "female"}
    # 显式字段（容忍全角空格）
    m = re.search(r"(?:性\s*别|患\s*者|受\s*检\s*者)[:：\s\u3000]*([男女另文久])", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    m = re.search(r"([男女另文久])性", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    # 病史写法：性别 + 紧邻年龄（岁/Y/y/歲或逗号后数字），兼容『男性，45岁』
    m = re.search(r"[，,、\s：:\u3000]([男女另文久])\s*性?\s*[，,、]?\s*\d{1,3}\s*[岁YyＹ歲]", text)
    if m and m.group(1) in _gn:
        return _gn[m.group(1)]
    m = re.search(r"\b([MFmf])\s*[,，]?\s*\d{1,3}\b", text)
    if m:
        return "male" if m.group(1).lower() == "m" else "female"
    # 英文整词：Male / Female（无紧邻数字的病史写法，如『Patient：ZHANG SAN  Male 45Y』）
    m = re.search(r"\b(male|female)\b", text, re.IGNORECASE)
    if m:
        return "male" if m.group(1).lower() == "male" else "female"
    return None


# -------------------- 元信息自动抽取（剪贴板/导入场景自动回填） --------------------
def _extract_gender_cn(text: str) -> str:
    """返回中文『男』/『女』，无法识别返回空串。"""
    return {"male": "男", "female": "女"}.get(_parse_gender_from_text(text), "")


def _extract_age(text: str) -> str:
    """从正文抽取年龄（数字）。优先显式字段，其次病史写法，最后孤立年龄写法。"""
    if not text:
        return ""
    # 显式：年龄：45 / 年龄 45岁
    m = re.search(r"年\s*龄[:：]?\s*(\d{1,3})\s*(?:岁|Y|y|歲)?", text)
    if m:
        return m.group(1)
    # 病史写法：男，45岁 / 女 32Y
    m = re.search(r"[，,、\s：:\u3000]([男女另文久])\s*性?\s*[，,、]?\s*(\d{1,3})\s*(?:岁|Y|y|歲)", text)
    if m:
        return m.group(2)
    # 英文病史写法：M,45 / F 32
    m = re.search(r"\b([MFmf])\s*[,，]?\s*(\d{1,3})\b", text)
    if m:
        return m.group(2)
    # 孤立年龄：45岁 / 45Y
    m = re.search(r"(\d{1,3})\s*(?:岁|Y|y|歲)", text)
    if m:
        return m.group(1)
    return ""


def _extract_modality(text: str) -> str:
    """抽取需评分标准的检查部位（乳腺→BI-RADS / 前列腺→PI-RADS 等）。"""
    if not text:
        return ""
    for k in MODALITY_SCORE:   # 乳腺/钼靶/乳腺x线/前列腺/盆腔
        if k in text:
            return k
    return ""


def _site_from_text(s: str) -> str:
    """在给定文本中按 特异性→通用 顺序匹配部位，返回中文标准部位族名。"""
    if not s:
        return ""
    for k in sorted(SITE_NORM, key=len, reverse=True):
        if k in s:
            return SITE_CANON.get(SITE_NORM[k], SITE_NORM[k])
    return ""


_RISKY_SINGLE = {"脑"}  # 全文降级扫描时排除的高误判单字键


def _site_from_fulltext(s: str) -> str:
    """全文降级匹配：行为同 _site_from_text，但排除高误判单字（如 '脑'）。"""
    if not s:
        return ""
    for k in sorted(SITE_NORM, key=len, reverse=True):
        if k in s and k not in _RISKY_SINGLE:
            return SITE_CANON.get(SITE_NORM[k], SITE_NORM[k])
    return ""


def _meta_header_region(text: str) -> str:
    """取报告开头到『影像/检查所见』之前的区域（登记/申请部位通常在开头）。"""
    m = re.search(r"(影像所见|检查所见|所见|影像表现|检查表现|超声所见|描述|表现|影像描述|检查描述)", text)
    if m:
        return text[:m.start()]
    return text[:400]


def _extract_site(text: str) -> str:
    """抽取登记/申请部位，归一化到 SITE_NORM 的中文 key（如『胸部』『盆腔』）。

    优先级：显式标签字段（申请部位/检查部位…）> 仅扫描元信息头部
    （避免正文偶发提及如『未见脑转移』误判为头颅）。
    """
    if not text:
        return ""
    # 1) 显式字段优先（标签后的内容最可靠）
    for label in ("申请部位", "检查部位", "检查名称", "检查项目", "扫描部位", "检查范围", "部位"):
        m = re.search(label + r"[:：]?\s*([^\n，,。；;：]+)", text)
        if m:
            val = m.group(1).strip()
            # 去掉拖尾的成像/扫描方式词，保留部位主体
            core = re.split(r"(?:CT|MR|MRI|PET|DR|CR|平扫|增强|三维|重建|摄片|扫描|超声|造影|图像|检查|及|和)", val)[0]
            core = core.strip(" 、,，")
            site = _site_from_text(core)
            if site:
                return site
            # 退一步：整值再匹配一次（兼容 胸腹部 这类复合写法，长词优先）
            site = _site_from_text(val)
            if site:
                return site
    # 2) 无显式字段：先扫元信息头部区域，避免正文偶发提及误判
    hdr = _site_from_text(_meta_header_region(text))
    if hdr:
        return hdr
    # 3) 降级：全文扫描（排除高误判单字，如 '脑' 在 '未见脑转移' 中的偶发提及）
    return _site_from_fulltext(text)


def _extract_laterality(text: str) -> str:
    """抽取主要侧别：左 / 右 / 双侧；未提及返回空串。"""
    if not text:
        return ""
    sides = set()
    for w, c in LATERALITY.items():
        if re.search(re.escape(w), text):
            sides.add(c)
    if sides == {"left"}:
        return "左"
    if sides == {"right"}:
        return "右"
    if "left" in sides or "right" in sides or "bilateral" in sides:
        return "双侧"
    return ""


def _lab_re(lab: str) -> str:
    """标签 → 容忍 OCR 噪声的正则：允许标签字符间混入半角/全角空格、制表符。

    真实 PACS 截图 OCR 常把『姓名』识别成『姓 名』或『姓　名』（字距大被拆行/拆词，
    全角空格 U+3000 在中文 OCR 里极常见），直接 re.escape 精确匹配会漏抽——
    这是「基础信息识别不精确」的常见根因之一。
    """
    return r"[ \t\u3000]*".join(re.escape(ch) for ch in lab)


# 姓名标签：容忍 OCR 误读（姓名→姓各/性名，患者→忠者，病人→病欠）与字符间空格。
# 注意：短标签（患者/病人…）后必须跟分隔符，避免误吞其后紧跟的『姓名』二字。
_PATIENT_LABEL = (
    r"(?:姓\s*[名各]|性\s*[名各]|名\s*[:：]|"
    r"患\s*[者吉][:：\s\u3000]+|忠\s*[者吉][:：\s\u3000]+|"
    r"病\s*[人欠员][:：\s\u3000]+|受\s*检\s*者[:：\s\u3000]+|就\s*诊\s*人[:：\s\u3000]+|"
    r"患者?姓名|病人姓名|name|patient)"
)


def _extract_patient(text: str) -> str:
    """抽取患者姓名（中文/英文），容忍 OCR 标签误读与字符间空格。

    覆盖：姓名/患者姓名/病人姓名/受检者姓名/就诊人姓名/病员姓名 及其常见 OCR 误读
    （姓各、性名、忠者、病欠 等）与英文 Patient/Name；无标签时按『中文串+性别字』
    启发式兜底（如『张三 男 45Y』）。姓名为空时不误填。
    """
    if not text:
        return ""
    # 噪声词（字段词/科室词）：无标签兜底时排除，避免把科室/字段误填成姓名
    _noise = ("性别|年龄|检查|部位|科室|门诊|住院|床号|影像|诊断|申请|病案|临床|设备|"
              "医院|报告|记录|呼吸|心血管|神经|骨科|普外|泌尿|妇科|产科|儿科|急诊|超声|"
              "放射|肿瘤|消化|内分泌|免疫|血液|皮肤|眼科|耳鼻喉|口腔|中医|康复|病理|"
              "心电|核医学|受理|登记|来源|类型|方法|所见|印象|建议|征象|结论|提示|说明|病床")
    # 复姓前缀：4 字姓名仅当以复姓开头才接受（欧阳娜娜…）。
    # 注意：必须用编译正则 + re.match（前缀匹配），不能用 str.startswith(_compound)，
    # 因为 startswith 不会把 '|' 当作「或」，原写法导致复姓检测始终失效。
    _compound = re.compile(r"^(欧阳|司马|诸葛|东方|上官|令狐|皇甫|宇文|慕容|司徒|夏侯|长孙|"
                            r"赫连|万俟|闻人|澹台|尉迟|公孙)")
    # 部位词：无标签兜底时排除，避免把『胸部/腹部/腰椎』等检查部位误填成姓名
    # （单字仅取明确非姓氏者，关/子/前/四/口/甲/尺等真实姓氏不纳入）。
    _bodypart = ("头部|颈部|胸部|腹部|盆腔|腰部|骶部|尾部|颅脑|头颅|鼻窦|眼眶|涎腺|"
                 "鼻咽|口咽|喉部|甲状腺|上腹部|中腹部|下腹部|肾上腺|肝脏|胆囊|胰腺|"
                 "脾脏|肾脏|胃肠|膀胱|前列腺|子宫|卵巢|四肢|关节|肩关节|肘关节|腕关节|"
                 "髋关节|膝关节|踝关节|腰椎|颈椎|胸椎|骶骨|尾骨|股骨|胫骨|腓骨|肱骨|"
                 "尺骨|桡骨|骨盆|肋骨|锁骨|脑|颈|胸|腹|盆|腰|骶|颅|颌|面|眼|耳|鼻|"
                 "咽|喉|肺|肝|胆|胰|脾|肾|胃|肠|膀|乳|肩|肘|腕|髋|膝|踝|指|趾|脊|"
                 "椎|骨|肋|锁|股|胫|腓|肱|桡")
    # 姓名字符：中文/英文与间隔号·，容忍姓名内 OCR 噪声空格；遇性别/年龄/字段词
    # （容忍字符间空格，如『性 别』『年 龄』）/英文性别词/数字即停，避免把后续字段
    # 吞入姓名（如『张三男』『张三性别：男』『孙七性别』）。
    _name_stop = (r"性\s*别|年\s*龄|检\s*查|部\s*位|科\s*室|门\s*诊|住\s*院|床\s*号|"
                  r"住\s*院\s*号|临\s*床|设\s*备|医\s*院|影\s*像|诊\s*断|申\s*请|病\s*案|"
                  r"男|女|male|female|sex|\d")
    _name_char = r"(?:(?!" + _name_stop + r")[\u4e00-\u9fa5A-Za-z·\s\u3000])"
    _clean_name = lambda s: re.sub(r"[\s\u3000]+", "", s) if re.search(r"[\u4e00-\u9fa5]", s) \
        else re.sub(r"\s+", " ", s).strip()

    m = re.search(_PATIENT_LABEL + r"[:：\s\u3000]*(" + _name_char + r"{1,12})", text, re.IGNORECASE)
    if m:
        cand = _clean_name(m.group(1))
        if cand and cand not in ("男", "女", "不详", "未知"):
            return cand
    # 无标签兜底1：中文姓名紧邻性别字（『张三 男 45Y』）。先用 2-3 字匹配，避免把
    # 前面的科室词（如『呼吸内科』）吞入姓名；候选含噪声词/科室首字则从左剥离后重试。
    compact = re.sub(r"[\s\u3000]+", "", text)
    _dept_char = set("呼吸内科外科科室门诊住院急诊放射超声影像神经骨科泌尿"
                     "妇科产科儿科肿瘤消化内分泌免疫血液皮肤眼科耳鼻喉口腔"
                     "中医康复病理心电核医学")
    def _strip_dept(s: str) -> str:
        while len(s) > 2 and s[0] in _dept_char:
            s = s[1:]
        return s
    # 4 字且为复姓（欧阳娜娜…）优先：避免 2-3 字匹配把复姓 4 字名从中段截走（如『阳娜娜』）
    m4 = re.search(r"([\u4e00-\u9fa5]{4})[男女]", compact)
    if m4:
        cand = _strip_dept(m4.group(1))
        if len(cand) >= 2 and re.match(_compound, cand) and not re.search(_noise, cand):
            return cand
    m3 = re.search(r"([\u4e00-\u9fa5]{2,3})[男女]", compact)
    if m3:
        cand = _strip_dept(m3.group(1))
        if len(cand) >= 2 and not re.search(_noise, cand) and not re.search(_bodypart, cand):
            return cand
    # 无标签兜底2：完全无标签/无性别字时，仅在「整行不含字段词（检查/部位/科室…）」
    # 的行中取行首像人名的 2-3 字中文串（如『张三 男 45岁』类无标签行），排除字段词/
    # 科室词/部位词，降低把科室/字段/检查部位（如『胸部』）误填成姓名的概率。
    # 含字段词的行（检查部位：胸部…）一律跳过，避免取到部位名。
    # 仅取「整行无字段词」且行内存在『人称/性别/年龄』标识的中文串作为姓名候选，
    # 避免把正文里没有字段词的人名形态串（如『未见实质性病灶』的『灶』）误填成姓名。
    _person_mark = r"患者?|就诊|受检|病员|病\s*人|name|patient|性\s*别|年\s*龄|男|女|\d"
    for line in text.splitlines():
        if re.search(_noise, line):
            continue
        if not re.search(_person_mark, line, re.IGNORECASE):
            continue
        m = re.match(r"\s*(?:[A-Za-z0-9\-]+)?\s*([\u4e00-\u9fa5]{2,3}(?:\s*[\u4e00-\u9fa5])?)", line)
        if not m:
            continue
        cand = re.sub(r"\s+", "", m.group(1))
        if len(cand) < 2:
            continue
        if re.search(_noise, cand) or re.search(_bodypart, cand):
            continue
        if len(cand) <= 3 or re.match(_compound, cand):
            return cand
    # 兜底3：整行恰好就是 2-3 字中文串（如『姓名独占一行』的 PACS 大字段：张三 / 王小明），
    # 既无标签也无性别字，兜底1/2 都会漏。用 fullmatch 限定「整行即姓名」，排除
    # 『未见实质性病灶』这类长句（其首 token 不会误中）；部位词(_bodypart)仍排除，
    # 故『胸部』『腰椎』等不会误填。仅作最后兜底，优先级最低。
    for line in text.splitlines():
        s = line.strip()
        if not re.fullmatch(r"[\u4e00-\u9fa5]{2,3}", s):
            continue
        if re.search(_noise, s) or re.search(_bodypart, s):
            continue
        return s
    return ""


# OCR 数字/字母常见混淆归一（仅在「以数字为主」的编号段启用）。
# O/o→0、I/i/l→1：这些字母几乎不会作为影像号中的有效区分字符，且极易与数字混淆 → 无条件归一。
# B/Z/S/G(b/z/s/g)：仅在被数字夹住时（如 "A1B23"）才视为误识别数字，
#   避免误伤 PACS / CT / MR 等含真实字母的编号（如 "PACS0001" 的 S 必须保留）。
_DIGIT_CONFUSION = {"B": "8", "Z": "2", "S": "5", "G": "6",
                    "b": "8", "z": "2", "s": "5", "g": "6"}
_DIGIT_CONFUSION_RE = re.compile(r"(?<=\d)([BbZzSsGg])(?=\d)")


def _ocr_fix_num(no: str) -> str:
    no = no.translate(str.maketrans("OoIl", "0011"))
    return _DIGIT_CONFUSION_RE.sub(lambda m: _DIGIT_CONFUSION[m.group(1)], no)


def _extract_exam_no(text: str) -> str:
    """抽取影像号/检查号（放射科用于唯一定位患者检查的编号）。

    强标签：影像号 / 影像编号 / 检查号 / 检查编号 / 图像号 / 放射号 /
    RIS号 / PACS号 / 门诊号 / 住院号；弱标签：编号（信息栏语境下通常即影像号）。
    OCR 数字混淆归一：编号以数字为主时，O/o→0、I/i/l→1 无条件归一；
    B/Z/S/G 仅当被数字夹住时（如 "A1B23"）才归一，避免误伤 PACS/CT/MR 等真实字母编号。
    长度上限放宽到 40 以容纳长影像号。无法识别返回空串。
    """
    if not text:
        return ""
    _labels = ("影像编号", "影像号", "检查编号", "检查号", "图像号",
               "放射号", "RIS号", "PACS号", "门诊号", "住院号", "编号")
    for lab in _labels:
        m = re.search(_lab_re(lab) + r"[:：\s\u3000]*([A-Za-z0-9\-]{3,40})", text)
        if not m:
            continue
        no = m.group(1)
        digits = sum(ch.isdigit() for ch in no)
        if digits >= max(1, len(no) // 2):
            # 以数字为主 → 修正 OCR 常见字符混淆
            no = _ocr_fix_num(no)
        return no
    return ""


def extract_meta(text: str) -> dict:
    """从报告正文抽取元信息，用于剪贴板/导入场景自动回填输入框。

    返回 {patient, exam_no, gender, age, modality, applied_site, laterality}
    （均为中文字符串，姓名可能为空）。抽取结果仅作提示，允许人工校正。
    """
    return {
        "patient": _extract_patient(text),
        "exam_no": _extract_exam_no(text),
        "gender": _extract_gender_cn(text),
        "age": _extract_age(text),
        "modality": _extract_modality(text),
        "applied_site": _extract_site(text),
        "laterality": _extract_laterality(text),
    }


def extract_meta_full(basic: str, findings: str = "", impression: str = "") -> dict:
    """同 :func:`extract_meta`，但部位 / 侧别 / 检查类型除『患者信息栏』(basic) 外，
    若为空还会从『影像所见 / 结论』(findings + impression) 补抽。

    多数 PACS 的**患者信息栏不含部位与侧别**（写在正文里），若只用 basic 区，
    ``applied_site`` / ``laterality`` 会长期为空，表现为「基础信息识别不全」。
    本函数把所见 / 结论文本一并送抽取，显著提升这两项回填率；basic 已有值时不覆盖。
    """
    meta = extract_meta(basic)
    fi = (findings or "") + "\n" + (impression or "")
    if fi.strip():
        fi_meta = extract_meta(fi)
        for k in ("patient", "exam_no", "gender", "age",
                  "modality", "applied_site", "laterality"):
            if not (meta.get(k) or "").strip() and (fi_meta.get(k) or "").strip():
                meta[k] = fi_meta[k]
    return meta


def format_patient_ident(exam_no: str, name: str) -> str:
    """组合『影像号/姓名』输入框显示值。

    - 影像号与姓名都有 → ``影像号/姓名``（斜杠分隔）；
    - 只有其一 → 单独显示；
    - 都为空 → 空串。

    纯函数，便于单测；app 在回填基础信息区时调用。
    """
    exam_no = (exam_no or "").strip()
    name = (name or "").strip()
    if exam_no and name:
        return f"{exam_no}/{name}"
    return exam_no or name
