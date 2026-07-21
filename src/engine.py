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
from typing import List, Optional, Dict


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
}

# 解剖部位同义词 → 规范节点（带左右前缀便于跨段比对）
ANATOMY_SYNONYMS = {
    "左肺上叶": "LUUL", "左上肺": "LUUL", "左肺上": "LUUL",
    "右肺上叶": "RUUL", "右上肺": "RUUL", "右肺上": "RUUL",
    "右肺": "RUL", "左肺": "LUL",
    "左肺": "LUL", "右肺": "RUL",
    "左侧股骨头": "L-femoral-head", "右侧股骨头": "R-femoral-head",
    "左肾": "L-kidney", "右肾": "R-kidney",
}

# 部位归一化（用于登记部位不符：申请部位词 → 规范部位族）
SITE_NORM = {
    "头颅": "head", "颅脑": "head", "头部": "head", "脑": "head", "脑实质": "head",
    "胸部": "chest", "双肺": "chest", "心肺": "chest",
    "腹部": "abdomen", "全腹": "abdomen",
    "盆腔": "pelvis", "骨盆": "pelvis",
    "腰椎": "lumbar", "脊柱": "spine",
    "左髋": "hip", "右髋": "hip", "髋关节": "hip", "股骨头": "hip",
    "左膝": "knee", "右膝": "knee", "膝关节": "knee",
}

VALID_UNITS = {"cm", "mm", "HU", "mm/s", "ml", "°", "mmhg"}

MODALITY_SCORE = {"乳腺": "BI-RADS", "钼靶": "BI-RADS", "乳腺x线": "BI-RADS",
                  "前列腺": "PI-RADS", "盆腔": "PI-RADS"}

# 描述内部阳性/阴性征（用于一致性）
POSITIVE_MARKERS = ["结节", "占位", "阴影", "肿块", "异常信号", "斑片", "渗出", "骨折", "扩张", "增大"]
NEGATIVE_MARKERS = ["未见", "无", "阴性", "通畅", "清晰", "正常", "未见明显", "未见异常"]

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


def load_rules_config(path: str = RULES_CONFIG_PATH) -> dict:
    """读取用户维护的规则配置。失败回退内置默认值，保证引擎始终可用。"""
    defaults = {"typos": dict(TYPO_MAP_DEFAULT), "conflicts": [],
                "ignores": [], "template": dict(DEFAULT_TEMPLATE)}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.setdefault("typos", dict(TYPO_MAP_DEFAULT))
        cfg.setdefault("conflicts", [])
        cfg.setdefault("ignores", [])
        cfg.setdefault("template", dict(DEFAULT_TEMPLATE))
        return cfg
    except Exception:
        return defaults


