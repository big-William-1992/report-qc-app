"""
report_qc_app/src/samplelib.py
样本库：持久化报告质控结果，支撑驾驶舱统计与样本管理

数据访问（2026-09-30 并轨 ORM）
===============================
本模块统一走 server/db.py 的 SQLAlchemy 数据层 + server/models.py 的 Sample
模型，全库只有**一个 schema 真相源、一套连接池与事务策略**
（BEGIN IMMEDIATE + busy_timeout=30s + WAL，见 server/db._make_engine）。

改造前的问题（本次修复）：
1. 本模块自建裸 sqlite3 连接并手写 CREATE TABLE/ALTER，与 models.Sample 形成
   两份 schema 声明，schema 演进要改两处；
2. 事务/并发语义与 ORM 不一致；
3. **源码运行时两个库文件**：db.py 曾算 <root>/qc.db（漏了 assets/ 段），
   而本模块算 <root>/assets/qc.db —— 账号/科室在一个库、样本在另一个库，
   多用户的数据归属无法靠同库约束保证。两者现已统一。

仍保留 sqlite3 的地方：只用于**读取外部遗留库文件**（旧独立 samples.db 等，
不是本库，无法用 ORM 表达），写入本库一律走 ORM。
"""

import os
import sys
import csv
import json
import sqlite3
import datetime
import threading
import contextlib

# 导入去重串行锁（2026-08-18 M1）：_import_rows 的「读 seen → 逐条 INSERT」跨事务，
# 并发导入会重复插入；进程内锁串行化。配合 WAL + busy_timeout 消除 database is locked。
_IMPORT_LOCK = threading.Lock()

# path= 显式指定的临时库（多机合并/导入/测试隔离）→ engine 缓存，避免每次重建。
_ENGINE_LOCK = threading.Lock()
_EXTRA_ENGINES: dict = {}

# 已确保建表的库（按绝对路径）；文件被删除/重建时自动失效重来。
_PREPARED: set = set()


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
    """统一数据层的 SQLite 落盘文件（单一真相源）。

    优先 QC_DB_OVERRIDE（E2E/测试隔离），其次直接取 server.db 当前 engine 的文件路径
    ——这样样本库与账号/科室/队列**必然同库**，不会再出现两条路径各自演化。
    仅在 server 包不可用（单文件工具脚本）时回退到按目录推算。
    """
    override = os.environ.get("QC_DB_OVERRIDE", "").strip()
    if override:
        return os.path.abspath(override)
    try:
        from server import db as _db
        if _db.engine.url.get_backend_name() == "sqlite" and _db.engine.url.database:
            return os.path.abspath(str(_db.engine.url.database))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        user_dir = os.path.dirname(_appdata_db())  # MedicalReportQC 用户数据目录
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, "qc.db")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "qc.db")


def _server_db():
    """延迟导入 server.db：本模块既被 server 调用，也被 src/ 内工具直接 import。"""
    try:
        from server import db as _db
    except ImportError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from server import db as _db
    return _db


def _models():
    _server_db()
    from server import models
    return models


def _use_global_engine(path: str) -> bool:
    """该路径是否即统一数据层当前绑定的库（SQLite 同文件 / 非 SQLite 后端）。"""
    db = _server_db()
    if db.engine.url.get_backend_name() != "sqlite":
        return True   # PostgreSQL 等：统一走 ORM 引擎，path 仅作兼容参数
    dbfile = db.engine.url.database
    return bool(dbfile) and os.path.abspath(str(dbfile)) == os.path.abspath(path)


def _engine_for(path: str):
    """取得 path 对应的 engine：统一库复用全局 engine，其余建/取缓存 engine。

    刻意复用 server.db._make_engine：只有一处会配置 BEGIN IMMEDIATE /
    busy_timeout / WAL，避免「数据层两套事务策略」的老问题重现。
    """
    db = _server_db()
    if _use_global_engine(path):
        return db.engine
    with _ENGINE_LOCK:
        eng = _EXTRA_ENGINES.get(path)
        if eng is None:
            from urllib.parse import quote as _urlquote
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            eng = db._make_engine(
                "sqlite:///" + _urlquote(path.replace("\\", "/"), safe="/:"))
            _EXTRA_ENGINES[path] = eng
        return eng


def init_db(path: str = None) -> None:
    """确保 samples 表存在且为当前 schema（幂等）。

    schema 来自 server/models.py 的 Sample（唯一真相源）；旧库的一次性升级见
    _upgrade_legacy_samples。
    """
    target = os.path.abspath(path or db_path())
    if target in _PREPARED and os.path.exists(target):
        return
    d = os.path.dirname(target)
    if d:
        os.makedirs(d, exist_ok=True)
    db = _server_db()
    if _use_global_engine(target):
        db.init_db()          # 统一库：建表 + queue/settings 迁移一并在 ORM 侧完成
        _PREPARED.add(target)
        return
    models = _models()
    eng = _engine_for(target)
    models.Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:
        _upgrade_legacy_samples(conn)
    _PREPARED.add(target)


