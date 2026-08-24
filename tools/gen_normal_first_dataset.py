#!/usr/bin/env python3
"""正确报告优先的质控训练集生成器。

思路：让模型大量学习「正确的报告长什么样」，建立各系统常见病多发病的
正常模式基线。推理时，偏离模式的就是可疑问题。

数据比例约 85% 正确报告 + 15% 错误报告（错别字/上下文矛盾）。

输出：百炼 ChatML JSONL。
"""
import argparse
import json
import os
import random
import sys
from typing import List, Dict, Tuple

# ── System prompt ──────────────────────────────────
SYSTEM_PROMPT = (
    "你是资深放射科质控专家，负责审核放射诊断报告。\n"
    "你已学习了大量正确报告的模式。现在请审查以下报告：\n"
    "1. 检查错别字（同音字、形近字、输入法误输入）\n"
    "2. 检查上下文对应（描述与结论是否一致、同一器官是否自相矛盾）\n"
    "3. 检查是否符合该系统常见病的报告规范\n"
    "仅输出 JSON 数组，每条含 error_type/location/severity/confidence/rationale；无问题输出 []。\n"
    "error_type 允许：R8-TYPO / R19-HOMOPHONE / R5-CONSISTENCY / R17-PERREGION / R12-SENTENCE / L1-OTHER。"
)

# ── 错别字映射 ──────────────────────────────────────
TYPO_MAP = {
    "姐姐": "结节", "结解": "结节", "姐节": "结节",
    "战位": "占位", "占为": "占位", "点位": "占位",
    "改化": "钙化", "钙话": "钙化", "钙华": "钙化",
    "病造": "病灶", "病兆": "病灶",
    "增墙": "增强", "墙化": "强化", "曾强": "增强",
    "摩玻璃": "磨玻璃",
    "纵格": "纵隔", "纵哥": "纵隔", "纵阁": "纵隔",
    "囊中": "囊肿", "曩肿": "囊肿",
    "费炎": "肺炎",
    "肺结合": "肺结核",
    "曾生": "增生",
    "息内": "息肉", "息巾": "息肉",
    "哽死": "梗死", "梗赛": "梗阻",
    "坎影": "龛影",
    "溃殇": "溃疡",
    "浸闰": "浸润",
    "珍断": "诊断",
    "造形": "造影",
    "随防": "随访", "随坊": "随访",
    "建义": "建议",
    "静桥": "静脉",
    "肿留": "肿瘤", "种瘤": "肿瘤",
    "转沂": "转移",
    "良姓": "良性",
    "恶姓": "恶性",
    "清析": "清晰",
    "模湖": "模糊",
    "毛皂": "毛糙",
    "费部": "肺部", "废纹理": "肺纹理",
    "子官": "子宫", "字官": "子宫",
    "前裂腺": "前列腺", "前例腺": "前列腺",
    "卵曹": "卵巢",
    "骨拆": "骨折",
    "申状腺": "甲状腺",
    "食官": "食管",
    "坐肺": "左肺", "又肺": "右肺",
    "影象": "影像",
    "实便": "实变",
    "低回升": "低回声", "高回升": "高回声",
    "密月": "密度",
}

def _finding(error_type: str, location: str, severity: str, rationale: str) -> Dict:
    return {"error_type": error_type, "location": location,
            "severity": severity, "confidence": 1.0, "rationale": rationale}


def build_record(report_text: str, output_findings) -> Dict:
    if isinstance(output_findings, dict):
        output_findings = [output_findings]
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": report_text},
        {"role": "assistant", "content": json.dumps(output_findings, ensure_ascii=False)},
    ]}


# ══════════════════════════════════════════════════════
#  各系统正确报告模板（按器官/常见病组合展开）
# ══════════════════════════════════════════════════════

