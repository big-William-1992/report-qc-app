"""
统一把 src 加入 sys.path（此前 17 个测试文件各自重复 insert）。
额外提供测试专用 DB 隔离辅助，避免 SQLAlchemy 绑定引用分叉。
"""
import os
import sys

_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def set_test_db(db_path: str, appdata_path: str = None) -> None:
    """测试统一入口：切换数据库、初始化表、清登录限流状态。

    SQLAlchemy 绑定已由 src/accounts.py 与 server/core.py 改为动态读取
    server.db.SessionLocal，因此这里只需要集中改 server.db 的绑定即可。
    """
    db_abs = os.path.abspath(db_path)
    os.environ["QC_DB_OVERRIDE"] = db_abs
    if appdata_path:
        os.environ["QC_APPDATA"] = os.path.abspath(appdata_path)
    os.environ["DATABASE_URL"] = "sqlite:///"+db_abs.replace("\\", "/")

    from server import db as _srv_db
    from server import deps as _srv_deps
    import accounts as _accounts

    _srv_db.set_database_override("sqlite:///"+db_abs.replace("\\", "/"))
    _accounts._DB_OVERRIDE = ""
    _srv_deps._LOGIN_FAIL.clear()
    _srv_db.init_db()
