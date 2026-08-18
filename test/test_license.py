"""
test_license.py — 授权防绕过回归（2026-08-18 第五轮审查修复）

覆盖：
1. 手改 license.json {activated:true}（无 machine_id / activation_code）不得绕过 → 落回试用
2. 复制他机已激活文件（machine_id 不匹配）不得被认作激活（防一码多机）
3. 真实激活链路（私钥签本机码）仍正常：activate → check_trial = activated
"""
import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import license_web as lw  # noqa: E402


class TestLicenseAntiForgery(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lic_test_")
        self.p = lw._license_path(self.dir)

    def _write(self, data):
        with open(self.p, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_fake_activated_bypass_blocked(self):
        # 手改 {activated:true}（无 machine_id/activation_code）→ 不得认作已激活
        self._write({"activated": True})
        state, _ = lw.check_trial(self.dir)
        self.assertEqual(state, "trial")

    def test_foreign_machine_activated_blocked(self):
        # 复制他机 license.json（machine_id 不匹配）→ 不得认作已激活（防一码多机）
        self._write({"activated": True, "machine_id": "another-machine",
                     "activation_code": "AAAAAAAAAAAAAAAAAAAA"})
        state, _ = lw.check_trial(self.dir)
        self.assertEqual(state, "trial")

    def test_real_activation_still_works(self):
        # 真实发卡链路：私钥签本机 machine_id → activate → check_trial = activated
        key_path = os.path.join(os.path.dirname(__file__), "..", "keys", "private_key.pem")
        if not os.path.isfile(key_path):
            self.skipTest("无私钥（不应入库），跳过真实激活用例")
        from cryptography.hazmat.primitives import serialization
        with open(key_path, "rb") as f:
            priv = serialization.load_pem_private_key(f.read(), password=None)
        mid = lw.machine_id()
        sig = priv.sign(mid.encode("utf-8"))
        code = base64.b32encode(sig).decode().rstrip("=")
        code = "-".join(code[i:i + 5] for i in range(0, len(code), 5))
        self.assertTrue(lw.validate_activation_code(code))
        self.assertTrue(lw.activate(self.dir, code))
        state, _ = lw.check_trial(self.dir)
        self.assertEqual(state, "activated")
        lic = json.load(open(self.p, encoding="utf-8"))
        self.assertEqual(lic.get("machine_id"), mid)


if __name__ == "__main__":
    unittest.main()
