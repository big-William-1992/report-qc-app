import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from gen_reports import generate  # noqa: E402
from engine import RuleEngine, extract_meta  # noqa: E402


def test_generate_schema_and_self_labels():
    recs = generate(20)
    assert len(recs) == 20
    eng = RuleEngine()
    for r in recs:
        assert set(r) == {"instruction", "input", "output"}
        gold = json.loads(r["output"])
        # gold 为空（干净）或能被引擎复现（错误）
        det = {f.rule_id.split("-")[0] for f in eng.run(r["input"], extract_meta(r["input"]))}
        if gold:
            assert any(g["error_type"].split("-")[0] in det for g in gold)
        else:
            assert not det


def test_generate_has_both_polarities():
    recs = generate(30)
    pos = sum(1 for r in recs if json.loads(r["output"]))
    assert 0 < pos < len(recs)  # 既有正例也有负例
