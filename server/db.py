"""
server/db.py — SQLAlchemy 统一数据层
====================================
- 通过 DATABASE_URL 环境变量切换数据库：默认 SQLite（院内单实例零运维），
  上线时改为 postgresql:// 即可切到 PostgreSQL，业务代码无需改动（抽象层价值）。
- 所有模型见 server/models.py，启动时 Base.metadata.create_all 建表。
- 多用户（科室自托管）核心：users / departments / samples / queue / settings 同库。
"""
import os
import sys
import hashlib
from typing import Optional
from urllib.parse import quote as _urlquote

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# 项目根：默认库落在 <root>/assets/qc.db（源码运行）。
# 冻结（PyInstaller）后不能回溯 __file__（PYZ 合成路径），也不能用 sys._MEIPASS
# （单文件模式是退出即删的临时解压目录，落那里账号/科室/权限每次重启全丢，
# 单目录模式会被整体升级覆盖、Program Files 只读则启动失败）。
# 2026-08-18 H2 修复：冻结模式统一落到用户可写数据目录
# <APPDATA|~/Library/Application Support|~>/MedicalReportQC/qc.db，
# 目录口径与 src/samplelib._appdata_db() 严格一致，保证账号表与样本表同库同文件。
if getattr(sys, "frozen", False):
    import platform as _plt
    if _plt.system() == "Windows":
        _base = os.path.expandvars("%APPDATA%")
    elif _plt.system() == "Darwin":
        _base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        _base = os.path.expanduser("~")
    _PROJECT_ROOT = os.path.join(_base, "MedicalReportQC")
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 2026-08-24：URL 编码空格/特殊字符（Windows 用户名含空格时
# sqlite:///C:\Users\John Doe\... 会被 SQLAlchemy 误解析）
#
# 2026-09-30 修正（源码态库位置分裂）：
# 此处此前写的是 os.path.join(_PROJECT_ROOT, "qc.db")，漏了 "assets" 段——
# 与本文件顶部的声明、src/samplelib.db_path()、以及历史上的 paths.qc_db_path()
# （已删除的死解析器）三处「应为 <root>/assets/qc.db」的口径全部不一致。
# 后果：源码运行时账号/科室/队列/设置落 <root>/qc.db，样本落 <root>/assets/qc.db，
# 多用户的数据归属无法靠同库约束保证（打包态两者恰好同目录，所以问题一直没暴露）。
_DEFAULT_DB_FILE = os.path.abspath(os.path.join(_PROJECT_ROOT, "assets", "qc.db"))
_DEFAULT_DB = "sqlite:///" + _urlquote(_DEFAULT_DB_FILE.replace("\\", "/"), safe="/:")

DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)


