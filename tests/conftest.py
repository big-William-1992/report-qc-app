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


# ── 临时库绑定：完整还原（2026-09-30）────────────────────────────────────────
# 背景：库绑定是**全局单例**（server/db.py 的 engine/SessionLocal），且
# set_test_db 还会写 QC_DB_OVERRIDE / DATABASE_URL / QC_APPDATA 三个环境变量。
# 测试里改绑后若只还原对象、不还原环境变量，后续模块懒加载时会按残留的 env
# 去建新引擎 → 出现"别的模块莫名其妙连到别的库"的连锁失败（实测 8 个用例）。
# 新写的测试请用 `with temp_db(path, appdata):` 或 conftest.temp_db 夹具。
_SNAPSHOT_KEYS = ("QC_DB_OVERRIDE", "DATABASE_URL", "QC_APPDATA")


def snapshot_binding():
    """快照当前"库绑定 + 相关环境变量"，供 fixture 在用例结束后完整还原。"""
    from server import db as _srv_db
    return (_srv_db.engine, _srv_db.SessionLocal,
            {k: os.environ.get(k) for k in _SNAPSHOT_KEYS})


def restore_binding(snap) -> None:
    """还原 snapshot_binding() 的快照。"""
    from server import db as _srv_db
    engine, session = snap[0], snap[1]
    _srv_db.engine, _srv_db.SessionLocal = engine, session
    for k, v in (snap[2] or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def temp_db(db_path: str, appdata_path: str = None):
    """绑定临时库的上下文管理器：退出时把库绑定与环境变量**全部还原**。

    用法：
        from conftest import temp_db
        with temp_db(str(tmp/"t.db"), str(tmp/"appdata")):
            ...   # 这里 server.db.SessionLocal 指向临时库
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        snap = snapshot_binding()
        try:
            set_test_db(db_path, appdata_path)
            yield
        finally:
            restore_binding(snap)

    return _cm()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_global_db_binding():
    """**每个用例前后自动快照/还原全局库绑定**（2026-09-30 新增，全仓生效）。

    为什么放在 conftest 而不是各测试自己管：库绑定是全局单例，任何一处
    `set_test_db()` 都会影响"之后跑的所有模块"。实测一个测试模块在用例里改绑
    而不还原，会连锁导致另外 6 个用例失败（data_layer_unified / health_endpoint /
    queue_ingest_paths），且失败信息完全指向无关模块，极难排查。

    这里在每个用例前记录（engine、SessionLocal、QC_DB_OVERRIDE、DATABASE_URL、
    QC_APPDATA），用例结束后原样还原：
      · 在 import 时绑定的模块：快照即其绑定，还原后不受影响；
      · 在用例内临时绑定的模块：自动回到进入本用例前的状态。
    """
    snap = snapshot_binding()
    # 归一化：让「环境变量」与「当前 engine」指向同一个库。
    # 为什么需要：多个测试模块在 **import 时**各自绑定，而 pytest 会先把所有模块
    # import 完再跑用例 —— 最后 import 的模块可能只改了 env（没重建 engine）或反之，
    # 于是 samplelib.db_path()（读 QC_DB_OVERRIDE）与 server.db.get_db_path()（读
    # engine）分叉，表现为"数据层路径分裂"这类与现场无关的诡异失败（实测踩到）。
    try:
        from server import db as _srv_db
        _p = _srv_db.get_db_path()
        if _p:
            os.environ["QC_DB_OVERRIDE"] = _p
            os.environ["DATABASE_URL"] = "sqlite:///" + _p.replace("\\", "/")
    except Exception:
        pass
    yield
    restore_binding(snap)
