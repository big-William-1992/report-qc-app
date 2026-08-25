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

from typo_lexicon import TYPO_MAP_DEFAULT  # noqa: F401
try:
    import app_paths
except ImportError:  # 兼容包式导入
    from . import app_paths  # type: ignore

def _assets_dir() -> str:
    return app_paths.frozen_resource_dir("assets")


def _rules_config_path() -> str:
    """定位规则配置：打包后优先用用户可写目录(%APPDATA%/MedicalReportQC)，
    不存在则从 exe 同级 assets 复制初始文件，避免安装到 Program Files 后只读。"""
    if getattr(sys, "frozen", False):
        user_dir = os.path.join(os.path.expandvars("%APPDATA%"), "MedicalReportQC")
        user_path = os.path.join(user_dir, "rules_config.json")
        if not os.path.exists(user_path):
            src = app_paths.frozen_resource_dir("assets", "rules_config.json")
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
    "require_followup": False,                         # 随访建议默认关闭（2026-08-20）：避免每份缺"随访/复查"字样的报告都触发 low 级噪声；可在设置中按需开启
    "severity": "low",
    "note": "结构化报告建议含『检查所见』与『诊断印象/结论』段，并给出随访/复查建议",
}


_RULES_LOG = logging.getLogger("qc.engine")

# 规则配置 schema 版本（2026-08-23）：default_rules_config() 写入、save_rules_config()
# 落盘、load_rules_config() 加载时缺省补齐。旧布局用户文件无此键——**不做自动迁移**
# （自动合并/改写用户手工维护的规则风险大于收益，可能静默覆盖用户意图），仅打
# warn 日志提示「检测到旧版规则配置，请手动核对」，并按当前默认值补齐该字段，
# 保证后续版本能凭 schema_version 判断配置年代。
RULES_CONFIG_SCHEMA_VERSION = 1


def default_rules_config() -> dict:
    from .claims import R19_SAFE_WORDS  # 延迟导入: 打破 config_store<->claims 循环
    """出厂默认规则配置（恢复默认用）。"""
    return {"schema_version": RULES_CONFIG_SCHEMA_VERSION,
            "typos": dict(TYPO_MAP_DEFAULT), "conflicts": [],
            "ignores": [], "template": dict(DEFAULT_TEMPLATE),
            "enable_r19": True, "r19_sensitivity": "medium",
            "r19_safe_words": list(R19_SAFE_WORDS),
            "disabled_typos": [], "require_lesion_size": False}


def load_rules_config(path: str = RULES_CONFIG_PATH) -> dict:
    """读取用户维护的规则配置。失败回退内置默认值，保证引擎始终可用。"""
    defaults = default_rules_config()
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        # schema_version（2026-08-23）：缺省视为旧布局（v0）——不自动迁移（避免
        # 误合并覆盖用户手工规则），只补齐字段 + warn 提醒人工核对；行为见本文件
        # RULES_CONFIG_SCHEMA_VERSION 注释。
        if not isinstance(cfg.get("schema_version"), int):
            _RULES_LOG.warning(
                "检测到旧版规则配置（%s 缺少 schema_version），已按当前版本 %d 补齐；"
                "请手动核对自定义规则是否符合预期。",
                os.path.basename(str(path)), RULES_CONFIG_SCHEMA_VERSION)
        cfg.setdefault("schema_version", RULES_CONFIG_SCHEMA_VERSION)
        cfg.setdefault("conflicts", [])
        cfg.setdefault("ignores", [])
        cfg.setdefault("template", dict(DEFAULT_TEMPLATE))
        cfg.setdefault("r19_sensitivity", "medium")
        cfg.setdefault("enable_r19", True)
        # R19 安全词（2026-08-22）：用户可在配置中追加机构特有同音合法词，
        # 与内置 R19_SAFE_WORDS 合并，exact 同音命中时放行，降低误报。
        cfg.setdefault("r19_safe_words", [])
        # 病灶必报尺寸（默认关）：开启后描述段阳性病灶无测量值则提示（R22-SIZE-MISSING）
        cfg.setdefault("require_lesion_size", False)
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
    """持久化规则配置到 JSON（2026-08-18 M4 修复：临时文件 + os.replace 原子替换，
    防写一半崩溃损坏配置、防并发覆盖写丢键）。"""
    # schema_version 缺省补齐：调用方传入旧结构（如前端回传历史配置）时也保证
    # 落盘文件带版本标记，下次加载可判断年代。
    cfg.setdefault("schema_version", RULES_CONFIG_SCHEMA_VERSION)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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
    # 高频白名单保护（2026-08-18 H6）：错词本身是常用合法词（有肺/直接/结界…）
    # 时拒绝学习，避免 learn_typo/scan 采纳把合法表述当错字静默改写报告。
    if _is_common_word(wrong):
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
    _hf_ok = False
    try:
        from highfreq_lexicon import find_homophone_suggestions, highfreq_words
        hf = {w for w, _ in highfreq_words()}
        _hf_ok = True
    except Exception:
        hf = set()
    for w, c in sorted(freq.items(), key=lambda kv: -kv[1]):
        if c >= 3 or len(w) < 2:
            continue
        if w in typos or w in ignores or w in hf or w in seen:
            continue
        if w in anchors:
            continue
        if not _hf_ok:
            continue
        cand = find_homophone_suggestions(w)
        if not cand:
            continue
        best, cat, sim, _k = cand[0]
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

