"""
test_feedback_endpoint.py — 医生反馈回流端到端回归（2026-09-30 审查新增）

背景（真实缺陷）：`badcase_store` 的 `feedback` 表只在显式调用 `init_db()` 时创建，
而 `server/main.py` 的启动流程**从未调用**它。于是生产环境：

    POST /api/v1/feedback
      → sqlite3.OperationalError: no such table: feedback
      → 被端点 except 吞成 ok=False「反馈暂存失败（不影响质控结果）」（HTTP 仍然 200）
      → 医生点的每一次「误报/漏报」全部静默丢失

`tests/test_badcase_store.py` 之所以没抓到：它自己先调 `init_db(path)`，
即**由测试代劳了生产没做的前置条件**。本文件从 HTTP 入口走完整链路。

隔离方式（不依赖 import 顺序、不复用其它测试模块的库状态）：
- 引入 server.main 之前先设好 DATABASE_URL / QC_APPDATA，避免 import 期的
  `db.init_db()` 落到真实开发库；**不调用 set_test_db**（不改全局绑定）；
- 鉴权用 `app.dependency_overrides` 覆盖 `require_emp_local`，不建账号
  （账号引导路径在其它模块已建过账号的库上会被要求 admin，属跨模块耦合）；
- 反馈库路径用 monkeypatch 钉到本用例的 tmp_path，断言只认这个文件。
"""
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="qc_feedback_")
os.environ["QC_APPDATA"] = os.path.join(_TMP, "appdata")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "orm.db").replace("\\", "/")
os.makedirs(os.environ["QC_APPDATA"], exist_ok=True)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient      # noqa: E402
from server import main as appmod              # noqa: E402
from server import security                    # noqa: E402

_client = TestClient(appmod.app, client=("127.0.0.1", 50000))
_EMP = "fb_test_user"

_PAYLOAD = {
    "feedback_type": "false_positive",
    "report_text": "患者男，58岁。检查所见：子宫未见异常。",
    "rule_id": "R19-HOMOPHONE",
    "engine_source": "rules",
    "severity": "low",
    "message": "疑似错字",
    "snippet": "子宫",
    "suggestion": "",
    "user_note": "这是正常表述",
}


@pytest.fixture()
def auth():
    """覆盖鉴权依赖：本文件只验证反馈链路本身。"""
    appmod.app.dependency_overrides[security.require_emp_local] = lambda: _EMP
    try:
        yield {"X-Emp-Id": _EMP}
    finally:
        appmod.app.dependency_overrides.pop(security.require_emp_local, None)


def _pin_feedback_db(monkeypatch, tmp_path) -> str:
    """把反馈库钉到本用例的临时文件（与 QC_DB_OVERRIDE / 全局绑定解耦）。"""
    import badcase_store as bs
    fdb = str(tmp_path / "feedback.db")
    monkeypatch.setattr(bs, "_db_path", lambda path=None: path or fdb)
    monkeypatch.setattr(bs, "_ENSURED", set())
    return fdb


def test_feedback_persists_end_to_end(auth, tmp_path, monkeypatch):
    """走 HTTP 提交反馈 → 必须真的落库（修复前返回 ok=False）。"""
    fdb = _pin_feedback_db(monkeypatch, tmp_path)
    import badcase_store as bs

    r = _client.post("/api/v1/feedback", json=_PAYLOAD, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, f"反馈未落库（缺陷复现）：{body}"
    assert body["data"].get("id"), body

    rows = bs.list_recent(limit=10, path=fdb)
    assert any(x["report_text"] == _PAYLOAD["report_text"] for x in rows), \
        f"刚提交的反馈读不回来：{rows}"
    assert bs.stats(path=fdb)["total"] == 1


def test_feedback_export_endpoint_serves_jsonl(auth, tmp_path, monkeypatch):
    _pin_feedback_db(monkeypatch, tmp_path)
    assert _client.post("/api/v1/feedback", json=_PAYLOAD,
                        headers=auth).json()["ok"] is True
    ex = _client.get("/api/v1/feedback/export?limit=10", headers=auth)
    assert ex.status_code == 200, ex.text
    assert "false_positive" in ex.text


def test_feedback_stats_endpoint_reads_temp_db(auth, tmp_path, monkeypatch):
    _pin_feedback_db(monkeypatch, tmp_path)
    _client.post("/api/v1/feedback", json=_PAYLOAD, headers=auth)
    r = _client.get("/api/v1/feedback/stats", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] >= 1

    # 非法类型 → 端点应转成 400（而非静默 ok=False）
    bad = dict(_PAYLOAD, feedback_type="bogus")
    r2 = _client.post("/api/v1/feedback", json=bad, headers=auth)
    assert r2.status_code == 400, r2.text


def test_store_self_heals_without_external_init(tmp_path):
    """库文件全新（没人调用 init_db）时，record/stats 也必须可用。"""
    import badcase_store as bs
    p = str(tmp_path / "fresh_feedback.db")
    assert not os.path.exists(p)
    assert bs.record({"feedback_type": "missed", "report_text": "漏报样本"}, path=p) >= 1
    assert bs.stats(path=p)["total"] == 1
    assert bs.list_recent(path=p)[0]["report_text"] == "漏报样本"


def test_record_rejects_invalid_type(tmp_path):
    """非法 feedback_type 仍应抛 ValueError（端点会转成 400）。"""
    import badcase_store as bs
    try:
        bs.record({"feedback_type": "bogus", "report_text": "x"},
                  path=str(tmp_path / "v.db"))
    except ValueError:
        return
    raise AssertionError("非法 feedback_type 应抛 ValueError")
