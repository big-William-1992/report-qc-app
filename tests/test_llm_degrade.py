"""
test_llm_degrade.py — LLM 语义层不可用 / 报错时的优雅降级回归（2026-09-30 审查新增）

定位：LLM 是**可选增强**。模型未部署（MLX 未装 / Ollama 未启动 / 云端不可达）时，
确定性规则结果必须照常返回，接口返回 200 + available=false，而不是 500 或抛异常。

补这块覆盖的原因：run_full_qc / run_llm_qc 是 /api/v1/qc/llm 与 /api/v1/qc/full
两个端点的唯一入口，此前**完全没有测试引用**（只有 server/main.py 调用），
而「LLM 不可用」恰好是默认部署状态。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import llm_qc                                    # noqa: E402
from llm_client import LLMResponse               # noqa: E402
from llm_qc import run_full_qc, run_llm_qc       # noqa: E402

# 男性 + 子宫 → 确定性规则 R1-GENDER 必中（用来证明规则层未受 LLM 影响）
REPORT = ("患者男，58岁。检查部位：盆腔。\n"
          "检查所见：子宫大小形态正常，双侧附件区未见异常。\n"
          "诊断印象：子宫及双附件未见异常。")


class _UnavailableClient:
    """模型未部署：available() 为假。"""

    def available(self) -> bool:
        return False

    def chat(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("LLM 不可用时不应发起调用")


class _ErrorClient:
    """模型可达但调用失败（超时 / 400 / 服务端错误）。"""

    def available(self) -> bool:
        return True

    def chat(self, *a, **k):
        return LLMResponse(text="", parsed=None, model="fake-model",
                           elapsed_ms=3, error="connection refused")


def test_run_llm_qc_unavailable_is_structured():
    r = run_llm_qc(REPORT, client=_UnavailableClient())
    assert r["available"] is False
    assert r["findings"] == []
    assert r["error"], "不可用时应给出可读原因"


def test_run_llm_qc_error_client_reports_not_raises():
    r = run_llm_qc(REPORT, client=_ErrorClient())
    assert r["available"] is True
    assert r["findings"] == []
    assert r["error"] == "connection refused"


def test_run_full_qc_degrades_when_llm_unavailable(monkeypatch):
    """LLM 不可用：规则结果照常、LLM 发现为空、不抛异常。"""
    monkeypatch.setattr(llm_qc, "get_llm_client", lambda config=None: _UnavailableClient())
    out = run_full_qc(REPORT, run_rules=True, run_llm=True)

    assert out["llm_available"] is False
    assert out["llm_findings"] == []
    assert out["llm_error"]
    rule_ids = [f["rule_id"] for f in out["rule_findings"]]
    assert any(rid.startswith("R1") for rid in rule_ids), \
        f"LLM 不可用时规则结果必须照常返回，实际：{rule_ids}"
    assert out["counts"]["rule"] == len(out["rule_findings"]) >= 1
    assert out["counts"]["llm"] == 0


def test_run_full_qc_llm_error_keeps_rules(monkeypatch):
    """LLM 调用失败：错误上报但不影响规则结论。"""
    monkeypatch.setattr(llm_qc, "get_llm_client", lambda config=None: _ErrorClient())
    out = run_full_qc(REPORT, run_rules=True, run_llm=True)
    assert out["llm_available"] is True
    assert out["llm_error"] == "connection refused"
    assert out["llm_findings"] == []
    assert out["rule_findings"], "规则结果不得因 LLM 报错而丢失"


def test_run_full_qc_can_disable_llm(monkeypatch):
    def _boom(config=None):  # pragma: no cover - 不应被调用
        raise AssertionError("run_llm=False 时不应创建 LLM 客户端")
    monkeypatch.setattr(llm_qc, "get_llm_client", _boom)
    out = run_full_qc(REPORT, run_rules=True, run_llm=False)
    assert out["llm_available"] is False
    assert out["rule_findings"]


@pytest.mark.parametrize("run_llm", [True, False])
def test_endpoint_contract_keys_always_present(monkeypatch, run_llm):
    """/api/v1/qc/llm 与 /qc/full 读取的键在任何降级路径下都必须存在。"""
    monkeypatch.setattr(llm_qc, "get_llm_client", lambda config=None: _UnavailableClient())
    out = run_full_qc(REPORT, run_rules=True, run_llm=run_llm)
    for key in ("rule_findings", "llm_findings", "fused", "counts",
                "llm_available", "llm_error", "llm_model"):
        assert key in out, f"降级路径缺键：{key}"
