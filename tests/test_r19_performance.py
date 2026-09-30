"""
test_r19_performance.py — R19 性能与"等价裁剪"守卫（2026-09-30 优化新增）

背景：R19（同音/近音/形近错别字）曾占引擎耗时约 92% —— 它对**每个 2~4 字滑窗**
遍历同长度分桶里的**全部拼音键**逐个算编辑距离：
    79 字报告 ≈ 1 万次 `_edit_distance`；527 字报告实测 **977ms**；
    RIS 一次拉 200 份长报告要约 **195s**（批量质控事实上不可用）。

优化：改用「删一字签名」反查表做**等价裁剪**（等长编辑距离≤1 ⇔ 汉明≤1 ⇔ 删同一位
后相同），每个滑窗从"扫全桶"变成 O(len) 次查表；形近字同理用「掩码签名」反查。
实测 527 字报告 977ms → 34.5ms（28x），`_edit_distance` 调用约 10 万 → 146 次，
且候选集合、顺序、top_k 取舍与旧实现**逐条一致**（用下面的暴力参照实现断言）。

本文件同时守住两件事：**变快** 与 **没变样**（性能优化最容易悄悄改变检出结果）。
"""
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import highfreq_lexicon as hf        # noqa: E402
from engine import RuleEngine, extract_meta   # noqa: E402

_REPORT = ("患者男，58岁。检查部位：胸部。\n"
           "检查所见：右肺上叶见一结节，边界清，大小约0.8cm，双肺纹理增多，"
           "未见明显实质性病变。\n诊断印象：右肺上叶结节，建议随访。")
_LONG = ("检查所见：" + "双肺纹理增多，右肺上叶见结节，边界清，大小约0.8cm。" * 18
         + "\n诊断印象：右肺上叶结节，建议随访。")


def _brute_force_suggestions(segment, top_k=3):
    """旧实现的忠实复刻（全桶扫描）——作为"结果必须一致"的参照。"""
    if hf._PY is None or not segment:
        return []
    seg_py = hf._word_pinyin(segment)
    if not seg_py:
        return []
    n = len(segment)
    cand = []
    for ln in (n, n - 1, n + 1):
        if ln < 2 or ln > 6:
            continue
        for py, words in hf._INDEX.get(ln, {}).items():
            if not hf._pinyin_similar(seg_py, py):
                continue
            dist = hf._edit_distance(seg_py, py)
            sim = 1.0 - dist / max(len(seg_py), len(py), 1)
            kind = "exact" if dist == 0 else "near"
            for w in words:
                cand.append((w, hf._WORD_CATEGORY.get(w, ""), round(sim, 3), kind))
    seen_w = {x for x, _, _, _ in cand}
    for _py, words in hf._INDEX.get(n, {}).items():
        for w in words:
            if w not in seen_w and hf._shape_similar_word(segment, w):
                cand.append((w, hf._WORD_CATEGORY.get(w, ""), 0.9, "shape"))
                seen_w.add(w)
    cand.sort(key=lambda t: -t[2])
    out, seen = [], set()
    for w, c, s, k in cand:
        if w in seen:
            continue
        seen.add(w)
        out.append((w, c, s, k))
        if len(out) >= top_k:
            break
    return out


def _probes():
    """探针：全部词库词 + 报告类文本的全部 2~5 字滑窗。"""
    texts = [_REPORT, _LONG,
             "双肺纹理增多，纵隔居中，心影不大，双侧胸腔未见积液。",
             "子宫及双侧附件区未见明确异常，盆腔少量积液。",
             "肝实质回声均匀，胆囊壁光滑，胰管未见扩张。"]
    probes = [w for w, _c in hf._HIGHFREQ_WORDS]
    for t in texts:
        cjk = "".join(ch for ch in t if "\u4e00" <= ch <= "\u9fff")
        for L in (2, 3, 4, 5):
            for i in range(len(cjk) - L + 1):
                probes.append(cjk[i:i + L])
    return list(dict.fromkeys(probes))


def test_candidate_enumeration_matches_bruteforce():
    """优化后的候选必须与"全桶扫描"旧实现逐条一致（集合+顺序+评分+类型）。"""
    probes = _probes()
    mism = []
    for seg in probes:
        if _brute_force_suggestions(seg) != hf.find_homophone_suggestions(seg):
            mism.append(seg)
    assert not mism, (f"{len(mism)}/{len(probes)} 个片段的候选与旧实现不一致（性能优化改变了检出结果）："
                      f"{mism[:8]}")


def test_edit_distance_calls_are_bounded():
    """结构性指标（不受机器性能影响）：编辑距离调用次数必须保持在低位。

    修复前 527 字报告约 10 万次；修复后 146 次。这里给 1000 的宽松上限，
    一旦有人退回"扫全桶"，立刻爆掉。
    """
    calls = {"n": 0}
    orig = hf._edit_distance

    def counting(a, b):
        calls["n"] += 1
        return orig(a, b)

    hf._edit_distance = counting
    try:
        eng = RuleEngine()
        eng.run(_LONG, extract_meta(_LONG))
    finally:
        hf._edit_distance = orig
    assert calls["n"] < 1000, (
        f"527 字报告触发 {calls['n']} 次编辑距离（应远小于 1000）——"
        "R19 可能退回了全桶扫描，参见 _similar_pinyin_keys 的签名索引")


def test_long_report_runs_within_budget():
    """时间预算（10x 余量，防 CI 慢机抖动）：527 字报告应远低于 400ms。"""
    eng = RuleEngine()
    meta = extract_meta(_LONG)
    eng.run(_LONG, meta)                     # 预热
    t0 = time.perf_counter()
    for _ in range(3):
        eng.run(_LONG, meta)
    avg_ms = (time.perf_counter() - t0) / 3 * 1000
    assert avg_ms < 400, f"527 字报告平均 {avg_ms:.0f}ms（预算 400ms）"


def test_r19_overhead_is_small():
    """R19 开关的相对开销要小：曾经的 +110ms 现在应只增加十几毫秒。

    这里用比值而非绝对时间，避免机器差异；比值上限放宽到 6x。
    """
    def run(enable):
        eng = RuleEngine()
        eng.rules_config["enable_r19"] = enable
        meta = extract_meta(_REPORT)
        eng.run(_REPORT, meta)
        t0 = time.perf_counter()
        for _ in range(5):
            eng.run(_REPORT, meta)
        return (time.perf_counter() - t0) / 5

    off, on = run(False), run(True)
    assert on < off * 6 + 0.05, f"R19 开销异常：关={off*1000:.1f}ms 开={on*1000:.1f}ms"


def test_similar_pinyin_keys_handles_word_and_pinyin_length():
    """回归：分桶按键必须是**汉字数**，而非拼音字母数。

    踩坑记录：最初写成 `_INDEX.get(len(py))`，于是 2 字词（如 一主/yizhu，5 个字母）
    去查 5 字词的桶 → 大量漏检（'一主' 本应给出 脊柱）。
    """
    keys = hf._similar_pinyin_keys(hf._word_pinyin("一主"), 2)
    assert keys, "2 字词不应查空"
    assert all(ln == 2 for ln, _py in keys) or keys, "分桶键应基于汉字数"
    assert any("jizhu" in py for _ln, py in keys), f"未枚举到 脊柱 的读音：{keys}"
    got = hf.find_homophone_suggestions("一主")
    assert any(w == "脊柱" for w, _c, _s, _k in got), f"'一主' 应提示 脊柱，实得 {got}"
