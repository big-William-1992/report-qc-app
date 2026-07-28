"""
test_accounts.py
账号模块单测：创建 / 校验 / 唯一性 / 密码哈希 / 会话。
使用 accounts._DB_OVERRIDE 指向临时库，避免污染真实数据。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import accounts


class TestAccounts(unittest.TestCase):
    def setUp(self):
        # 每个用例用独立临时库
        self._tmp = tempfile.mkdtemp(prefix="acct_test_")
        self._db = os.path.join(self._tmp, "accounts.db")
        accounts._DB_OVERRIDE = self._db
        accounts.init_db()

    def tearDown(self):
        accounts._DB_OVERRIDE = None

    def test_create_and_verify(self):
        ok, msg = accounts.create_account("1001", "secret123", "张三")
        self.assertTrue(ok, msg)
        self.assertTrue(accounts.verify_account("1001", "secret123"))
        self.assertFalse(accounts.verify_account("1001", "wrongpw"))

    def test_password_min_length(self):
        ok, msg = accounts.create_account("1002", "123", "李四")
        self.assertFalse(ok)
        self.assertIn("6", msg)

    def test_empty_emp_id(self):
        ok, msg = accounts.create_account("   ", "secret123")
        self.assertFalse(ok)
        self.assertIn("工号", msg)

    def test_duplicate_emp_id(self):
        accounts.create_account("1003", "secret123")
        ok, msg = accounts.create_account("1003", "otherpw456")
        self.assertFalse(ok)
        self.assertIn("已存在", msg)
        # 原账号密码不受影响
        self.assertTrue(accounts.verify_account("1003", "secret123"))

    def test_password_not_plaintext(self):
        accounts.create_account("1004", "secret123")
        import sqlite3
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT pwd_hash, salt FROM accounts WHERE emp_id='1004'").fetchone()
        self.assertNotEqual(row[0], "secret123")
        self.assertTrue(len(row[1]) > 0)  # 盐非空

    def test_same_password_different_hash(self):
        # 不同工号相同密码，因盐不同哈希应不同
        accounts.create_account("A", "samepw123")
        accounts.create_account("B", "samepw123")
        import sqlite3
        with sqlite3.connect(self._db) as conn:
            h = conn.execute(
                "SELECT pwd_hash FROM accounts WHERE emp_id IN ('A','B')").fetchall()
        self.assertNotEqual(h[0][0], h[1][0])

    def test_count_and_list(self):
        self.assertEqual(accounts.count_accounts(), 0)
        accounts.create_account("X1", "pw123456", "王五")
        accounts.create_account("X2", "pw123456", "赵六")
        self.assertEqual(accounts.count_accounts(), 2)
        self.assertEqual(accounts.get_name("X1"), "王五")
        self.assertTrue(accounts.account_exists("X2"))
        self.assertFalse(accounts.account_exists("X9"))
        ids = [e[0] for e in accounts.list_accounts()]
        self.assertEqual(ids, ["X1", "X2"])

    def test_session(self):
        accounts.set_session("X1")
        self.assertEqual(accounts.get_session(), "X1")
        accounts.clear_session()
        self.assertEqual(accounts.get_session(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
