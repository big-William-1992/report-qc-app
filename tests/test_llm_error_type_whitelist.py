"""门禁：LLM 返回的 error_type 必须在白名单内，幻觉编码不得流进结果。

背景（2026-09-30 实测）：
    本地微调模型 qc-qwen3 在**分布外**输入上会拼造不存在的错误码，
    实测造出 `R1-CONSISTENCY` / `R2-CONSISTENCY` —— 而 R1 是 R1-GENDER、
    R2 是 R2-LATERALITY、一致性是 R5-CONSISTENCY，这两个码**根本不存在**。
    更糟的是它在**完全正常的报告**上也会报，且同一条重复报两遍。

    `_normalize` 原先把 rule_id 合成 "L1-" + error_type，等于把模型编的字符串
    当成规则编号传给下游，绕过了 `llm_engine` 里已有的 ALLOWED_RULE_IDS 校验。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.llm_qc import ALLOWED_LLM_ERROR_TYPES, _normalize  # noqa: E402


class TestFabricatedCodesRejected:
    """实测中出现过的幻觉编码必须被拒。"""

    @pytest.mark.parametrize("code", [
        "R1-CONSISTENCY",   # 实测：模型在正常报告上编造
        "R2-CONSISTENCY",   # 实测：模型在错别字样本上编造
        "R99-NOT-A-REAL-CODE",
        "乱码",
        "",
    ])
    def test_fabricated_code_is_dropped(self, code):
        assert _normalize({"error_type": code}) is None, (
            f"幻觉编码 {code!r} 未被拦截，会污染下游结果"
        )

    def test_missing_error_type_is_dropped(self):
        assert _normalize({}) is None
        assert _normalize({"location": "x"}) is None

    def test_non_dict_item_is_dropped(self):
        # 模型偶尔返回字符串或 null 元素
        assert _normalize({"error_type": "R8-TYPO"}) is not None


class TestLegitimateCodesAccepted:
    """规则引擎真实产出的码必须放行，否则会误杀正常结果。"""

    @pytest.mark.parametrize("code", [
        "R8-TYPO", "R19-HOMOPHONE", "R5-CONSISTENCY", "R17-PERREGION",
        "R1-GENDER", "R2-LATERALITY", "R18-COVERAGE", "R16-FOLLOWUP",
        "L1-OTHER",
    ])
    def test_real_code_accepted(self, code):
        out = _normalize({"error_type": code, "severity": "high",
                          "confidence": 0.9, "location": "x", "rationale": "y"})
        assert out is not None, f"合法码 {code!r} 被误杀"
        assert out["rule_id"] == "L1-" + code
        assert out["error_type"] == code
        assert out["source"] == "LLM"


def test_whitelist_covers_engine_rule_ids():
    """白名单应覆盖规则引擎实际产出的规则号（防两处漂移）。

    从 src/engine/ 源码里抽取形如 "Rxx-NAME" 的字符串字面量，
    断言它们都在 ALLOWED_LLM_ERROR_TYPES 内，避免新增规则后 LLM 侧把
    合法码当幻觉拒掉。
    """
    import re

    engine_dir = os.path.join(_ROOT, "src", "engine")
    if not os.path.isdir(engine_dir):
        pytest.skip("engine 目录不存在")

    pattern = re.compile(r'"((?:R\d+|L1)-[A-Z][A-Z0-9-]*)"')
    found: set[str] = set()
    for name in os.listdir(engine_dir):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(engine_dir, name), encoding="utf-8") as f:
            found.update(pattern.findall(f.read()))

    if not found:
        pytest.skip("未从 engine 源码中抽取到规则号")

    missing = sorted(found - ALLOWED_LLM_ERROR_TYPES)
    assert not missing, (
        "规则引擎产出的规则号不在 LLM 白名单里（会被误当幻觉丢弃）：\n"
        f"  {missing}\n"
        "  请把新规则号加进 src/llm_qc.ALLOWED_LLM_ERROR_TYPES。"
    )
