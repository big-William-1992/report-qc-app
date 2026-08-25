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
import threading

# 导入去重串行锁（2026-08-18 M1）：_import_rows 的「读 seen → 逐条 INSERT」跨事务，
# 并发导入会重复插入；进程内锁串行化。配合 WAL + busy_timeout 消除 database is locked。
_IMPORT_LOCK = threading.Lock()


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
    """统一数据层（2026-08-18 收敛）：样本库并入 qc.db（与 server/db.py 的 SQLAlchemy 同库），
    替代独立 samples.db。frozen 态仍落用户可写目录；init_db() 会自动建 samples 表。
    QC_DB_OVERRIDE（E2E 测试隔离）优先于默认路径，与 server/db.py 收敛一致。"""
    override = os.environ.get("QC_DB_OVERRIDE", "").strip()
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        user_dir = os.path.dirname(_appdata_db())  # MedicalReportQC 用户数据目录
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, "qc.db")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "qc.db")


def rescue_samples_conv(path: str = None) -> int:
    """抢救 samples_conv_old 滞留数据（2026-08-18 P0 修复）：此前 user_id INTEGER→TEXT
    重建迁移因旧表 created_at 列不匹配 INSERT 失败，历史样本滞留孤儿表（真实库取证：
    samples_conv_old 含李四等旧行、user_id 被截断为 559）。幂等：成功迁移后 DROP 旧表。
    返回迁回行数。"""
    path = path or db_path()
    try:
        conn = sqlite3.connect(path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        if "samples_conv_old" not in tables or "samples" not in tables:
            conn.close()
            return 0
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(samples_conv_old)")]
        new_cols = [r[1] for r in conn.execute("PRAGMA table_info(samples)")]
        common = [c for c in old_cols if c in new_cols]
        if not common:
            conn.close()
            return 0
        # user_id 校正：旧 INTEGER 截断（559 → 前导补零 0559），按 users.emp_id 匹配
        emp_ids = [r[0] for r in conn.execute("SELECT emp_id FROM users")]
        rows = conn.execute(
            "SELECT " + ",".join(common) + " FROM samples_conv_old").fetchall()
        n = 0
        for r in rows:
            d = dict(zip(common, r))
            uid = d.get("user_id")
            if uid is not None and str(uid).strip().isdigit():
                cand = str(uid).strip()
                fixed = next((e for e in emp_ids
                              if str(e).lstrip("0") == cand.lstrip("0")), cand)
                d["user_id"] = fixed
            if conn.execute("SELECT 1 FROM samples WHERE id=?",
                            (d.get("id"),)).fetchone():
                d.pop("id", None)  # id 冲突：改自增插入
            cols = ",".join(d.keys())
            ph = ",".join("?" * len(d))
            try:
                # 安全说明: cols 来自模块内白名单常量(见上方 _COLUMNS 定义), 非用户输入;
                # 值全部参数化({ph}), 无注入面。
                conn.execute(f"INSERT INTO samples ({cols}) VALUES ({ph})",
                             list(d.values()))
                n += 1
            except sqlite3.IntegrityError:
                continue
        conn.execute("DROP TABLE samples_conv_old")
        conn.commit()
        conn.close()
        return n
    except Exception:
        try:
            conn.close()
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
        return 0


def _legacy_source_dir() -> str:
    """源码布局下的项目根（assets/ 的上级）。独立成函数便于测试 monkeypatch
    模拟不同布局（冻结态 __file__ 解析到 _MEIPASS，不能作为用户旧库依据）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _legacy_sample_db_candidates() -> list:
    """旧独立 samples.db 的候选位置（按优先级排序，去重）。

    1) 用户可写数据目录 samples.db（_appdata_db() 口径，与 db_path() 冻结态同目录）
       ——冻结打包后老版本实际写在这里，是升级迁移的主目标；
    2) <项目根>/assets/samples.db ——源码运行的历史位置。冻结态下 __file__ 会
       解析进 _MEIPASS 临时解压目录，那里即便存在 samples.db 也是随包分发的
       初始资源而非用户数据；因此候选仅用于兼容源码/开发态，冻结态靠候选 1。
    """
    cands = [_appdata_db(), os.path.join(_legacy_source_dir(), "assets", "samples.db")]
    out, seen = [], set()
    for c in cands:
        key = os.path.abspath(c)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def migrate_legacy_samples() -> None:
    """把旧独立 samples.db 的数据一次性迁入统一 qc.db（2026-08-18 收敛，幂等）。

    仅当旧库存在且有数据、目标 samples 表为空时执行；完成后旧库改名 samples.db.bak。
    由 server 启动时（db.init_db 之后）调用一次。

    2026-08-23 断链修复：此前只探测 <__file__>/../assets/samples.db——冻结态
    __file__ 指向 _MEIPASS 随包资源目录，用户在 %APPDATA%\\MedicalReportQC 留下的
    旧库永远探测不到，升级后历史样本"消失"。现同时探测两个位置（见
    _legacy_sample_db_candidates），存在且有数据才迁移；不做任何自动删除，
    迁移成功才归档 .bak。
    """
    target = db_path()
    for old in _legacy_sample_db_candidates():
        if not os.path.exists(old):
            continue
        if os.path.abspath(target) == os.path.abspath(old):
            continue  # 同一文件（如 override 指向旧库），无从迁移
        try:
            with sqlite3.connect(old) as conn:
                n = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        except Exception:
            n = 0
        if n == 0:
            try:
                os.rename(old, old + ".bak")
            except Exception:
                try:
                    from .log_utils import log_quiet
                except ImportError:
                    from log_utils import log_quiet
                log_quiet(__name__)
            continue
        init_db(target)
        try:
            with sqlite3.connect(target) as conn:
                tn = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        except Exception:
            tn = 0
        if tn > 0:
            return  # 目标已有数据，不重复迁移（幂等）
        with sqlite3.connect(old) as src, sqlite3.connect(target) as dst:
            rows = src.execute("SELECT * FROM samples").fetchall()
            cols = [d[0] for d in src.execute("SELECT * FROM samples LIMIT 1").description]
            cols_sql = ",".join(cols)
            ph = ",".join("?" * len(cols))
            dst.executemany(f"INSERT INTO samples ({cols_sql}) VALUES ({ph})", rows)
            dst.commit()
        try:
            os.rename(old, old + ".bak")
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
        return


# samples 表统一 schema（user_id 存工号 TEXT，2026-08-18 数据层收敛对齐 models.Sample）
# 2026-08-21 架构收敛：列集合与 server/models.py 的 Sample 完全对齐（含 created_at），
# 使本模块与 ORM 共享同一 schema 真相源，杜绝「手写 SQL 少一列」导致的字段口径分叉。
# models.Sample 的 user_id/dept_id 均为 String 无 FK，与本表声明完全一致。
_SAMPLES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        patient TEXT,
        gender TEXT,
        age TEXT,
        modality TEXT,
        applied_site TEXT,
        laterality TEXT,
        user_id TEXT,
        dept_id TEXT,
        report_text TEXT,
        findings_json TEXT,
        scores_json TEXT,
        created_at TIMESTAMP
    )
"""


