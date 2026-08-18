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

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# 项目根：默认库落在 <root>/assets/qc.db。
# 冻结（PyInstaller）后 __file__ 指向 PYZ 合成路径，不能用其回溯，改用 exe 所在目录。
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = "sqlite:///" + os.path.join(_PROJECT_ROOT, "assets", "qc.db")

DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)


def _make_engine(url: str):
    """按 URL 创建 engine。SQLite 额外：
    - BEGIN IMMEDIATE：所有事务以写锁启动，把「判空→插入」等复合操作串行化，
      避免并发首账号引导时两个账号都成为 admin（见 accounts.create_account）。
    - busy_timeout：写锁等待 5s 而非立刻抛 "database is locked"。
    """
    _args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=_args, future=True, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")  # 写锁等待 30s（2026-08-18 由 5s 提高）
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
    _migrate_queue_hash(engine)


def _migrate_queue_hash(eng) -> None:
    """幂等迁移：旧 queue 表补 report_hash 列 + 唯一索引（数据库层并发去重）。

    create_all 对已存在的表不会加新列，需手工 ALTER；SQLite 索引名全局唯一，
    已存在时跳过（幂等）。补列后回填存量行的 hash（与 _queue_orm_all 同口径 MD5，
    去空格后计算），重复 hash 只保留最早一条。
    """
    try:
        with eng.connect() as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(queue)").fetchall()]
            if "report_hash" not in cols:
                c.execute("ALTER TABLE queue ADD COLUMN report_hash VARCHAR(32)")
                c.execute("COMMIT")
            else:
                # 新库：models.QueueItem.report_hash unique=True 已由 create_all 生成
                # 唯一约束 autoindex，无需再手工建索引/清理（2026-08-18 双索引冗余修复）
                return
    except Exception:
        return  # 表不存在等：由 create_all 兜底
    try:
        with eng.connect() as c:
            rows = c.execute(
                "SELECT id, report_text FROM queue "
                "WHERE report_hash IS NULL OR report_hash = ''").fetchall()
            for rid, rt in rows:
                h = hashlib.md5("".join((rt or "").split()).encode("utf-8", "ignore")).hexdigest() \
                    if (rt or "").strip() else None
                if h:
                    c.execute("UPDATE queue SET report_hash=? WHERE id=?", (h, rid))
            c.execute("COMMIT")
        with eng.begin() as c:
            # 唯一索引冲突防御（仅旧库补列时执行一次）：重复 hash 只保留最早一条
            c.execute(
                "DELETE FROM queue WHERE id NOT IN "
                "(SELECT MIN(id) FROM queue GROUP BY report_hash) "
                "AND report_hash IS NOT NULL AND report_hash != ''")
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_queue_hash ON queue(report_hash)")
    except Exception:
        pass


def get_db():
    """FastAPI 依赖：yield 一个会话并在结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
