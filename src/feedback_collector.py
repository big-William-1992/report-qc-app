"""R19 误报/漏报反馈收集管道
================================================================================
设计目标：把引擎 R19 检测结果收集起来，形成「检测 → 审核 → 词表更新」的闭环。

工作流：
  1. 引擎每次 run 后，调用 collect() 把 R19 告警写入 pending.json
  2. 审核者运行 review()，逐条标注 y（真错字）/ n（误报）/ s（跳过）
  3. 标注结果写入 reviewed.json，同时生成 whitelist_delta.json（待补入/待移除）
  4. apply_delta() 把确认的词更新到白名单

数据结构：
  data/feedback/
  ├── pending.json      # [{id, text, rule_id, seg, suggestion, timestamp, context}]
  ├── reviewed.json     # [{id, verdict, note, reviewed_at}]
  └── whitelist_delta.json  # {"add": [...], "remove": [...]}
"""
from __future__ import annotations

import json
import os
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple

# 默认数据目录（统一由 paths.py 解析：源码 → 项目 data/feedback；frozen → 用户数据目录）
import paths
DATA_DIR = paths.feedback_data_dir()
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")
REVIEWED_FILE = os.path.join(DATA_DIR, "reviewed.json")
DELTA_FILE = os.path.join(DATA_DIR, "whitelist_delta.json")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _load_json(path: str) -> list | dict:
    if not os.path.exists(path):
        return [] if path != DELTA_FILE else {"add": [], "remove": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. 收集（由引擎调用）
# ---------------------------------------------------------------------------
def collect(findings: List[Any], report_text: str, context: Optional[dict] = None) -> int:
    """把 R19 告警写入待审核队列。

    Args:
        findings: engine.run() 返回的 Finding 列表
        report_text: 原始报告文本（用于审核时回显上下文）
        context: 额外元数据（患者ID、检查类型等）

    Returns:
        新增的待审核条数
    """
    _ensure_dir()
    pending = _load_json(PENDING_FILE)
    existing_keys = {(p["text_hash"], p["seg"]) for p in pending}
    new_count = 0

    for f in findings:
        if "R19" not in getattr(f, "rule_id", ""):
            continue
        key = (_hash(report_text), f.snippet or f.message[:20])
        if key in existing_keys:
            continue
        pending.append({
            "id": f"r19_{_hash(report_text + f.snippet)}_{len(pending)}",
            "text_hash": _hash(report_text),
            "report_snippet": report_text[:120],
            "rule_id": f.rule_id,
            "error_type": f.error_type,
            "seg": f.snippet or "",          # 可疑片段
            "suggestion": f.suggestion or "", # 建议的正确词（R19-HOMOPHONE 有值）
            "message": f.message,
            "span": list(f.span),            # [start, end]
            "severity": f.severity,
            "timestamp": _now(),
            "context": context or {},
            "verdict": None,                 # y=真错字, n=误报, s=跳过
            "note": "",
            "reviewed_at": None,
        })
        new_count += 1

    _save_json(PENDING_FILE, pending)
    return new_count


def get_pending(limit: int = 0) -> List[Dict]:
    """获取待审核列表（按时间倒序）"""
    _ensure_dir()
    pending = _load_json(PENDING_FILE)
    unreviewed = [p for p in pending if p["verdict"] is None]
    unreviewed.sort(key=lambda x: x["timestamp"], reverse=True)
    if limit > 0:
        unreviewed = unreviewed[:limit]
    return unreviewed


def get_stats() -> Dict[str, int]:
    """统计反馈数据"""
    _ensure_dir()
    pending = _load_json(PENDING_FILE)
    reviewed = _load_json(REVIEWED_FILE)
    total = len(pending) + len(reviewed)
    return {
        "total": total,
        "pending": sum(1 for p in pending if p["verdict"] is None),
        "reviewed": len(reviewed),
        "true_typo": sum(1 for p in reviewed if p.get("verdict") == "y"),
        "false_positive": sum(1 for p in reviewed if p.get("verdict") == "n"),
        "skipped": sum(1 for p in reviewed if p.get("verdict") == "s"),
    }


# ---------------------------------------------------------------------------
# 2. 审核（人工）
# ---------------------------------------------------------------------------
def review_one(item_id: str, verdict: str, note: str = "") -> bool:
    """审核单条告警。

    Args:
        item_id: pending.json 中的 id
        verdict: "y"=真错字, "n"=误报, "s"=跳过
        note: 审核备注

    Returns:
        是否成功
    """
    _ensure_dir()
    pending = _load_json(PENDING_FILE)
    reviewed = _load_json(REVIEWED_FILE)

    for p in pending:
        if p["id"] == item_id:
            p["verdict"] = verdict
            p["note"] = note
            p["reviewed_at"] = _now()
            reviewed.append(p)
            pending.remove(p)
            _save_json(PENDING_FILE, pending)
            _save_json(REVIEWED_FILE, reviewed)
            _update_delta(p, verdict)
            return True
    return False


def _update_delta(item: Dict, verdict: str):
    """根据审核结果更新 whitelist_delta（语义与闭环一致）：
    - n（误报）→ seg 补入白名单（apply_delta 时写入 user_whitelist.json）
    - y（真错字）→ 建议词直接学习进 rules_config typos（engine.learn_typo），
      不再经 delta 中转（此前 y/n 都塞 delta["add"]，语义错乱）
    - s（跳过）→ 不动
    """
    seg = item.get("seg", "").strip()
    if not seg:
        return

    if verdict == "n":
        delta = _load_json(DELTA_FILE)
        if seg not in delta["add"]:
            delta["add"].append(seg)
        _save_json(DELTA_FILE, delta)
    elif verdict == "y":
        sug = item.get("suggestion", "").strip()
        if sug and seg != sug:
            try:
                import engine
                engine.learn_typo(seg, sug)
            except Exception:
                pass  # 学习失败不影响审核流程


# ---------------------------------------------------------------------------
# 3. 应用 delta 到白名单
# ---------------------------------------------------------------------------
def apply_delta(dry_run: bool = True) -> Tuple[List[str], List[str]]:
    """把 whitelist_delta.json 中的词应用到白名单。

    Args:
        dry_run: True=只打印不写入，False=实际修改 medical_whitelist.py

    Returns:
        (added, removed) 两个列表
    """
    delta = _load_json(DELTA_FILE)
    added = [w for w in delta.get("add", []) if w]
    removed = [w for w in delta.get("remove", []) if w]

    if dry_run:
        print(f"[DRY RUN] 待补入白名单: {len(added)} 条")
        for w in added[:20]:
            print(f"  + {w}")
        if len(added) > 20:
            print(f"  ... 还有 {len(added) - 20} 条")
        if removed:
            print(f"[DRY RUN] 待移除: {len(removed)} 条")
            for w in removed[:10]:
                print(f"  - {w}")
        return added, removed

    # 实际写入 user_whitelist.json（medical_whitelist.add_user_words/remove_user_words，
    # 取代此前文本改写 medical_whitelist.py 源码的脆弱机制——marker 不匹配会静默 no-op）
    import medical_whitelist
    n_add = medical_whitelist.add_user_words(added)
    n_rm = medical_whitelist.remove_user_words(removed)
    print(f"[OK] 已补入白名单 {n_add} 条、移除 {n_rm} 条（user_whitelist.json）")

    # 清空 delta
    delta["add"] = []
    delta["remove"] = []
    _save_json(DELTA_FILE, delta)
    return added, removed


# ---------------------------------------------------------------------------
# 4. 交互式审核界面
# ---------------------------------------------------------------------------
def review_interactive(limit: int = 0):
    """交互式审核：逐条显示待审核告警，输入 y/n/s/q。

    y = 真错字（检测正确）
    n = 误报（应补入白名单）
    s = 跳过（不确定）
    q = 退出
    """
    _ensure_dir()
    pending = get_pending(limit=limit)

    if not pending:
        print("没有待审核的告警。")
        return

    stats = get_stats()
    print(f"=== R19 反馈审核 ({stats['pending']} 条待审，已审 {stats['reviewed']} 条) ===\n")

    for i, item in enumerate(pending, 1):
        print(f"{'=' * 60}")
        print(f"[{i}/{len(pending)}] ID: {item['id']}")
        print(f"  规则: {item['rule_id']} ({item['error_type']})")
        print(f"  可疑片段: 「{item['seg']}」")
        if item["suggestion"]:
            print(f"  建议修正: 「{item['suggestion']}」")
        print(f"  告警内容: {item['message']}")
        print(f"  报告片段: {item['report_snippet']}")
        print(f"  时间: {item['timestamp']}")
        print()
        verdict = input("审核 [y=真错字 n=误报 s=跳过 q=退出]: ").strip().lower()
        if verdict == "q":
            break
        if verdict not in ("y", "n", "s"):
            print("  无效输入，跳过。")
            continue
        note = input("  备注(可选): ").strip()
        review_one(item["id"], verdict, note)
        print(f"  ✓ 已记录: {verdict}\n")

    # 最终统计
    stats = get_stats()
    print(f"\n=== 审核完成 ===")
    print(f"  待审核: {stats['pending']}")
    print(f"  已审核: {stats['reviewed']}")
    print(f"  真错字: {stats['true_typo']}")
    print(f"  误报: {stats['false_positive']}")
    print(f"  跳过: {stats['skipped']}")


# ---------------------------------------------------------------------------
# 5. 报告生成
# ---------------------------------------------------------------------------
def generate_report() -> str:
    """生成反馈数据汇总报告（Markdown 格式）"""
    _ensure_dir()
    stats = get_stats()
    pending = get_pending()
    reviewed = _load_json(REVIEWED_FILE)

    lines = [
        "# R19 反馈报告",
        f"\n生成时间: {_now()}",
        f"\n## 统计",
        f"- 总告警数: {stats['total']}",
        f"- 待审核: {stats['pending']}",
        f"- 已审核: {stats['reviewed']}",
        f"  - 真错字: {stats['true_typo']}",
        f"  - 误报: {stats['false_positive']}",
        f"  - 跳过: {stats['skipped']}",
    ]

    if stats["total"] > 0:
        accuracy = stats["true_typo"] / stats["total"] * 100
        lines.append(f"- R19 准确率: {accuracy:.1f}%")

    if reviewed:
        lines.append(f"\n## 最近审核记录")
        for p in reviewed[-10:]:
            verdict = {"y": "✓ 真错字", "n": "✗ 误报", "s": "? 跳过"}.get(p["verdict"], p["verdict"])
            lines.append(f"- 「{p['seg']}」 {verdict} — {p.get('note', '')}")

    if pending:
        lines.append(f"\n## 待审核（前10条）")
        for p in pending[:10]:
            lines.append(f"- [{p['rule_id']}] 「{p['seg']}」 {p['message'][:50]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python feedback_collector.py stats          # 查看统计")
        print("  python feedback_collector.py review [N]     # 交互式审核前N条")
        print("  python feedback_collector.py pending [N]    # 查看待审核")
        print("  python feedback_collector.py apply --dry    # 预览白名单更新")
        print("  python feedback_collector.py apply --write  # 写入白名单")
        print("  python feedback_collector.py report         # 生成Markdown报告")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "stats":
        s = get_stats()
        for k, v in s.items():
            print(f"  {k}: {v}")

    elif cmd == "pending":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        items = get_pending(limit=limit)
        for p in items:
            print(f"  [{p['rule_id']}] 「{p['seg']}」 {p['message'][:50]}")

    elif cmd == "review":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        review_interactive(limit=limit)

    elif cmd == "apply":
        dry = "--write" not in sys.argv
        apply_delta(dry_run=dry)

    elif cmd == "report":
        print(generate_report())
        # 同时保存为文件
        report_path = os.path.join(DATA_DIR, "report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(generate_report())
        print(f"\n报告已保存: {report_path}")

    else:
        print(f"未知命令: {cmd}")
