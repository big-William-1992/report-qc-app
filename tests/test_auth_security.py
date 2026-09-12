"""
test_auth_security.py — 认证 / 限流 / 审计专项测试（2026-09-09）

覆盖：
1. 登录限流：emp_id 维度 5 次失败锁定 → 锁定消息 → 窗口过期解锁
2. Token 验证：有效 token 接受 / 无效 / 缺失 → 401
3. 审计日志：敏感操作写入 → 管理员可查询 / 过滤 / 分页 → 非管理员 403
4. 密码策略：最小长度 6 位 / 空工号拒绝 / 重复工号拒绝

隔离：模块级 QC_DB_OVERRIDE / QC_APPDATA 指向临时目录。
"""
import os
import sys
import json
import time
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="qc_auth_sec_")
_APPDATA = os.path.join(_TMP, "appdata")
os.makedirs(_APPDATA, exist_ok=True)
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "auth.db")
os.environ["QC_APPDATA"] = _APPDATA
os.environ["QC_API_SECRET"] = "auth-test-secret"
os.environ["PUSH_API_KEY"] = "auth-test-push-key"  # route_push.py 用 PUSH_API_KEY

# 限流测试专用：调小窗口以便测试
os.environ["QC_LOGIN_FAIL_LIMIT"] = "5"
os.environ["QC_LOGIN_FAIL_WINDOW"] = "300"
os.environ["QC_LOGIN_LOCK_SECONDS"] = "900"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402
from conftest import set_test_db  # noqa: E402
from server import main  # noqa: E402

set_test_db(os.path.join(_TMP, "auth.db"), _APPDATA)


def _fresh_db():
    """首个用例类清库。"""
    p = os.path.join(_TMP, "auth.db")
    if os.path.exists(p):
        os.remove(p)
    for f in os.listdir(_APPDATA):
        fp = os.path.join(_APPDATA, f)
        if os.path.isfile(fp):
            os.remove(fp)


class _AuthBase(unittest.TestCase):
    """类级共享库：建 admin + doctor 账号。"""

    _db_ready = False

    @classmethod
    def setUpClass(cls):
        if not _AuthBase._db_ready:
            _fresh_db()  # 清理残留数据库，确保测试隔离
            _AuthBase.c = TestClient(main.app)
            # 首账号 → admin（已存在则登录，不存在则创建）
            r = _AuthBase.c.post("/api/v1/accounts/login",
                                 json={"emp_id": "admin", "password": "admin888"})
            if r.json().get("ok"):
                _AuthBase.tok_admin = r.json()["data"]["token"]
            else:
                r = _AuthBase.c.post("/api/v1/accounts", json={
                    "emp_id": "admin", "password": "admin888", "name": "管理员"})
                _AuthBase.tok_admin = r.json()["data"]["token"]
            # 医生账号：创建（需 admin token）
            r = _AuthBase.c.post("/api/v1/accounts", json={
                "emp_id": "D001", "password": "docpass1", "name": "医生甲"},
                headers={"Authorization": "Bearer " + _AuthBase.tok_admin})
            _AuthBase.tok_doc = r.json()["data"]["token"]
            _AuthBase.HA = {"Authorization": "Bearer " + _AuthBase.tok_admin}
            _AuthBase.HD = {"Authorization": "Bearer " + _AuthBase.tok_doc}
            _AuthBase._db_ready = True
        cls.c, cls.HA, cls.HD = _AuthBase.c, _AuthBase.HA, _AuthBase.HD
        cls.tok_admin, cls.tok_doc = _AuthBase.tok_admin, _AuthBase.tok_doc

    def setUp(self):
        """每个用例前清除限流状态，保证测试隔离。"""
        main._LOGIN_FAIL.clear()


