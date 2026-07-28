"""
report_qc_app/src/samplelib.py
样本库：SQLite 持久化报告质控结果，支撑驾驶舱统计与样本管理
"""

import os
import sys
import sqlite3
import json
import datetime
import shutil


def _appdata_db() -> str:
    d = os.path.join(os.path.expandvars("%APPDATA%"),
                     "MedicalReportQC", "samples.db")
    return d


def db_path() -> str:
    if getattr(sys, "frozen", False):
        # 打包后：样本库放在用户可写目录，避免安装到 Program Files 后只读报错
        user_db = _appdata_db()
        if not os.path.exists(user_db):
            # 首次运行：从 exe 同级 assets/ 复制初始库到用户目录
            src = os.path.join(os.path.dirname(sys.executable), "assets", "samples.db")
            try:
                os.makedirs(os.path.dirname(user_db), exist_ok=True)
                if os.path.exists(src):
                    shutil.copyfile(src, user_db)
            except Exception:
                return src  # 兜底：仍用只读源
        return user_db
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "samples.db")


def init_db(path: str = None) -> None:
    path = path or db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                patient TEXT,
                gender TEXT,
                age TEXT,
                modality TEXT,
                applied_site TEXT,
                report_text TEXT,
                findings_json TEXT,
                scores_json TEXT
            )
        """)
        # 向后兼容：旧库无 laterality 列时追加（SQLite 不支持 ADD COLUMN IF NOT EXISTS）
        try:
            conn.execute("ALTER TABLE samples ADD COLUMN laterality TEXT")
        except sqlite3.OperationalError:
            pass
        # 向后兼容：旧库无 user_id 列时追加（记录质控责任人工号）
        try:
            conn.execute("ALTER TABLE samples ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def save_sample(report: str, meta: dict, findings: list, scores: dict,
                path: str = None, anonymize: bool = False,
                user_id: str = None) -> int:
    init_db(path)
    m = dict(meta)
    if anonymize:
        m["patient"] = "已脱敏"   # 入库时剥离患者姓名，降低隐私合规风险
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            """INSERT INTO samples
               (ts, patient, gender, age, modality, applied_site, laterality,
                user_id, report_text, findings_json, scores_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.datetime.now().isoformat(timespec="seconds"),
                m.get("patient", ""),
                m.get("gender", ""),
                str(m.get("age", "")),
                m.get("modality", ""),
                m.get("applied_site", ""),
                m.get("laterality", ""),
                (user_id or "").strip(),
                report,
                json.dumps([f.__dict__ for f in findings], ensure_ascii=False),
                json.dumps(scores, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def list_samples(path: str = None) -> list:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, patient, gender, modality, applied_site, user_id "
            "FROM samples ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_sample(sid: int, path: str = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM samples WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else {}


def list_samples_full(path: str = None) -> list:
    """返回样本全部字段（含 report_text / findings_json / scores_json），供导出报表使用。"""
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM samples ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def delete_sample(sid: int, path: str = None) -> None:
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute("DELETE FROM samples WHERE id=?", (sid,))


def stats_by_error_type(path: str = None) -> dict:
    """汇总所有样本的错误类型计数，供饼图"""
    init_db(path)
    counts = {}
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute("SELECT findings_json FROM samples").fetchall()
    for (fj,) in rows:
        for f in json.loads(fj):
            et = f.get("error_type", "其他")
            counts[et] = counts.get(et, 0) + 1
    return counts


def stats_by_date(path: str = None) -> dict:
    """按日期汇总报告数与平均准确性，供趋势图"""
    init_db(path)
    by_date = {}
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute("SELECT ts, scores_json FROM samples").fetchall()
    for ts, sj in rows:
        day = ts[:10]
        sc = json.loads(sj)
        acc = sc.get("准确性", 100)
        if isinstance(acc, dict):   # 兼容新版 score() 返回的明细结构
            acc = acc.get("score", 100)
        d = by_date.setdefault(day, {"n": 0, "acc_sum": 0})
        d["n"] += 1
        d["acc_sum"] += acc
    return {d: {"n": v["n"], "avg_acc": round(v["acc_sum"] / v["n"], 1)} for d, v in by_date.items()}
