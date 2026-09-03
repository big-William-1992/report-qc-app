"""医学词组白名单 + 域外检测
================================================================================
从所有现有词表合并构建统一白名单，用于检测报告中不在医学范围内的可疑词组。

设计思路（来自放射科医师经验）：
- 医学文书有强烈的逻辑性和规范性，很多词组反复出现
- 「不明显」是正确写法，「部明显」不在任何医学词表中 → 大概率错别字
- 白名单内的词组 = 高置信正确；白名单外的中文片段 = 低置信，需关注

数据来源（全部来自项目现有词表，零新增外部依赖）：
1. highfreq_lexicon._HIGHFREQ_WORDS — 高频正确词组（结构/征象/方位/程度/随访/正常/结论）
2. zh_radiology_synonyms — 征象/程度/随访同义词键+值
3. _lexicons — 解剖部位、部位归一、性别器官、方位、阳性征、正常声明
4. anatomy_lexicon.ANATOMY — RadLex 器官族中文别名
5. engine.REGION_LEXICON — R17 逐部位比对词表
6. engine.TYPO_MAP_DEFAULT 的正确侧 — 已知正确写法
7. 补充：常见医学虚词/连接词（未见/及/与/考虑/建议/大小/形态...）

用法：
    from medical_whitelist import find_suspicious_segments, WHITELIST_SIZE
    # 返回 [(start, end, segment_text), ...]
    suspicious = find_suspicious_segments(report_text)
"""
from __future__ import annotations

import re
import json
import os
import logging
from typing import List, Tuple, Set

logger = logging.getLogger(__name__)

# Path to test reports for bigram frequency analysis（统一由 paths 解析）
import paths as _paths
_TEST_REPORTS_PATH = _paths.test_reports_path()


def _build_common_bigrams() -> Set[str]:
    """从正常报告中提取高频二元组（相邻两字），用于过滤合法文本片段。

    原理：合法文本的相邻字组合通常有较高的共现频率；
    缺字/错字导致的相邻组合（如"未见明异常"中的"见明"）频率极低。
    """
    common: Set[str] = set()
    try:
        path = _TEST_REPORTS_PATH
        if not os.path.exists(path):
            logger.warning(
                f"Bigram 频率表构建失败：{path} 不存在。"
                "频率过滤器将完全失效，所有非白名单片段均可能被标记为可疑。"
                "请运行 generate_test_reports.py 生成测试数据。"
            )
            return common
        with open(path, "r", encoding="utf-8") as f:
            reports = json.load(f)
        cjk = re.compile(r"[一-鿿]+")
        counts: dict = {}
        for r in reports:
            if r.get("has_typo"):
                continue  # 只用正常报告构建频率表
            text = r.get("text", "")
            for m in cjk.finditer(text):
                run = m.group()
                for i in range(len(run) - 1):
                    bg = run[i:i + 2]
                    counts[bg] = counts.get(bg, 0) + 1
        # 出现 >= 2 次的二元组视为常见
        common = {bg for bg, cnt in counts.items() if cnt >= 2}
    except Exception as e:
        logger.warning(f"Bigram 频率表构建异常：{e}")
        pass
    if not common:
        logger.warning(
            "Bigram 频率表为空（_COMMON_BIGRAMS = ∅）。"
            "频率过滤层将失效，建议检查 test_reports.json 是否正常。"
        )
    return common


# 高频合法二元组（在初始化时构建）
_COMMON_BIGRAMS: Set[str] = _build_common_bigrams()


def _collect_from_highfreq() -> Set[str]:
    """从 highfreq_lexicon 收集高频正确词组"""
    words: Set[str] = set()
    try:
        from highfreq_lexicon import _HIGHFREQ_WORDS
        for w, _cat in _HIGHFREQ_WORDS:
            words.add(w)
    except Exception:
        pass
    return words


