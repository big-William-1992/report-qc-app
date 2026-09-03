# -*- coding: utf-8 -*-
"""反馈闭环一次性迁移（P2 修复）：385 条积压 + 已审记录 → 新 JSON 词表
================================================================================
背景：反馈闭环原用「文本改写 medical_whitelist.py 源码」（_patch_whitelist）应用
审核结果，其 marker 与现代码不匹配导致 added 静默 no-op。本次迁移：

1. reviewed.json（24 条已审）：
   - verdict=y（真错字）→ 调 engine.learn_typo 写入 rules_config.json typos
   - verdict=n（误报）→ 写入 user_whitelist.json（抑制今后误报）
   - verdict=s（跳过）→ 不动
2. whitelist_delta.json 的 add 16 条 → 去重后写入 user_whitelist.json
3. pending.json（385 条未审）自动分流：
   - seg 有读音/形近正确候选（segment_candidates 命中）→ 留在 pending 待人工审核
   - seg 无候选（新词/合法组合，多为滑窗碎片）→ 自动写入 user_whitelist 抑制误报
     （R19-WHITELIST 的语义即「不在任何词表 → 标记」，被标记片段经一次审核
     确认非错字后补入白名单是既定闭环；无候选片段无修正价值，自动补入合理）

用法：
  python3 tools/migrate_feedback.py --dry-run    # 预览（默认）
  python3 tools/migrate_feedback.py --write      # 实际迁移（先备份）
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from typing import List

# 项目根 / src 入 path（脚本从项目根运行）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import engine  # noqa: E402
import medical_whitelist  # noqa: E402
import feedback_collector as fb  # noqa: E402


def _backup(files: List[str]) -> str:
    """迁移前备份反馈数据文件，返回备份目录。"""
    bak = os.path.join(fb.DATA_DIR, "backup_before_migrate")
    os.makedirs(bak, exist_ok=True)
    for name in files:
        src = os.path.join(fb.DATA_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(bak, name))
    return bak


def _load_corpus_words() -> set:
    """从正常报告语料（data/processed/test_reports.json，has_typo=False）统计
    「独立成词」片段：前后字符均非中文（标点/空白/段首段尾）。

    关键：滑窗切出的碎片（如「叶见磨玻」是「右肺上叶见磨玻璃结节」的滑窗产物）
    永远不独立成词，但会作为 covered span 屏蔽同音层对内部真错字（磨玻离→磨玻璃）
    的检测——因此**只有独立成词的片段才允许自动补入白名单**。
    """
    import re as _re
    path = os.path.join(ROOT, "data", "processed", "test_reports.json")
    standalone = {}
    try:
        with open(path, encoding="utf-8") as f:
            reports = json.load(f)
        cjk = _re.compile(r"[一-鿿]+")
        for r in reports:
            # 注意：has_typo 在生成器中是字符串 'True'/'False'，须按字符串判断
            if str(r.get("has_typo", "")).lower() == "true":
                continue
            text = r.get("text") or r.get("report_text") or ""
            for run in cjk.findall(text):
                n = len(run)
                for i in range(n):
                    for L in (4, 3, 2):
                        if i + L > n:
                            continue
                        w = run[i:i + L]
                        # 独立成词：该词在原文中前后都不是中文
                        if _is_standalone(text, run, i, L):
                            standalone[w] = standalone.get(w, 0) + 1
    except Exception as _e:
        print(f"[WARN] 语料加载失败（{_e}），自动补入降级为空（全部留审）")
        return set()
    return set(standalone)


def _is_standalone(text: str, run: str, i: int, L: int) -> bool:
    """片段在原文中前后均非中文（独立成词）才算合法补入候选。"""
    # 定位 run 在 text 中的起点
    base = text.find(run)
    while base != -1:
        s, e = base + i, base + i + L
        pre = text[s - 1] if s > 0 else ""
        post = text[e] if e < len(text) else ""
        if not _re_cjk_char(pre) and not _re_cjk_char(post):
            return True
        nxt = text.find(run, base + 1)
        if nxt == -1:
            break
        base = nxt
    return False


_re_cjk_char = re.compile(r"[一-鿿]").match





def _is_real_typo_like(seg: str) -> bool:
    """seg 是否有读音/形近正确候选（是则可能是真错字，留人工审核）。"""
    try:
        from highfreq_lexicon import segment_candidates
        hit, cand = segment_candidates(seg, "medium")
        return bool(hit and cand)
    except Exception:
        return False


def _contains_typo_candidate(seg: str) -> bool:
    """片段是否含「疑似错字」：任 2-4 字子段与某高频正确词读音相似。

    若有（如「叶见磨玻」含「磨玻离」→「磨玻璃」），该片段绝不可进白名单：
    白名单加入的片段会成为 covered span，同音层会跳过 covered span 内的
    子段（R19-HOMOPHONE 的白名单覆盖逻辑），导致真错字「磨玻离」被屏蔽漏检。
    """
    try:
        from highfreq_lexicon import segment_candidates, highfreq_words, _word_pinyin
        hf_set = {w for w, _c in highfreq_words()}
        n = len(seg)
        for i in range(n):
            for L in (4, 3, 2):
                if i + L > n:
                    continue
                sub = seg[i:i + L]
                if sub in hf_set:
                    continue  # 本身是正确词（如「考虑」），可补入
                hit, cand = segment_candidates(sub, "medium")
                if hit and cand:
                    return True
    except Exception:
        return False
    return False


def migrate(dry_run: bool = True) -> dict:
    corpus_words = _load_corpus_words()
    reviewed = fb._load_json(fb.REVIEWED_FILE) if os.path.exists(fb.REVIEWED_FILE) else []
    pending = fb._load_json(fb.PENDING_FILE) if os.path.exists(fb.PENDING_FILE) else []
    delta = fb._load_json(fb.DELTA_FILE) if os.path.exists(fb.DELTA_FILE) else {"add": [], "remove": []}

    # ---- 1) reviewed.json 已审记录 ----
    learn_ok = learn_skip = 0
    wl_reviewed: List[str] = []
    for p in reviewed:
        seg = (p.get("seg") or "").strip()
        verdict = p.get("verdict")
        if verdict == "y" and seg:
            sug = (p.get("suggestion") or "").strip()
            if sug and seg != sug:
                if engine.learn_typo(seg, sug):
                    learn_ok += 1
                else:
                    learn_skip += 1
            else:
                learn_skip += 1  # y 但无建议词：无法学
        elif verdict == "n" and seg:
            wl_reviewed.append(seg)

    # ---- 2) whitelist_delta.json 的 add ----
    wl_delta = [w for w in delta.get("add", []) if w and isinstance(w, str)]

    # ---- 3) pending 自动分流 ----
    wl_auto: List[str] = []
    keep_pending: List[str] = []
    by_rule = Counter(p.get("rule_id") for p in pending)
    for p in pending:
        seg = (p.get("seg") or "").strip()
        if not seg:
            keep_pending.append(p["id"])
            continue
        # 保守分流（三层过滤，避免把真错字片段静默屏蔽）：
        # 1) 非 R19-WHITELIST（如 HOMOPHONE）→ 留审
        # 2) 带建议词（同音层确认疑似错字）→ 留审
        # 3) 在正常报告语料中不成词（滑窗碎片，如「见磨玻离」）→ 留审
        # 仅同时满足「WHITELIST 层 + 无建议词 + 语料成词」才自动补入。
        if p.get("rule_id") != "R19-WHITELIST" or (p.get("suggestion") or "").strip():
            keep_pending.append(p["id"])
        elif seg not in corpus_words:
            keep_pending.append(p["id"])
        else:
            wl_auto.append(seg)

    # 去重合并（三条来源统一过防线）
    to_add = list(dict.fromkeys(wl_reviewed + wl_delta + wl_auto))
    to_add = [w for w in to_add if len(w) <= 8]  # 与 build_whitelist 的 ≤8 字过滤一致
    # 关键防线（对已审/增量词同样生效，避免历史审核结论屏蔽同音层检测）：
    # 1) 含错字候选（读音/形近）的片段绝不入白名单（会屏蔽同音层检测）
    # 2) 非独立成词的滑窗碎片（如上叶见磨）不入白名单（同样屏蔽内部真错字）
    typo_like = [w for w in to_add if _contains_typo_candidate(w)]
    fragments = [w for w in to_add if w not in typo_like and w not in corpus_words]
    to_add = [w for w in to_add if w not in typo_like and w not in fragments]

    print("=" * 64)
    print("反馈闭环迁移预览")
    print("=" * 64)
    print(f"pending 总数: {len(pending)}（{dict(by_rule)}）")
    print(f"  → 有正确候选、留人工审核: {len(keep_pending)} 条")
    print(f"  → 无候选、自动补入白名单: {len(wl_auto)} 条")
    print(f"reviewed: y(学入 typos)={learn_ok}, y(无建议词跳过)={learn_skip}, n(补白名单)={len(wl_reviewed)}, s=忽略")
    print(f"delta add 条数: {len(wl_delta)}")
    print(f"含错字候选、拒绝入白名单: {len(typo_like)} 条（{typo_like[:6]}...）")
    print(f"非独立成词滑窗碎片、拒绝入白名单: {len(fragments)} 条（{fragments[:6]}...）")
    print(f"合计补入白名单（去重+过滤后）: {len(to_add)} 条")
    for w in to_add[:30]:
        print(f"  + {w}")
    if len(to_add) > 30:
        print(f"  ... 其余 {len(to_add) - 30} 条")
    print(f"迁移后 whitelist_size 预期: {medical_whitelist.whitelist_size()} + {len(to_add)} = "
          f"{medical_whitelist.whitelist_size() + len(to_add)}")

    if dry_run:
        print("\n[DRY RUN] 未写入任何文件。加 --write 实际迁移。")
        return {"dry_run": True, "to_add": to_add, "keep_pending": keep_pending}

    # ---- 实际写入 ----
    bak = _backup(["pending.json", "reviewed.json", "whitelist_delta.json"])
    added = medical_whitelist.add_user_words(to_add)
    # 已分流/已迁移的 pending 移出队列（保留留审部分）
    pending_kept = [p for p in pending if p["id"] in set(keep_pending)]
    fb._save_json(fb.PENDING_FILE, pending_kept)
    fb._save_json(fb.REVIEWED_FILE, [])
    fb._save_json(fb.DELTA_FILE, {"add": [], "remove": []})
    print(f"\n[OK] 已补入白名单 {added} 条（去重后）。")
    print(f"[OK] pending 剩余 {len(pending_kept)} 条待人工审核。")
    print(f"[OK] reviewed/delta 已清空。备份目录: {bak}")
    return {"dry_run": False, "added": added, "pending_kept": len(pending_kept)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="反馈闭环一次性迁移")
    ap.add_argument("--write", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()
    migrate(dry_run=not args.write)