def _chest_reports() -> List[str]:
    """胸部报告：肺纹理/结节/炎症/肿块/纵隔/胸膜等。"""
    genders = [("男", range(35, 75)), ("女", range(30, 70))]
    reports = []

    # --- 正常 ---
    for sex, ages in genders:
        for age in random.sample(list(ages), 3):
            reports.append(
                f"患者{sex}，{age}岁。检查部位：胸部。\n"
                f"检查所见：双肺纹理清晰，肺野透亮度正常，未见实变影。肺门及纵隔未见肿大淋巴结。心影大小形态正常。\n"
                f"诊断印象：胸部未见明显异常。"
            )

    # --- 肺结节（常见） ---
    sizes = ["0.3cm", "0.5cm", "0.6cm", "0.8cm", "1.0cm", "1.2cm"]
    locations = ["右肺上叶", "右肺下叶", "左肺上叶", "左肺下叶", "右肺中叶"]
    shapes = ["磨玻璃结节", "实性结节", "部分实性结节", "结节"]
    for loc in locations:
        for shape in shapes:
            for size in random.sample(sizes, 2):
                reports.append(
                    f"患者男，{random.randint(40,70)}岁。检查部位：胸部。\n"
                    f"检查所见：{loc}见一{shape}，边界清，大小约{size}，密度均匀。双肺纹理清晰。\n"
                    f"诊断印象：{loc}{shape}，建议定期随访。"
                )

    # --- 肺炎（常见） ---
    pneumonia_locs = ["右肺下叶", "左肺下叶", "右肺中叶", "双肺下叶"]
    for loc in pneumonia_locs:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(30,70)}岁。检查部位：胸部。\n"
            f"检查所见：{loc}见斑片状密度增高影，边缘模糊，密度不均。\n"
            f"诊断印象：{loc}肺炎，建议抗炎治疗后复查。"
        )

    # --- 肺气肿/慢支 ---
    reports.append(
        f"患者男，65岁。检查部位：胸部。\n"
        f"检查所见：双肺纹理增多紊乱，以双下肺为著。肺野透亮度增高。双肺散在纤维条索影。\n"
        f"诊断印象：慢性支气管炎、肺气肿改变。"
    )

    # --- 纵隔淋巴结 ---
    reports.append(
        f"患者男，55岁。检查部位：胸部。\n"
        f"检查所见：纵隔内见数枚淋巴结，较大者短径约1.2cm。双肺纹理清晰。\n"
        f"诊断印象：纵隔淋巴结稍大，建议随诊。"
    )

    # --- 胸腔积液 ---
    for side in ["右侧", "左侧"]:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(50,75)}岁。检查部位：胸部。\n"
            f"检查所见：{side}胸腔见少量弧形液性密度影。肺组织受压膨胀不全。\n"
            f"诊断印象：{side}少量胸腔积液。"
        )

    # --- 肺结核 ---
    reports.append(
        f"患者男，35岁。检查部位：胸部。\n"
        f"检查所见：右肺上叶见斑片状密度增高影，内见小点状钙化。双肺纹理清晰。\n"
        f"诊断印象：右肺上叶陈旧性结核可能。"
    )

    # --- 磨玻璃影（GGO） ---
    for loc in ["右肺上叶", "左肺上叶"]:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(45,65)}岁。检查部位：胸部。\n"
            f"检查所见：{loc}见磨玻璃密度影，边界清，大小约1.5cm。\n"
            f"诊断印象：{loc}磨玻璃影，建议随访。"
        )

    # --- 肺占位/肿块 ---
    for loc in ["右肺上叶", "左肺上叶", "右肺下叶"]:
        size = random.choice(["2.5cm", "3.0cm", "3.5cm", "4.0cm"])
        reports.append(
            f"患者男，{random.randint(50,70)}岁。检查部位：胸部。\n"
            f"检查所见：{loc}见一类圆形软组织密度影，边界欠清，大小约{size}，密度不均。\n"
            f"诊断印象：{loc}占位性病变，性质待定，建议增强CT。"
        )

    # --- 支气管扩张 ---
    reports.append(
        f"患者女，50岁。检查部位：胸部。\n"
        f"检查所见：双肺下叶支气管管壁增厚，管腔扩张，呈「轨道征」。\n"
        f"诊断印象：双肺下叶支气管扩张。"
    )

    return reports


