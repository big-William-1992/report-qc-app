"""badcase_store.py — 医生反馈(badcase)回流存储层 (2026-08-25 P1-4)。

闭环设计:
    前端「误报👎 / 漏报➕」→ POST /api/v1/feedback → 本库
    → 月度 `tools/export_badcase_training.py` 导出 → 增量精调 / 规则调参

独立于 samples.db(不动既有 schema); 同目录存放便于诊断包一并导出。
纯标准库, 零依赖。
"""
import json
import os
import sqlite3
import datetime
from typing import List, Optional, Dict, Any

_DB_NAME = "feedback.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                -- ISO 时间戳
    feedback_type TEXT NOT NULL,     -- false_positive | missed | wrong_type | other
    report_text TEXT NOT NULL,
    rule_id TEXT,                    -- 误报时: 被质疑的规则; 漏报时可空
    engine_source TEXT,              -- rules | llm | fused (该发现来源)
    severity TEXT,
    message TEXT,                    -- 引擎原文案
    snippet TEXT,                    -- 引擎定位片段
    suggestion TEXT,                 -- 引擎建议修正
    user_note TEXT,                  -- 医生备注(漏报时尤其重要)
    user_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(feedback_type);
"""


def _db_path(path: Optional[str] = None) -> str:
    if path:
        return path
    try:
        from samplelib import db_path
        return os.path.join(os.path.dirname(db_path()), _DB_NAME)
    except Exception:
        # 兜底: 用户可写数据目录
        from log_utils import user_data_dir
        return os.path.join(user_data_dir(), _DB_NAME)


def _conn(path: Optional[str] = None) -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(path), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory = sqlite3.Row
    return c


def init_db(path: str = None) -> None:
    with _conn(path) as c:
        c.executescript(_SCHEMA)


_VALID_TYPES = {"false_positive", "missed", "wrong_type", "other"}


def record(data: Dict[str, Any], path: str = None) -> int:
    """写入一条反馈。返回行 id。

    必填: feedback_type, report_text
    选填: rule_id/engine_source/severity/message/snippet/suggestion/user_note/user_id
    """
    ftype = str(data.get("feedback_type", "")).strip()
    if ftype not in _VALID_TYPES:
        raise ValueError(f"feedback_type 必须是 {_VALID_TYPES} 之一")
    report = str(data.get("report_text", "")).strip()
    if not report:
        raise ValueError("report_text 不能为空")
    with _conn(path) as c:
        cur = c.execute(
            "INSERT INTO feedback(ts, feedback_type, report_text, rule_id,"
            " engine_source, severity, message, snippet, suggestion,"
            " user_note, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.datetime.now().isoformat(timespec="seconds"),
                ftype, report[:20000],
                str(data.get("rule_id") or "")[:64],
                str(data.get("engine_source") or "")[:16],
                str(data.get("severity") or "")[:16],
                str(data.get("message") or "")[:2000],
                str(data.get("snippet") or "")[:2000],
                str(data.get("suggestion") or "")[:2000],
                str(data.get("user_note") or "")[:2000],
                str(data.get("user_id") or "")[:64],
            ),
        )
        return cur.lastrowid


def list_recent(limit: int = 100, feedback_type: Optional[str] = None,
                path: str = None) -> List[dict]:
    q = "SELECT * FROM feedback"
    args: list = []
    if feedback_type:
        q += " WHERE feedback_type=?"
        args.append(feedback_type)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with _conn(path) as c:
        return [dict(r) for r in c.execute(q, args)]


def stats(path: str = None) -> dict:
    """按类型计数 + 最近7天数量(供驾驶舱展示)。"""
    with _conn(path) as c:
        by_type = {r[0]: r[1] for r in c.execute(
            "SELECT feedback_type, COUNT(*) FROM feedback GROUP BY feedback_type")}
        week_ago = (datetime.datetime.now()
                    - datetime.timedelta(days=7)).isoformat(timespec="seconds")
        recent = c.execute(
            "SELECT COUNT(*) FROM feedback WHERE ts >= ?", (week_ago,)).fetchone()[0]
    total = sum(by_type.values())
    return {"total": total, "by_type": by_type, "last_7d": recent}


def export_jsonl(out_path: str, path: str = None,
                 feedback_type: Optional[str] = None) -> int:
    """全量导出 JSONL(供精调管线消费)。返回条数。"""
    rows = list_recent(limit=10**9, feedback_type=feedback_type, path=path)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)
