"""
test_sample_user.py
验证 samplelib 的 user_id 字段：保存时写入工号，列表/详情/导出能取回。
使用临时库（path 参数），不污染真实数据。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import samplelib


class FakeFinding:
    def __init__(self, rule_id, error_type, severity, message):
        self.rule_id = rule_id
        self.error_type = error_type
        self.severity = severity
        self.message = message


class TestSampleUser(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sample_user_")
        self._db = os.path.join(self._tmp, "samples.db")
        samplelib.init_db(self._db)

    def _save(self, user_id):
        meta = {"name": "张三", "gender": "男", "age": "54",
                "modality": "胸部", "applied_site": "肺", "laterality": "左"}
        findings = [FakeFinding("R1", "性别矛盾", "high", "测试")]
        scores = {"准确性": 90}
        return samplelib.save_sample("报告文本", meta, findings, scores,
                                     path=self._db, user_id=user_id)

    def test_user_id_stored_and_listed(self):
        sid = self._save("1001")
        rows = samplelib.list_samples(self._db)
        self.assertEqual(rows[0]["user_id"], "1001")
        full = samplelib.list_samples_full(self._db)
        self.assertEqual(full[0]["user_id"], "1001")

    def test_get_sample_has_user_id(self):
        sid = self._save("2002")
        sm = samplelib.get_sample(sid, self._db)
        self.assertEqual(sm["user_id"], "2002")

    def test_empty_user_id(self):
        sid = self._save("")
        sm = samplelib.get_sample(sid, self._db)
        self.assertEqual(sm["user_id"], "")

    def test_anonymize_strips_patient_not_user(self):
        sid = self._save("3003")
        # 重新保存一份脱敏的，确认 user_id 仍保留
        meta = {"name": "李四", "gender": "女", "age": "30",
                "modality": "头部", "applied_site": "脑", "laterality": "右"}
        sid2 = samplelib.save_sample("报告2", meta, [], {},
                                     path=self._db, anonymize=True, user_id="3003")
        sm = samplelib.get_sample(sid2, self._db)
        self.assertEqual(sm["patient"], "已脱敏")
        self.assertEqual(sm["user_id"], "3003")


if __name__ == "__main__":
    unittest.main(verbosity=2)
