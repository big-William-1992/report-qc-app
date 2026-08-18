"""质控引擎纯词表常量模块（从 engine.py 渐进式抽取，2026-08-16）。

仅包含纯数据常量与编译正则，无引擎依赖，不 import RuleEngine/NER/_KG。
engine.py 通过 `from _lexicons import *` 引入，保持 `from engine import XXX` 对外兼容。
"""
import re

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
    # 2026-08-18 补复合词条（P0 修复）：短别名（右肺/左肾）此前在复合词内被误切分，
    # "右肺门→右肺" 驱动 R5 误报、"左肾上腺→左肾" 使 R2 把肾上腺误归肾族；
    # 补入 3 字词条后按最长匹配优先命中（_REGION_ALIAS_LIST_SORTED 按长度降序）。
    "左肺门": "L-hilum", "右肺门": "R-hilum",
    "肺门": "hilum",
    "左肾上腺": "L-adrenal", "右肾上腺": "R-adrenal",
    "肾上腺": "adrenal",
    "左肾盂": "L-renalpelvis", "右肾盂": "R-renalpelvis",
    "左肾盏": "L-renalcalyx", "右肾盏": "R-renalcalyx",
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
    # 腹部
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

# 英文规范值 → 中文标准部位族名
SITE_CANON = {
    "head": "头颅", "cspine": "颈椎", "tspine": "胸椎", "sacrum": "骶椎",
    "spine": "脊柱", "lumbar": "腰椎", "chest": "胸部", "abdomen": "腹部",
    "pelvis": "盆腔", "shoulder": "肩关节", "elbow": "肘关节", "wrist": "腕关节",
    "hip": "髋关节", "knee": "膝关节", "ankle": "踝关节", "limb": "四肢",
}

VALID_UNITS = {"cm", "mm", "HU", "mm/s", "ml", "°", "mmhg"}

MODALITY_SCORE = {"乳腺": "BI-RADS", "钼靶": "BI-RADS", "乳腺x线": "BI-RADS",
                  "前列腺": "PI-RADS"}

# 描述内部阳性/阴性征（用于一致性）
POSITIVE_MARKERS = ["结节", "占位", "阴影", "肿块", "异常信号", "斑片", "渗出", "骨折", "扩张", "增大"]

# 强阳性征（用于『描述异常→结论正常』与『同一句话逻辑错误』判定，覆盖面更广）
POSITIVE_STRONG = ["结节", "占位", "肿块", "骨折", "扩张", "增大", "囊肿", "结石", "出血",
                   "水肿", "癌", "瘤", "病变", "异常信号", "斑片", "渗出", "增厚", "狭窄",
                   "积液", "缺血", "梗死", "钙化灶",
                   "软化灶", "梗塞灶", "低密度灶", "高密度灶", "低密度影", "高密度影",
                   "萎缩", "脱髓鞘", "变性", "缺如", "膨出", "积气", "闭塞",
                   "信号异常", "占位效应", "破坏", "充盈缺损", "间盘突出", "盘突出",
                   "增生", "炎症", "炎性", "肿胀", "瘘", "畸形", "囊变", "积血", "积脓"]
# 阳性征否定前缀
_NEG_PREFIXES = ("未见", "未见明显", "未见明确", "未见确切", "未见可疑",
                 "无明显", "未发现", "未示", "不伴", "不含", "排除", "否认", "未及", "无")

# 明确的『正常/未见异常』声明
NORMAL_CLAIM = ["未见异常", "未见明显异常", "无明显异常", "无异常", "未见异常征象",
                "未见明显异常征象", "未见占位", "未见占位性病变", "未见明确异常", "未见异常改变"]

# R19 空格/标点容忍
_R19_NORM_RE = re.compile(r"([\u4e00-\u9fff])\s*[·\．\.\s，,、；;]+\s*([\u4e00-\u9fff])")

# 常见解剖部位/区域词（用于精准识别『部位+正常』）
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
# 『部位+正常』正则
# 2026-08-18：补『部位+未见异常』变体——『左肺见结节，左肺未见异常』同句同侧
# 自相矛盾此前因只匹配『X正常』而漏检（_r12_sentence 依赖此正则）。
_REGION_NORMAL_RE = re.compile(
    "(?:" + "|".join(re.escape(w) for w in
        sorted(set(REGION_NORMAL_WORDS) | set(ANATOMY_SYNONYMS.keys()) | set(SITE_NORM.keys()),
               key=len, reverse=True)) + ")(?:正常|未见异常|未见明显异常)"
)

# 模块导出清单：供 engine.py `from _lexicons import *` 使用
__all__ = [
    "LATERALITY", "GENDER_ORGANS", "ANATOMY_SYNONYMS", "SITE_NORM", "SITE_CANON",
    "VALID_UNITS", "MODALITY_SCORE", "POSITIVE_MARKERS",
    "POSITIVE_STRONG", "_NEG_PREFIXES", "NORMAL_CLAIM", "_R19_NORM_RE",
    "REGION_NORMAL_WORDS", "_REGION_NORMAL_RE",
]