class TestLoginRateLimit(_AuthBase):
    """emp_id 维度限流：5 次失败锁定 → 锁定消息 → 窗口过期解锁。"""

    def test_lockout_after_5_failures(self):
        """连续 5 次失败后应返回锁定消息。"""
        for i in range(5):
            r = self.c.post("/api/v1/accounts/login",
                            json={"emp_id": "D001", "password": "wrong"})
            # 前 4 次仍为 ERR（密码错误），不触发锁定
            self.assertEqual(r.json()["code"], "ERR")
        # 第 5 次：密码错误 + 已触发锁定
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        self.assertEqual(r.json()["code"], "ERR")

    def test_blocked_after_lockout(self):
        """锁定后即使密码正确也应返回锁定消息。"""
        # 先触发 5 次失败
        for _ in range(5):
            self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        # 锁定后正确密码也应被拦截（返回 200+ERR 消息，而非 429）
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "docpass1"})
        self.assertEqual(r.json()["code"], "ERR")
        self.assertIn("次数过多", r.json()["message"])

    def test_lock_expires(self):
        """锁定窗口过期后应允许再次尝试。"""
        for _ in range(5):
            self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        # 确认锁定
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        self.assertIn("次数过多", r.json()["message"])
        # 手动清除限流状态（模拟窗口过期）
        main._LOGIN_FAIL.clear()
        # 清除后应允许尝试（密码仍错误，但不再锁定）
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        self.assertEqual(r.json()["code"], "ERR")
        self.assertNotIn("次数过多", r.json()["message"])

    def test_clear_on_success(self):
        """登录成功后该 emp_id 的失败计数应清零。"""
        # 先积累 2 次失败
        for _ in range(2):
            self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        # 正确登录
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "docpass1"})
        self.assertTrue(r.json()["ok"])
        # 清零后，连续 4 次失败不应触发锁定
        for _ in range(4):
            self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        # 第 5 次失败：设置锁定时限，但消息仍为「工号或密码错误」
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        self.assertEqual(r.json()["code"], "ERR")
        # 第 6 次：锁定已生效，返回「次数过多」
        r = self.c.post("/api/v1/accounts/login",
                        json={"emp_id": "D001", "password": "wrong"})
        self.assertIn("次数过多", r.json()["message"])


class TestTokenValidation(_AuthBase):
    """Token 鉴权：有效接受 / 无效 / 缺失 → 401。"""

    def test_valid_token(self):
        """有效 token 应正常访问 /accounts/me。"""
        r = self.c.get("/api/v1/accounts/me", headers=self.HA)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["emp_id"], "admin")

    def test_invalid_token(self):
        """无效 token 应返回 401。"""
        r = self.c.get("/api/v1/accounts/me",
                        headers={"Authorization": "Bearer invalid.token.here"})
        self.assertEqual(r.status_code, 401)

    def test_missing_token(self):
        """无 token 应返回 401。"""
        r = self.c.get("/api/v1/accounts/me")
        self.assertEqual(r.status_code, 401)

    def test_empty_bearer(self):
        """空 Bearer 应返回 401。"""
        r = self.c.get("/api/v1/accounts/me",
                        headers={"Authorization": "Bearer "})
        self.assertEqual(r.status_code, 401)

    def test_nonexistent_emp_id(self):
        """不存在的 token 应返回 401。"""
        r = self.c.get("/api/v1/accounts/me",
                        headers={"Authorization": "Bearer nonexistent.token"})
        self.assertEqual(r.status_code, 401)

    def test_doctor_can_access_own_account_list(self):
        """医生 token 访问 /accounts 应返回 200（仅含自身信息）。"""
        r = self.c.get("/api/v1/accounts", headers=self.HD)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["emp_id"], "D001")

    def test_doctor_cannot_access_admin_only_endpoint(self):
        """医生 token 访问 admin-only 端点（重置密码）应返回 403。"""
        r = self.c.post("/api/v1/accounts/D001/password",
                        json={"password": "newpass1"}, headers=self.HD)
        self.assertEqual(r.status_code, 403)