def _collect_from_synonyms() -> Set[str]:
    """从 zh_radiology_synonyms 收集征象/程度/随访同义词（键+值）"""
    words: Set[str] = set()
    try:
        from zh_radiology_synonyms import SIGN_SYNONYMS, DEGREE_SYNONYMS, FOLLOWUP_SYNONYMS
        for d in (SIGN_SYNONYMS, DEGREE_SYNONYMS, FOLLOWUP_SYNONYMS):
            for k, v in d.items():
                words.add(k)
                words.add(v)
    except Exception:
        pass
    return words


def _collect_from_lexicons() -> Set[str]:
    """从 _lexicons 收集解剖部位、部位归一、性别器官、方位、阳性征、正常声明等"""
    words: Set[str] = set()
    try:
        from _lexicons import (
            ANATOMY_SYNONYMS, SITE_NORM, GENDER_ORGANS, LATERALITY,
            POSITIVE_STRONG, NORMAL_CLAIM, REGION_NORMAL_WORDS,
        )
        # 解剖部位同义词（键=含方位写法，值=规范节点）
        for k, v in ANATOMY_SYNONYMS.items():
            words.add(k)
            words.add(v)
        # 部位归一
        words.update(SITE_NORM.keys())
        words.update(SITE_NORM.values())
        # 性别器官
        words.update(GENDER_ORGANS.keys())
        # 方位词
        words.update(LATERALITY.keys())
        # 阳性征（>=2字的才加，单字如"癌""瘤"意义不大）
        for w in POSITIVE_STRONG:
            if len(w) >= 2:
                words.add(w)
        # 正常声明
        words.update(NORMAL_CLAIM)
        # 区域正常词
        words.update(REGION_NORMAL_WORDS)
    except Exception:
        pass
    return words


def _collect_from_anatomy_lexicon() -> Set[str]:
    """从 anatomy_lexicon 收集 RadLex 器官族中文别名"""
    words: Set[str] = set()
    try:
        from anatomy_lexicon import ANATOMY
        for _key, meta in ANATOMY.items():
            for zh in meta.get("zh", []):
                words.add(zh)
    except Exception:
        pass
    return words


def _collect_from_engine_region() -> Set[str]:
    """从 engine.REGION_LEXICON 收集 R17 逐部位比对词表"""
    words: Set[str] = set()
    try:
        from rules_region import REGION_LEXICON
        for _key, meta in REGION_LEXICON.items():
            base = meta.get("base", "")
            if base:
                words.add(base)
            for extra in meta.get("extra", []):
                words.add(extra)
    except Exception:
        pass
    return words


def _collect_typo_correct_side() -> Set[str]:
    """从 TYPO_MAP_DEFAULT 收集已知正确写法（错词映射的正确侧）"""
    words: Set[str] = set()
    try:
        from engine_config import TYPO_MAP_DEFAULT
        for _wrong, correct in TYPO_MAP_DEFAULT.items():
            if len(correct) >= 2:
                words.add(correct)
    except Exception:
        pass
    return words


def _load_json_lexicon(name: str) -> Set[str]:
    """从 assets/lexicons/{name}.json 加载词表；缺失/损坏回退空集并告警。"""
    try:
        import paths as _paths
        with open(os.path.join(_paths.assets_dir(), "lexicons", name + ".json"),
                  encoding="utf-8") as _f:
            return set(json.load(_f))
    except Exception as _e:
        logger.warning(f"词表 {name}.json 加载失败（{_e}），该项词源为空")
        return set()


def _user_words() -> Set[str]:
    """用户/反馈闭环学习词表（assets/lexicons/user_whitelist.json，frozen 在用户目录）。

    缺失/损坏按空集处理（首次运行无用户词条是正常状态）。
    """
    try:
        import paths as _paths
        p = _paths.user_lexicon_path()
        if not os.path.exists(p):
            return set()
        with open(p, encoding="utf-8") as _f:
            data = json.load(_f)
        return set(w for w in data if isinstance(w, str) and w)
    except Exception as _e:
        logger.warning(f"用户词表加载失败（{_e}），按空集处理")
        return set()


def _collect_common_particles() -> Set[str]:
    """常见医学文书虚词/连接词/结构词（外部化：assets/lexicons/particles.json）"""
    return _load_json_lexicon("particles")


