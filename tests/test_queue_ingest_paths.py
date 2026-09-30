"""
test_queue_ingest_paths.py — 队列写入必须落在「界面读到的那一份」（2026-09-30 审计新增）

背景（真实功能缺陷）：
  server/deps.py 里曾另有一份 `_queue_add_text`，把队列写进 `qc_queue.json`；
  而队列端点 `GET /api/v1/queue` 读的是 qc.db 的 QueueItem 表（core._queue_orm_all）。
  route_push.py（PACS 主动推送）恰好 import 的是 deps 那份 →
  **推送进来的报告在界面队列里永远看不到**（RIS 轮询那条路径当时已改用 ORM）。
  另：core._appdata_dir() 与 deps._appdata_dir() 各算一套目录（非 Windows 时
  一个是 ~/.medical_report_qc、一个是用户数据目录），同一个队列文件写在两处。

本文件从 HTTP 入口验证：
1. PACS 推送（/api/v1/push/report）入队后，能在 /api/v1/queue 里读到；
2. 队列的 app 数据目录（core / deps / paths）三者一致。
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="qc_queue_")
os.environ["QC_APPDATA"] = os.path.join(_TMP, "appdata")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "queue.db").replace("\\", "/")
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "queue.db")
os.environ["PUSH_API_KEY"] = "push-test-key"
os.makedirs(os.environ["QC_APPDATA"], exist_ok=True)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from fastapi.testclient import TestClient      # noqa: E402
from server import main as appmod              # noqa: E402
from server import core, deps, security        # noqa: E402
import paths                                   # noqa: E402

_client = TestClient(appmod.app, client=("127.0.0.1", 50000))


@pytest.fixture()
def auth():
    """覆盖鉴权依赖：本文件只验证队列写入路径，避免依赖库里是否已有账号。"""
    appmod.app.dependency_overrides[security.require_emp_local] = lambda: "queue_test"
    try:
        yield {"X-Emp-Id": "queue_test"}
    finally:
        appmod.app.dependency_overrides.pop(security.require_emp_local, None)

_PUSH_BODY = {
    "report_text": ("患者男，58岁。检查部位：胸部。\n"
                    "检查所见：右肺上叶见结节，边界清。\n"
                    "诊断印象：右肺上叶结节。"),
    "patient": "张三",
    "gender": "男",
    "age": "58",
    "modality": "CT",
    "applied_site": "胸部",
}


def test_app_data_dir_has_single_source():
    """core / deps / paths 的 app 数据目录必须是同一个。"""
    dirs = {os.path.abspath(core._appdata_dir()),
            os.path.abspath(deps._appdata_dir()),
            os.path.abspath(paths.user_data_dir())}
    assert len(dirs) == 1, f"app 数据目录分叉：{dirs}"


def test_pushed_report_lands_in_queue_endpoint(auth):
    """PACS 推送入队 → GET /api/v1/queue 必须能看到（此前写进 JSON，永远看不到）。"""
    r = _client.post("/api/v1/push/report", json=_PUSH_BODY,
                     headers={"X-API-Key": "push-test-key"})
    assert r.status_code == 200, r.text

    q = _client.get("/api/v1/queue", headers=auth)
    assert q.status_code == 200, q.text
    items = q.json()["data"]["items"]
    assert any((_PUSH_BODY["report_text"][:20] in (it.get("text") or ""))
               for it in items), \
        f"推送的报告没有进入队列（界面读的是 QueueItem 表）：{[it.get('text','')[:20] for it in items]}"


def test_queue_written_to_db_not_json():
    """入队后 DB 里应有记录，且不应再产生 qc_queue.json。"""
    _client.post("/api/v1/push/report", json=dict(_PUSH_BODY, report_text="另一份报告 1 2 3"),
                 headers={"X-API-Key": "push-test-key"})
    rows = core._queue_orm_all()
    assert rows, "入队后 QueueItem 表为空——写入没有走 ORM"
    assert not os.path.exists(os.path.join(core._appdata_dir(), "qc_queue.json")), \
        "不应再生成 qc_queue.json（队列已收敛到 qc.db）"


def test_queue_add_text_is_single_implementation():
    """main 与 deps 的入队都必须转调 core.queue_add_text。"""
    for rel in ("server/main.py", "server/deps.py"):
        src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        assert "core.queue_add_text" in src or "queue_add_text(text, meta, source=source)" in src, \
            f"{rel} 的 _queue_add_text 没有转调 core 的唯一实现"
        assert "_save_queue(items)" not in src, f"{rel} 仍在写 JSON 队列"
