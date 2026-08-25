# 本文件由机械拆分脚本从原 engine.py 迁移而来 (2026-08-25)
# 原单文件按规则族/职责切分为包结构; 对外接口经 src/engine/__init__.py 完全兼容
import re, os, sys, json, shutil, logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple
from _lexicons import *  # noqa: F401,F403
from _utils import *     # noqa: F401,F403
from engine_text import *  # noqa: F401,F403
from anatomy_lexicon import SIDE_CHECK_ORGANS, R2_COVERED, EN_SIDE_ORGANS  # noqa: F401
try:
    from zh_radiology_synonyms import (
        normalize_text as _zh_norm_text,
        extract_followup as _zh_extract_followup,
    )
    from zh_ner import extract_entities as _zh_ner_entities
    _ZH_NLP_OK = True
except Exception:  # pragma: no cover
    _ZH_NLP_OK = False
try:
    from highfreq_lexicon import (
        segment_candidates as _hf_segment_candidates,
        highfreq_words as _hf_highfreq_words,
        is_pinyin_available as _hf_pinyin_available,
    )
    _HF_OK = True
except Exception:  # pragma: no cover
    _HF_OK = False

_TRADITIONAL_CHARS = ("雙紋門結節顯異竈腫塊縱斷層動脈靜診療預質掃強寬鈣瀰潤轉圍"
                      "氣腸膽攝護宮狀實幹顱鎖葉葉聲腫據臨牀檢驗檢視術後慮蓋約"
                      "數滿積處監測網絡環時間間數據統設備權錄")


# ----------------------------- 词典 / 知识图谱 -----------------------------
# 纯词表常量已抽取至 src/_lexicons.py，纯辅助函数已抽取至 src/_utils.py，
# engine.py 顶部通过 `from _lexicons import *` / `from _utils import *` 引入。

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

