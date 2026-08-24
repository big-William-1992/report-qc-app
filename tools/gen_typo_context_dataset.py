#!/usr/bin/env python3
"""专注错别字 + 上下文对应的质控训练集生成器。

目标：
1. 错别字（R8-TYPO / R19-HOMOPHONE）：覆盖同音、形近、输入法错字，
   含上下文敏感词（坐肺 在医学语境 vs 非医学语境）
2. 上下文对应（R5-CONSISTENCY / R17-PERREGION / R12-SENTENCE）：
   描述-结论矛盾、逐部位矛盾、句内自相矛盾

其他规则（R1-GENDER / R2-LATERALITY 等）交给确定性质控规则引擎处理。

输出：百炼 ChatML JSONL，可直接 `bl dataset upload` + `bl finetune text create`。
"""
import argparse
import json
import os
import random
import sys
from typing import List, Tuple, Dict

# ── System prompt（与推理时对齐）──────────────────────
SYSTEM_PROMPT = (
    "你是资深放射科质控专家，审查下面的放射诊断报告。\n"
    "重点检查：\n"
    "1. 错别字（同音字、形近字、输入法误输入）：如「姐姐」应为「结节」「坐肺」应为「左肺」「费炎」应为「肺炎」等；\n"
    "2. 上下文对应：描述段提到的征象/部位与诊断印象是否一致，同一器官的描述是否自相矛盾。\n"
    "仅输出一个 JSON 数组，每条含 error_type/location/severity/confidence/rationale；无问题输出 []。\n"
    "error_type 仅允许：R8-TYPO / R19-HOMOPHONE / R5-CONSISTENCY / R17-PERREGION / R12-SENTENCE / L1-OTHER。"
)

# ── 错别字映射（wrong → correct）──────────────────────
TYPO_MAP = {
    # 同音字（语音录入）
    "姐姐": "结节", "结解": "结节", "姐节": "结节", "节结": "结节",
    "战位": "占位", "占为": "占位", "点位": "占位",
    "改化": "钙化", "盖化": "钙化", "钙话": "钙化", "钙划": "钙化", "钙华": "钙化",
    "病造": "病灶", "病燥": "病灶", "病兆": "病灶",
    "增墙": "增强", "墙化": "强化", "墙实": "强化", "墙强": "增强", "曾强": "增强",
    "迷漫": "弥漫", "弥慢": "弥漫",
    "摩玻璃": "磨玻璃", "磨破璃": "磨玻璃",
    "深出": "渗出", "参出": "渗出",
    "胸模": "胸膜",
    "般片": "斑片", "斑偏": "斑片",
    "纵格": "纵隔", "纵哥": "纵隔", "纵阁": "纵隔", "纵膈": "纵隔",
    "囊中": "囊肿", "曩中": "囊肿", "囊重": "囊肿", "曩肿": "囊肿",
    "水种": "水肿",
    "肺结合": "肺结核",
    "费炎": "肺炎",
    "曾生": "增生", "增牛": "增生", "曾牛": "增生",
    "积夜": "积液",
    "息内": "息肉", "息巾": "息肉",
    "动肪瘤": "动脉瘤", "动肪": "动脉", "动泳": "动脉",
    "哽死": "梗死", "梗赛": "梗阻", "更塞": "梗阻", "梗陻": "梗阻",
    "坎影": "龛影", "咀影": "龛影", "龛形": "龛影",
    "溃殇": "溃疡", "馈疡": "溃疡", "溃汤": "溃疡",
    "浸闰": "浸润",
    "珍断": "诊断", "诊段": "诊断", "珍段": "诊断",
    "造形": "造影", "造景": "造影",
    "覆查": "复查", "复杳": "复查",
    "随防": "随访", "随坊": "随访", "随妨": "随访",
    "建义": "建议", "建意": "建议", "见意": "建议",
    "病请": "病情", "病清": "病情", "病晴": "病情",
    "静桥": "静脉", "精脉": "静脉", "进脉": "静脉",
    "肿留": "肿瘤", "种瘤": "肿瘤", "钟瘤": "肿瘤",
    "转沂": "转移", "转依": "转移", "转栘": "转移",
    "良姓": "良性", "良形": "良性",
    "恶姓": "恶性", "恶形": "恶性",
    "清析": "清晰", "清淅": "清晰", "清稀": "清晰",
    "模湖": "模糊", "模胡": "模糊",
    "边原": "边缘", "边远": "边缘", "边元": "边缘", "边绿": "边缘", "结节边绿": "结节边缘",
    "规侧": "规则", "规刘": "规则", "规厕": "规则",
    "对程": "对称", "对趁": "对称", "对衬": "对称",
    "毛皂": "毛糙", "毛糟": "毛糙",
    "实便": "实变", "便变": "实变",
    "曩性": "囊性", "曩壁": "囊壁",
    "低回升": "低回声", "高回升": "高回声", "回升区": "回声区",
    "强升": "强回声",
    "密月": "密度", "蜜度": "密度",
    "硬花": "硬化", "硬华": "硬化",
    "纤纬": "纤维", "纤微": "纤维", "纤惟": "纤维",
    "坐肺": "左肺", "坐费": "左肺",
    "又肺": "右肺", "由肺": "右肺",
    "费部": "肺部", "废部": "肺部", "废纹理": "肺纹理",
    "费纹理": "肺纹理", "费门": "肺门", "临门": "肺门",
    "双废纹理": "双肺纹理",
    "未梢": "末梢", "末稍": "末梢",
    "子官": "子宫", "字官": "子宫", "字宫": "子宫",
    "前裂腺": "前列腺", "前例腺": "前列腺", "前腺": "前列腺",
    "腮线": "腮腺", "腮泉": "腮腺",
    "骨拆": "骨折", "骨忻": "骨折", "骨析": "骨折",
    "申状腺": "甲状腺", "甲壮腺": "甲状腺", "申壮腺": "甲状腺",
    "食官": "食管", "食到": "食道", "食通": "食管",
    "兰尾": "阑尾",
    "卵曹": "卵巢", "卵槽": "卵巢",
    "蜘蛛膜": "蛛网膜",
    "精脉曲张": "静脉曲张",
    "淋马": "淋巴", "淋疤": "淋巴", "临吧": "淋巴",
    "胆襄": "胆囊",
    "肩钾骨": "肩胛骨",
    "肋果": "肋骨", "胸骨果": "胸骨",
    "盆骨": "骨盆",
    "肾藏": "肾脏",
    "胰缐": "胰腺", "胰腺线": "胰腺", "胰线": "胰腺",
    "影相": "影像", "影象": "影像", "显象": "显像",
    "征像": "征象", "政象": "征象",
    "锐力": "锐利", "瑞利": "锐利",
    "均质": "均匀", "均勾": "均匀", "均句": "均匀",
    "绯厚": "肥厚",
    "倡化": "强化", "倡实": "强化",
}