def _abdomen_reports() -> List[str]:
    """腹部报告：肝胆胰脾肾。"""
    reports = []

    # --- 正常 ---
    for sex in ["男", "女"]:
        for age in random.sample(range(30, 70), 3):
            reports.append(
                f"患者{sex}，{age}岁。检查部位：腹部。\n"
                f"检查所见：肝脏大小形态正常，实质回声均匀，未见占位。胆囊未见结石。脾脏未见增大。双肾大小形态正常。\n"
                f"诊断印象：腹部未见明显异常。"
            )

    # --- 肝囊肿（常见） ---
    for size in ["1.5cm", "2.0cm", "2.5cm", "3.0cm"]:
        side = random.choice(["左叶", "右叶"])
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(40,65)}岁。检查部位：腹部。\n"
            f"检查所见：肝{side}见一类圆形无回声区，边界清，大小约{size}，后方回声增强。\n"
            f"诊断印象：肝{side}囊肿。"
        )

    # --- 肝血管瘤 ---
    for size in ["1.0cm", "1.5cm", "2.0cm"]:
        reports.append(
            f"患者女，{random.randint(35,55)}岁。检查部位：腹部。\n"
            f"检查所见：肝右叶见一高回声结节，边界清，大小约{size}，内部回声均匀。\n"
            f"诊断印象：肝右叶血管瘤，建议随访。"
        )

    # --- 胆囊结石（常见） ---
    for size in ["0.5cm", "0.8cm", "1.0cm", "1.5cm", "2.0cm"]:
        count = random.choice(["一枚", "数枚", "多枚"])
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(35,70)}岁。检查部位：腹部。\n"
            f"检查所见：胆囊壁毛糙，胆囊内见强回声团{count}，后方伴声影，较大约{size}。\n"
            f"诊断印象：胆囊结石。"
        )

    # --- 胆囊息肉 ---
    reports.append(
        f"患者男，45岁。检查部位：腹部。\n"
        f"检查所见：胆囊壁见一中等回声附壁结节，大小约0.4cm，基底较宽，无声影。\n"
        f"诊断印象：胆囊息肉样病变，建议随访。"
    )

    # --- 肾囊肿 ---
    for side in ["左肾", "右肾"]:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(40,65)}岁。检查部位：腹部。\n"
            f"检查所见：{side}见一类圆形无回声区，边界清，大小约2.0cm。\n"
            f"诊断印象：{side}囊肿。"
        )

    # --- 肾结石 ---
    for side in ["左肾", "右肾"]:
        for size in ["0.4cm", "0.6cm", "0.8cm"]:
            reports.append(
                f"患者{random.choice(['男','女'])}，{random.randint(30,60)}岁。检查部位：腹部。\n"
                f"检查所见：{side}盂内见强回声团，后方伴声影，大小约{size}。\n"
                f"诊断印象：{side}结石。"
            )

    # --- 脾脏增大 ---
    reports.append(
        f"患者男，50岁。检查部位：腹部。\n"
        f"检查所见：脾脏增大，厚约5.5cm，肋下可及。肝胆胰肾未见明显异常。\n"
        f"诊断印象：脾脏增大。"
    )

    # --- 脂肪肝 ---
    for degree in ["轻度", "中度"]:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(40,60)}岁。检查部位：腹部。\n"
            f"检查所见：肝脏回声增强，后方衰减，肝肾对比明显。肝内血管纹理清晰。\n"
            f"诊断印象：{degree}脂肪肝。"
        )

    # --- 胰腺 ---
    reports.append(
        f"患者男，55岁。检查部位：腹部。\n"
        f"检查所见：胰腺大小形态正常，实质回声均匀，胰管未见扩张。\n"
        f"诊断印象：胰腺未见明显异常。"
    )

    return reports


