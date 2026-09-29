"""
test_health_endpoint.py — /api/v1/health 的 DB 探针回归（2026-09-30 审查新增）

背景：健康检查里写的是
    with _db_mod.get_db() as _sess:      # get_db() 是生成器依赖，不是上下文管理器
        _sess.execute(text("SELECT 1"))
→ 每次调用都抛 TypeError，被 except 吞成 `db="error: TypeError"`。
后果：探活永远"绿"、真正的连库/缺表故障永远查不出来（CI 的 launch-test 与
桌面壳就绪判断都看这个接口），属典型"静默失效的可观测性"。
"""
import os
import sqlite3
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="qc_health_")
os.environ["QC_APPDATA"] = os.path.join(_TMP, "appdata")
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "health.db")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "health.db").replace("\\", "/")
os.makedirs(os.environ["QC_APPDATA"], exist_ok=True)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient      # noqa: E402
from conftest import set_test_db               # noqa: E402
from server import main as appmod              # noqa: E402
from server import db as sdb                   # noqa: E402

set_test_db(os.environ["QC_DB_OVERRIDE"], os.environ["QC_APPDATA"])
_client = TestClient(appmod.app)


def _health() -> dict:
    r = _client.get("/api/v1/health")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_health_reports_db_connected():
    data = _health()
    assert data["status"] == "up"
    assert data["db"] == "connected", (
        f"DB 探针失效（返回 {data['db']}）——探活将无法发现连库/缺表故障")


def test_health_detects_missing_table(tmp_path):
    """反向验证：探针必须真的查库，而不是恒返回 connected。"""
    old_url = str(sdb.engine.url)
    db_file = tmp_path / "broken.db"
    try:
        sdb.set_database_override("sqlite:///" + str(db_file).replace("\\", "/"))
        sdb.init_db()
        assert _health()["db"] == "connected"
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("DROP TABLE samples")
            conn.commit()
        assert _health()["db"] != "connected", "samples 表缺失时探针仍报 connected"
    finally:
        sdb.set_database_override(old_url)
