"""样本库导出 / 导入 / 多机合并 的回归测试。

覆盖：
- export_samples → CSV / JSON
- import_samples 去重（同一文件二次导入应跳过）
- merge_from_db 跨库合并
- 确定性去重：两条 (ts, report_text) 完全相同的样本合并时应跳过
"""
import csv
import json
import os
import sqlite3
import tempfile
import unittest

from src import samplelib


class _TempDB:
    def __init__(self):
        self.path = tempfile.mktemp(suffix=".db")

    def __enter__(self):
        samplelib.init_db(self.path)
        return self.path

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _seed(db, n=3, prefix="P"):
    for i in range(n):
        samplelib.save_sample(
            report=f"{prefix}-report-{i}",
            meta={"patient": f"{prefix}患者{i}",
                  "gender": "male" if i % 2 else "female",
                  "age": str(20 + i), "modality": "CT",
                  "applied_site": "胸部",
                  "laterality": "左" if i % 2 else "右"},
            findings=[], scores={"准确性": 90 + i},
            path=db, user_id=f"U{i}")


def _count(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT COUNT(*) FROM samples").fetchone()[0]


class TestExportImport(unittest.TestCase):
    def test_export_csv_then_import(self):
        with _TempDB() as A, _TempDB() as B:
            _seed(A, 3)
            csv_path = tempfile.mktemp(suffix=".csv")
            out = samplelib.export_samples(path=A, out_path=csv_path, fmt="csv")
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            self.assertEqual(len(rows), 3)
            self.assertIn("report_text", reader.fieldnames)
            self.assertIn("findings_json", reader.fieldnames)
            self.assertIn("scores_json", reader.fieldnames)
            ins, skip = samplelib.import_samples(csv_path, target=B)
            self.assertEqual(ins, 3)
            self.assertEqual(skip, 0)
            self.assertEqual(_count(B), 3)

    def test_import_dedup(self):
        with _TempDB() as A, _TempDB() as B:
            _seed(A, 3)
            csv_path = tempfile.mktemp(suffix=".csv")
            samplelib.export_samples(path=A, out_path=csv_path, fmt="csv")
            samplelib.import_samples(csv_path, target=B)              # 首次 3 条
            ins, skip = samplelib.import_samples(csv_path, target=B)  # 重复应跳过
            self.assertEqual(ins, 0)
            self.assertEqual(skip, 3)
            self.assertEqual(_count(B), 3)

    def test_export_json_then_import(self):
        with _TempDB() as A, _TempDB() as B:
            _seed(A, 2)
            json_path = tempfile.mktemp(suffix=".json")
            samplelib.export_samples(path=A, out_path=json_path, fmt="json")
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(len(data), 2)
            ins, skip = samplelib.import_samples(json_path, target=B)
            self.assertEqual(ins, 2)
            self.assertEqual(_count(B), 2)

    def test_merge_from_db(self):
        with _TempDB() as A, _TempDB() as D:
            _seed(A, 3, prefix="A")
            _seed(D, 2, prefix="D")
            ins, skip = samplelib.merge_from_db(D, target=A)
            self.assertEqual(ins, 2)
            self.assertEqual(skip, 0)
            self.assertEqual(_count(A), 5)

    def test_merge_dedup_on_identical(self):
        # 两条 (ts, report_text) 完全相同的样本合并时应跳过
        with _TempDB() as A, _TempDB() as D:
            samplelib.init_db(A)
            samplelib.init_db(D)
            fixed_ts = "2026-08-01T00:00:00"
            with sqlite3.connect(A) as c:
                c.execute("INSERT INTO samples(ts,report_text) VALUES(?,?)",
                          (fixed_ts, "r1"))
            with sqlite3.connect(D) as c:
                c.execute("INSERT INTO samples(ts,report_text) VALUES(?,?)",
                          (fixed_ts, "r1"))
            ins, skip = samplelib.merge_from_db(D, target=A)
            self.assertEqual(ins, 0)
            self.assertEqual(skip, 1)
            self.assertEqual(_count(A), 1)


if __name__ == "__main__":
    unittest.main()