def _head_reports() -> List[str]:
    """头颅报告：脑实质/脑室/出血/梗死/占位。"""
    reports = []

    # --- 正常 ---
    for sex in ["男", "女"]:
        for age in random.sample(range(30, 75), 3):
            reports.append(
                f"患者{sex}，{age}岁。检查部位：头颅。\n"
                f"检查所见：双侧脑实质对称，密度均匀，未见明显异常密度灶。脑室系统大小形态正常，中线结构居中。\n"
                f"诊断印象：头颅CT未见明显异常。"
            )

    # --- 腔隙性脑梗死（常见） ---
    for side in ["左侧", "右侧", "双侧"]:
        for loc in ["基底节区", "放射冠区", "半卵圆中心"]:
            reports.append(
                f"患者男，{random.randint(55,75)}岁。检查部位：头颅。\n"
                f"检查所见：{side}{loc}见小片状低密度灶，边界欠清。\n"
                f"诊断印象：{side}{loc}腔隙性脑梗死可能。"
            )

    # --- 脑萎缩 ---
    reports.append(
        f"患者男，70岁。检查部位：头颅。\n"
        f"检查所见：脑沟增宽，脑裂增宽，脑室系统轻度扩大。中线结构居中。\n"
        f"诊断印象：脑萎缩改变。"
    )

    # --- 脑出血 ---
    for loc in ["基底节区", "丘脑", "脑叶"]:
        for side in ["左侧", "右侧"]:
            size = random.choice(["2.0ml", "5.0ml", "8.0ml", "15ml"])
            reports.append(
                f"患者男，{random.randint(55,75)}岁。检查部位：头颅。\n"
                f"检查所见：{side}{loc}见片状高密度灶，大小约{size}，周围见低密度水肿带。中线结构轻度偏移。\n"
                f"诊断印象：{side}{loc}脑出血。"
            )

    # --- 硬膜下血肿 ---
    for side in ["左侧", "右侧"]:
        reports.append(
            f"患者男，{random.randint(60,80)}岁。检查部位：头颅。\n"
            f"检查所见：{side}额颞顶颅骨内板下见新月形等密度影，范围较广，中线结构轻度向对侧偏移。\n"
            f"诊断印象：{side}亚急性硬膜下血肿。"
        )

    # --- 脑室扩张 ---
    reports.append(
        f"患者男，65岁。检查部位：头颅。\n"
        f"检查所见：双侧侧脑室及第三脑室扩张，脑沟变浅。中线结构居中。\n"
        f"诊断印象：脑室扩张，脑积水可能。"
    )

    # --- 占位 ---
    reports.append(
        f"患者女，50岁。检查部位：头颅。\n"
        f"检查所见：右额叶见一类圆形等密度灶，边界清，大小约3.0cm，周围见低密度水肿带。中线结构轻度左偏。\n"
        f"诊断印象：右额叶占位性病变，性质待定，建议增强MRI。"
    )

    return reports


def _pelvis_reports() -> List[str]:
    """盆腔报告：子宫/卵巢/前列腺/膀胱。"""
    reports = []

    # --- 女性正常 ---
    for age in random.sample(range(25, 55), 4):
        reports.append(
            f"患者女，{age}岁。检查部位：盆腔。\n"
            f"检查所见：子宫前位，大小形态正常，肌层回声均匀。双侧附件区未见明显异常。膀胱充盈好。\n"
            f"诊断印象：盆腔未见明显异常。"
        )

    # --- 子宫肌瘤（常见） ---
    for size in ["1.5cm", "2.0cm", "3.0cm", "4.0cm"]:
        loc = random.choice(["肌壁间", "浆膜下", "黏膜下"])
        reports.append(
            f"患者女，{random.randint(35,55)}岁。检查部位：盆腔。\n"
            f"检查所见：子宫前位，肌层内见一低回声结节，大小约{size}，位于{loc}。\n"
            f"诊断印象：子宫肌瘤。"
        )

    # --- 卵巢囊肿 ---
    for side in ["左侧", "右侧"]:
        for size in ["2.0cm", "3.0cm", "4.0cm"]:
            reports.append(
                f"患者女，{random.randint(25,50)}岁。检查部位：盆腔。\n"
                f"检查所见：{side}卵巢见一囊性无回声区，边界清，大小约{size}，壁薄光滑。\n"
                f"诊断印象：{side}卵巢囊肿，建议随访。"
            )

    # --- 前列腺增生（男性） ---
    for size in ["25ml", "30ml", "35ml", "40ml"]:
        reports.append(
            f"患者男，{random.randint(55,75)}岁。检查部位：盆腔。\n"
            f"检查所见：前列腺增大，体积约{size}，向膀胱内突入。内回声不均。\n"
            f"诊断印象：前列腺增生。"
        )

    # --- 膀胱结石 ---
    reports.append(
        f"患者男，60岁。检查部位：盆腔。\n"
        f"检查所见：膀胱内见强回声团，后方伴声影，大小约1.5cm，可随体位改变移动。\n"
        f"诊断印象：膀胱结石。"
    )

    # --- 正常男性 ---
    for age in random.sample(range(30, 70), 3):
        reports.append(
            f"患者男，{age}岁。检查部位：盆腔。\n"
            f"检查所见：前列腺大小形态正常，内部回声均匀。膀胱充盈好，壁光滑。\n"
            f"诊断印象：盆腔未见明显异常。"
        )

    return reports


