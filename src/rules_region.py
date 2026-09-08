"""
rules_region.py — 逐部位精确比对（R17）与区域覆盖（R18）（P2 拆分自 engine.py）
==============================================================================
- REGION_LEXICON / REGION_TO_ORGANS：R17/R18 的解剖部位与区域-器官词表
- 别名派生（import 时执行）与 _region_spans_in_text / _region_assertions_in_section /
  _is_segment_global_normal / _region_cn_name / _extract_regions / _r12_same_region
- RegionRulesMixin：_r17_cross_region（描述段↔结论段按器官+侧别精确比对）、
  _r18_region_coverage（登记区域 → 描述段器官覆盖校验）

依赖：_lexicons、engine_helpers、engine_types。无反向依赖。
"""
import re
from typing import List

from _lexicons import *  # noqa: F401,F403

from engine_helpers import _has_positive, _claims_normal
from engine_types import Finding


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
        # 句边界保护：窗口内遇句号/分号/换行则截断，避免跨句吞入相邻部位阳性征
        # （如『右肺见结节，左肺正常。』后 16 字窗口不能吞掉下一句的阳性征）。
        for _bnd in ("。", "；", ";", "\n"):
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



def _r12_same_region(sent: str) -> bool:
    """R12 句级矛盾辅助：判断句中『部位+正常』与阳性征是否指向同一部位。

    - 相同部位（如『左肺见结节，左肺正常』）→ 真自相矛盾，返回 True。
    - 不同部位（如『右肺见结节，左肺正常』的对称描述）→ 不判矛盾，返回 False。
    对每个阳性征，取其之前最近的部位提及与『部位+正常』的提及比对。
    """
    m = _REGION_NORMAL_RE.search(sent)
    if not m:
        return False
    norm_s, norm_e = m.start(), m.end()          # 『部位正常』区间
    region_spans = _region_spans_in_text(sent)   # [(key, side, start, end)]
    # 正常声明所对应的部位提及
    normal_spans = [sp for sp in region_spans if sp[2] >= norm_s and sp[3] <= norm_e]
    # 收集未被否定的阳性征位置
    pos_idx = []
    for k in POSITIVE_STRONG:
        i = sent.find(k)
        while i != -1:
            pre = sent[max(0, i - 5): i]
            if not any(pre.endswith(neg) for neg in _NEG_PREFIXES):
                pos_idx.append(i)
            i = sent.find(k, i + 1)
    if not pos_idx:
        return False
    for pi in pos_idx:
        before = [sp for sp in region_spans if sp[3] <= pi]
        if not before:
            # 无部位限定词：若句内确有部位+正常声明，保守视为同部位（维持判矛盾）
            if normal_spans:
                return True
            continue
        nearest = max(before, key=lambda sp: sp[3])   # 阳性征前最近的部位
        if any(sp[0] == nearest[0] and sp[1] == nearest[1] for sp in normal_spans):
            return True
    return False




class RegionRulesMixin:
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
        secs = self._secs(text)
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
