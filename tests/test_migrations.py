"""
test_migrations.py — 版本化数据库迁移与「就地升级」演练（2026-09-30 审计新增）

为什么重要：医院场景是**就地升级、库里已有真实数据**。此前 schema 变更是散落在
`db.init_db()` 里的临时函数，每次启动无条件跑，且**没有任何版本记录**：
出了问题无法判断库升到第几版，也没有迁移前现场可退。

本文件守：
1. 新库首次启动会把所有迁移登记进 `schema_migrations`；
2. 重复启动是幂等的（不会重复应用、不报错）；
3. **老库升级演练**：用旧 schema（queue 无 report_hash、settings.key 单列唯一）造库，
   走正常启动流程后必须自动补齐列/索引并回填数据；
4. 迁移前自动快照存在（唯一的退路），且保留份数有上限。
"""
import os
import sqlite3
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from conftest import set_test_db, snapshot_binding, restore_binding  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_binding():
    """每个用例结束后完整还原"库绑定 + 环境变量"。

    本模块会把全局 engine 指向多个临时库；不还原会让后面跑的模块连到别的库上
    （仓库的库绑定是全局单例，见 AGENTS §10）。conftest.snapshot_binding/
    restore_binding 就是为此提供的公共工具。
    """
    snap = snapshot_binding()
    yield
    restore_binding(snap)


@pytest.fixture()
def workdir():
    d = tempfile.mkdtemp(prefix="qc_mig_")
    return d