def _thyroid_reports() -> List[str]:
    """甲状腺报告。"""
    reports = []

    # --- 正常 ---
    for sex in ["男", "女"]:
        for age in random.sample(range(25, 60), 3):
            reports.append(
                f"患者{sex}，{age}岁。检查部位：甲状腺。\n"
                f"检查所见：甲状腺两侧叶大小形态正常，实质回声均匀，未见明显结节。CDFI未见异常血流信号。\n"
                f"诊断印象：甲状腺未见明显异常。"
            )

    # --- 甲状腺结节（常见） ---
    for side in ["左侧叶", "右侧叶", "峡部"]:
        for size in ["0.3cm", "0.5cm", "0.8cm", "1.0cm"]:
            reports.append(
                f"患者女，{random.randint(30,60)}岁。检查部位：甲状腺。\n"
                f"检查所见：{side}见一低回声结节，边界清，大小约{size}，纵横比<1。\n"
                f"诊断印象：{side}结节，TI-RADS 3类，建议随访。"
            )

    # --- TI-RADS 4类结节 ---
    reports.append(
        f"患者女，45岁。检查部位：甲状腺。\n"
        f"检查所见：右侧叶见一低回声结节，边界不清，内见点状强回声，大小约0.8cm。\n"
        f"诊断印象：右侧叶结节，TI-RADS 4A类，建议穿刺活检。"
    )

    # --- 甲状腺弥漫性病变 ---
    reports.append(
        f"患者女，40岁。检查部位：甲状腺。\n"
        f"检查所见：甲状腺弥漫性增大，实质回声增粗不均，血流信号丰富。\n"
        f"诊断印象：甲状腺弥漫性病变，考虑桥本甲状腺炎可能。"
    )

    # --- 甲状腺囊肿 ---
    reports.append(
        f"患者女，35岁。检查部位：甲状腺。\n"
        f"检查所见：左侧叶见一无回声区，边界清，大小约1.5cm，后方回声增强。\n"
        f"诊断印象：左侧叶甲状腺囊肿。"
    )

    return reports


def _breast_reports() -> List[str]:
    """乳腺报告。"""
    reports = []

    # --- 正常 ---
    for age in random.sample(range(30, 60), 4):
        reports.append(
            f"患者女，{age}岁。检查部位：乳腺。\n"
            f"检查所见：双侧乳腺腺体回声均匀，未见明显结节及异常回声区。乳头无凹陷，皮肤无增厚。\n"
            f"诊断印象：双侧乳腺未见明显异常。"
        )

    # --- BI-RADS 3 结节 ---
    for side in ["右侧", "左侧"]:
        for size in ["0.8cm", "1.0cm", "1.2cm"]:
            reports.append(
                f"患者女，{random.randint(35,55)}岁。检查部位：乳腺。\n"
                f"检查所见：{side}乳腺外上象限见一低回声结节，边界清，形态规则，大小约{size}。\n"
                f"诊断印象：{side}乳腺结节，BI-RADS 3，建议随访。"
            )

    # --- BI-RADS 4 钙化 ---
    reports.append(
        f"患者女，50岁。检查部位：乳腺。\n"
        f"检查所见：左侧乳腺见簇状微钙化，范围约1.5cm，形态不规则。\n"
        f"诊断印象：左侧乳腺可疑钙化，BI-RADS 4A，建议穿刺活检。"
    )

    # --- 乳腺增生 ---
    reports.append(
        f"患者女，40岁。检查部位：乳腺。\n"
        f"检查所见：双侧乳腺腺体结构紊乱，回声增粗不均，可见散在小囊性回声。\n"
        f"诊断印象：双侧乳腺增生。"
    )

    return reports