# 形近字（仅形近不同音的组合）
SHAPE_TYPOS = {
    "末见": "未见", "末分化": "未分化", "末见明确": "未见明确", "末见异常": "未见异常",
    "己经": "已经", "巳经": "已经",
    "主干增租": "主干增粗",
}

# 需要上下文判断的错别字（在医学语境中才是错别字）
CONTEXT_TYPOS = {
    "坐肺": ("左肺", ["上叶", "下叶", "中叶", "肺门", "纹理", "结节", "占位", "病灶", "肺炎", "积液"]),
    "又肺": ("右肺", ["上叶", "下叶", "中叶", "肺门", "纹理", "结节", "占位", "病灶", "肺炎", "积液"]),
    "费炎": ("肺炎", ["肺", "上叶", "下叶", "抗炎", "治疗"]),
    "纵哥": ("纵隔", ["肿大", "淋巴结", "占位", "移位"]),
}

# ── 基础干净报告 ──────────────────────────────────
CLEAN_REPORTS: List[str] = [
    "患者男，58岁。检查部位：胸部。\n检查所见：双肺纹理清晰，肺野透亮度正常，未见实变影。肺门及纵隔未见肿大淋巴结。\n诊断印象：胸部未见明显异常。",
    "患者女，45岁。检查部位：胸部。\n检查所见：右肺上叶见一磨玻璃结节，边界清，大小约0.8cm。双肺纹理清晰。\n诊断印象：右肺上叶磨玻璃结节，建议定期随访。",
    "患者男，62岁。检查部位：头颅。\n检查所见：双侧脑实质对称，密度均匀，未见明显异常密度灶。脑室系统大小形态正常，中线结构居中。\n诊断印象：头颅CT未见明显异常。",
    "患者女，50岁。检查部位：腹部。\n检查所见：肝脏大小形态正常，实质回声均匀，未见占位。胆囊未见结石。脾脏未见增大。双肾大小形态正常。\n诊断印象：腹部未见明显异常。",
    "患者男，55岁。检查部位：腹部。\n检查所见：肝左叶见一类圆形低密度灶，边界清，大小约2.0cm，考虑囊肿。\n诊断印象：肝左叶囊肿。",
    "患者女，42岁。检查部位：乳腺。\n检查所见：右乳外上象限见一低回声结节，边界清，大小约1.2cm。左侧乳腺未见明显异常。\n诊断印象：右乳结节，BI-RADS 3，建议随访。",
    "患者女，48岁。检查部位：盆腔。\n检查所见：子宫前位，大小形态正常，左卵巢见一囊肿，大小约3cm，壁薄光滑。\n诊断印象：左卵巢囊肿，建议随访。",
    "患者男，60岁。检查部位：腰椎。\n检查所见：腰椎生理曲度存在，椎体序列尚齐，L4-5椎间盘轻度突向椎管。硬膜囊受压不明显。\n诊断印象：L4-5椎间盘轻度突出。",
    "患者女，38岁。检查部位：甲状腺。\n检查所见：甲状腺两侧叶大小形态正常，实质回声均匀，未见明显结节。\n诊断印象：甲状腺未见明显异常。",
    "患者男，50岁。检查部位：胸部。\n检查所见：右肺下叶见斑片状密度增高影，边缘模糊。\n诊断印象：右肺下叶肺炎，建议抗炎治疗后复查。",
    "患者男，66岁。检查部位：头颅。\n检查所见：右侧基底节区见一低密度灶，边界不清，周围见低密度水肿带。\n诊断印象：右侧基底节区脑梗死可能。",
    "患者男，48岁。检查部位：腹部。\n检查所见：肝右叶见一占位性病变，大小约4.5cm，强化方式呈快进快出。\n诊断印象：肝右叶占位，原发性肝细胞癌可能，建议增强MRI。",
    "患者女，52岁。检查部位：乳腺。\n检查所见：左侧乳腺见一高密度钙化簇，大小约0.5cm。\n诊断印象：左乳可疑钙化，BI-RADS 4A，建议穿刺活检。",
    "患者男，40岁。检查部位：腹部。\n检查所见：左肾见一结石，大小约0.6cm，位于肾盂。双肾形态大小正常。\n诊断印象：左肾结石。",
    "患者女，35岁。检查部位：盆腔。\n检查所见：子宫前位，肌层见一低回声肌瘤，大小约2.5cm，突向宫腔。\n诊断印象：子宫肌瘤。",
    "患者男，70岁。检查部位：胸部。\n检查所见：双肺纹理增多，以双下肺为著。双肺野散在少许纤维条索影。\n诊断印象：双肺慢性支气管炎改变。",
    "患者女，55岁。检查部位：腹部。\n检查所见：胆囊壁毛糙，胆囊内见强回声团，后方伴声影，大小约1.5cm。\n诊断印象：胆囊结石。",
    "患者男，45岁。检查部位：膝关节。\n检查所见：左膝关节间隙正常，内外侧半月板形态信号正常。前后交叉韧带连续完整。\n诊断印象：左膝关节未见明显异常。",
    "患者女，60岁。检查部位：胸部。\n检查所见：右肺中叶见一小结节，边界清，大小约0.5cm，密度均匀。\n诊断印象：右肺中叶小结节，建议随访。",
    "患者男，52岁。检查部位：头颅。\n检查所见：双侧额叶见少许脱髓鞘改变。脑室系统无扩张。\n诊断印象：双侧额叶脱髓鞘改变，建议随诊。",
]

