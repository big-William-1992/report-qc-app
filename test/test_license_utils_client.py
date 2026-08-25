"""license_utils.py (客户端授权校验) 单元测试 — 补齐 0% 覆盖 (2026-08-25 审计 P3)。

覆盖:
- check_trial(): 首次运行 / 试用中 / 过期 / 时钟回拨 / HMAC 篡改
- _activated_valid(): 未激活 / 机器不匹配 / 垃圾码 / 真码(gen_activation_gui 私钥签发)
- validate_activation_code(): 空/垃圾/格式

隔离: monkeypatch license_utils._LICENSE_FILE 指向 tmp_path, 不碰真实 license.dat。
"""
import base64
import datetime
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import license_utils as lu  # noqa: E402


def _fake_lic_file(monkeypatch_tmpdir):
    """把许可文件指到临时目录。"""
    lic = os.path.join(monkeypatch_tmpdir, "license.dat")
    lu._LICENSE_FILE = lic
    return lic


def _write_lic(d):
    with open(lu._LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f)


class TestCheckTrial(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        _fake_lic_file(self.dir)

    def tearDown(self):
        for k in ("first_run", ):
            pass
        # 清理模块级缓存(若有)

    def test_first_run_starts_trial(self):
        status, rest = lu.check_trial()
        self.assertEqual(status, "trial")
        self.assertEqual(rest, lu.TRIAL_DAYS)
        # 落盘的 first_run 必须带 HMAC sig
        with open(lu._LICENSE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("sig", d["first_run"])

    def test_valid_sig_still_trial(self):
        today = datetime.date.today().isoformat()
        _write_lic({"first_run": {"date": today, "sig": lu._trial_sign(today)}})
        status, rest = lu.check_trial()
        self.assertEqual(status, "trial")

    def test_tampered_sig_expired(self):
        """手改日期但签名对不上 → 判篡改, 直接过期(不白送试用)。"""
        today = datetime.date.today().isoformat()
        _write_lic({"first_run": {"date": today, "sig": "DEADBEEF"}})
        status, _ = lu.check_trial()
        self.assertEqual(status, "expired")

    def test_future_start_expired(self):
        """试用起点在未来 = 时钟回拨痕迹。"""
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        _write_lic({"first_run": {"date": future, "sig": lu._trial_sign(future)}})
        status, _ = lu.check_trial()
        self.assertEqual(status, "expired")

    def test_over_trial_days_expired(self):
        old = (datetime.date.today()
               - datetime.timedelta(days=lu.TRIAL_DAYS + 1)).isoformat()
        _write_lic({"first_run": {"date": old, "sig": lu._trial_sign(old)}})
        status, _ = lu.check_trial()
        self.assertEqual(status, "expired")


class TestActivatedValid(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        _fake_lic_file(self.dir)
        self.machine = lu._machine_id()

    def _real_code(self):
        """用仓库内发卡私钥签本机 machine_id → 真激活码。
        私钥缺失(keys/ 仅本地)时跳过正例分支。"""
        sys.path.insert(0, ROOT)
        try:
            import gen_activation_gui as g
            return g.sign(self.machine)
        except FileNotFoundError:
            self.skipTest("发卡私钥不在本机, 跳过正例")
        return None

    def test_not_activated_false(self):
        self.assertFalse(lu._activated_valid({"activated": False}))

    def test_wrong_machine_false(self):
        code = self._real_code() or "AAAA-BBBB"
        lic = {"activated": True, "machine_id": "000000000000",
               "activation_code": code}
        self.assertFalse(lu._activated_valid(lic))

    def test_garbage_code_false(self):
        lic = {"activated": True, "machine_id": self.machine,
               "activation_code": "不是激活码"}
        self.assertFalse(lu._activated_valid(lic))

    def test_real_code_activated_true(self):
        code = self._real_code()
        if not code:
            return
        lic = {"activated": True, "machine_id": self.machine,
               "activation_code": code}
        self.assertTrue(lu._activated_valid(lic))
        _write_lic(lic)  # check_trial 从文件读取激活态
        self.assertEqual(lu.check_trial()[0], "activated")


class TestValidateCode(unittest.TestCase):
    def test_empty_false(self):
        self.assertFalse(lu.validate_activation_code(""))
        self.assertFalse(lu.validate_activation_code(None))

    def test_junk_false(self):
        self.assertFalse(lu.validate_activation_code("HELLO-WORLD-123"))


if __name__ == "__main__":
    unittest.main()