def _spine_joint_reports() -> List[str]:
    """脊柱/关节报告。"""
    reports = []

    # --- 腰椎正常 ---
    for sex in ["男", "女"]:
        for age in random.sample(range(30, 65), 2):
            reports.append(
                f"患者{sex}，{age}岁。检查部位：腰椎。\n"
                f"检查所见：腰椎生理曲度存在，椎体序列尚齐，各椎体及附件骨质结构正常。椎间盘未见明显突出。\n"
                f"诊断印象：腰椎未见明显异常。"
            )

    # --- 腰椎间盘突出 ---
    for segment in ["L3-4", "L4-5", "L5-S1"]:
        degree = random.choice(["轻度", "中度"])
        reports.append(
            f"患者男，{random.randint(40,65)}岁。检查部位：腰椎。\n"
            f"检查所见：{segment}椎间盘向后{degree}突出，硬膜囊前缘受压。椎管无明显狭窄。\n"
            f"诊断印象：{segment}椎间盘{degree}突出。"
        )

    # --- 颈椎退变 ---
    reports.append(
        f"患者女，55岁。检查部位：颈椎。\n"
        f"检查所见：颈椎生理曲度变直，椎体边缘骨质增生。C4-5、C5-6椎间盘轻度突出。\n"
        f"诊断印象：颈椎退行性改变。"
    )

    # --- 膝关节 ---
    reports.append(
        f"患者男，50岁。检查部位：膝关节。\n"
        f"检查所见：左膝关节间隙正常，内外侧半月板形态信号正常。前后交叉韧带连续完整。\n"
        f"诊断印象：左膝关节未见明显异常。"
    )

    # --- 半月板损伤 ---
    reports.append(
        f"患者男，35岁。检查部位：膝关节。\n"
        f"检查所见：右膝内侧半月板后角见线状高信号，达关节面。前后交叉韧带连续完整。\n"
        f"诊断印象：右膝内侧半月板损伤。"
    )

    # --- 骨折 ---
    for bone in ["桡骨远端", "踝关节", "肱骨近端"]:
        reports.append(
            f"患者{random.choice(['男','女'])}，{random.randint(30,70)}岁。检查部位：{bone}。\n"
            f"检查所见：{bone}见骨皮质断裂，骨折线清晰，断端有移位。周围软组织肿胀。\n"
            f"诊断印象：{bone}骨折。"
        )

    return reports


# ══════════════════════════════════════════════════════
#  错误报告注入器
# ══════════════════════════════════════════════════════

def inject_typo(text: str) -> Tuple[str, Dict] | None:
    for wrong, correct in TYPO_MAP.items():
        if correct in text:
            return text.replace(correct, wrong, 1), _finding(
                "R8-TYPO", wrong, "medium",
                f"错别字：'{correct}' 误写为 '{wrong}'，应为 '{correct}'"
            )
    return None


def inject_multi_typo(text: str) -> Tuple[str, List[Dict]]:
    findings = []
    t = text
    candidates = [(w, c) for w, c in TYPO_MAP.items() if c in t]
    random.shuffle(candidates)
    for wrong, correct in candidates[:2]:
        if correct in t:
            t = t.replace(correct, wrong, 1)
            findings.append(_finding(
                "R8-TYPO", wrong, "medium",
                f"错别字：'{correct}' 误写为 '{wrong}'，应为 '{correct}'"
            ))
    return t, findings


