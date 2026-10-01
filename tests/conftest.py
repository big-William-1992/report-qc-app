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


def subprocess_env(**extra) -> dict:
    """返回给 `subprocess.run/Popen` 用的环境变量：强制子进程 UTF-8 输出。

    为什么需要（2026-10-01 实测）：本仓的脚本/工具大量 `print` 中文与 ✓/✗ 符号。
    Windows 上子进程 stdout 默认走 **cp1252**（即便 Python 3.15 之前，
    非 UTF-8 控制台就是 ANSI 代码页），于是：

        UnicodeEncodeError: 'charmap' codec can't encode character '\\u2717'

    多个测试靠「跑脚本 + 断言返回码/输出」来验证门禁，在 Windows CI 上因此集体
    失败（Build Windows 实测：test_silent_exceptions / test_semantic_eval /
    test_diagnostic_bundle / test_frontend_bundle 等）。这不是脚本写错了，
    而是**子进程输出编码**问题，故在调用侧统一归一化。

    用法：`subprocess.run([...], env=subprocess_env())`
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.update({k: str(v) for k, v in extra.items()})
    return env


def checked_output(proc) -> tuple:
    """返回 `(stdout, stderr)`，并把「管道内容意外为 None」显式暴露出来。

    Windows 上实测：子进程 `returncode=0` 但 `proc.stdout is None`
    （管道内容读不到，且不报错）→ 断言 `"关键字" in r.stdout` 抛
    `TypeError: argument of type 'NoneType' is not iterable`，
    把「输出丢失」这一真因伪装成断言类型错误。

    这里统一转成字符串；若返回码为 0 却**完全没有任何输出**，说明子进程输出
    丢失（而非"脚本没打印"），此时抛 AssertionError 明确指出问题，
    避免下游再报难以理解的 NoneType 错。
    """
    out = proc.stdout if proc.stdout is not None else ""
    err = proc.stderr if proc.stderr is not None else ""
    if proc.returncode == 0 and not out and not err:
        raise AssertionError(
            f"子进程返回码 0 但 stdout/stderr 均为空（输出可能丢失）：{proc.args}"
        )
    return out, err


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


# ── 仓库内真实文件保护（2026-10-01 新增）────────────────────────────────────
# 背景（真实事故）：写备份恢复的回归用例时，用 `restore_backup("qc.db")` 验证
# 「正常名仍可恢复」，而 `backup._resolve_known("qc.db")` 解析到的目标是
# **真实开发库 `<root>/assets/qc.db`** —— 测试把假内容复制覆盖上去，
# 开发库从 77824 字节被毁成 14 字节，后续 `import server.main` 直接
# `sqlite3.DatabaseError: file is not a database`，整个测试套件无法收集。
#
# 这类事故的共同点：**测试直接操作了仓库内的真实文件**。仅靠"写用例时小心"
# 不可靠，故加本守卫：跑完整个会话后校验这些文件未被改动，改了就直接报错。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROTECTED_REPO_FILES = (
    "assets/qc.db",
    "assets/accounts.db",
    "assets/feedback.db",
    "assets/rules_config.json",
    "assets/ris_config.json",
    "assets/license.dat",
    "qc.db",
)


def _fingerprint(path: str):
    """返回文件指纹；不存在返回 None。

    只比对**内容哈希**，不比对 mtime：实测 `assets/ris_config.json` 会被某些
    用例以**相同内容**重写一遍（mtime 变、内容不变），用 mtime 会误报。
    本守卫要抓的是「真实文件被改坏/覆盖」，内容相同就不算事故。
    """
    if not os.path.isfile(path):
        return None
    try:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "<unreadable>"


@pytest.fixture(scope="session", autouse=True)
def _guard_repo_files():
    """会话前后比对仓库内真实文件；**已存在**的文件被改写则本会话失败。

    ⚠️ 判据要点（踩过的坑）：只保护**会话开始时已存在**的文件，不能把「不存在 →
    被创建」也算事故。原因：`assets/*.db`、`assets/ris_config.json`、`assets/license.dat`
    等**未纳入 git**（开发机的本地数据），CI 全新 checkout 上根本不存在，
    用例运行中创建它们是**正常行为**（如 health/data 层用例会初始化临时库到该路径）。
    第一版按「受保护清单」无差别比对，导致 CI 上 `ris_config.json`
    `None → 哈希` 被判为污染（Build Windows 单元测试闸实测误报）。

    只**检测并报错**，不做自动还原 —— 自动还原会把"测试污染了真实数据"这件事
    掩盖过去，而数据可能已经被破坏，掩盖更危险。
    """
    tracked = {rel: _fingerprint(os.path.join(_REPO_ROOT, rel))
               for rel in _PROTECTED_REPO_FILES}
    # 只关心开始时确实存在的文件（CI 上未跟踪的文件本就不存在）
    before = {k: v for k, v in tracked.items() if v is not None}
    yield
    changed = []
    for rel, old in before.items():
        now = _fingerprint(os.path.join(_REPO_ROOT, rel))
        if now != old:
            changed.append(rel)
    if changed:
        raise AssertionError(
            "测试改动了仓库内**已存在**的真实文件（必须用 tmp_path / QC_APPDATA / "
            "QC_BACKUP_DIR / temp_db 隔离）：\n  " + "\n  ".join(changed) +
            "\n请修复对应测试；若属误报请调整 _PROTECTED_REPO_FILES。"
        )

