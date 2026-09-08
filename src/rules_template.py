"""
rules_template.py — 模板/随访规则（R10/R16/R20）（P2 拆分自 engine.py）
====================================================================
- REPORT_TYPE_REQUIREMENTS / _TYPE_KEYWORDS：检查类型 → 必查要素清单（R20）
- TemplateRulesMixin：_r10_template（结构化模板合规）、
  _r16_followup_timeframe（随访时限缺失，默认关闭）、
  _r20_template_completeness（按检查类型校验必查要素）

依赖：_lexicons、engine_helpers、engine_ner、engine_config、engine_types。
"""
import re
from typing import List

from _lexicons import *  # noqa: F401,F403

from engine_helpers import _claims_normal
from engine_ner import _ZH_NLP_OK, _zh_extract_followup
from engine_config import DEFAULT_TEMPLATE
from engine_types import Finding


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



class TemplateRulesMixin:
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
        secs = self._secs(text)
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