# 聚焦型检查：描述了下列主器官即视为报告已覆盖，豁免结构要素清单误报
# （如盆腔报告描述子宫/卵巢/前列腺，不再因缺膀胱/直肠/盆壁而误报子宫肌瘤/卵巢囊肿等
# 聚焦性合法报告）。普查型检查（胸部CT/头颅CT/腰椎等）不在此表，要素清单为强制项。
FOCUSED_TYPE_PRIMARY = {
    "盆腔": ["子宫", "卵巢", "前列腺", "宫颈", "阴道", "附件", "输卵管", "精囊", "盆底"],
    "乳腺": ["腺体", "导管", "乳管", "乳头", "皮肤", "乳房"],
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
        # 句边界保护：窗口内遇句号/分号/换行/逗号则截断，避免跨句吞入相邻部位阳性征
        # （如『右肺见结节，左肺正常。』后 16 字窗口不能吞掉下一句的阳性征；
        #   2026-08-18 追加逗号：『右肺上叶见结节，余肺未见异常』中「未见异常」不再误贴给右肺）。
        for _bnd in ("。", "；", ";", "\n", "，", ","):
            _bi = after_big.find(_bnd)
            if 0 < _bi < len(after_big):
                after_big = after_big[:_bi]
                break
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
    """段级『全局正常』判定：仅当该段无阳性征、含 NORMAL_CLAIM 短语或否定式阴性声明
    （如『未见实质性病变』，见 _is_negative_claim）、且【不含任何具体部位提及】
    时才视为覆盖全段（可扩展至所有部位）。若段内已点名某部位（如『小脑正常』『余两肺未见异常』），
    则该正常声明是『部位级/局部』的，不得外溢到其它部位，避免『左侧小脑正常』被误判为『右侧小脑也正常』。"""
    if not text or _has_positive(text):
        return False
    if not any(k in text for k in NORMAL_CLAIM) and not _is_negative_claim(text):
        return False
    if spans:
        return False
    return True

# 良恶性定性标记（用于前后文定性矛盾 R14-NATURE）
_PRESENCE_VERBS = ["见", "示", "可见", "探及", "发现", "查见", "考虑", "提示", "显示"]
_ABSENCE_VERBS = ["未见", "消失", "吸收", "已吸收", "消退"]
LESION_WORDS = ["结节", "占位", "肿块", "病灶", "囊肿", "结石", "骨折", "积液",
                "阴影", "斑片", "异常信号"]

# ===== R24 建议强度矛盾（2026-08-23 新增）=====
# 窄模式（宁可漏报不可误报）：仅「双重明确」同时满足才报——
# ① 报告出现明确良性定性词（印象段优先，其次描述段；均无段落结构时退回全文）；
# ② 建议/印象部分出现强处置词，且该处置词被建议类引导词（建议/必要时/可考虑/酌情）
#    在同句内先行锚定——『胆囊切除术后』『化疗后复查』等病史陈述、以及
#    『穿刺细胞学』等弱表述不满足锚定，不触发。
R24_BENIGN_CONFIRM_RE = re.compile(r"考虑良性|未见恶性|无恶性征象|良性")
R24_BENIGN_NEG_PRE_RE = re.compile(r"(?:非|不|未|无|除外|排除)[^。；，,\n]{0,2}$")
R24_STRONG_INTERVENTION = ("穿刺活检", "抗肿瘤", "切除", "化疗", "放疗")
R24_ADVICE_LEAD_RE = re.compile(r"建议|必要时|可考虑|可以考虑|酌情")

# ===== R25 时序方向矛盾（2026-08-23 新增）=====
# 同一报告内对同一病灶的随访对比方向自相矛盾（既称较前增大又称较前缩小）。
# 窄模式：仅当两个反向描述各自能提取到「同一主体」（最近病灶关键词 + 侧别字符），
# 且主体键一致才报。主体缺失或不同（左肺增大/右肺缩小、部分增大/部分缩小等
# 跨病灶差异化转归）均不报——不涉及跨报告数值对比。
_R25_TEMPORAL_RE = re.compile(r"较前[^。！？；;\n]{0,4}?(增大|缩小|增多|减少)")
_R25_SUBJECT_KW = ("胸腔积液", "结节", "肿块", "病灶", "占位", "囊肿", "结石",
                   "积液", "淋巴结", "胸水", "斑片", "钙化")

# R5 文本级兜底：NER 易漏标的高频器官 → 器官族（2026-08-21）。
# 仅当描述段该器官词附近出现阳性征、而印象段未就该器官族给结论时，补报 R5，
# 解决「NER 漏标『肝』等致整条规则失明」的漏报。
R5_TEXT_ORGANS = {
    "肝": "liver", "肝脏": "liver",
    "肺": "lung", "肺脏": "lung",
    "肾": "kidney", "肾脏": "kidney",
    "肾上腺": "adrenal",
    "胰腺": "pancreas", "胰": "pancreas",
    "脾": "spleen", "脾脏": "spleen",
    "脑": "brain",
    "甲状腺": "thyroid",
    "卵巢": "ovary",
    "子宫": "uterus",
    "前列腺": "prostate",
    "乳腺": "breast", "乳房": "breast",
    "腮腺": "parotid",
    "胸膜": "pleura",
    "食管": "esophagus",
    "胃": "stomach",
    "肠": "bowel",
    "膀胱": "bladder",
    "胆囊": "gallbladder",
    "淋巴结": "lymphnode",
    "纵隔": "mediastinum",
    "气管": "trachea", "支气管": "bronchus",
}

# R5 印象段「已结论」词表（描述阳性征后，印象段出现这些词视为已就该器官给结论）。
_R5_CONCLUDED = (
    "占位", "肿块", "肿物", "结节", "癌", "瘤", "恶性", "病变", "异常",
    "增大", "扩张", "囊肿", "结石", "水肿", "出血",
    "肺炎", "结核", "炎症", "感染", "渗出", "实变", "纤维化",
    "未见异常", "未见明显异常", "正常", "未见占位", "良性",
    "未见明确异常", "未见确切异常",
)

# 需做左右侧一致性比对的成对解剖结构
# —— 跨段比对(R14)用此表：文本级兜底，覆盖 R2（NER 带 L-/R- 前缀规范节点）未能可靠覆盖的器官。
#    来源：src/anatomy_lexicon.py（RadLex 器官族 + 侧别建模）动态派生；保留原有的 R2 跨段覆盖分离，
#    排除 R2 已覆盖的「肾/股骨头」避免重复告警。新增 输卵管/精囊/锁骨/肋骨 等成对器官扩大覆盖。
MALIGNANT_MARKERS = ["恶性", "癌", "转移", "浸润", "侵犯", "恶变", "ca", "mt"]
BENIGN_MARKERS = ["良性", "炎性", "炎症", "符合良性", "考虑良性"]

# 描述段内「先见后无」检测的阳性/阴性动词