def init_db(path: str = None) -> None:
    path = path or db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        # 2026-08-18 M1：WAL 并发读不阻塞写 + 30s 写锁等待，消除多线程写 database is locked
        try:
            conn.execute("PRAGMA journal_mode=WAL").fetchall()
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
        conn.execute(_SAMPLES_TABLE_SQL)
        # 向后兼容：旧库无 laterality / user_id / dept_id 列时追加（SQLite 不支持 ADD COLUMN IF NOT EXISTS）
        for _col, _decl in (("laterality", "TEXT"), ("user_id", "TEXT"), ("dept_id", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE samples ADD COLUMN {_col} {_decl}")
            except sqlite3.OperationalError:
                try:
                    from .log_utils import log_quiet
                except ImportError:
                    from log_utils import log_quiet
                log_quiet(__name__)
        # 2026-08-18 收敛修正：早期 models.Sample.user_id 误配为 INTEGER FK，工号 '0559'
        # 会被 SQLite 转为 559，导致样本归属/角色过滤失配。检测到 INTEGER 声明则重建为 TEXT（幂等）。
        _pt = [r for r in conn.execute("PRAGMA table_info(samples)").fetchall() if r[1] == "user_id"]
        if _pt and _pt[0][2].upper() == "INTEGER":
            conn.execute("ALTER TABLE samples RENAME TO samples_conv_old")
            conn.execute(_SAMPLES_TABLE_SQL)
            for _col, _decl in (("laterality", "TEXT"), ("user_id", "TEXT"), ("dept_id", "TEXT")):
                try:
                    conn.execute(f"ALTER TABLE samples ADD COLUMN {_col} {_decl}")
                except sqlite3.OperationalError:
                    try:
                        from .log_utils import log_quiet
                    except ImportError:
                        from log_utils import log_quiet
                    log_quiet(__name__)
            _old_cols = [r[1] for r in conn.execute("PRAGMA table_info(samples_conv_old)").fetchall()]
            _new_cols = [r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()]
            # 列交集（2026-08-18 P0 修复）：旧 ORM 表含 created_at 等新表没有的列，
            # 全列直拷会 INSERT 失败且 DDL 已自动提交——历史样本滞留 samples_conv_old。
            _common = [c for c in _old_cols if c in _new_cols]
            if _common:
                _cc = ",".join(_common)
                conn.execute(f"INSERT INTO samples ({_cc}) SELECT {_cc} FROM samples_conv_old")
            conn.execute("DROP TABLE samples_conv_old")
        # 2026-08-18：samples 按 user_id/ts 过滤无索引，多用户/数据增长后列表与统计全表扫描
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS ix_samples_user_ts ON samples(user_id, ts)")
        except sqlite3.OperationalError:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
        conn.commit()


def save_sample(report: str, meta: dict, findings: list, scores: dict,
                path: str = None, anonymize: bool = False,
                user_id: str = None, dept_id: str = None) -> int:
    init_db(path)
    m = dict(meta)
    if anonymize:
        m["patient"] = "已脱敏"   # 入库时剥离患者姓名，降低隐私合规风险
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            """INSERT INTO samples
               (ts, patient, gender, age, modality, applied_site, laterality,
                user_id, dept_id, report_text, findings_json, scores_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.datetime.now().isoformat(timespec="seconds"),
                m.get("patient", ""),
                m.get("gender", ""),
                str(m.get("age", "")),
                m.get("modality", ""),
                m.get("applied_site", ""),
                m.get("laterality", ""),
                (user_id or "").strip(),
                str(dept_id or m.get("dept_id", "") or "").strip(),
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


def list_samples_full(path: str = None, limit: int = None, offset: int = 0,
                      user_id: str = None) -> list:
    """返回样本全部字段（含 report_text / findings_json / scores_json），供导出报表使用。
    limit 可选：>0 时只在 SQL 层取最近 N 条，避免全量载入长文本（扫描学习用）。
    offset 可选：与 limit 配合做 SQL 层分页（M10，2026-08-19）。
    user_id 可选：非空时只返回该责任人的样本（多用户隔离，2026-08-18）。"""
    init_db(path)
    _where = ""
    _args = []
    if user_id:
        _where = " WHERE user_id=?"
        _args = [user_id]
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _sql = "SELECT * FROM samples" + _where + " ORDER BY id DESC"
        if limit and limit > 0:
            _sql += " LIMIT ? OFFSET ?"
            _args = _args + [int(limit), int(max(0, offset))]
        rows = conn.execute(_sql, _args).fetchall()
        return [dict(r) for r in rows]


def count_samples(path: str = None, user_id: str = None) -> int:
    """返回样本总数（SQL COUNT，避免为分页而全表载入长文本，M10，2026-08-19）。
    user_id 非空时仅统计该责任人样本（与 list_samples_full 隔离口径一致）。"""
    init_db(path)
    _where = " WHERE user_id=?" if user_id else ""
    _args = (user_id,) if user_id else ()
    with sqlite3.connect(path or db_path()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM samples" + _where, _args).fetchone()[0]


def delete_sample(sid: int, path: str = None) -> None:
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute("DELETE FROM samples WHERE id=?", (sid,))


def stats_by_error_type(path: str = None, user_id: str = None) -> dict:
    """汇总样本的错误类型计数，供饼图。user_id 非空时仅统计该责任人样本。"""
    init_db(path)
    counts = {}
    _where = " WHERE user_id=?" if user_id else ""
    _args = (user_id,) if user_id else ()
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute(
            "SELECT findings_json FROM samples" + _where, _args).fetchall()
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


def stats_by_date(path: str = None, user_id: str = None) -> dict:
    """按日期汇总报告数与平均准确性，供趋势图。user_id 非空时仅统计该责任人样本。"""
    init_db(path)
    by_date = {}
    _where = " WHERE user_id=?" if user_id else ""
    _args = (user_id,) if user_id else ()
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute(
            "SELECT ts, scores_json FROM samples" + _where, _args).fetchall()
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


def stats_report(start: str = None, end: str = None, path: str = None,
                 user_id: str = None) -> dict:
    """质控问题分类统计报表（时间段筛选）。

    start / end : "YYYY-MM-DD"（含边界），None 表示不限。
    user_id     : 非空时仅统计该责任人的样本（多用户隔离，2026-08-18）。
    返回：
      period        {start, end, total, n_critical, n_warning, n_info}
      error_type_top  [{name, count}] 按问题类型计数降序
      rule_top        [{rule_id, count}] 按规则计数降序
      doctor_rank     [{user_id, name, samples, findings}] 医生排行（samples=报告数, findings=问题数）
      daily           [{date, count, acc}] 逐日趋势
    """
    init_db(path)
    _where = ""
    _args = ()
    if user_id:
        _where = " WHERE user_id=?"
        _args = (user_id,)
    rows = []
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        # 只取统计所需列（ts/user_id/findings_json/scores_json），
        # 不载入 report_text 全文（2026-08-18 性能优化）
        rows = [dict(r) for r in conn.execute(
            "SELECT ts, user_id, findings_json, scores_json FROM samples"
            + _where, _args).fetchall()]

    def _in_range(ts: str) -> bool:
        day = (ts or "")[:10]
        if start and day < start:
            return False
        if end and day > end:
            return False
        return bool(day)

    rows = [r for r in rows if _in_range(r.get("ts", ""))]

    err_cnt = {}
    rule_cnt = {}
    doc = {}          # user_id -> {"name","samples","findings"}
    n_critical = n_warning = n_info = 0
    daily = {}
    acc_sum = {}
    for r in rows:
        uid = r.get("user_id") or ""
        d = doc.setdefault(uid, {"name": "", "samples": 0, "findings": 0})
        d["samples"] += 1
        findings = json.loads(r.get("findings_json") or "[]")
        for f in findings:
            et = f.get("error_type", "其他")
            err_cnt[et] = err_cnt.get(et, 0) + 1
            rule_cnt[f.get("rule_id", "")] = rule_cnt.get(f.get("rule_id", ""), 0) + 1
            sev = f.get("severity", "low")
            if sev == "high":
                n_critical += 1
            elif sev == "medium":
                n_warning += 1
            else:
                n_info += 1
            d["findings"] += 1
        day = (r.get("ts") or "")[:10]
        if day:
            dl = daily.setdefault(day, {"count": 0, "acc": 0.0})
            dl["count"] += 1
            try:
                sc = json.loads(r.get("scores_json") or "{}")
                acc = sc.get("准确性", 100)
                if isinstance(acc, dict):
                    acc = acc.get("score", 100)
                dl["acc"] += acc
            except Exception:
                dl["acc"] += 100
            acc_sum[day] = acc_sum.get(day, 0) + 1

    # 医生姓名：从 accounts 补齐（可能无账号系统，容错）
    try:
        import accounts
        for uid in doc:
            if uid:
                doc[uid]["name"] = accounts.get_name(uid) or ""
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

    error_type_top = [{"name": k, "count": v}
                      for k, v in sorted(err_cnt.items(), key=lambda x: -x[1])]
    rule_top = [{"rule_id": k, "count": v}
                for k, v in sorted(rule_cnt.items(), key=lambda x: -x[1]) if k]
    doctor_rank = sorted(
        [{"user_id": k, "name": v["name"] or k, "samples": v["samples"],
          "findings": v["findings"]} for k, v in doc.items() if k],
        key=lambda x: (-x["findings"], -x["samples"]))
    daily_list = [{"date": d, "count": v["count"],
                   "avg_acc": round(v["acc"] / acc_sum.get(d, 1), 1)}
                  for d, v in sorted(daily.items())]
    return {
        "period": {"start": start, "end": end, "total": len(rows),
                   "critical": n_critical, "warning": n_warning, "info": n_info},
        "error_type_top": error_type_top,
        "rule_top": rule_top,
        "doctor_rank": doctor_rank,
        "daily": daily_list,
    }


# ---------------------------------------------------------------------------
# 导出 / 导入 / 多机合并（支撑单机汇总与多机器数据聚合，零服务器成本）
# ---------------------------------------------------------------------------

def _import_rows(rows: list, target: str = None):
    """核心：把 dict 列表去重插入 target 库。去重键 (ts, report_text)。返回 (inserted, skipped)。
    2026-08-18 M1：进程内锁串行化「读 seen → 逐条 INSERT」跨事务窗口，防并发导入重复样本。"""
    target = target or db_path()
    init_db(target)
    with _IMPORT_LOCK:
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
    """从 CSV/JSON 文件导入样本到 target 库（默认当前库）。返回 (inserted, skipped)。

    2026-08-18：CSV 表头缺必填列或含空 report_text 行时抛 ValueError（可读错误），
    避免静默插入"空壳"样本污染样本库与统计。
    """
    if src_path.lower().endswith(".json"):
        with open(src_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        with open(src_path, encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = set(reader.fieldnames or [])
            if "report_text" not in cols:
                raise ValueError(
                    f"CSV 表头缺少必填列 report_text（当前列：{sorted(cols)}）")
            data = [dict(r) for r in reader]
    if not data:
        return 0, 0
    # 过滤空 report_text 行（表头不匹配/空行会产生空键行）
    _before = len(data)
    data = [r for r in data if (r.get("report_text") or "").strip()]
    if _before != len(data):
        raise ValueError(f"检测到 {_before - len(data)} 行无报告内容（空行或表头不符），已中止导入")
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

# ── 兼容层: 导出能力自 sample_export.py 汇聚 (2026-08-25 拆分) ──
# 双路径兼容: 支持 `import samplelib`(顶层) 与 `from src import samplelib`(包式)。
try:
    from .sample_export import *  # noqa: F401,F403,E402  (包式)
except ImportError:
    from sample_export import *  # noqa: F401,F403,E402  (顶层)