def inject_contradiction(text: str) -> Tuple[str, List[Dict]]:
    """注入描述-结论矛盾。"""
    if "诊断印象" not in text:
        return text, []

    import re
    findings_part, imp_part = text.split("诊断印象", 1)

    # 描述段有阳性征象 → 结论否定
    m = re.search(r"见[一]?(结节|占位|肿块|囊肿|结石|肌瘤)", findings_part)
    if m:
        term = m.group(1)
        new_imp = imp_part.replace(term, f"未见{term}")
        if new_imp != imp_part:
            return findings_part + "诊断印象" + new_imp, [_finding(
                "R5-CONSISTENCY", f"诊断印象：未见{term}", "high",
                f"描述段见'{term}'，诊断印象却写'未见{term}'，前后矛盾"
            )]

    # 描述段说未见异常 → 结论说有问题
    if "未见明显异常" in findings_part or "未见异常" in findings_part:
        new_imp = imp_part.replace("未见", "可见")
        if new_imp != imp_part:
            return findings_part + "诊断印象" + new_imp, [_finding(
                "R5-CONSISTENCY", f"诊断印象：{new_imp.strip()[:20]}", "high",
                "描述段说'未见'，诊断印象却改为'可见'，否定矛盾"
            )]

    return text, []


# ══════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="正确报告优先的质控训练集")
    parser.add_argument("--out", default="data/qc_normal_first_bailian.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    # 1. 生成各系统正确报告
    all_clean = []
    all_clean.extend(_chest_reports())
    all_clean.extend(_abdomen_reports())
    all_clean.extend(_head_reports())
    all_clean.extend(_pelvis_reports())
    all_clean.extend(_thyroid_reports())
    all_clean.extend(_breast_reports())
    all_clean.extend(_spine_joint_reports())

    # 去重
    seen, clean_reports = set(), []
    for r in all_clean:
        k = r.strip()
        if k not in seen:
            seen.add(k)
            clean_reports.append(k)

    print(f"正确报告总数：{len(clean_reports)}")

    records = []
    seen_inputs = set()

    def _add(text, findings):
        key = text.strip()
        if key not in seen_inputs:
            seen_inputs.add(key)
            records.append(build_record(text, findings))

    # 2. 正确报告 → 输出 []（让模型学会「正确」的样子）
    for text in clean_reports:
        _add(text, [])

    # 3. 错误报告（约 15%）
    n_errors = max(1, len(clean_reports) // 6)
    error_pool = random.sample(clean_reports, min(n_errors, len(clean_reports)))

    for text in error_pool:
        # 错别字
        result = inject_typo(text)
        if result:
            _add(*result)

    for text in error_pool[:len(error_pool)//2]:
        # 多错别字
        t, f = inject_multi_typo(text)
        if f:
            _add(t, f)

    for text in error_pool[:len(error_pool)//3]:
        # 上下文矛盾
        t, f = inject_contradiction(text)
        if f:
            _add(t, f)

    # 打乱
    random.shuffle(records)

    # 统计
    total = len(records)
    pos = sum(1 for r in records if json.loads(r["messages"][2]["content"]))
    neg = total - pos

    # 写出
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n✅ 生成完成：{args.out}")
    print(f"   总记录数：{total}")
    print(f"   ├─ 正确报告（反例）：{neg} ({neg*100//total}%)")
    print(f"   └─ 含缺陷报告（正例）：{pos} ({pos*100//total}%)")
    print(f"\n各系统分布：")
    systems = {"胸部": 0, "腹部": 0, "头颅": 0, "盆腔": 0, "甲状腺": 0, "乳腺": 0, "脊柱": 0}
    for r in records:
        text = r["messages"][1]["content"]
        for sys_name in systems:
            if sys_name in text:
                systems[sys_name] += 1
                break
    for sys_name, cnt in systems.items():
        print(f"   {sys_name}：{cnt}")

    print(f"\n上传 + 微调命令：")
    print(f"  bl dataset validate --file {args.out}")
    print(f"  bl dataset upload --file {args.out}")
    print(f"  # 拿到 file-xxx 后：")
    print(f"  bl finetune text create --base-model qwen3-4b-instruct-2507 --datasets file-xxx --training-type sft-lora --model-name qc-normal-first-v1")


if __name__ == "__main__":
    main()