# ── 错误注入器 ──────────────────────────────────────

def _finding(error_type: str, location: str, severity: str, rationale: str) -> Dict:
    return {
        "error_type": error_type,
        "location": location,
        "severity": severity,
        "confidence": 1.0,
        "rationale": rationale,
    }


def inject_single_typo(text: str) -> Tuple[str, Dict] | None:
    """向报告注入一个错别字（医学术语→错字）。"""
    for wrong, correct in TYPO_MAP.items():
        if correct in text:
            injected = text.replace(correct, wrong, 1)
            return injected, _finding(
                "R8-TYPO", wrong, "medium",
                f"错别字：'{correct}' 误写为 '{wrong}'，应为 '{correct}'"
            )
    return None


def inject_shape_typo(text: str) -> Tuple[str, Dict] | None:
    """注入形近字错别字（五笔/OCR 来源）。"""
    for wrong, correct in SHAPE_TYPOS.items():
        if correct in text:
            injected = text.replace(correct, wrong, 1)
            return injected, _finding(
                "R19-HOMOPHONE", wrong, "medium",
                f"形近字：'{correct}' 误写为 '{wrong}'，应为 '{correct}'"
            )
    return None


def inject_multi_typo(text: str, n: int = 2) -> Tuple[str, List[Dict]]:
    """注入多个错别字（最多 n 个）。"""
    findings = []
    t = text
    candidates = [(w, c) for w, c in TYPO_MAP.items() if c in t]
    random.shuffle(candidates)
    for wrong, correct in candidates[:n]:
        if correct in t:
            t = t.replace(correct, wrong, 1)
            findings.append(_finding(
                "R8-TYPO", wrong, "medium",
                f"错别字：'{correct}' 误写为 '{wrong}'，应为 '{correct}'"
            ))
    return t, findings if findings else None


