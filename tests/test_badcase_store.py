"""badcase_store.py 单元测试 — 固化 P1-4 闭环核心 (2026-08-25 审计 P3)。

覆盖: init/record 校验/list/stats/export_jsonl/类型白名单。
全部使用 tmp 目录, 不触碰真实 feedback.db。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import badcase_store as bs  # noqa: E402


class TestBadcaseStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "feedback.db")
        bs.init_db(self.db)

    def _fp(self, **kw):
        d = {"feedback_type": "false_positive", "report_text": "患者男，45岁。右肺结节。",
             "rule_id": "R19-HOMOPHONE", "message": "疑为错字", "severity": "medium"}
        d.update(kw)
        return bs.record(d, self.db)

    def test_record_and_list(self):
        i = self._fp()
        rows = bs.list_recent(10, path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_id"], "R19-HOMOPHONE")
        self.assertEqual(rows[0]["id"], i)

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            bs.record({"feedback_type": "hack", "report_text": "x"}, self.db)

    def test_empty_report_rejected(self):
        with self.assertRaises(ValueError):
            bs.record({"feedback_type": "missed", "report_text": "   "}, self.db)

    def test_long_fields_truncated(self):
        """超长字段截断入库而非报错(前端不可信输入)。"""
        self._fp(report_text="长" * 50000, message="m" * 5000)
        row = bs.list_recent(1, path=self.db)[0]
        self.assertLessEqual(len(row["report_text"]), 20000)
        self.assertLessEqual(len(row["message"]), 2000)

    def test_stats_counts(self):
        self._fp()
        bs.record({"feedback_type": "missed", "report_text": "B",
                   "user_note": "漏了"}, self.db)
        st = bs.stats(self.db)
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["by_type"]["false_positive"], 1)
        self.assertEqual(st["last_7d"], 2)

    def test_filter_by_type(self):
        self._fp()
        bs.record({"feedback_type": "missed", "report_text": "B"}, self.db)
        fps = bs.list_recent(10, feedback_type="false_positive", path=self.db)
        self.assertEqual(len(fps), 1)
        self.assertEqual(fps[0]["feedback_type"], "false_positive")

    def test_export_jsonl_roundtrip(self):
        self._fp(user_note="备注1")
        out = os.path.join(self.dir, "out.jsonl")
        n = bs.export_jsonl(out, path=self.db)
        self.assertEqual(n, 1)
        with open(out, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["user_note"], "备注1")
        self.assertIn("ts", rec)


if __name__ == "__main__":
    unittest.main()
