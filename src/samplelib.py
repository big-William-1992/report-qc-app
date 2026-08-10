"""
report_qc_app/src/samplelib.py
样本库：SQLite 持久化报告质控结果，支撑驾驶舱统计与样本管理
"""

import os
import sys
import sqlite3
import json
import csv
import datetime
import shutil


def _appdata_db() -> str:
    # %APPDATA% 仅 Windows 存在；macOS/Linux 上 expandvars 不展开会得到字面相对路径，
    # 冻结打包后会把样本库写到奇怪位置。此处按平台取用户可写目录。
    import platform as _plt
    if _plt.system() == "Windows":
        base = os.path.expandvars("%APPDATA%")
    elif _plt.system() == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.path.expanduser("~")
    return os.path.join(base, "MedicalReportQC", "samples.db")


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
        fj = fj or "[]"
        try:
            items = json.loads(fj)
        except Exception:
            continue
        for f in items:
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
        sj = sj or "[]"
        try:
            sc = json.loads(sj)
        except Exception:
            continue
        acc = sc.get("准确性", 100)
        if isinstance(acc, dict):   # 兼容新版 score() 返回的明细结构
            acc = acc.get("score", 100)
        d = by_date.setdefault(day, {"n": 0, "acc_sum": 0})
        d["n"] += 1
        d["acc_sum"] += acc
    return {d: {"n": v["n"], "avg_acc": round(v["acc_sum"] / v["n"], 1)} for d, v in by_date.items()}


# ---------------------------------------------------------------------------
# 导出 / 导入 / 多机合并（支撑单机汇总与多机器数据聚合，零服务器成本）
# ---------------------------------------------------------------------------
FIELDS = ["id", "ts", "patient", "gender", "age", "modality",
          "applied_site", "laterality", "user_id",
          "report_text", "findings_json", "scores_json"]


def export_samples(path: str = None, out_path: str = None, fmt: str = "csv") -> str:
    """导出样本库为 CSV（Excel 友好，utf-8-sig 带 BOM）或 JSON。

    path     : 源库路径，默认 db_path()
    out_path : 输出文件，默认在源库同目录生成 samples_export_<时间戳>.<ext>
    fmt      : 'csv' | 'json'
    返回输出文件路径。
    """
    rows = list_samples_full(path)
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.path.dirname(path or db_path()),
                                f"samples_export_{stamp}.{fmt}")
    if fmt == "json":
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
    else:
        with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    return out_path


def _import_rows(rows: list, target: str = None):
    """核心：把 dict 列表去重插入 target 库。去重键 (ts, report_text)。返回 (inserted, skipped)。"""
    target = target or db_path()
    init_db(target)
    inserted = skipped = 0
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        seen = {(r["ts"], r["report_text"])
                for r in conn.execute("SELECT ts, report_text FROM samples")}
        for r in rows:
            key = (r.get("ts", "") or "", r.get("report_text", "") or "")
            if key in seen:
                skipped += 1
                continue
            conn.execute(
                """INSERT INTO samples
                   (ts, patient, gender, age, modality, applied_site, laterality,
                    user_id, report_text, findings_json, scores_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key[0], r.get("patient", "") or "",
                    r.get("gender", "") or "", str(r.get("age", "") or ""),
                    r.get("modality", "") or "", r.get("applied_site", "") or "",
                    r.get("laterality", "") or "", (r.get("user_id") or "").strip(),
                    key[1], r.get("findings_json", "") or "[]",
                    r.get("scores_json", "") or "[]",
                ),
            )
            seen.add(key)
            inserted += 1
        conn.commit()
    return inserted, skipped


def import_samples(src_path: str, target: str = None):
    """从 CSV/JSON 文件导入样本到 target 库（默认当前库）。返回 (inserted, skipped)。"""
    if src_path.lower().endswith(".json"):
        with open(src_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        with open(src_path, encoding="utf-8-sig") as fh:
            data = [dict(r) for r in csv.DictReader(fh)]
    if not data:
        return 0, 0
    return _import_rows(data, target)


def merge_from_db(src_db: str, target: str = None):
    """把另一个 samples.db 的全部样本合并进 target（按 (ts,report_text) 去重）。返回 (inserted, skipped)。"""
    init_db(src_db)
    with sqlite3.connect(src_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM samples")]
    if not rows:
        return 0, 0
    return _import_rows(rows, target)