def inject_context_typo(text: str) -> Tuple[str, Dict] | None:
    """注入上下文敏感的错别字（如 坐肺→左肺，需要医学上下文判断）。"""
    for wrong, (correct, ctx_tokens) in CONTEXT_TYPOS.items():
        if correct in text:
            # 检查是否存在医学上下文
            has_ctx = any(tok in text for tok in ctx_tokens)
            if has_ctx:
                injected = text.replace(correct, wrong, 1)
                return injected, _finding(
                    "R8-TYPO", wrong, "medium",
                    f"错别字：'{correct}' 误写为 '{wrong}'，在医学语境中应为 '{correct}'"
                )
    return None


def make_context_negative() -> Tuple[str, List[Dict]]:
    """生成上下文无关的「坐」「又」「费」等字的非医学语境报告（不应触发 R8）。"""
    templates = [
        "患者男，45岁。主诉：坐骨神经痛，行走加重。\n检查所见：腰椎序列正常，椎间盘未见明显突出。\n诊断印象：坐骨神经痛。",
        "患者女，30岁。主诉：又感风寒，咳嗽流涕。\n检查所见：双肺纹理清晰。\n诊断印象：上呼吸道感染。",
        "患者男，50岁。费用结算：本次检查费用已结清。\n检查所见：胸部CT未见明显异常。\n诊断印象：胸部未见明显异常。",
    ]
    return random.choice(templates), []


# ── 上下文对应错误注入器 ────────────────────────────

def inject_findings_impression_contradiction(text: str) -> Tuple[str, List[Dict]]:
    """注入描述-结论矛盾（R5-CONSISTENCY）：修改诊断印象使其与描述段矛盾。"""
    if "诊断印象" not in text:
        return text, []

    findings_part, imp_part = text.split("诊断印象", 1)

    # 策略 1：描述段有阳性征象 → 诊断印象否定
    import re
    pos_match = re.search(r"见[一]?(结节|占位|肿块|囊肿|结石|肌瘤)", findings_part)
    if pos_match:
        term = pos_match.group(1)
        # 把诊断印象中提到的该术语替换为「未见XX」
        new_imp = imp_part.replace(term, f"未见{term}")
        if new_imp != imp_part:
            return findings_part + "诊断印象" + new_imp, [_finding(
                "R5-CONSISTENCY", f"诊断印象：未见{term}", "high",
                f"描述段明确见'{term}'，诊断印象却写'未见{term}'，前后矛盾"
            )]

    # 策略 2：描述段有器官正常 → 诊断印象说该器官有问题
    normal_match = re.search(r"(右肺上叶|左肺上叶|右肺下叶|左肺下叶|肝左叶|肝右叶|左肾|右肾|子宫|甲状腺)", findings_part)
    if normal_match and ("未见异常" in findings_part or "正常" in findings_part):
        organ = normal_match.group(1)
        if organ in imp_part:
            return text, [_finding(
                "R5-CONSISTENCY", f"诊断印象：{organ}", "high",
                f"描述段'{organ}未见异常'，诊断印象却提及{organ}异常，前后矛盾"
            )]

    # 策略 3：描述段说未见占位 → 诊断印象说见占位
    if "未见占位" in findings_part or "未见明显异常" in findings_part:
        new_imp = imp_part.replace("未见", "可见").replace("无", "有")
        if new_imp != imp_part:
            return findings_part + "诊断印象" + new_imp, [_finding(
                "R5-CONSISTENCY", f"诊断印象：{new_imp.strip()[:20]}", "high",
                "描述段说'未见'/'无'，诊断印象却改为'可见'/'有'，否定矛盾"
            )]

    return text, []


