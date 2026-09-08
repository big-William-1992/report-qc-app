"""
engine_config.py — 规则配置层（P2 拆分自 engine.py）
====================================================
- TYPO_MAP_DEFAULT：R8 兜底错字表（rules_config.json 读取失败时的默认值；
  load_rules_config 会把用户 typos 与之合并，用户优先）
- DEFAULT_TEMPLATE / default_rules_config / load_rules_config / save_rules_config
  / learn_typo / RULES_CONFIG_PATH

路径统一由 paths.py 解析（frozen/源码双分支单一事实源）。
"""
import json
import os
import re

import paths

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
    # —— P2 形近字错字（五笔/形码/OCR 误识别：形近但不同音）——
    "未梢": "末梢", "末见": "未见", "已见": "未见",
    "结节边绿": "结节边缘", "边绿": "边缘",
    "末分化": "未分化", "己经": "已经", "巳经": "已经",
    "主干增租": "主干增粗", "末见明确": "未见明确", "末见异常": "未见异常",
    # —— P2 输入法常见错（拼音重码）——
    "费部": "肺部", "费纹理": "肺纹理", "费门": "肺门",
    "双废纹理": "双肺纹理", "废纹理": "肺纹理", "纵阁": "纵隔", "临门": "肺门",
    "实便": "实变", "便变": "实变",
    "曩肿": "囊肿", "曩性": "囊性", "曩壁": "囊壁",
    "低回升": "低回声", "高回升": "高回声", "回升区": "回声区",
    "强升": "强回声",
    "腺体曾生": "腺体增生", "曾生": "增生",
    "边缘毛皂": "边缘毛糙", "毛皂": "毛糙",
    # —— P1 同音字换序/换字（单字换字，R19 同音层因单字白名单漏检）——
    "锐力": "锐利", "正长": "正常",
    # —— P1 字符换序（诊断→断诊 等，仅用于非子串冲突的情况）——
    # "断诊"→"诊断" 注释掉：正常报告中"诊断"包含子串"断诊"，会导致大量FP
    # "显窄"→"狭窄" 注释掉：同理"狭窄"包含子串"显窄"
    # "高信"→"信号" 注释掉："高信号"包含子串"高信"，FP率6%
}

# 规则配置文件路径（与 samples.db 同目录：assets/rules_config.json）
RULES_CONFIG_PATH = paths.rules_config_path()

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
            "enable_r19": True, "r19_sensitivity": "medium",
            "disabled_typos": []}


def load_rules_config(path: str = RULES_CONFIG_PATH) -> dict:
    """读取用户维护的规则配置。失败回退内置默认值，保证引擎始终可用。"""
    defaults = default_rules_config()
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.setdefault("conflicts", [])
        cfg.setdefault("ignores", [])
        cfg.setdefault("template", dict(DEFAULT_TEMPLATE))
        cfg.setdefault("r19_sensitivity", "medium")
        cfg.setdefault("enable_r19", True)
        # 启用/停用单条错字：disabled_typos 为「停用的错词」列表（P0 词库可视化管理）
        cfg.setdefault("disabled_typos", [])
        # typos 升级合并：默认错字表的新增词自动并入（用户自定义映射优先保留），
        # 避免老用户升级后缺失新版本内置的错字识别能力。
        _u_typos = cfg.get("typos") or {}
        if isinstance(_u_typos, dict):
            _merged = dict(TYPO_MAP_DEFAULT)
            _merged.update(_u_typos)   # 用户映射优先（同错词以用户为准）
            cfg["typos"] = _merged
        else:
            cfg.setdefault("typos", dict(TYPO_MAP_DEFAULT))
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
    _CN = re.compile(r"^[一-龥]+$")
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
