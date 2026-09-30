"""
test_backup_upgrade_drill.py — 跨版本备份/恢复演练（2026-09-30 新增）

真实场景（医院就地升级）：医生用的是上一版的软件 → 生成了备份；
装上**新版**后从备份恢复 → 新版的 schema 变更必须能在**恢复出来的旧库**上
自动完成，且数据不丢。

这条链路此前从未被验证过，而它同时踩过本仓已知的两类坑：
1. 备份/恢复写错位置（`_resolve_known` 不一致 → "恢复成功"却不生效）；
2. schema 迁移静默失效（字符串 SQL 在 SQLAlchemy 2.0 下抛异常被吞 →
   老库的 queue 表一直缺 report_hash 列）。

本文件把「旧版库 → 备份 → 新版恢复 → 启动迁移」整条路走一遍。
"""
import json
import os
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from conftest import set_test_db          # noqa: E402

_LEGACY_ROWS = [
    ("双肺纹理增多，右肺上叶见结节。", "张三"),
    ("肝实质回声均匀，未见占位。", "李四"),
]


def _make_legacy_db(path) -> None:
    """造一个"上一版软件"的库：queue 无 report_hash，settings.key 单列唯一。"""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE queue (id INTEGER PRIMARY KEY, report_text TEXT,
                            meta_json TEXT, status TEXT);
        CREATE TABLE settings (key TEXT UNIQUE, value_json TEXT);
        CREATE TABLE samples (id INTEGER PRIMARY KEY, report_text TEXT);
    """)
    for text, patient in _LEGACY_ROWS:
        con.execute("INSERT INTO queue (report_text, meta_json, status) VALUES (?,?,?)",
                    (text, json.dumps({"patient": patient}, ensure_ascii=False), "pending"))
        con.execute("INSERT INTO samples (report_text) VALUES (?)", (text,))
    con.execute("INSERT INTO settings (key, value_json) VALUES ('theme', '\"dark\"')")
    con.commit()
    con.close()


@pytest.fixture()
def legacy_env(tmp_path, monkeypatch):
    import backup
    import samplelib

    legacy = tmp_path / "legacy.db"
    _make_legacy_db(legacy)
    monkeypatch.setattr(samplelib, "db_path", lambda: str(legacy))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    return {"backup": backup, "db": legacy, "dir": tmp_path}


def test_backup_of_legacy_db_is_restorable_and_upgradable(legacy_env, tmp_path, monkeypatch):
    bk, legacy = legacy_env["backup"], legacy_env["db"]

    # ① 用"上一版"的库做一次备份（备份逻辑本身不依赖新 schema）
    res = bk.run_backup()
    assert res["errors"] == [], f"备份报错：{res['errors']}"
    entry = next(f for f in res["files"] if f["label"] == "qc.db")
    assert os.path.getsize(entry["dest"]) > 0

    # ② 模拟"装了新版后从备份恢复"：恢复目标指向一个新位置
    target = tmp_path / "restored.db"
    # 必须用 monkeypatch（用完自动还原）：直接给共享模块的属性赋值会**永久**污染
    # 其它测试 —— 实测这一行曾让 test_data_layer_unified 的 4 个用例连带失败。
    monkeypatch.setattr(bk, "_resolve_known", lambda name: str(target))
    out = bk.restore_backup(os.path.basename(entry["dest"]))
    assert out["ok"], out
    assert os.path.abspath(out["restored"][0]["to"]) == os.path.abspath(str(target))

    # ③ 恢复出来的仍是**旧 schema**
    con = sqlite3.connect(target)
    cols = [r[1] for r in con.execute("PRAGMA table_info(queue)")]
    con.close()
    assert "report_hash" not in cols, "测试前提：恢复出来的应是旧结构"

    # ④ 新版启动（init_db）必须自动完成迁移
    set_test_db(str(target), str(tmp_path / "appdata"))
    con = sqlite3.connect(target)
    cols = [r[1] for r in con.execute("PRAGMA table_info(queue)")]
    n_queue = con.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
    n_samples = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    filled = con.execute("SELECT COUNT(*) FROM queue WHERE report_hash IS NOT NULL "
                         "AND report_hash <> ''").fetchone()[0]
    ucols = []
    for name, uniq in [(r[1], r[2]) for r in con.execute("PRAGMA index_list(settings)")]:
        cs = [r[2] for r in con.execute(f"PRAGMA index_info('{name}')")]
        if uniq:
            ucols.append(cs)
    con.close()

    assert "report_hash" in cols, "升级后 queue 仍缺 report_hash（迁移没生效）"
    assert n_queue == len(_LEGACY_ROWS), f"队列数据丢失：{n_queue}"
    assert n_samples == len(_LEGACY_ROWS), f"样本数据丢失：{n_samples}"
    assert filled == len(_LEGACY_ROWS), f"存量行未回填 report_hash：{filled}"
    assert ["key", "user_id"] in ucols, f"settings 唯一性未升级为复合：{ucols}"

    # ⑤ 迁移有版本记录（可追溯"这个库升到第几版"）
    from server import db as sdb
    from server import migrations as m
    applied = m.applied_versions(sdb.engine)
    assert {v for v, _ in m.registered()} <= applied


def test_restored_legacy_db_passes_integrity_check(legacy_env):
    """恢复出来的文件必须能通过 SQLite 完整性校验（VACUUM INTO 的意义所在）。"""
    bk = legacy_env["backup"]
    res = bk.run_backup()
    # 备份文件名带时间戳后缀（如 qc.db.20260930_134647），所以按 API 返回的
    # 产物清单来校验，而不是按扩展名猜。
    dbs = [f["dest"] for f in res["files"] if f["label"].endswith(".db")]
    assert dbs, f"备份未产出数据库文件：{res.get('files')}"
    for path in dbs:
        assert os.path.isfile(path)
        with sqlite3.connect(path) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", path