def _upgrade_legacy_samples(conn) -> None:
    """旧库一次性升级（幂等）：
    - 缺 laterality / dept_id 列 → ALTER 补齐；
    - 早期 models.Sample.user_id 误声明为 INTEGER FK，工号 '0559' 会被 SQLite
      截断为 559，导致样本归属/角色过滤失配 → 检测到 INTEGER 声明则重建为 TEXT。
    """
    cols = {r[1]: (r[2] or "").upper()
            for r in conn.exec_driver_sql("PRAGMA table_info(samples)").fetchall()}
    if not cols:
        return
    # 安全说明：列名为本文件内硬编码常量，非外部输入。
    for _col in ("laterality", "dept_id"):
        if _col not in cols:
            conn.exec_driver_sql(f"ALTER TABLE samples ADD COLUMN {_col} TEXT")
    if cols.get("user_id") == "INTEGER":
        _rebuild_samples_user_id_text(conn)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_samples_user_ts ON samples(user_id, ts)")


def _rebuild_samples_user_id_text(conn) -> None:
    """把 samples.user_id 由 INTEGER 重建为 TEXT（按列交集搬运，避免丢历史样本）。"""
    models = _models()
    conn.exec_driver_sql("ALTER TABLE samples RENAME TO samples_conv_old")
    models.Base.metadata.create_all(bind=conn)   # 用当前模型声明重建 samples
    old_cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(samples_conv_old)")]
    new_cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(samples)")]
    # 列交集（2026-08-18 P0 修复）：旧 ORM 表含 created_at 等列，全列直拷会
    # INSERT 失败且 DDL 已自动提交——历史样本会滞留 samples_conv_old（由
    # rescue_samples_conv 兜底抢救）。列名来自 PRAGMA，非外部输入。
    common = [c for c in old_cols if c in new_cols]
    if common:
        cc = ",".join(common)
        conn.exec_driver_sql(
            f"INSERT INTO samples ({cc}) SELECT {cc} FROM samples_conv_old")
    conn.exec_driver_sql("DROP TABLE samples_conv_old")


@contextlib.contextmanager
def _session(path: str = None):
    """产出 (session, Sample)。退出时提交；异常则回滚。

    统一库复用 server.db.SessionLocal（共享连接池与事务策略）；显式 path 时用
    该文件的 engine 建会话。
    """
    from sqlalchemy.orm import sessionmaker
    db = _server_db()
    models = _models()
    target = os.path.abspath(path or db_path())
    init_db(target)
    if _use_global_engine(target):
        sess = db.SessionLocal()
    else:
        sess = sessionmaker(bind=_engine_for(target), autoflush=False,
                            expire_on_commit=False, future=True)()
    try:
        yield sess, models.Sample
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def _row_dict(obj, only: list = None) -> dict:
    """模型实例 → dict（与旧 sqlite3.Row 行为一致：datetime 转字符串）。

    SQLite 原生就把 datetime 存成 'YYYY-MM-DD HH:MM:SS.ffffff' 文本，str() 输出
    与之完全一致，保证既有前端/导出/接口的字段格式不变。
    """
    cols = only if only is not None else [c.name for c in obj.__table__.columns]
    out = {}
    for name in cols:
        v = getattr(obj, name, None)
        if isinstance(v, datetime.datetime):
            v = str(v)
        out[name] = v
    return out


# 列表页字段（刻意不含 report_text：列表/分页不需要长文本）
_LIST_COLS = ["id", "ts", "patient", "gender", "modality", "applied_site", "user_id"]


