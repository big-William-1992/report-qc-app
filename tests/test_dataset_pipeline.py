import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from dataset_adapters import corbenchx_records, map_err  # noqa: E402
from cn_error_synth import synthesize, to_record  # noqa: E402


def test_map_err_known():
    assert map_err("Spelling Error") == "R8-TYPO"
    assert map_err("Side Confusion") == "R2-LATERALITY"
    assert map_err("negation") == "R5-CONSISTENCY"
    assert map_err("something weird") == "L1-OTHER"


def test_corbenchx_adapter_schema():
    recs = corbenchx_records([
        {"input_report": "Consolodation at base.", "error_type": "Spelling Error",
         "error_description": "Misspelling 'Consolidation' as 'Consolodation'."},
    ])
    assert len(recs) == 1
    r = recs[0]
    assert set(r) == {"instruction", "input", "output"}
    out = json.loads(r["output"])
    assert isinstance(out, list) and out[0]["error_type"] == "R8-TYPO"


def test_synth_laterality_and_gender():
    text = "患者男，58岁。检查所见：右肺上叶见结节。"
    inj, findings = synthesize(text, kinds=("laterality", "gender"))
    assert "左肺" in inj and "患者女" in inj
    ets = {f["error_type"] for f in findings}
    assert "R2-LATERALITY" in ets and "R1-GENDER" in ets
    rec = to_record(inj, findings)
    assert json.loads(rec["output"]) == findings


def test_synth_clean_has_no_finding():
    text = "患者男，58岁。检查所见：右肺上叶见结节，未见占位。"
    inj, findings = synthesize(text, kinds=())
    assert findings == []
