"""
test_api_guard.py — 后端访问控制与数据一致性回归（2026-08-18 第四轮审查修复）

覆盖：
1. 队列原子去重（并发口径重复提交只入队一条）
2. 归属隔离：普通医生读不到他人样本 / 删不了他人队列条目 / 不能整表清空队列
3. RIS 连接配置持久化 + password 脱敏
4. 登录锁定期满自动解锁（fails 清零）

隔离：模块级设置 QC_DB_OVERRIDE / QC_APPDATA 指向临时目录（main.py 在
import server.db 之前读取，真实启动路径生效）。
"""
import os
import sys
import time
import unittest

_TMP = "/tmp/qc_api_guard_tests"
os.makedirs(_TMP, exist_ok=True)
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "guard.db")
os.environ["QC_APPDATA"] = os.path.join(_TMP, "appdata")
os.environ["QC_API_SECRET"] = "guard-test-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402
from server import main  # noqa: E402


def _fresh_db():
    """首个用例类清库（连接池绑定固定路径，仅在进程开始时清理一次）。"""
    for f in ("guard.db",):
        p = os.path.join(_TMP, f)
        if os.path.exists(p):
            os.remove(p)
    for p in os.listdir(_TMP):
        fp = os.path.join(_TMP, p)
        if os.path.isfile(fp) and p.startswith("appdata"):
            os.remove(fp)


class _ApiBase(unittest.TestCase):
    """类级共享库（SQLAlchemy 连接池绑定固定路径，删除库文件会破坏池内连接）。

    guard.db 由 main 模块级建表创建；本测试登录既有账号（不存在则注册）。
    每个用例通过 admin 清空队列等可变数据，保证独立性。
    """

    _db_ready = False

    @classmethod
    def setUpClass(cls):
        if not _ApiBase._db_ready:
            _ApiBase.c = TestClient(main.app)
            # 登录既有 admin；不存在则作为首账号注册（自动 admin）
            r = _ApiBase.c.post("/api/v1/accounts/login",
                                json={"emp_id": "admin", "password": "admin888"})
            if r.json().get("ok"):
                _ApiBase.tok_admin = r.json()["data"]["token"]
            else:
                r = _ApiBase.c.post("/api/v1/accounts", json={"emp_id": "admin",
                                                              "password": "admin888",
                                                              "name": "管理员"})
                _ApiBase.tok_admin = r.json()["data"]["token"]
            # 医生账号：登录或创建
            r = _ApiBase.c.post("/api/v1/accounts/login",
                                json={"emp_id": "0559", "password": "pass1234"})
            if r.json().get("ok"):
                _ApiBase.tok_doc = r.json()["data"]["token"]
            else:
                r = _ApiBase.c.post("/api/v1/accounts", json={"emp_id": "0559",
                                                              "password": "pass1234",
                                                              "name": "医生A"},
                                    headers={"Authorization": "Bearer " + _ApiBase.tok_admin})
                _ApiBase.tok_doc = r.json()["data"]["token"]
            _ApiBase.HA = {"Authorization": "Bearer " + _ApiBase.tok_admin}
            _ApiBase.HD = {"Authorization": "Bearer " + _ApiBase.tok_doc}
            _ApiBase._db_ready = True
        cls.c, cls.HA, cls.HD = _ApiBase.c, _ApiBase.HA, _ApiBase.HD
        cls.tok_admin, cls.tok_doc = _ApiBase.tok_admin, _ApiBase.tok_doc

    def setUp(self):
        # 每个用例开始前用 admin 清空队列，保证队列类用例独立
        self.c.delete("/api/v1/queue", headers=self.HA)

    def _add_queue(self, text, headers):
        return self.c.post("/api/v1/queue", json={"text": text}, headers=headers)


class TestQueueDedup(_ApiBase):
    def test_concurrent_style_dedup(self):
        r1 = self._add_queue("同一份报告正文内容。", self.HD)
        r2 = self._add_queue("同一份报告正文内容。", self.HD)
        self.assertFalse(r1.json()["data"]["duplicated"])
        self.assertTrue(r2.json()["data"]["duplicated"], r2.json())
        r = self.c.get("/api/v1/queue", headers=self.HD)
        self.assertEqual(r.json()["data"]["count"], 1)


