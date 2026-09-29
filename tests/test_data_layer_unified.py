"""
test_data_layer_unified.py — 数据层「单一真相源」回归（2026-09-30 审查新增）

锁定三个曾经真实存在、且都会静默损坏用户数据的问题：

1. **源码态库位置分裂**：server/db.py 曾算 <root>/qc.db（漏了 assets/ 段），
   src/samplelib.py 算 <root>/assets/qc.db —— 账号/科室在一个库、样本在另一个库，
   多用户的数据归属无法靠同库约束保证。打包态两者恰好同目录，所以一直没暴露。
2. **samplelib 自建 schema**：手写 CREATE TABLE / ALTER 与 models.Sample 形成
   两份声明；现统一由 ORM 声明建表。
3. **备份整体失效**：src/backup.py 一直 `from server.db import get_db_path`，
   而该函数不存在 —— ImportError 让「一键备份」接口 500、每日自动备份被调度器
   静默吞掉；且恢复时一律写回 <assets>/，打包态「恢复成功」却不生效。
   本文件做备份→恢复往返与目标路径断言（此前 backup 无任何测试）。
"""
import os
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import samplelib                      # noqa: E402
from server import db as sdb          # noqa: E402


# --------------------------------------------------------------------------
# 1) 统一库位置
# --------------------------------------------------------------------------

def test_samplelib_and_orm_share_one_database_file():
    """样本库与 ORM 必须是同一个 SQLite 文件（否则多用户归属无法保证）。"""
    assert sdb.get_db_path(), "ORM 应解析出 SQLite 文件路径"
    assert os.path.abspath(samplelib.db_path()) == os.path.abspath(sdb.get_db_path()), (
        f"数据层路径分裂：samplelib={samplelib.db_path()} "
        f"vs server.db={sdb.get_db_path()}")


def test_get_db_path_exists_and_returns_file():
    """server.db.get_db_path 必须存在（backup.py 依赖它）。"""
    p = sdb.get_db_path()
    assert isinstance(p, str) and p.endswith(".db")


