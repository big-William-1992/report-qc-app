"""
test_feedback_whitelist_loop.py — 反馈闭环端到端回归（2026-09-30 审查新增）

闭环设计：医生在反馈里把某条 R19 告警标为「误报」
  → feedback_collector.apply_delta → medical_whitelist.add_user_words
  → assets/lexicons/user_whitelist.json
  → engine.config_store.load_rules_config 并入 r19_user_whitelist
  → R19 上述片段不再报

修复前该闭环在生产引擎上是**空转**的：user_whitelist.json 只被已删除的旧引擎栈
（src/rules_typo.py）读取，活引擎（src/engine/）完全不认；而 test_r19_sensitivity.py
用 `rule_id in ("R19-WHITELIST", "R19-HOMOPHONE")` 的联合断言把这一缺陷掩盖了
（旧栈的 R19-WHITELIST 通道在活引擎里根本不存在，任一侧成立即通过）。

本文件锁死「写入学习词 → 同一报告不再报」的端到端行为。
"""
import json
import os
import tempfile

import engine
from engine import config_store

# 该报告在 high 敏感度下会触发 R19-HOMOPHONE（形近「王动脉」→「主动脉」）
REPORT = "影像描述：见王动脉增宽。\n影像诊断：王动脉增宽。"


def _r19(eng, text=REPORT):
    eng.rules_config["r19_sensitivity"] = "high"
    return [f for f in eng.run(text, {}) if f.rule_id == "R19-HOMOPHONE"]


def test_user_whitelist_is_merged_into_config(tmp_path):
    """load_rules_config 必须把学习词表并入 r19_user_whitelist 键。"""
    wl = tmp_path / "user_whitelist.json"
    wl.write_text(json.dumps(["甲片段", "乙片段"], ensure_ascii=False), encoding="utf-8")

    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_store, "user_whitelist_path", lambda: str(wl))
        assert config_store.load_user_whitelist() == ["乙片段", "甲片段"]
        cfg = config_store.load_rules_config()
        assert cfg["r19_user_whitelist"] == ["乙片段", "甲片段"]


def test_feedback_whitelist_suppresses_r19(tmp_path):
    """端到端：学习词写入后，同一报告的同一片段不再被 R19 报出。"""
    import pytest

    # 1) 基线：确认该报告本会触发 R19（否则本测试无意义）
    base = _r19(engine.RuleEngine())
    assert base, "基线失效：该报告本应触发 R19-HOMOPHONE"
    learned = (base[0].snippet or "").strip()
    assert len(learned) >= 2, f"学习片段异常：{learned!r}"

    # 2) 模拟反馈闭环把「误报」写入学习词表
    wl = tmp_path / "user_whitelist.json"
    wl.write_text(json.dumps([learned], ensure_ascii=False), encoding="utf-8")

    # 3) 指向该词表并重建引擎（引擎在 __init__ 时读配置，与生产一致）
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_store, "user_whitelist_path", lambda: str(wl))
        assert learned in config_store.load_rules_config()["r19_user_whitelist"]
        after = _r19(engine.RuleEngine())
        still = [f.snippet for f in after if f.snippet == learned]
        assert not still, f"学习词「{learned}」未抑制 R19，闭环空转：{still}"


def test_user_whitelist_missing_file_is_tolerated():
    """词表文件缺失属正常状态（首次运行），不得抛异常、不得返回 None。"""
    assert config_store.load_user_whitelist(
        os.path.join(tempfile.gettempdir(), "no_such_whitelist_xyz.json")) == []


def test_derived_key_is_not_persisted(tmp_path):
    """r19_user_whitelist 是派生键，不得落盘（否则形成第二份真相源）。"""
    cfg_file = tmp_path / "rules_config.json"
    cfg = config_store.load_rules_config(str(cfg_file))
    cfg["r19_user_whitelist"] = ["甲", "乙"]
    config_store.save_rules_config(cfg, str(cfg_file))

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "r19_user_whitelist" not in raw, "派生键不应写入 rules_config.json"
    assert "typos" in raw, "正常字段必须照常落盘"
