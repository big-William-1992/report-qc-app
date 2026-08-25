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
from ._compat_lexicons import _pull_symbols  # noqa: F401
_pull_symbols("models", "textsplit", "lexicon_region", "claims",
              "config_store", "ner", "scoring", "meta_extract")

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


_RISKY_SINGLE = {k for k in SITE_NORM if len(k) == 1}  # 全文降级扫描时排除的高误判单字键（脑/肺/胰/脾/肾）

# 单字部位键的『病变/器官语境』后缀（计入 R6 的依据之一）
_SITE_SINGLE_OK_RIGHT = ("结石", "囊肿", "占位", "脓肿", "肿瘤", "钙化", "积水",
                         "梗死", "炎症", "炎性", "结核", "转移", "病变", "实质",
                         "结节", "包膜", "门区", "叶内", "叶间", "上叶", "中叶", "下叶")


def _r6_site_single_key_hits(body: str, k: str) -> bool:
    """单字部位键（肾/肺/胰/脾/脑）是否构成『报告主体』依据（2026-08-18 修复）。

    仅当满足其一才计入，避免正文偶发提及（如『未见脑转移』）把申请腹部误判为头颅：
      1) 非否定语境（前 12 字无 _NEG_BEFORE_POS_RE 匹配）；
      2) 且（左侧为左右双两 或 右侧 3 字内含病变/器官语境词，如『左肾见结石』『肺实质』）。
    """
    for m in re.finditer(re.escape(k), body):
        pre = body[max(0, m.start() - 12): m.start()]
        if _NEG_BEFORE_POS_RE.search(pre):
            continue
        left = body[m.start() - 1] if m.start() > 0 else ""
        right3 = body[m.end(): m.end() + 3]
        if left in "左右双两" or any(w in right3 for w in _SITE_SINGLE_OK_RIGHT):
            return True
    return False


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