def _collect_from_en_zh_glossary() -> Set[str]:
    """从 radiology_glossary_en_zh 收集英文术语的中文翻译（2-8字）"""
    words: Set[str] = set()
    try:
        from radiology_translator import get_en_to_zh_map
        for en, zh in get_en_to_zh_map().items():
            if len(en) < 3:
                continue
            if ' ' in zh:
                continue
            if 2 <= len(zh) <= 8:
                words.add(zh)
    except Exception:
        pass
    return words


def _collect_frequent_fp_terms() -> Set[str]:
    """历史误报词组（外部化：assets/lexicons/frequent_fp_terms.json）"""
    return _load_json_lexicon("frequent_fp_terms")



def build_whitelist() -> Set[str]:
    """从所有词源构建统一医学词组白名单"""
    words: Set[str] = set()
    words |= _collect_from_highfreq()
    words |= _collect_from_synonyms()
    words |= _collect_from_lexicons()
    words |= _collect_from_anatomy_lexicon()
    words |= _collect_from_engine_region()
    words |= _collect_typo_correct_side()
    words |= _collect_common_particles()
    words |= _collect_from_en_zh_glossary()
    words |= _collect_frequent_fp_terms()

    # 过滤掉超长词组（>8字几乎不会是错别字）
    words = {w for w in words if len(w) <= 8}
    # 补充：放射报告高频单字结构词（见/示/余/等），这些字反复出现于
    # 「右肺上叶见磨玻璃结节」「余肺未见异常」等合法搭配中
    # 同时补入白名单缺失的高频医学用字，减少合法词组被误报
    words.update({
        # 结构词
        "见", "示", "余", "等", "仍", "尚",
        # 高频医学用字（出现在大量合法词组中，但作为单字不在词表中）
        "内", "实", "质", "系", "统",   # 实质/系统/内
        "形", "态",                     # 形态
        "大", "小",                     # 大小（非测量语境）
        "指", "十二",                   # 指肠/十二指肠
        # 通过测试报告误报分析补充
        "结", "节", "钙", "斑", "增", "宽", "钝", "灶",
        "移", "位", "廓", "旁", "隙", "裂", "鞍", "囊", "肿", "积", "窄",
        "水", "合", "均", "匀", "纹", "理", "脑", "眼", "髓",
        "血", "管", "腺", "淋", "巴", "密", "度", "信", "号", "强",
        "出", "梗", "阻", "塞", "缺", "乏", "坏", "死",
        "瘤", "转", "退", "化", "狭", "扩", "糜", "烂", "溃", "疡",
        "穿", "孔", "胸", "腹", "液", "炎", "症", "胀", "消", "失",
        "软", "硬", "固", "定", "术", "后", "前", "复", "查",
        "壁", "年", "龄", "居", "可", "薄", "局", "部", "弥", "漫", "散", "发",
        "中", "钝", "效", "变", "占", "应", "慢", "性",
    })
    # 报告结构词（段名/检查技术）
    words.update({
        "检查所见", "影像描述", "诊断印象", "诊断意见", "结论",
        "建议", "随访", "复查",
        "测量", "层面", "定位",
        "增强扫描", "多平面重建", "最大密度投影", "容积再现",
        "高分辨率", "多期扫描",
    })
    # 用户/反馈闭环学习词表（可编辑，医师审核后自动生效）
    words |= _user_words()
    return words


# 构建白名单（模块导入时执行一次，约 2000+ 条）
_WHITELIST: Set[str] = build_whitelist()
_WHITELIST_SORTED: List[str] = sorted(_WHITELIST, key=len, reverse=True)
_WHITELIST_RE = re.compile("|".join(re.escape(w) for w in _WHITELIST_SORTED))


def _rebuild() -> None:
    """用户词表变更后重建白名单索引（新增/删除即时生效）。"""
    global _WHITELIST, _WHITELIST_SORTED, _WHITELIST_RE
    _WHITELIST = build_whitelist()
    _WHITELIST_SORTED = sorted(_WHITELIST, key=len, reverse=True)
    _WHITELIST_RE = re.compile("|".join(re.escape(w) for w in _WHITELIST_SORTED))