class TestOwnershipGuard(_ApiBase):
    def test_doctor_cannot_read_other_sample(self):
        r = self.c.post("/api/v1/samples", json={"report": "admin 的样本。",
                                                 "meta": {"patient": "A"}},
                        headers=self.HA)
        sid = r.json()["data"]["id"]
        r = self.c.get(f"/api/v1/samples/{sid}", headers=self.HD)
        self.assertEqual(r.status_code, 403)

    def test_doctor_cannot_delete_other_queue_item(self):
        r = self._add_queue("admin 队列条目。", self.HA)
        qid = r.json()["data"]["id"]
        r = self.c.delete(f"/api/v1/queue/{qid}", headers=self.HD)
        self.assertEqual(r.status_code, 403)
        # admin 自己可以删
        r = self.c.delete(f"/api/v1/queue/{qid}", headers=self.HA)
        self.assertEqual(r.status_code, 200)

    def test_doctor_cannot_clear_queue(self):
        self._add_queue("条目。", self.HD)
        r = self.c.delete("/api/v1/queue", headers=self.HD)
        self.assertEqual(r.status_code, 403)
        r = self.c.delete("/api/v1/queue", headers=self.HA)
        self.assertEqual(r.status_code, 200)


class TestRisConfigPersist(_ApiBase):
    def test_save_masked_load(self):
        r = self.c.put("/api/v1/ris/config", json={"host": "10.0.0.9", "port": 1433,
                                                   "db_type": "sqlserver", "user": "ro",
                                                   "password": "secret",
                                                   "query_sql": "SELECT 1"},
                       headers=self.HA)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.get("/api/v1/ris/config", headers=self.HA)
        d = r.json()["data"]
        self.assertEqual(d.get("host"), "10.0.0.9")
        self.assertNotIn("secret", str(d.get("password", "")))
        # 非 admin 不能保存
        r = self.c.put("/api/v1/ris/config", json={"host": "x"},
                       headers=self.HD)
        self.assertEqual(r.status_code, 403)


class TestLoginLockExpiry(_ApiBase):
    def test_lock_expires_after_5min(self):
        # 5 次失败 → 锁定
        for _ in range(5):
            self.c.post("/api/v1/accounts/login", json={"emp_id": "0559",
                                                        "password": "wrong"})
        r = self.c.post("/api/v1/accounts/login", json={"emp_id": "0559",
                                                        "password": "wrong"})
        self.assertEqual(r.json()["code"], "ERR")
        # 手动把 locked_until 置为过去 → 应立即解锁（fails 清零，不再锁定）
        rec = main._LOGIN_FAIL.get("0559")
        self.assertIsNotNone(rec)
        rec[2] = time.time() - 1
        r = self.c.post("/api/v1/accounts/login", json={"emp_id": "0559",
                                                        "password": "wrong"})
        # 解锁后允许继续尝试（错误仍返回 ERR 但不再提示"次数过多"）
        self.assertEqual(r.json()["code"], "ERR")
        self.assertNotIn("次数过多", r.json().get("message", ""))


if __name__ == "__main__":
    unittest.main()


class TestDownloadWhitelist(_ApiBase):
    """导出下载白名单（2026-08-18）：仅导出产物可下载，禁止库/配置/模型文件。"""

    def test_db_file_rejected(self):
        r = self.c.get("/api/v1/files/download?file=qc.db", headers=self.HA)
        self.assertEqual(r.status_code, 404)

    def test_export_file_allowed_and_cleaned(self):
        import samplelib
        d = os.path.dirname(samplelib.db_path())
        p = os.path.join(d, "samples_export_20990101_000000.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("a,b\n")
        r = self.c.get("/api/v1/files/download?file=samples_export_20990101_000000.csv",
                       headers=self.HA)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(p), "下载后服务端文件应被删除")


class TestRegionsValidation(_ApiBase):
    """/screen/regions PUT 坐标校验（2026-08-18）：越界/NaN 拒绝，防止 OCR 500。"""

    def test_out_of_range_rejected(self):
        r = self.c.put("/api/v1/screen/regions",
                       json={"basic": {"x": 2.5, "y": 0.1, "w": 0.3, "h": 0.3}},
                       headers=self.HA)
        self.assertEqual(r.status_code, 400)

    def test_non_numeric_rejected(self):
        # NaN 无法通过标准 JSON 通道（序列化即失败）；非数字类型同样拒绝（400）
        r = self.c.put("/api/v1/screen/regions",
                       json={"basic": {"x": "abc", "y": 0.1, "w": 0.3, "h": 0.3}},
                       headers=self.HA)
        self.assertEqual(r.status_code, 400)

    def test_valid_accepted(self):
        r = self.c.put("/api/v1/screen/regions",
                       json={"basic": {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.3}},
                       headers=self.HA)
        self.assertEqual(r.status_code, 200)
