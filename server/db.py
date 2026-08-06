"""
server/db.py — SQLAlchemy 统一数据层
====================================
- 通过 DATABASE_URL 环境变量切换数据库：默认 SQLite（院内单实例零运维），
  上线时改为 postgresql:// 即可切到 PostgreSQL，业务代码无需改动（抽象层价值）。
- 所有模型见 server/models.py，启动时 Base.metadata.create_all 建表。
- 多用户（科室自托管）核心：users / departments / samples / queue / settings 同库。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 项目根（server/ 的上一级），默认库落在 <root>/assets/qc.db
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = "sqlite:///" + os.path.join(_PROJECT_ROOT, "assets", "qc.db")

DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# future=True 使用 2.0 风格；echo 可在调试期开
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


def init_db() -> None:
    """建表（幂等）。延迟 import models 以避免循环依赖。"""
    import models  # noqa: F401  确保模型注册到 Base.metadata
    # 确保库文件所在目录存在：干净克隆 / assets 被误删时，SQLite 无法自动建目录，
    # create_all 会抛 "unable to open database file"，导致后端导入失败、桌面端打不开。
    _db_path = getattr(engine.url, "database", None)
    if _db_path:
        _dir = os.path.dirname(_db_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖：yield 一个会话并在结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
