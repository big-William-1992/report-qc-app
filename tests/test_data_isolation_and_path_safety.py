"""数据隔离回归测试（2026-09-30 安全修复）。

覆盖两个**实测复现过**的跨用户越权：

1. `GET /api/v1/export/data` —— 用 `require_emp_local`（普通医生即可）且
   `.query(Sample).all()` 完全不过滤。实测医生 B 能拿到医生 A 的
   `patient` / `report_text`。
2. `GET /api/v1/feedback/export` —— `list_recent()` 不过滤 user_id，
   任何登录用户可下载全院医生的反馈原文（`report` 正文最长 20000 字符）。

以及 `src/backup.py::restore_backup` 的路径穿越（实测 `../../../../tmp/evil.db`
可逃出备份目录，配合 admin 端点是任意文件复制原语）。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="iso_")
_APPDATA = os.path.join(_TMP, "appdata")
os.makedirs(_APPDATA, exist_ok=True)
os.environ["QC_APPDATA"] = _APPDATA
os.environ["QC_BACKUP_DIR"] = os.path.join(_TMP, "backups")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient  # noqa: E402
from conftest import temp_db  # noqa: E402
from server import main as appmod  # noqa: E402


@pytest.fixture()
def two_users():
    """建两个账号（docA 引导管理员 / docB 普通医生），各带一条含 PHI 的样本。"""
    with temp_db(os.path.join(_TMP, "iso.db"), _APPDATA):
        import accounts
        c = TestClient(appmod.app, client=("127.0.0.1", 50000))
        c.post("/api/v1/license/disclaimer", json={})
        c.post("/api/v1/accounts", json={"emp_id": "docA",
                                         "password": "pass-word-1", "name": "docA"})
        accounts.create_account("docB", "pass-word-1", role="user")

        from server.core import SessionLocal
        from server.models import Sample
        s = SessionLocal()
        s.add(Sample(ts="1", patient="张三-仅A可见", gender="男", age="50",
                     modality="CT", applied_site="胸部", laterality="",
                     user_id="docA", dept_id=None, report_text="A患者私密正文",
                     findings_json="[]", scores_json="{}"))
        s.add(Sample(ts="2", patient="李四-仅B可见", gender="女", age="60",
                     modality="CT", applied_site="胸部", laterality="",
                     user_id="docB", dept_id=None, report_text="B患者私密正文",
                     findings_json="[]", scores_json="{}"))
        s.commit()
        s.close()
        yield c


def test_export_data_does_not_leak_other_users_samples(two_users):
    """普通医生的 /export/data 只能含本人样本（回归：此前返回全表）。"""
    r = two_users.get("/api/v1/export/data", headers={"X-Emp-Id": "docB"})
    assert r.status_code == 200, r.text[:200]
    patients = [x["patient"] for x in r.json().get("samples", [])]
    assert not any("张三" in (p or "") for p in patients), (
        f"越权泄露他人样本：{patients}"
    )
    assert any("李四" in (p or "") for p in patients), (
        f"本人样本应可见：{patients}"
    )


def test_samples_list_and_export_agree_on_scope(two_users):
    """/samples 与 /export/data 的可见范围必须一致（防两处各写一套隔离）。"""
    c = two_users
    h = {"X-Emp-Id": "docB"}
    listed = c.get("/api/v1/samples", headers=h).json().get("data")
    items = listed.get("items") if isinstance(listed, dict) else listed
    exported = c.get("/api/v1/export/data", headers=h).json().get("samples", [])
    assert len(exported) == len(items or []), (
        f"两个端点的可见条数不一致：samples={len(items or [])} export={len(exported)}"
    )


def test_feedback_export_is_user_scoped(two_users):
    """普通医生的 feedback 导出不得含他人记录。"""
    from badcase_store import record
    record({"feedback_type": "false_positive", "report_text": "A医生的报告正文",
            "user_id": "docA"})
    record({"feedback_type": "missed", "report_text": "B医生的报告正文",
            "user_id": "docB"})
    r = two_users.get("/api/v1/feedback/export", headers={"X-Emp-Id": "docB"})
    assert r.status_code == 200, r.text[:200]
    assert "A医生的报告正文" not in r.text, "越权泄露他人反馈原文"
    assert "B医生的报告正文" in r.text, "本人反馈应可见"


class TestBackupRestorePathTraversal:
    """restore_backup 必须拒绝逃出备份目录的名称。

    ⚠️ 断言要点（踩过的坑）：**不要**只断言 `_safe_backup_path() is None` —— 那只是
    辅助函数，即便 `restore_backup` 里的守卫被删掉，辅助函数仍在，测试会假通过。
    必须断言 `restore_backup()` 的行为：**未真的把文件复制出备份目录**。
    """

    @pytest.mark.parametrize("name", [
        "../../../../tmp/evil_traversal.db",
        "../evil_traversal.db",
        "/etc/passwd",
        "sub/../../evil_traversal.db",
    ])
    def test_traversal_rejected(self, name):
        import backup
        bdir = backup.backup_dir()
        os.makedirs(bdir, exist_ok=True)
        out = backup.restore_backup(name)
        assert out.get("ok") is False, f"{name!r} 竟然恢复成功：{out}"
        # 行为断言：确认没有文件被复制到备份目录之外
        assert not out.get("restored"), f"{name!r} 产生了恢复记录：{out.get('restored')}"

    def test_traversal_cannot_copy_file_out_of_backup_dir(self):
        """端到端：在备份目录外放一个源文件，用穿越名恢复，必须失败且不落盘。"""
        import backup
        bdir = os.path.realpath(backup.backup_dir())
        os.makedirs(bdir, exist_ok=True)
        outside = os.path.join(os.path.dirname(bdir), "evil_source.db")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("outside")
        try:
            out = backup.restore_backup("../evil_source.db")
            assert out.get("ok") is False, f"穿越恢复不该成功：{out}"
            assert not out.get("restored"), out.get("restored")
        finally:
            if os.path.exists(outside):
                os.remove(outside)

    def test_normal_name_allowed(self):
        import backup
        os.makedirs(backup.backup_dir(), exist_ok=True)
        p = backup._safe_backup_path("qc.db")
        assert p is not None and p.startswith(os.path.realpath(backup.backup_dir()))

    def test_real_backup_still_restorable(self, tmp_path, monkeypatch):
        """防「修安全把功能修坏」：正常备份名仍能恢复。

        ⚠️⚠️ 血泪教训（2026-09-30）：本用例第一版用 `restore_backup("qc.db")`
        验证"正常名仍可恢复"，而 `backup._resolve_known("qc.db")` 解析到的目标是
        **真实开发库 `<root>/assets/qc.db`** —— 于是测试把假内容 `"backup-content"`
        复制覆盖了开发库，导致后续 `server.main` 导入时
        `sqlite3.DatabaseError: file is not a database`（开发库从 77824 字节被毁成 14 字节）。

        因此本用例现在**强制把两个目录都挪到 tmp_path**，绝不碰仓库内真实文件。
        """
        import backup
        monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
        monkeypatch.setenv("QC_APPDATA", str(tmp_path / "appdata"))
        bdir = os.path.realpath(backup.backup_dir())
        os.makedirs(bdir, exist_ok=True)
        # 用不匹配 _resolve_known 前缀的名字 → 恢复目标落在备份目录内，不碰真实库
        name = "probe_restore_check.db"
        with open(os.path.join(bdir, name), "w", encoding="utf-8") as f:
            f.write("probe")
        # 断言目标不指向仓库内真实库（防再次误伤）
        dest = backup._resolve_known(name)
        assert "assets/qc.db" not in dest.replace("\\", "/"), (
            f"恢复目标指向真实开发库，测试会污染它：{dest}"
        )
        out = backup.restore_backup(name)
        assert out.get("ok") is True, f"正常备份名恢复失败：{out}"
        assert out.get("restored"), out