def _make_engine(url: str):
    """按 URL 创建 engine。SQLite 额外：
    - BEGIN IMMEDIATE：所有事务以写锁启动，把「判空→插入」等复合操作串行化，
      避免并发首账号引导时两个账号都成为 admin（见 accounts.create_account）。
    - busy_timeout：写锁等待 5s 而非立刻抛 "database is locked"。
    - journal_mode=WAL（2026-08-23 冷启动兜底）：读不阻塞写，桌面壳线程与
      uvicorn 线程并发访问 qc.db 时不再互锁。WAL 是库级持久属性（设一次永久生效，
      重复执行幂等无害）；个别文件系统/网络盘不支持时降级回滚模式即可，不致命。
    """
    _args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=_args, future=True, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")  # 写锁等待 30s（2026-08-18 由 5s 提高）
            try:
                cur.execute("PRAGMA journal_mode=WAL").fetchall()
            except Exception:  # noqa: BLE001  WAL 不被支持时保持默认 rollback journal
                pass
            cur.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin_immediate(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    return eng


engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


def set_database_override(url: str) -> None:
    """测试专用：把 engine/SessionLocal 切到指定库（如临时 sqlite 文件），
    避免测试污染真实数据。调用方在完成后可传 DATABASE_URL 恢复。"""
    global engine, SessionLocal, DATABASE_URL
    DATABASE_URL = url
    engine = _make_engine(url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db_path() -> Optional[str]:
    """当前数据库的 SQLite 文件绝对路径；非 SQLite 后端（如 PostgreSQL）返回 None。

    2026-09-30 新增：src/backup.py 自 v4.3.6 起一直写
    `from server.db import get_db_path`，但本函数从未存在——该 ImportError 在
    _database_files() 里未被 try 包住（try 只包住了调用处），直接向上抛出，
    于是 POST /api/v1/admin/backup/run 返回 500、每日自动备份被调度器
    静默吞掉，备份功能整体失效且无任何告警。
    """
    if engine.url.get_backend_name() != "sqlite":
        return None
    db_file = engine.url.database
    return os.path.abspath(str(db_file)) if db_file else None


def _migrate_legacy_root_db(eng) -> None:
    """把历史上误落在 <root>/qc.db 的数据一次性并回 assets/qc.db（幂等）。

    2026-09-30：修正默认库路径前，源码运行把 users/departments/queue/settings/
    orders/audit_log 写进了 <root>/qc.db。不改回会导致「账号/科室/历史队列消失」。
    逐表按列交集 INSERT OR IGNORE；目标表已有数据则整表跳过；旧文件保留不删。

    仅在**未被覆盖的默认库**上执行：测试/多实例通过 DATABASE_URL、
    QC_APPDATA 或 set_database_override 指定临时库时绝不搬运开发库数据。
    """
    if eng.url.get_backend_name() != "sqlite":
        return
    target = eng.url.database
    if not target or os.path.abspath(str(target)) != _DEFAULT_DB_FILE:
        return
    legacy = os.path.join(_PROJECT_ROOT, "qc.db")
    if os.path.abspath(legacy) == os.path.abspath(str(target)):
        return
    if not os.path.isfile(legacy):
        return
    try:
        from server import models  # noqa: F401  确保模型注册到 Base.metadata
        tables = [t.name for t in Base.metadata.sorted_tables]
        moved = []
        with eng.begin() as conn:
            conn.exec_driver_sql("ATTACH DATABASE ? AS legacy", (legacy,))
            try:
                for t in tables:
                    try:
                        n = conn.exec_driver_sql(
                            f"SELECT COUNT(*) FROM main.{t}").scalar()
                        if n:
                            continue   # 目标已有数据，不覆盖
                        # 安全说明：表名来自 models 声明、列名来自 PRAGMA，
                        # 均为本进程内白名单，非外部输入；无值拼接。
                        main_cols = [r[1] for r in conn.exec_driver_sql(
                            f"PRAGMA main.table_info({t})")]
                        leg_cols = [r[1] for r in conn.exec_driver_sql(
                            f"PRAGMA legacy.table_info({t})")]
                        common = [c for c in main_cols if c in leg_cols]
                        if not common:
                            continue
                        cc = ",".join(common)
                        conn.exec_driver_sql(
                            f"INSERT OR IGNORE INTO main.{t} ({cc}) "
                            f"SELECT {cc} FROM legacy.{t}")
                        moved.append(t)
                    except Exception:
                        continue   # 单表失败不影响其他表
            finally:
                try:
                    conn.exec_driver_sql("DETACH DATABASE legacy")
                except Exception:
                    pass
        if moved:
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def init_db() -> None:
    """建表（幂等）。延迟 import models 以避免循环依赖。"""
    from server import models  # noqa: F401  确保模型注册到 Base.metadata
    # 确保库文件所在目录存在：干净克隆 / assets 被误删时，SQLite 无法自动建目录，
    # create_all 会抛 "unable to open database file"，导致后端导入失败、桌面端打不开。
    _db_path = getattr(engine.url, "database", None)
    if _db_path:
        _dir = os.path.dirname(_db_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # 2026-09-30：schema 变更纳入版本化管理（server/migrations.py）。
    # 之前 _migrate_* 每次启动无条件跑、没有任何版本记录，出问题无法判断库升到第几版；
    # 现在：迁移前自动快照 + 只跑未应用的版本 + 失败留痕且不写版本号（下次重试）。
    # 注：老的 _migrate_queue_hash / _migrate_settings_uk 仍保留为函数体，
    # 由 migrations 0002/0003 转调（避免逻辑双写）；_migrate_legacy_root_db 是
    # **文件级**数据迁移（把误落在仓库根的 qc.db 并回 assets/），带自身守卫，
    # 保持每次启动无条件调用。
    try:
        from server import migrations as _mig
        _applied = _mig.run_migrations(engine)
        if _applied:
            try:
                from server.log_utils import get_logger
                get_logger().info("数据库迁移完成：%s", ", ".join(_applied))
            except Exception:       # silent-except-ok: 仅仅是补一条日志，失败不能影响启动
                pass
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet("server.db.migrations")
    _migrate_legacy_root_db(engine)


def _migrate_settings_uk(eng) -> None:
    """幂等迁移（2026-08-18 D13）：settings 的唯一性由 key 单列 → (key, user_id) 复合。

    背景：旧模型 `key` 声明 unique=True，SQLite 生成隐式索引 sqlite_autoindex_settings_1。
    它会让「同一 key 的全局值 + 用户级覆盖」两条记录**无法并存**，与
    「NULL=全局 / 非 NULL=用户级」的设计直接冲突。

    ⚠️ 2026-09-30 两处修复：
    1) **静默失效**：本函数此前用 `conn.execute("PRAGMA ...")` 字符串 SQL，
       SQLAlchemy 2.0 会抛 ObjectNotExecutableError，而异常被自身 except 吞掉
       → 自 2026-08-18 起这个迁移**从未真正执行**。
    2) **方案本身不可行**：即便 SQL 写对，SQLite **不允许 DROP 由 UNIQUE 约束隐式
       创建的自动索引**（sqlite_autoindex_*）→ 原 `DROP INDEX` 必然失败，
       复合索引也就永远建不上。正确做法是**重建表**（见下）。
    """
    try:
        with eng.begin() as c:
            names = [r[1] for r in c.exec_driver_sql(
                "PRAGMA index_list(settings)").fetchall()]
            if not names:
                return                      # 表不存在：交给 create_all
            legacy = False
            for name in names:
                cols = [r[2] for r in c.exec_driver_sql(
                    "PRAGMA index_info('%s')" % name).fetchall()]
                if name.startswith("sqlite_autoindex_settings") and cols == ["key"]:
                    legacy = True
            if not legacy:
                return                      # 已经是 (key, user_id) 复合唯一
            # 整表重建（SQLite 唯一可行路径）。全程在**同一个事务**内：
            # 任一步失败都会回滚，原 settings 表名与数据保持原样。
            old_cols = [r[1] for r in c.exec_driver_sql(
                "PRAGMA table_info(settings)").fetchall()]
            c.exec_driver_sql("ALTER TABLE settings RENAME TO settings_legacy_uk")
            from server import models
            models.Setting.__table__.create(bind=c)   # 新表自带 (key,user_id) 复合唯一
            common = [col.name for col in models.Setting.__table__.columns
                      if col.name in old_cols]
            if common:
                cc = ",".join(common)
                c.exec_driver_sql(
                    f"INSERT OR IGNORE INTO settings ({cc}) "
                    f"SELECT {cc} FROM settings_legacy_uk")
            c.exec_driver_sql("DROP TABLE settings_legacy_uk")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def _migrate_queue_hash(eng) -> None:
    """幂等迁移：旧 queue 表补 report_hash 列 + 唯一索引（数据库层并发去重）。

    create_all 对已存在的表不会加新列，需手工 ALTER；SQLite 索引名全局唯一，
    已存在时跳过（幂等）。补列后回填存量行的 hash（与 _queue_orm_all 同口径 MD5，
    去空格后计算），重复 hash 只保留最早一条。

    ⚠️ 2026-09-30 修复（静默失效）：同 _migrate_settings_uk —— 字符串 SQL 在
    SQLAlchemy 2.0 下必然抛 ObjectNotExecutableError 且被自身 except 吞掉，
    使本迁移从未生效。后果：**老库的 queue 表没有 report_hash 列**，而
    models.QueueItem 一直带该列 → 老库升级后入队 INSERT 会因"表中无此列"失败，
    "数据库层并发去重"也从未存在。
    """
    try:
        with eng.begin() as c:
            cols = [r[1] for r in c.exec_driver_sql(
                "PRAGMA table_info(queue)").fetchall()]
            if "report_hash" not in cols:
                c.exec_driver_sql("ALTER TABLE queue ADD COLUMN report_hash VARCHAR(32)")
            else:
                # 新库：models.QueueItem.report_hash unique=True 已由 create_all 生成
                # 唯一约束 autoindex，无需再手工建索引/清理（2026-08-18 双索引冗余修复）
                return
    except Exception:
        return  # 表不存在等：由 create_all 兜底
    try:
        with eng.begin() as c:
            rows = c.exec_driver_sql(
                "SELECT id, report_text FROM queue "
                "WHERE report_hash IS NULL OR report_hash = ''").fetchall()
            for rid, rt in rows:
                h = hashlib.md5("".join((rt or "").split()).encode("utf-8", "ignore")).hexdigest() \
                    if (rt or "").strip() else None
                if h:
                    c.exec_driver_sql("UPDATE queue SET report_hash=? WHERE id=?", (h, rid))
            # 唯一索引冲突防御（仅旧库补列时执行一次）：重复 hash 只保留最早一条
            c.exec_driver_sql(
                "DELETE FROM queue WHERE id NOT IN "
                "(SELECT MIN(id) FROM queue GROUP BY report_hash) "
                "AND report_hash IS NOT NULL AND report_hash != ''")
            c.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_queue_hash ON queue(report_hash)")
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def get_db():
    """FastAPI 依赖：yield 一个会话并在结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
