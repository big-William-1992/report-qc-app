"""
test_semantic_eval.py — 语义评测集脚手架（2026-09-30 新增）

守三件事：
1. **银标必须被拒收**：现有 200 例评测集的标签由规则自身蒸馏，规则在它上面
   100%/100% 是自证。新的语义评测集必须标 `label_source="human"`，否则校验失败。
2. **研究 A 与研究 B 不许合并**：输出结构里规则效能（Se/Sp）与 LLM 增量
   （Δrecall / ΔFP）是两个独立字段，避免"把规则+LLM 合成一个好看的数字"。
3. **反馈飞轮可用**：医生反馈能真正生成待标注模板（这是把临床反馈变成
   评测集/精调数据的唯一通道）。
"""
import json
import os
import subprocess

import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import subprocess_env  # noqa: E402


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PY = sys.executable
_TOOL = os.path.join(_ROOT, "tools", "semantic_eval.py")

_POOL = [
    {"report_text": "患者女，31岁。检查所见：前列腺体积增大。诊断印象：前列腺增生。"},
    {"report_text": "患者男，58岁。检查所见：右肺上叶见结节。诊断印象：右肺上叶结节，建议随访。"},
]


def _run(args, appdata, **env):
    # 用 subprocess_env() 强制子进程 UTF-8：脚本会 print 中文，
    # Windows 默认 cp1252 会 UnicodeEncodeError（CI 实测）。
    #
    # 同时统一给 QC_DB_OVERRIDE：`badcase_store._db_path()` 取「样本库同目录」，
    # 只设 QC_APPDATA 时 `samplelib.db_path()` 仍落到仓库 `assets/qc.db`，
    # 于是 feedback.db 会被写进**仓库真实目录**（被 conftest 仓库文件守卫抓到）。
    # 写入端（seed 子进程）与读取端（本函数）必须用**同一个** override，
    # 否则出现"写入 A 目录、读取 B 目录"→ 反馈飞轮看似断裂。
    e = subprocess_env(QC_APPDATA=appdata,
                       QC_DB_OVERRIDE=os.path.join(str(appdata), "t.db"), **env)
    return subprocess.run([_PY, _TOOL, *args], capture_output=True, text=True,
                          timeout=180, env=e)


@pytest.fixture()
def ws(tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    pool = tmp_path / "pool.jsonl"
    pool.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in _POOL),
                    encoding="utf-8")
    return {"dir": tmp_path, "appdata": str(appdata), "pool": str(pool)}


def test_template_from_jsonl_has_blank_labels(ws):
    out = str(ws["dir"] / "tmpl.jsonl")
    r = _run(["template", "--from-jsonl", ws["pool"], "--out", out], ws["appdata"])
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in open(out, encoding="utf-8")]
    assert len(rows) == len(_POOL)
    assert all(x["label"]["is_true_error"] is None for x in rows)
    assert all(x["label_source"] == "" for x in rows), "模板不应预填人工标签来源"


def test_validate_rejects_silver_labels(ws, tmp_path):
    """规则自蒸馏的银标不得进入效能评测（否则又是自证）。"""
    silver = tmp_path / "silver.jsonl"
    row = {"id": "s1", "report_text": _POOL[0]["report_text"],
           "label_source": "silver",                       # ← 关键：非人工
           "label": {"is_true_error": True, "error_type": "R1-GENDER", "location": "前列腺"}}
    silver.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    r = _run(["validate", str(silver)], ws["appdata"])
    assert r.returncode == 1, "银标必须被拒收"
    assert "human" in r.stdout


def test_validate_accepts_human_labels(ws, tmp_path):
    good = tmp_path / "good.jsonl"
    rows = [
        {"id": "g1", "report_text": _POOL[0]["report_text"], "label_source": "human",
         "label": {"is_true_error": True, "error_type": "R1-GENDER", "location": "前列腺"}},
        {"id": "g2", "report_text": _POOL[1]["report_text"], "label_source": "human",
         "label": {"is_true_error": False}},
    ]
    good.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                    encoding="utf-8")
    r = _run(["validate", str(good)], ws["appdata"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "校验通过" in r.stdout


def test_score_keeps_studies_separate(ws, tmp_path):
    """研究 A 与研究 B 必须是两个独立字段（防止合成一个数字上报）。"""
    labeled = tmp_path / "labeled.jsonl"
    rows = [
        {"id": "g1", "report_text": _POOL[0]["report_text"], "label_source": "human",
         "label": {"is_true_error": True, "error_type": "R1-GENDER", "location": "前列腺"},
         "meta": {"origin": "pool"}},
        {"id": "g2", "report_text": _POOL[1]["report_text"], "label_source": "human",
         "label": {"is_true_error": False}, "meta": {"origin": "pool"}},
    ]
    labeled.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                       encoding="utf-8")
    out = str(tmp_path / "result.json")
    r = _run(["score", str(labeled), "--json", "--save", out], ws["appdata"])
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.load(open(out, encoding="utf-8"))
    assert "study_a_rules" in res and res["study_a_rules"]["n_err"] == 1
    assert "study_b_llm_increment" in res
    # 不跑 LLM 时该项为 None，绝不能把规则结果冒充成 LLM 结果
    assert res["study_b_llm_increment"] is None
    # 人类可读输出里也必须分节标注
    assert "研究 A" in r.stdout


def test_feedback_promotes_to_labeling_template(ws):
    """医生反馈 → 待标注模板（反馈飞轮的唯一通道，必须真的通）。"""
    seed = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r);"
        "import badcase_store as b;"
        "b.record({'feedback_type':'missed','report_text':%r,'user_note':'缺少随访建议'})"
        % (os.path.join(_ROOT, "src"), _ROOT, "患者女，45岁。右肺上叶结节，建议半年后复查。")
    )
    r0 = subprocess.run([_PY, "-c", seed], capture_output=True, text=True, timeout=120,
                        env=subprocess_env(
                            QC_APPDATA=ws["appdata"],
                            # 必须同时给 QC_DB_OVERRIDE：`badcase_store._db_path()`
                            # 取的是「样本库同目录」，只设 QC_APPDATA 时
                            # `samplelib.db_path()` 仍解析到仓库 `assets/qc.db`，
                            # 于是 feedback.db 被写进**仓库真实目录**
                            # （实测被 conftest 的仓库文件守卫抓到：
                            #  assets/feedback.db 被改写）。
                            QC_DB_OVERRIDE=os.path.join(ws["appdata"], "t.db")))
    assert r0.returncode == 0, r0.stderr

    out = str(ws["dir"] / "from_fb.jsonl")
    r = _run(["template", "--from-feedback", "--out", out], ws["appdata"])
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in open(out, encoding="utf-8")]
    assert rows, "反馈未生成任何候选（飞轮断了）"
    assert any("missed" in (x["meta"].get("origin") or "") for x in rows)
    assert any("缺少随访建议" == (x["meta"].get("doctor_note") or "") for x in rows), \
        "医生备注应带进模板（这是漏报判定最重要的线索）"