def inject_per_region_contradiction(text: str) -> Tuple[str, List[Dict]]:
    """注入逐部位矛盾（R17-PERREGION）：修改诊断印象使其与描述段的特定器官描述矛盾。"""
    if "诊断印象" not in text:
        return text, []

    findings_part, imp_part = text.split("诊断印象", 1)
    import re

    organs = [
        ("右肺上叶", "右肺上叶"), ("左肺上叶", "左肺上叶"),
        ("右肺下叶", "右肺下叶"), ("左肺下叶", "左肺下叶"),
        ("肝左叶", "肝左叶"), ("肝右叶", "肝右叶"),
        ("左肾", "左肾"), ("右肾", "右肾"),
    ]
    for organ, label in organs:
        if organ in findings_part:
            # 策略：描述段有该器官 → 诊断印象添加矛盾描述
            # 如果描述段有结节，诊断印象说该器官未见异常
            has_positive = re.search(rf"{organ}.*?(?:见[一]?结节|见[一]?占位|见[一]?肿块)", findings_part)
            if has_positive:
                # 诊断印象说该器官正常（矛盾）
                contradiction = f"{organ}未见明显异常"
                new_imp = imp_part.rstrip("。") + "。" + contradiction
                return findings_part + "诊断印象" + new_imp, [_finding(
                    "R17-PERREGION", f"诊断印象：{contradiction}", "high",
                    f"描述段见{organ}结节/占位，诊断印象却写'{organ}未见明显异常'，逐部位矛盾"
                )]

    return text, []


def inject_sentence_contradiction(text: str) -> Tuple[str, List[Dict]]:
    """注入句内自相矛盾（R12-SENTENCE）：在描述段中插入矛盾句。"""
    if "检查所见" not in text:
        return text, []

    import re
    parts = text.split("检查所见")
    if len(parts) < 2:
        return text, []

    findings_text = parts[1].split("诊断印象")[0] if "诊断印象" in parts[1] else parts[1]

    # 策略 1：描述段有结节 → 插入一句说该器官正常（矛盾）
    pos_match = re.search(r"(右肺上叶|左肺上叶|右肺下叶|左肺下叶).*?见[一]?(结节|占位|肿块)", findings_text)
    if pos_match:
        organ = pos_match.group(1)
        contradiction_sent = f"{organ}未见明显异常。"
        new_findings = findings_text.rstrip("。\n") + "。" + contradiction_sent
        new_text = parts[0] + "检查所见" + new_findings
        if "诊断印象" in text:
            new_text += "诊断印象" + text.split("诊断印象")[1]
        return new_text, [_finding(
            "R12-SENTENCE", contradiction_sent, "high",
            f"同一段内自相矛盾：前文见{organ}结节，后文却说'{organ}未见明显异常'"
        )]

    # 策略 2：描述段有器官 → 插入该器官正常又异常的矛盾句
    organ_match = re.search(r"(肝脏|胆囊|脾脏|双肾|子宫|甲状腺)", findings_text)
    if organ_match:
        organ = organ_match.group(1)
        contradiction_sent = f"{organ}大小形态正常，但见一占位。"
        new_findings = findings_text.rstrip("。\n") + "。" + contradiction_sent
        new_text = parts[0] + "检查所见" + new_findings
        if "诊断印象" in text:
            new_text += "诊断印象" + text.split("诊断印象")[1]
        return new_text, [_finding(
            "R12-SENTENCE", contradiction_sent, "high",
            f"同一句内自相矛盾：既说'{organ}大小形态正常'，又说'见一占位'"
        )]

    return text, []


def inject_multi_contradiction(text: str) -> Tuple[str, List[Dict]]:
    """注入多种上下文矛盾（最多 2 种组合）。"""
    all_findings = []
    t = text

    # 尝试注入描述-结论矛盾
    t2, f2 = inject_findings_impression_contradiction(t)
    if f2:
        t = t2
        all_findings.extend(f2)

    # 尝试注入逐部位矛盾
    t2, f2 = inject_per_region_contradiction(t)
    if f2:
        t = t2
        all_findings.extend(f2)

    return t, all_findings if all_findings else []