def add_user_words(words) -> int:
    """把用户确认的词加入 user_whitelist.json 并即时生效。返回实际新增数。

    供反馈闭环（apply_delta）与未来 GUI 词表管理使用；重复词/非空串自动去重。
    """
    words = [w.strip() for w in words if w and isinstance(w, str) and w.strip()]
    if not words:
        return 0
    import paths as _paths
    p = _paths.user_lexicon_path()
    cur = list(_user_words())
    before = len(cur)
    for w in words:
        if w not in cur:
            cur.append(w)
    if len(cur) == before:
        return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    _rebuild()
    return len(cur) - before


def remove_user_words(words) -> int:
    """把词从 user_whitelist.json 移除并即时生效。返回实际移除数。"""
    rm = {w.strip() for w in words if w and isinstance(w, str) and w.strip()}
    if not rm:
        return 0
    import paths as _paths
    p = _paths.user_lexicon_path()
    cur = list(_user_words())
    kept = [w for w in cur if w not in rm]
    if len(kept) == len(cur):
        return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1)
    _rebuild()
    return len(cur) - len(kept)


def whitelist_size() -> int:
    """返回白名单词条数（用于监控/调试）"""
    return len(_WHITELIST)


def covered_spans(text: str) -> List[Tuple[int, int]]:
    """返回文本中被白名单完全覆盖的区间列表（仅多字词，≥2字）。

    只用长度≥2的词做覆盖判断：单字白名单只用于逐字符过滤（find_suspicious_segments），
    不用作区间覆盖。否则"锐力""正长"这类单字虽在白名单、但组合不在白名单的错字会被漏检。
    """
    if not text:
        return []
    raw = []
    for m in _WHITELIST_RE.finditer(text):
        s, e = m.start(), m.end()
        if e - s >= 2:  # 只用多字词做覆盖
            raw.append((s, e))
    # 按 (start, -length) 排序 → 先保留靠前且更长的
    raw.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    result: List[Tuple[int, int]] = []
    for s, e in raw:
        # 如果已被某个已保留的区间完全覆盖，跳过
        if any(rs <= s and e <= re for rs, re in result):
            continue
        result.append((s, e))
    return sorted(result)


def _covered_positions(text: str) -> List[bool]:
    """返回每个字符位置是否被【某个 ≥2 字白名单词】覆盖（允许重叠匹配）。

    与 covered_spans 的区别：covered_spans 用单一交替正则 finditer（非重叠），
    长词会抢占短词区间，导致跨词边界字符（如「小脑及脑干未见」中「干」同时
    属于「脑干」）漏覆盖。本函数逐词独立 finditer 再并集，覆盖判定更完整，
    用于滑窗「每字符属于某合法词即豁免」的判定。
    """
    covered = [False] * len(text)
    for w in _WHITELIST_SORTED:
        if len(w) < 2:
            continue
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            for pos in range(i, i + len(w)):
                covered[pos] = True
            start = i + 1
    return covered


def _is_covered_by_whitelist(text: str, start: int, end: int) -> bool:
    """检查 [start, end) 是否被白名单中的某个词完全覆盖"""
    for s, e in covered_spans(text):
        if s <= start and end <= e:
            return True
    return False