def _cols(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def test_migration_registry_is_wellformed():
    from server import migrations as m
    reg = m.registered()
    assert reg, "迁移清单为空？"
    versions = [v for v, _d in reg]
    assert len(versions) == len(set(versions)), "迁移版本号重复"
    assert versions == sorted(versions), "迁移未按版本号排序"
    for v, _d in reg:
        assert len(v.split("_")[0]) == 4 and v.split("_")[0].isdigit(), \
            f"版本号应形如 NNNN_slug：{v}"


def test_fresh_db_applies_all_and_is_idempotent(workdir):
    from server import db as sdb
    from server import migrations as m

    db_path = os.path.join(workdir, "fresh.db")
    set_test_db(db_path, os.path.join(workdir, "appdata"))

    applied = m.applied_versions(sdb.engine)
    assert applied == {v for v, _ in m.registered()}, \
        f"新库未登记全部迁移：缺少 {({v for v, _ in m.registered()} - applied)}"

    # 再跑一次：应无待应用项、无异常
    assert m.pending(sdb.engine) == []
    assert m.run_migrations(sdb.engine) == [], "重复启动不应重复应用迁移"


def test_old_database_is_upgraded_in_place(workdir):
    """核心演练：旧 schema 库 + 正常启动流程 → 自动补齐并回填。"""
    db_path = os.path.join(workdir, "legacy.db")
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE queue (id INTEGER PRIMARY KEY, report_text TEXT,
                            meta_json TEXT, status TEXT);
        INSERT INTO queue (report_text, meta_json, status)
             VALUES ('双肺纹理增多，右肺上叶见结节。', '{}', 'pending');
        INSERT INTO queue (report_text, meta_json, status)
             VALUES ('双肺纹理增多，右肺上叶见结节。', '{}', 'pending');
        CREATE TABLE settings (key TEXT UNIQUE, value TEXT);
        INSERT INTO settings VALUES ('theme', 'dark');
    """)
    con.commit()
    con.close()
    assert "report_hash" not in _cols(db_path, "queue")

    set_test_db(db_path, os.path.join(workdir, "appdata"))       # = 正常启动

    # ① 列已补
    assert "report_hash" in _cols(db_path, "queue"), "queue.report_hash 未补上"
    # ② 存量行已回填，且重复内容只保留一条被去重（同一 hash）
    con = sqlite3.connect(db_path)
    filled = con.execute("SELECT COUNT(*) FROM queue WHERE report_hash IS NOT NULL "
                         "AND report_hash <> ''").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
    idx_names = [r[1] for r in con.execute("PRAGMA index_list(settings)")]
    # 复合唯一索引的判定看**列**而不是名字：SQLite 对 UNIQUE 约束生成的是
    # sqlite_autoindex_*（会忽略模型里写的约束名），所以按列断言才可靠。
    unique_cols = []
    for name, uniq in [(r[1], r[2]) for r in con.execute("PRAGMA index_list(settings)")]:
        cols = [r[2] for r in con.execute(f"PRAGMA index_info('{name}')")]
        if uniq:
            unique_cols.append(cols)
    con.close()
    assert filled >= 1, "存量 queue 行未回填 report_hash"
    assert total >= 1
    # ③ settings 唯一性已从 key 单列改为 (key, user_id) 复合
    assert ["key", "user_id"] in unique_cols, \
        f"settings 仍不是复合唯一：唯一索引列={unique_cols}（索引={idx_names}）"
    assert ["key"] not in unique_cols, \
        f"旧的 key 单列唯一仍存在：{unique_cols}"

    # ④ 迁移前快照存在（唯一的退路）
    snaps = [f for f in os.listdir(workdir) if ".premigrate-" in f]
    assert snaps, "缺少迁移前快照（升坏就无路可退）"

    # ⑤ 版本表记录了本次升级
    from server import db as sdb
    from server import migrations as m
    assert m.applied_versions(sdb.engine) == {v for v, _ in m.registered()}


def test_snapshot_retention_is_bounded(workdir):
    """快照不能无限堆积（会吃掉用户磁盘）。"""
    from server import db as sdb
    from server import migrations as m
    db_path = os.path.join(workdir, "snap.db")
    set_test_db(db_path, os.path.join(workdir, "appdata"))

    for i in range(6):
        m.snapshot_before_migrate(sdb.engine, f"9999_test{i}", keep=3)
    snaps = [f for f in os.listdir(workdir) if ".premigrate-" in f]
    assert len(snaps) <= 3, f"快照保留份数超限：{snaps}"


def test_failed_migration_does_not_block_startup(workdir, monkeypatch):
    """迁移失败必须留痕但不写版本号、不阻断启动（用户还能进界面导出数据）。"""
    from server import db as sdb
    from server import migrations as m

    db_path = os.path.join(workdir, "fail.db")
    set_test_db(db_path, os.path.join(workdir, "appdata"))

    def boom(_eng):
        raise RuntimeError("模拟迁移失败")

    bad = ("9998_broken", "故意失败的迁移", boom)
    monkeypatch.setattr(m, "_MIGRATIONS", list(m._MIGRATIONS) + [bad])
    applied = m.run_migrations(sdb.engine, snapshot=False)
    assert "9998_broken" not in applied, "失败迁移不应记为已应用"
    assert "9998_broken" not in m.applied_versions(sdb.engine), "失败迁移不应写版本号"
    assert "9998_broken" in m.pending(sdb.engine), "失败迁移应留待下次重试"


def test_global_and_user_setting_can_coexist_after_upgrade(workdir):
    """升级的**业务目的**：同一个 key 允许「全局值 + 用户级覆盖」并存。

    旧结构（key 单列唯一）下第二条插入会 IntegrityError —— 这正是当年
    「用户改了设置却保存不上」的根因。这里用 ORM 真插一条全局 + 一条用户级。
    """
    db_path = os.path.join(workdir, "coexist.db")
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE settings (key TEXT UNIQUE, value_json TEXT);
        INSERT INTO settings VALUES ('theme', '"dark"');
    """)
    con.commit()
    con.close()

    set_test_db(db_path, os.path.join(workdir, "appdata"))

    from server import db as sdb
    from server.models import Setting, User
    with sdb.SessionLocal() as s:
        # 造一个用户，作为 user_id 外键目标
        u = User(emp_id="mig_u1", name="迁移测试", pwd_hash="x", salt="y", role="doctor")
        s.add(u)
        s.commit()
        uid = u.id
        # 存量全局值仍在
        assert s.query(Setting).filter(Setting.key == "theme").count() >= 1
        # 同一 key 再插一条用户级覆盖：旧结构下这里会 IntegrityError
        s.add(Setting(key="theme", value_json='"light"', user_id=uid))
        s.commit()
        rows = s.query(Setting).filter(Setting.key == "theme").all()
        assert len({(r.user_id) for r in rows}) >= 2, "全局与用户级未能并存"