# ── 主流程 ──────────────────────────────────────────

def build_record(report_text: str, output_findings) -> Dict:
    """构造百炼 ChatML 训练记录。output_findings 可以是 list 或单个 dict。"""
    if isinstance(output_findings, dict):
        output_findings = [output_findings]
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": report_text},
            {"role": "assistant", "content": json.dumps(output_findings, ensure_ascii=False)},
        ]
    }


def main():
    parser = argparse.ArgumentParser(description="生成专注错别字+上下文对应的质控训练集")
    parser.add_argument("--out", default="data/qc_typo_context_bailian.jsonl",
                        help="输出文件路径（百炼 ChatML JSONL）")
    parser.add_argument("--n-per-base", type=int, default=8,
                        help="每份基础报告生成的变异数（默认 8）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    random.seed(args.seed)
    records: List[Dict] = []
    seen_inputs = set()

    def _add(report_text, findings):
        key = report_text.strip()
        if key not in seen_inputs:
            seen_inputs.add(key)
            records.append(build_record(report_text, findings))

    # ── 第一类：负例（干净报告，无错别字，无矛盾）──
    for text in CLEAN_REPORTS:
        _add(text, [])

    # ── 第二类：错别字单错误注入 ──
    for text in CLEAN_REPORTS:
        # 同音/形近错别字
        for _ in range(args.n_per_base):
            result = inject_single_typo(text)
            if result:
                _add(*result)
            result = inject_shape_typo(text)
            if result:
                _add(*result)

    # ── 第三类：多错别字组合注入 ──
    for text in CLEAN_REPORTS:
        for _ in range(max(1, args.n_per_base // 2)):
            t, findings = inject_multi_typo(text, n=2)
            if findings:
                _add(t, findings)

    # ── 第四类：上下文敏感错别字（正例+负例）──
    for text in CLEAN_REPORTS:
        result = inject_context_typo(text)
        if result:
            _add(*result)
    # 非医学语境负例
    neg_text, neg_findings = make_context_negative()
    _add(neg_text, neg_findings)

    # ── 第五类：上下文对应错误（R5 / R17 / R12）──
    for text in CLEAN_REPORTS:
        # 描述-结论矛盾
        t, f = inject_findings_impression_contradiction(text)
        if f:
            _add(t, f)
        # 逐部位矛盾
        t, f = inject_per_region_contradiction(text)
        if f:
            _add(t, f)
        # 句内矛盾
        t, f = inject_sentence_contradiction(text)
        if f:
            _add(t, f)

    # ── 第六类：组合错误（错别字 + 上下文矛盾）──
    for text in CLEAN_REPORTS:
        for _ in range(max(1, args.n_per_base // 3)):
            t1, f1 = inject_single_typo(text)
            if f1:
                t2, f2 = inject_findings_impression_contradiction(t1)
                if f2:
                    _add(t2, [f1] + f2)
                else:
                    _add(t1, [f1])

    # ── 打乱顺序 ──
    random.shuffle(records)

    # ── 统计 ──
    total = len(records)
    pos = sum(1 for r in records if json.loads(r["messages"][2]["content"]))
    neg = total - pos

    typo_count = sum(
        1 for r in records
        for f in json.loads(r["messages"][2]["content"])
        if f["error_type"] in ("R8-TYPO", "R19-HOMOPHONE")
    )
    ctx_count = sum(
        1 for r in records
        for f in json.loads(r["messages"][2]["content"])
        if f["error_type"] in ("R5-CONSISTENCY", "R17-PERREGION", "R12-SENTENCE")
    )

    # ── 写出 ──
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"✅ 生成完成：{args.out}")
    print(f"   总记录数：{total}")
    print(f"   ├─ 正例（含缺陷）：{pos}")
    print(f"   │   ├─ 错别字相关：{typo_count}")
    print(f"   │   └─ 上下文对应：{ctx_count}")
    print(f"   └─ 反例（干净）：{neg}")
    print(f"\n上传命令：")
    print(f"  bl dataset validate --file {args.out}")
    print(f"  bl dataset upload --file {args.out}")
    print(f"\n微调命令：")
    print(f"  bl finetune text create --base-model qwen3-4b-instruct-2507 --datasets {args.out} --training-type sft-lora --model-name qc-typo-ctx-v1")


if __name__ == "__main__":
    main()
