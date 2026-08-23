"""LLM 客户端健壮性测试（无需 GPU / 模型）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_client import _extract_json  # noqa: E402


def test_extract_plain():
    assert _extract_json('[{"a": 1}]') == [{"a": 1}]


def test_extract_fenced():
    assert _extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_extract_with_preamble():
    assert _extract_json('以下是结果：\n[{"a": 1}] 结束') == [{"a": 1}]


def test_extract_object():
    assert _extract_json('说明 {"k": 2} 尾') == {"k": 2}


def test_extract_none():
    assert _extract_json("无可用内容") is None