def save_rules_config(cfg: dict, path: str = RULES_CONFIG_PATH) -> None:
    """持久化规则配置到 JSON。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# ----------------------------- NER -----------------------------
class ChineseRadiologyNER:
    SECTION_MAP = [
        (re.compile(r"检查所见|影像描述|表现"), "findings"),
        (re.compile(r"诊断印象|印象|诊断意见|结论"), "impression"),
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
        for syn, canon in ANATOMY_SYNONYMS.items():
            for m in re.finditer(re.escape(syn), text):
                ents.append(Entity(syn, "anatomy", m.start(), m.end(),
                                    self._section_of(sections, m.start()), canon))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([A-Za-z°/][A-Za-z°/0-9]*|\u00b0)", text):
            unit = m.group(2).lower()
            label = "measurement" if unit in VALID_UNITS else "bad_unit"
            ents.append(Entity(m.group(0), label, m.start(), m.end(),
                                self._section_of(sections, m.start()), unit))
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
        ner = ChineseRadiologyNER()
        ents = ner.extract(text)
        return (self._r1_gender(text, ents, meta)
                + self._r2_laterality(text, ents)
                + self._r3_score(text, meta)
                + self._r4_unit(ents)
                + self._r5_consistency(text, ents)
                + self._r6_site(text, meta)
                + self._r7_internal(text, ents)
                + self._r8_typo(text)
                + self._r9_conflict(text)
                + self._r10_template(text))

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
                out.append(Finding("R1-GENDER", "性别矛盾", "high",
                    f"{src}性别为{_zh(rg)}，但文中出现{_zh(expect)}性专属器官「{e.text}」",
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
        # 按"规范器官族"归组：描述段出现阳性征的器官族
        fam_mk = {}  # 器官族 -> 展示名
        for e in ents:
            if e.label != "anatomy" or e.section != "findings" or not e.canonical:
                continue
            fam = e.canonical.split("-", 1)[-1]
            seg = f_txt[max(0, e.start - 20): e.end + 20]
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
        """按段落标题切分描述/印象原文（与 NER 段落划分一致）。"""
        spans = []
        for pat, sec in [("检查所见|影像描述|表现", "findings"),
                         ("诊断印象|印象|诊断意见|结论", "impression")]:
            for m in re.finditer(pat, text):
                spans.append((m.start(), sec))
        spans.sort()
        res = {"findings": text, "impression": ""}
        if spans:
            # 从第一个标题起往后切：findings 取首个 findings 起 ~ 下一个 impression；impression 取首个 impression 起
            f0 = next((s for s in spans if s[1] == "findings"), None)
            i0 = next((s for s in spans if s[1] == "impression"), None)
            if f0 and i0 and i0[0] > f0[0]:
                res["findings"] = text[f0[0]:i0[0]]
                res["impression"] = text[i0[0]:]
            elif i0:
                res["impression"] = text[i0[0]:]
                res["findings"] = text[:i0[0]]
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

    # R9 用户自定义互斥冲突（由 rules_config.json 维护：词A 与 词B 不应在同一范围内共存）
    def _r9_conflict(self, text) -> List[Finding]:
        out = []
        if not text:
            return out
        conflicts = self.rules_config.get("conflicts", []) or []
        for rule in conflicts:
            a = (rule.get("a") or "").strip()
            b = (rule.get("b") or "").strip()
            if not a or not b:
                continue
            scope = rule.get("scope", "正文")
            sev = rule.get("severity", "medium")
            # 范围：描述段 → 仅影像描述/检查所见；正文（默认）→ 整篇
            if scope == "描述段":
                secs = self._split_for_r5(text)
                target = secs["findings"]
            else:
                target = text
            if a in target and b in target:
                note = rule.get("note", "")
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


class _KG:
    def expected_gender_for_organ(self, organ: str) -> Optional[str]:
        return GENDER_ORGANS.get(organ)

    def required_score_for_modality(self, modality: str) -> Optional[str]:
        return MODALITY_SCORE.get(modality)

    def norm_site(self, site: str) -> Optional[str]:
        return SITE_NORM.get(site.strip())


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
    仅在证据明确时返回，避免误判。
    """
    if not text:
        return None
    # 显式字段
    m = re.search(r"(?:性别|患者|受检者)[:：\s]*([男女])", text)
    if m:
        return "male" if m.group(1) == "男" else "female"
    m = re.search(r"([男女])性", text)
    if m:
        return "male" if m.group(1) == "男" else "female"
    # 病史写法：性别 + 紧邻年龄（岁/Y/y/岁数或逗号后数字）
    m = re.search(r"[，,、\s：:]([男女])\s*[，,、]?\s*\d{1,3}\s*[岁YyＹ歲]", text)
    if m:
        return "male" if m.group(1) == "male" or m.group(1) == "男" else "female"
    m = re.search(r"\b([MFmf])\s*[,，]?\s*\d{1,3}\b", text)
    if m:
        return "male" if m.group(1).lower() == "m" else "female"
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
    m = re.search(r"年龄[:：]?\s*(\d{1,3})\s*(?:岁|Y|y|歲)?", text)
    if m:
        return m.group(1)
    # 病史写法：男，45岁 / 女 32Y
    m = re.search(r"[，,、\s：:]([男女])\s*[，,、]?\s*(\d{1,3})\s*(?:岁|Y|y|歲)", text)
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


def _extract_site(text: str) -> str:
    """抽取登记/申请部位，归一化到 SITE_NORM 的中文 key（如『胸部』『盆腔』）。"""
    if not text:
        return ""
    # 显式字段优先：申请部位 / 检查部位 / 检查名称 / 部位
    for label in ("申请部位", "检查部位", "检查名称", "部位"):
        m = re.search(label + r"[:：]?\s*([^\s，,。；;：]+)", text)
        if m:
            kw = m.group(1)
            for k in SITE_NORM:
                if k in kw:
                    return k
            return kw
    # 否则从正文部位词推断
    for k in SITE_NORM:
        if k in text:
            return k
    return ""


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


def extract_meta(text: str) -> dict:
    """从报告正文抽取元信息，用于剪贴板/导入场景自动回填输入框。

    返回 {gender, age, modality, applied_site, laterality}（均为中文字符串）。
    抽取结果仅作提示，允许人工校正。
    """
    return {
        "gender": _extract_gender_cn(text),
        "age": _extract_age(text),
        "modality": _extract_modality(text),
        "applied_site": _extract_site(text),
        "laterality": _extract_laterality(text),
    }