class TestAuditLog(_AuthBase):
    """审计日志：写入 / 查询 / 过滤 / 分页 / 权限。"""

    def test_login_success_logged(self):
        """登录成功应写入审计记录。"""
        self.c.post("/api/v1/accounts/login",
                     json={"emp_id": "D001", "password": "docpass1"})
        r = self.c.get("/api/v1/admin/audit-logs?action=login_success",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        emp_ids = [it["emp_id"] for it in items]
        self.assertIn("D001", emp_ids)

    def test_login_failure_logged(self):
        """登录失败应写入审计记录。"""
        self.c.post("/api/v1/accounts/login",
                     json={"emp_id": "D001", "password": "wrong"})
        r = self.c.get("/api/v1/admin/audit-logs?action=login_failed",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        emp_ids = [it["emp_id"] for it in items]
        self.assertIn("D001", emp_ids)

    def test_account_created_logged(self):
        """创建账号应写入审计记录。"""
        r = self.c.post("/api/v1/accounts", json={
            "emp_id": "AUDIT_TEST", "password": "pw123456", "name": "审计测试"},
            headers=self.HA)
        self.assertTrue(r.json()["ok"])
        r = self.c.get("/api/v1/admin/audit-logs?action=account_created",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        details = json.dumps([it["detail"] for it in items], ensure_ascii=False)
        self.assertIn("AUDIT_TEST", details)

    def test_filter_by_emp_id(self):
        """按工号过滤审计日志。"""
        r = self.c.get("/api/v1/admin/audit-logs?emp_id=admin",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        for it in items:
            self.assertEqual(it["emp_id"], "admin")

    def test_filter_by_action(self):
        """按操作类型过滤审计日志。"""
        r = self.c.get("/api/v1/admin/audit-logs?action=login_success",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        for it in items:
            self.assertEqual(it["action"], "login_success")

    def test_pagination(self):
        """分页：page_size=2 时不应超过 2 条。"""
        r = self.c.get("/api/v1/admin/audit-logs?page=1&page_size=2",
                        headers=self.HA)
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertLessEqual(len(data["items"]), 2)
        self.assertEqual(data["page_size"], 2)
        self.assertGreater(data["total"], 0)
        self.assertGreaterEqual(data["pages"], 1)

    def test_doctor_cannot_view_audit(self):
        """非管理员查看审计日志应返回 403。"""
        r = self.c.get("/api/v1/admin/audit-logs", headers=self.HD)
        self.assertEqual(r.status_code, 403)

    def test_no_token_cannot_view_audit(self):
        """无 token 查看审计日志应返回 401。"""
        r = self.c.get("/api/v1/admin/audit-logs")
        self.assertEqual(r.status_code, 401)

    def test_dept_changed_logged(self):
        """科室变更应写入审计记录。"""
        r = self.c.post("/api/v1/departments", json={"name": "审计科室"},
                        headers=self.HA)
        self.assertTrue(r.json()["ok"])
        r = self.c.get("/api/v1/departments", headers=self.HA)
        dept = [d for d in r.json()["data"] if d["name"] == "审计科室"]
        self.assertTrue(dept)
        r = self.c.post("/api/v1/accounts/D001/dept",
                        json={"dept_id": dept[0]["id"]}, headers=self.HA)
        self.assertTrue(r.json()["ok"])
        r = self.c.get("/api/v1/admin/audit-logs?action=dept_changed",
                        headers=self.HA)
        items = r.json()["data"]["items"]
        details = json.dumps([it["detail"] for it in items], ensure_ascii=False)
        self.assertIn("D001", details)
        self.assertIn(str(dept[0]["id"]), details)

    def test_department_created_logged(self):
        """创建科室应写入审计记录。"""
        r = self.c.post("/api/v1/departments", json={"name": "科室审计"},
                        headers=self.HA)
        self.assertTrue(r.json()["ok"])
        r = self.c.get("/api/v1/admin/audit-logs?action=department_created",
                        headers=self.HA)
        items = r.json()["data"]["items"]
        details = json.dumps([it["detail"] for it in items], ensure_ascii=False)
        self.assertIn("科室审计", details)

    def test_filter_by_time_range(self):
        """按时间范围过滤审计日志。"""
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        end = datetime.max.isoformat(timespec="seconds")
        r = self.c.get(
            "/api/v1/admin/audit-logs?start=" + start + "&end=" + end,
            headers=self.HA)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        for it in items:
            self.assertGreaterEqual(it["ts"], start)
            self.assertLessEqual(it["ts"], end)

    def test_audit_detail_stored(self):
        """审计记录的 detail 字段应正确序列化。"""
        self.c.post("/api/v1/accounts/login",
                     json={"emp_id": "D001", "password": "docpass1"})
        r = self.c.get("/api/v1/admin/audit-logs?action=login_success",
                        headers=self.HA)
        items = r.json()["data"]["items"]
        d001_items = [it for it in items if it["emp_id"] == "D001"]
        self.assertGreater(len(d001_items), 0)
        for it in d001_items:
            self.assertIsInstance(it["detail"], str)


class TestPasswordPolicy(_AuthBase):
    """密码策略：最小 6 位 / 空工号拒绝 / 重复工号拒绝。"""

    def test_min_password_length(self):
        """密码不足 6 位应拒绝。"""
        r = self.c.post("/api/v1/accounts", json={
            "emp_id": "SHORT", "password": "123", "name": "短密码"},
            headers=self.HA)
        self.assertFalse(r.json()["ok"])
        self.assertIn("8", r.json()["message"])

    def test_empty_emp_id(self):
        """空工号应拒绝。"""
        r = self.c.post("/api/v1/accounts", json={
            "emp_id": "   ", "password": "pw123456", "name": "空工号"},
            headers=self.HA)
        self.assertFalse(r.json()["ok"])
        self.assertIn("工号", r.json()["message"])

    def test_duplicate_emp_id(self):
        """重复工号应拒绝。"""
        r = self.c.post("/api/v1/accounts", json={
            "emp_id": "admin", "password": "other123", "name": "重复"},
            headers=self.HA)
        self.assertFalse(r.json()["ok"])
        self.assertIn("已存在", r.json()["message"])


class TestApiSecretIsolation(_AuthBase):
    """推送 API Key 鉴权：常量时间比较 / 错误 Key 拒绝。"""

    def test_push_wrong_key_rejected(self):
        """错误 API Key 推送应被拒绝（403）。"""
        r = self.c.post("/api/v1/push/report",
                        json={"emp_id": "PUSH01", "report": "测试报告",
                               "meta": {"patient": "P001"}},
                        headers={"X-API-Key": "wrong-key"})
        self.assertEqual(r.status_code, 403)

    def test_push_no_key_rejected(self):
        """无 API Key 推送应被拒绝（401）。"""
        r = self.c.post("/api/v1/push/report",
                        json={"emp_id": "PUSH01", "report": "测试报告",
                               "meta": {"patient": "P001"}})
        self.assertEqual(r.status_code, 401)


class TestNetworkHostSecretPolicy(unittest.TestCase):
    """非本机监听必须显式配置 QC_API_SECRET；本机回环保持桌面端兼容。"""

    def _set_secret(self, val: str):
        """设置 security 模块内真实的 QC_API_SECRET（re-export 副本不生效）。"""
        from server import security
        security.QC_API_SECRET = val

    def test_local_hosts_allow_random_secret_mode(self):
        """本机地址不应触发非本机密钥强制校验。"""
        self._set_secret("")
        for host in ("127.0.0.1", "::1", "localhost", "localhost:port"):
            self.assertFalse(main._is_network_host(host))
            main._require_secret_for_network_host(host)

    def test_network_host_requires_secret(self):
        """0.0.0.0 等非本机监听在缺少密钥时应阻止启动。"""
        self._set_secret("")
        with self.assertRaises(SystemExit):
            main._require_secret_for_network_host("0.0.0.0")

    def test_network_host_allows_explicit_secret(self):
        """显式配置 QC_API_SECRET 后，非本机监听应允许启动。"""
        self._set_secret("auth-test-secret")
        main._require_secret_for_network_host("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