def rescue_samples_conv(path: str = None) -> int:
    """抢救 samples_conv_old 滞留数据（2026-08-18 P0 修复）：此前 user_id INTEGER→TEXT
    重建迁移因旧表 created_at 列不匹配 INSERT 失败，历史样本滞留孤儿表（真实库取证：
    samples_conv_old 含李四等旧行、user_id 被截断为 559）。幂等：成功迁移后 DROP 旧表。
    返回迁回行数。"""
    target = os.path.abspath(path or db_path())
    init_db(target)
    eng = _engine_for(target)
    n = 0
    try:
        with eng.begin() as conn:
            tables = [r[0] for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "samples_conv_old" not in tables or "samples" not in tables:
                return 0
            old_cols = [r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(samples_conv_old)").fetchall()]
            new_cols = [r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(samples)").fetchall()]
            common = [c for c in old_cols if c in new_cols]
            if not common:
                return 0
            try:
                emp_ids = [r[0] for r in conn.exec_driver_sql(
                    "SELECT emp_id FROM users").fetchall()]
            except Exception:
                emp_ids = []
            # 安全说明：cols 来自上方白名单交集（PRAGMA），非用户输入；值全部参数化。
            cc = ",".join(common)
            rows = conn.exec_driver_sql(
                f"SELECT {cc} FROM samples_conv_old").fetchall()
            ph = ",".join("?" * len(common))
            for r in rows:
                d = dict(zip(common, r))
                uid = d.get("user_id")
                # user_id 校正：旧 INTEGER 截断（559 → 前导补零 0559），按 users.emp_id 匹配
                if uid is not None and str(uid).strip().isdigit():
                    cand = str(uid).strip()
                    d["user_id"] = next(
                        (e for e in emp_ids if str(e).lstrip("0") == cand.lstrip("0")), cand)
                if d.get("id") is not None and conn.exec_driver_sql(
                        "SELECT 1 FROM samples WHERE id=?", (d.get("id"),)).fetchone():
                    d.pop("id", None)   # id 冲突：改自增插入
                cols_d = ",".join(d.keys())
                ph_d = ",".join("?" * len(d))
                try:
                    conn.exec_driver_sql(
                        f"INSERT INTO samples ({cols_d}) VALUES ({ph_d})",
                        tuple(d.values()))
                    n += 1
                except Exception:
                    continue
            conn.exec_driver_sql("DROP TABLE samples_conv_old")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
        return 0
    return n


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


def _read_legacy_samples(db_file: str) -> list:
    """读取**外部遗留库**的 samples 全表（只读，唯一保留 sqlite3 的场景）。"""
    # 安全说明：表名为本文件硬编码常量，非外部输入。
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM samples").fetchall()]


def migrate_legacy_samples() -> None:
    """把旧独立 samples.db 的数据一次性迁入统一库（2026-08-18 收敛，幂等）。

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
        if count_samples(target) > 0:
            return  # 目标已有数据，不重复迁移（幂等）
        try:
            rows = _read_legacy_samples(old)
        except Exception:
            rows = []
        if rows:
            _import_rows(rows, target)
        try:
            os.rename(old, old + ".bak")
        except Exception:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
        return


def save_sample(report: str, meta: dict, findings: list, scores: dict,
                path: str = None, anonymize: bool = False,
                user_id: str = None, dept_id: str = None) -> int:
    m = dict(meta)
    if anonymize:
        m["patient"] = "已脱敏"   # 入库时剥离患者姓名，降低隐私合规风险
    with _session(path) as (sess, Sample):
        obj = Sample(
            ts=datetime.datetime.now().isoformat(timespec="seconds"),
            patient=m.get("patient", ""),
            gender=m.get("gender", ""),
            age=str(m.get("age", "")),
            modality=m.get("modality", ""),
            applied_site=m.get("applied_site", ""),
            laterality=m.get("laterality", ""),
            user_id=(user_id or "").strip(),
            dept_id=str(dept_id or m.get("dept_id", "") or "").strip(),
            report_text=report,
            findings_json=json.dumps([f.__dict__ for f in findings], ensure_ascii=False),
            scores_json=json.dumps(scores, ensure_ascii=False),
        )
        sess.add(obj)
        sess.flush()
        return int(obj.id or 0)


def list_samples(path: str = None) -> list:
    from sqlalchemy import select
    with _session(path) as (sess, Sample):
        rows = sess.execute(
            select(*[getattr(Sample, c) for c in _LIST_COLS])
            .order_by(Sample.id.desc())).all()
        return [{c: (str(v) if isinstance(v, datetime.datetime) else v)
                 for c, v in zip(_LIST_COLS, r)} for r in rows]


def get_sample(sid: int, path: str = None) -> dict:
    with _session(path) as (sess, Sample):
        obj = sess.get(Sample, sid)
        return _row_dict(obj) if obj is not None else {}


def list_samples_full(path: str = None, limit: int = None, offset: int = 0,
                      user_id: str = None) -> list:
    """返回样本全部字段（含 report_text / findings_json / scores_json），供导出报表使用。
    limit 可选：>0 时只在 SQL 层取最近 N 条，避免全量载入长文本（扫描学习用）。
    offset 可选：与 limit 配合做 SQL 层分页（M10，2026-08-19）。
    user_id 可选：非空时只返回该责任人的样本（多用户隔离，2026-08-18）。"""
    from sqlalchemy import select
    with _session(path) as (sess, Sample):
        stmt = select(Sample)
        if user_id:
            stmt = stmt.where(Sample.user_id == user_id)
        stmt = stmt.order_by(Sample.id.desc())
        if limit and limit > 0:
            stmt = stmt.limit(int(limit)).offset(int(max(0, offset)))
        return [_row_dict(o) for o in sess.execute(stmt).scalars().all()]


def count_samples(path: str = None, user_id: str = None) -> int:
    """返回样本总数（SQL COUNT，避免为分页而全表载入长文本，M10，2026-08-19）。
    user_id 非空时仅统计该责任人样本（与 list_samples_full 隔离口径一致）。"""
    from sqlalchemy import select, func
    with _session(path) as (sess, Sample):
        stmt = select(func.count()).select_from(Sample)
        if user_id:
            stmt = stmt.where(Sample.user_id == user_id)
        return int(sess.execute(stmt).scalar() or 0)


def delete_sample(sid: int, path: str = None) -> None:
    from sqlalchemy import delete
    with _session(path) as (sess, Sample):
        sess.execute(delete(Sample).where(Sample.id == sid))


def stats_by_error_type(path: str = None, user_id: str = None) -> dict:
    """汇总样本的错误类型计数，供饼图。user_id 非空时仅统计该责任人样本。"""
    from sqlalchemy import select
    counts = {}
    with _session(path) as (sess, Sample):
        stmt = select(Sample.findings_json)
        if user_id:
            stmt = stmt.where(Sample.user_id == user_id)
        rows = sess.execute(stmt).all()
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
    from sqlalchemy import select
    by_date = {}
    with _session(path) as (sess, Sample):
        stmt = select(Sample.ts, Sample.scores_json)
        if user_id:
            stmt = stmt.where(Sample.user_id == user_id)
        rows = sess.execute(stmt).all()
    for ts, sj in rows:
        day = (ts or "")[:10]
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
    return {d: {"n": v["n"], "avg_acc": round(v["acc_sum"] / v["n"], 1)}
            for d, v in by_date.items()}


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
    from sqlalchemy import select
    # 只取统计所需列（ts/user_id/findings_json/scores_json），
    # 不载入 report_text 全文（2026-08-18 性能优化）
    with _session(path) as (sess, Sample):
        stmt = select(Sample.ts, Sample.user_id, Sample.findings_json,
                      Sample.scores_json)
        if user_id:
            stmt = stmt.where(Sample.user_id == user_id)
        rows = [dict(ts=r[0], user_id=r[1], findings_json=r[2], scores_json=r[3])
                for r in sess.execute(stmt).all()]

    def _in_range(ts: str) -> bool:
        day = (ts or "")[:10]
        if start and day < start:
            return False
        if end and day > end:
            return False
        return bool(day)

    rows = [r for r in rows if _in_range(r.get("ts", "") or "")]

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
        try:
            findings = json.loads(r.get("findings_json") or "[]")
        except Exception:
            findings = []
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
    2026-08-18 M1：进程内锁串行化「读 seen → 逐条 INSERT」跨事务窗口，防并发导入重复样本。
    2026-09-30：改走 ORM，并补上此前被漏掉的 dept_id —— 旧 INSERT 未含该列，
    导致跨机合并/导入的样本丢失科室归属，破坏科室级隔离。"""
    from sqlalchemy import select
    target = os.path.abspath(target or db_path())
    with _IMPORT_LOCK:
        inserted = skipped = 0
        with _session(target) as (sess, Sample):
            seen = {(ts or "", rt or "")
                    for ts, rt in sess.execute(select(Sample.ts, Sample.report_text))}
            for r in rows:
                key = (r.get("ts", "") or "", r.get("report_text", "") or "")
                if key in seen:
                    skipped += 1
                    continue
                sess.add(Sample(
                    ts=key[0],
                    patient=r.get("patient", "") or "",
                    gender=r.get("gender", "") or "",
                    age=str(r.get("age", "") or ""),
                    modality=r.get("modality", "") or "",
                    applied_site=r.get("applied_site", "") or "",
                    laterality=r.get("laterality", "") or "",
                    user_id=(r.get("user_id") or "").strip(),
                    dept_id=str(r.get("dept_id", "") or "").strip(),
                    report_text=key[1],
                    findings_json=r.get("findings_json", "") or "[]",
                    scores_json=r.get("scores_json", "") or "[]",
                ))
                seen.add(key)
                inserted += 1
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
    """把另一个库的全部样本合并进 target（按 (ts,report_text) 去重）。返回 (inserted, skipped)。

    2026-09-30：源库改走 ORM 全字段读取（含 dept_id），不再用 SELECT * 手拼。
    """
    if not os.path.exists(src_db):
        return 0, 0
    init_db(src_db)
    rows = list_samples_full(src_db)
    if not rows:
        return 0, 0
    return _import_rows(rows, target)

# ── 兼容层: 导出能力自 sample_export.py 汇聚 (2026-08-25 拆分) ──
# 双路径兼容: 支持 `import samplelib`(顶层) 与 `from src import samplelib`(包式)。
try:
    from .sample_export import *  # noqa: F401,F403,E402  (包式)
except ImportError:
    from sample_export import *  # noqa: F401,F403,E402  (顶层)