def test_samples_table_declared_once_by_models():
    """samples 表结构只能来自 models.Sample —— 不允许再手写建表 DDL。"""
    src = open(os.path.join(_ROOT, "src", "samplelib.py"), encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS samples" not in src, \
        "samplelib.py 不应再手写建表 DDL（schema 真相源是 server/models.py）"
    assert "_SAMPLES_TABLE_SQL" not in src, "旧的表结构常量应已删除"
    import server.models as models
    cols = {c.name for c in models.Sample.__table__.columns}
    for col in ("ts", "patient", "gender", "age", "modality", "applied_site",
                "laterality", "user_id", "dept_id", "report_text",
                "findings_json", "scores_json"):
        assert col in cols, f"models.Sample 缺列 {col}（samplelib 依赖）"


def test_samplelib_roundtrip_on_explicit_path(tmp_path):
    """显式 path= 的临时库仍能独立建表/读写（多机合并与测试隔离依赖）。"""
    db_file = str(tmp_path / "samples.db")
    sid = samplelib.save_sample(
        "报告正文", {"patient": "张三", "gender": "男", "age": "54"},
        [], {"准确性": 90}, path=db_file, user_id="0559", dept_id="RAD")
    assert sid > 0
    rows = samplelib.list_samples(db_file)
    assert rows and rows[0]["user_id"] == "0559"
    full = samplelib.get_sample(sid, db_file)
    assert full["dept_id"] == "RAD", "科室归属必须随样本落库（多用户隔离前置）"
    assert full["report_text"] == "报告正文"
    assert isinstance(full["created_at"], str), "created_at 需保持字符串（兼容既有接口）"
    assert samplelib.count_samples(db_file) == 1
    samplelib.delete_sample(sid, db_file)
    assert samplelib.count_samples(db_file) == 0


def test_stats_keep_shapes(tmp_path):
    """统计接口的返回结构与口径不得变化（前端看板/报表依赖）。"""
    db_file = str(tmp_path / "stats.db")
    findings = [{"error_type": "性别矛盾", "rule_id": "R1", "severity": "high"},
                {"error_type": "错别字", "rule_id": "R8", "severity": "low"}]
    samplelib.save_sample("报告正文", {}, [], {"准确性": 80},
                          path=db_file, user_id="1001")
    # 直接写 findings_json 以固定输入
    with sqlite3.connect(db_file) as c:
        c.execute("UPDATE samples SET findings_json=?, dept_id='RAD'",
                  (__import__("json").dumps(findings, ensure_ascii=False),))
        c.commit()
    assert samplelib.stats_by_error_type(db_file) == {"性别矛盾": 1, "错别字": 1}
    rep = samplelib.stats_report(path=db_file)
    assert rep["period"]["total"] == 1
    assert rep["period"]["critical"] == 1 and rep["period"]["info"] == 1
    assert rep["rule_top"][0] == {"rule_id": "R1", "count": 1}
    assert rep["doctor_rank"][0]["user_id"] == "1001"
    assert rep["daily"][0]["count"] == 1
    assert list(samplelib.stats_by_date(db_file).values())[0]["avg_acc"] == 80.0


def test_import_keeps_dept_id(tmp_path):
    """跨机合并/导入必须保留 dept_id（旧实现漏了这一列，破坏科室隔离）。"""
    src = str(tmp_path / "src.db")
    dst = str(tmp_path / "dst.db")
    samplelib.save_sample("报告A", {"patient": ""}, [], {}, path=src,
                          user_id="1001", dept_id="RAD-A")
    inserted, skipped = samplelib.merge_from_db(src, dst)
    assert (inserted, skipped) == (1, 0)
    row = samplelib.get_sample(1, dst)
    assert row["dept_id"] == "RAD-A"
    assert row["user_id"] == "1001"


# --------------------------------------------------------------------------
# 2) 备份 / 恢复（此前整条链路不可用，且无任何测试）
# --------------------------------------------------------------------------

@pytest.fixture()
def isolated_backup(tmp_path, monkeypatch):
    """把「统一库」与备份目录都指向临时位置，避免碰真实数据。"""
    import backup
    unified = tmp_path / "qc.db"
    samplelib.init_db(str(unified))
    samplelib.save_sample("备份用报告", {"patient": ""}, [], {"准确性": 88},
                          path=str(unified), user_id="1001", dept_id="RAD")
    monkeypatch.setattr(samplelib, "db_path", lambda: str(unified))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    return backup, unified


def test_backup_lists_unified_db_once(isolated_backup):
    backup, unified = isolated_backup
    files = backup._database_files()
    assert files, "应至少备份到统一库文件"
    paths = [os.path.abspath(p) for _, p in files]
    assert len(paths) == len(set(paths)), f"同一文件被备份多份：{paths}"
    assert os.path.abspath(str(unified)) in paths


def test_backup_run_produces_valid_sqlite(isolated_backup):
    backup, _ = isolated_backup
    res = backup.run_backup()
    assert res["errors"] == [], f"备份报错：{res['errors']}"
    assert res["files"], "备份未产出任何文件"
    for f in res["files"]:
        if f["label"].endswith(".db"):
            assert os.path.getsize(f["dest"]) > 0
            with sqlite3.connect(f["dest"]) as c:
                assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_resolve_known_matches_runtime_locations(isolated_backup):
    """恢复目标必须等于真实运行时位置（打包态库在用户目录，不在 assets/）。"""
    backup, unified = isolated_backup
    assert os.path.abspath(backup._resolve_known("qc.db")) == os.path.abspath(str(unified))
    import paths
    assert backup._resolve_known("rules_config.json") == paths.rules_config_path()
    assert backup._resolve_known("ris_config.json") == paths.ris_config_path()


def test_restore_roundtrip_hits_resolved_target(isolated_backup):
    backup, unified = isolated_backup
    res = backup.run_backup()
    entry = next(f for f in res["files"] if f["label"] == "qc.db")
    out = backup.restore_backup(os.path.basename(entry["dest"]))
    assert out["ok"], out
    assert os.path.abspath(out["restored"][0]["to"]) == os.path.abspath(str(unified))
    # 恢复后的库仍可读回数据
    assert samplelib.get_sample(1, str(unified))["dept_id"] == "RAD"