def find_suspicious_segments(
    text: str,
    min_len: int = 2,
    max_len: int = 4,
) -> List[Tuple[int, int, str]]:
    """在文本中查找可疑中文片段（不在医学白名单中、且含未被白名单覆盖的字）。

    算法：
    1. 先用 covered_spans() 获取白名单覆盖区间
    2. 对每个 2~5 字中文滑窗：
       a. 滑窗本身在白名单中 → 合法，跳过
       b. 滑窗中每个字符都被白名单覆盖区间覆盖 → 可能是合法词组的子部分，跳过
       c. 否则 → 标记为可疑（含未覆盖字符）
    3. 去重叠（长覆盖短）

    设计理由：「部明显」的「部」未被覆盖 → 可疑；
    「肺上叶见」每个字都被覆盖 → 不报。
    """
    if not text:
        return []

    # 白名单覆盖区间（非重叠，供 c2 二次确认）与重叠覆盖位图（供 b 每字符豁免）
    covered = covered_spans(text)
    _pos_covered = _covered_positions(text)

    # 提取所有中文连续段（>= min_len）
    cjk = re.compile(r"[一-鿿]{" + str(min_len) + r",}")
    suspicious: List[Tuple[int, int, str]] = []

    for m in cjk.finditer(text):
        s0, e0 = m.start(), m.end()
        run = m.group()

        for i in range(len(run)):
            found_suspicious = False
            for ln in range(max_len, min_len - 1, -1):
                if i + ln > len(run):
                    continue
                sub = run[i:i + ln]
                ss, se = s0 + i, s0 + i + ln

                # a. 滑窗本身在白名单中 → 合法
                if sub in _WHITELIST:
                    continue

                # b. 滑窗中每个字符都被白名单【≥2字】词覆盖（允许重叠，如「小脑及
                #    脑干未见」中「干」同时属于「脑干」）或为结构虚词（见/示/余/等/
                #    仍/尚，如「盆腔见子」的「见」）→ 合法词组的子部分。
                # 注意：普通单字白名单【不】豁免——「大膀胱充」的 大/充 非结构虚词且
                #    无 ≥2 字词覆盖，属滑窗碎片仍标记（此前用单字豁免导致「膀胱充盈
                #    正常」被滑窗切出大量碎片误报）。
                _struct_single = {"见", "示", "余", "等", "仍", "尚",
                                 "无", "未", "及", "与", "或", "并", "已",
                                 "之", "其", "该", "此", "多", "少",
                                 "约", "内", "外", "上", "下", "前", "后",
                                 "左", "右", "中", "时", "间", "年", "月",
                                 "日", "内径", "大小", "正常",
                                 # 单字后缀量词/结构字（covered_spans 仅覆盖 ≥2 字词，
                                 # 这些单字在 BI-RADS 4类/胸壁/腔隙灶/异常信号 等边界出现）
                                 "类", "级", "型", "度", "性", "灶", "壁", "管",
                                 "影", "信", "数", "腔", "位", "层", "区", "域",
                                 "见", "于", "为", "有", "在", "伴", "可",
                                 # 数字/量词（见一占位灶/约8mm/两枚/三个）
                                 "一", "两", "三", "四", "五", "六", "七",
                                 "八", "九", "十", "枚", "个", "每"}
                all_covered = all(
                    _pos_covered[pos] or text[pos] in _struct_single
                    for pos in range(ss, se)
                )
                if all_covered:
                    continue

                # c1. 二元组频率过滤：如果窗口内所有相邻二元组都是高频合法组合，
                #     则可能是合法文本的碎片（如"未见明异常"各字单独覆盖但整体是缺字），跳过
                if _COMMON_BIGRAMS and len(sub) >= 3:
                    window_bigrams = {sub[j:j + 2] for j in range(len(sub) - 1)}
                    if window_bigrams.issubset(_COMMON_BIGRAMS):
                        continue

                # c2. 二次确认：如果单字白名单已覆盖（b阶段跳过），但二元组非全高频，
                #     再用多字词覆盖区间验证。避免"锐力""正长"等2字换字/换序被单字白名单漏检。
                all_covered_mc = all(
                    any(cs <= pos < ce for cs, ce in covered)
                    for pos in range(ss, se)
                )
                if all_covered_mc:
                    continue

                # d. 可疑：不在白名单且含未覆盖字符，或含低频二元组
                suspicious.append((ss, se, sub))
                found_suspicious = True
                break

    # 去重叠（长优先）：有重叠的只保留最长那条
    # 使用 any-overlap 而非 containment，避免跨词表边界产生的碎片化告警
    result: List[Tuple[int, int, str]] = []
    for s, e, seg in suspicious:
        if any(rs < e and s < re for rs, re, _ in result):
            continue
        result.append((s, e, seg))

    return result
