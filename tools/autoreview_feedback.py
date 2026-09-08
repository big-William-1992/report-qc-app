# -*- coding: utf-8 -*-
"""反馈积压自动分流（一次性批处理，走正式审核通道）
================================================================================
背景：引擎修复（滑窗碎片过滤 + 白名单扩充）后，历史 pending 大部分已不再被标记。
本脚本用当前引擎复检全部 pending，按结果走 feedback_collector 正式审核通道：

1. 复检已不再被 R19 标记（引擎修复解决）→ verdict=n（误报），seg 补入白名单
2. 复检仍被标记 且 带建议词（同音层确认真错字）→ verdict=y（学入 R8 错字表）
3. 复检仍被标记 且 无建议词（疑似真错字待人工确认）→ 留在 pending

完成后 apply_delta 应用白名单增量，并打印 R19 精确率统计。

用法：
  python3 tools/autoreview_feedback.py            # dry-run 预览
  python3 tools/autoreview_feedback.py --write    # 实际审核 + 应用
"""
import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import feedback_collector as fb  # noqa: E402
from engine import RuleEngine  # noqa: E402
from medical_whitelist import _WHITELIST  # noqa: E402


def _still_flagged(p, eng) -> bool:
    """复检该 pending 条目是否仍被 R19 标记（用其 report_snippet 重跑）。"""
    text = p.get("report_snippet") or ""
    if not text:
        return True  # 无上下文，保守留审
    seg = p.get("seg") or ""
    fs = eng.run(text, {})
    return any(seg in (f.snippet or "") for f in fs if "R19" in f.rule_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="实际执行（默认 dry-run）")
    args = ap.parse_args()

    eng = RuleEngine()
    pending = fb.get_pending(limit=0)
    print(f"pending 总数: {len(pending)}")

    to_n = []   # 复检干净 且 无错字候选 → 误报补白名单
    to_y = []   # 带建议词 → 学入 R8
    keep = []   # 仍标记 或 含错字候选 → 留人工

    from highfreq_lexicon import segment_candidates

    def _has_typo_candidate(seg: str) -> bool:
        """片段或其 2-4 字子串是否有读音/形近正确候选（含则可能是真错字，不可补白名单）。"""
        n = len(seg)
        for i in range(n):
            for L in (4, 3, 2):
                if i + L <= n:
                    try:
                        hit, cand = segment_candidates(seg[i:i + L], "medium")
                        if hit and cand:
                            return True
                    except Exception:
                        pass
        return False

    # 独立成词判定：滑窗碎片（如『居忠未见』=居忠|未见）横跨词边界，
    # 补入白名单会屏蔽相邻真错字，故仅允许『正常语料中独立成词』的片段自动补入
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        from migrate_feedback import _load_corpus_words
        corpus_words = _load_corpus_words()
    except Exception:
        corpus_words = set()

    for p in pending:
        seg = p.get("seg") or ""

        # 新增：检查 R8 词典是否已覆盖该 seg
        # 含精确匹配和双向子串：
        #   seg="主肪" ⊂ "主动肪"(R8) → R8 覆盖
        #   seg="界部清楚" ⊃ "界部"(R8) → seg 包含 R8 错字
        typo_map = eng.rules_config.get("typos", {})
        r8_covered = (
            seg in typo_map or
            any(seg in w for w in typo_map if len(w) >= 2) or      # seg 是 R8 条目的子串
            any(w in seg for w in typo_map if len(w) >= 2)          # seg 包含 R8 条目
        )

        if r8_covered:
            # R8 已收录 → 确认真错字，标记 y
            to_y.append(p)
        elif not _still_flagged(p, eng) and seg in _WHITELIST:
            # 已在白名单 且 不再被标记 → 误报，直接放行
            to_n.append(p)
        elif not _still_flagged(p, eng) and not _has_typo_candidate(seg) and seg in corpus_words:
            # 不在白名单但已不标记 且 无错字候选 且 语料中独立成词 → 补入
            to_n.append(p)
        elif (p.get("suggestion") or "").strip():
            to_y.append(p)
        else:
            keep.append(p)

    print(f"→ 误报补白名单 (n): {len(to_n)}（复检干净且无错字候选）")
    print(f"→ 真错字学入 R8 (y): {len(to_y)}")
    print(f"→ 留人工审核: {len(keep)}")

    if args.write:
        # 逐条走正式审核通道（n→delta add，y→learn_typo）
        for p in to_n:
            fb.review_one(p["id"], "n", "引擎修复后复检已不再标记（自动分流）")
        for p in to_y:
            fb.review_one(p["id"], "y", "同音层建议词自动确认（自动分流）")
        # 应用白名单增量
        added, removed = fb.apply_delta(dry_run=False)
        print(f"\n[OK] 已审核 {len(to_n) + len(to_y)} 条；白名单补入 {len(added)} 条")
        print(f"[OK] pending 剩余 {len(fb.get_pending())} 条待人工审核")
    else:
        print("\n[DRY RUN] 未写入。加 --write 实际执行。")
        # 预览 delta 规模
        sug = [p["suggestion"] for p in to_y]
        print(f"  将学入 R8 的建议词: {sug}")


if __name__ == "__main__":
    main()
