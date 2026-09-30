"""门禁：ft 模式 prompt 必须与 LoRA 训练分布逐字一致。

背景（2026-09-30 排查）：
    `prompt_mode: "ft"` 的设计前提是「推理 prompt 与微调训练时的 prompt 对齐」，
    否则模型看到的是分布外输入 —— 轻则不报错（静默退化成 []），重则乱造 error_type。
    这个前提此前**没有任何测试守着**，只要有人改了 `FT_SYSTEM_PROMPTS` 而不重训，
    线上就会静默失效，而 pytest 仍全绿。

本测试用「训练数据 + adapter 元数据」作为事实源反查：
    1. adapter_config.json 记录该 adapter 实际用的数据集目录；
    2. 该目录 train.jsonl 里的 system prompt 就是模型真正见过的分布；
    3. 断言 FT_SYSTEM_PROMPTS 中的每一条都出现在训练集里（子集关系）。

为什么是子集而不是相等：v2 数据集对每条样本随机 4 选 1 个 prompt 变体
（tools/gen_v2_robust_dataset.py PROMPT_VARIANTS），因此线上只发其中一种
（PROMPT_VARIANTS[0]）是**合法**的 —— 只要它确实在训练集里出现过。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.llm_prompt import FT_SYSTEM_PROMPTS  # noqa: E402


def _active_adapter_dir() -> str | None:
    """返回 src/llm_config.json 里当前生效的 adapter 目录（未配置则 None）。"""
    cfg_path = os.path.join(_ROOT, "src", "llm_config.json")
    if not os.path.exists(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    adapter = cfg.get("adapter_path")
    if not adapter:
        return None
    return adapter if os.path.isabs(adapter) else os.path.join(_ROOT, adapter)


def _training_system_prompts(data_dir: str) -> set[str]:
    """读取 train.jsonl 里出现过的全部 system prompt（去首尾空白）。"""
    train = os.path.join(data_dir, "train.jsonl")
    if not os.path.exists(train):
        return set()
    seen: set[str] = set()
    with open(train, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs = json.loads(line)["messages"]
            except Exception:
                continue
            for m in msgs:
                if m.get("role") == "system":
                    seen.add((m.get("content") or "").strip())
                    break
    return seen


def _adapter_dataset_dir(adapter_dir: str) -> str | None:
    """从 adapter_config.json 的 "data" 字段解析出训练数据集目录。"""
    cfg_path = os.path.join(adapter_dir, "adapter_config.json")
    if not os.path.exists(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    rel = data.get("data")
    if not rel:
        return None
    return rel if os.path.isabs(rel) else os.path.join(_ROOT, rel)


def test_ft_prompts_are_subset_of_training_distribution():
    """FT_SYSTEM_PROMPTS 必须全部来自当前 adapter 的训练集。

    失败意味着：有人改了 ft prompt（或换了 adapter）却没重训 —— 线上会在
    分布外输入下静默退化。修复方式二选一：把 prompt 改回训练分布，
    或用新 prompt 重新训练 adapter 后更新 llm_config.json。
    """
    adapter_dir = _active_adapter_dir()
    if not adapter_dir or not os.path.isdir(adapter_dir):
        pytest.skip("未配置本地 adapter（src/llm_config.json 无有效 adapter_path）")

    data_dir = _adapter_dataset_dir(adapter_dir)
    if not data_dir or not os.path.isdir(data_dir):
        pytest.skip(f"adapter 未记录训练集目录：{adapter_dir}")

    trained = _training_system_prompts(data_dir)
    if not trained:
        pytest.skip(f"训练集为空或格式异常：{data_dir}")

    missing = [p for p in FT_SYSTEM_PROMPTS if p.strip() not in trained]
    assert not missing, (
        "ft 模式 prompt 与训练分布不一致（线上会静默失效）：\n"
        f"  adapter : {adapter_dir}\n"
        f"  训练集  : {data_dir}（{len(trained)} 种 system prompt）\n"
        f"  线上发的 prompt 不在训练集里：{missing[0][:120]!r}\n"
        "  修复：改回训练分布 prompt，或用该 prompt 重训 adapter。"
    )


def test_ft_prompt_does_not_leak_taxonomy():
    """ft prompt 不应包含完整 taxonomy（那是 full 模式的事）。

    训练分布里 user 侧只有报告原文，没有 taxonomy/RAG 注入；
    若 ft prompt 混入长 taxonomy，会把模型推向「凑类型」幻觉。
    """
    for i, p in enumerate(FT_SYSTEM_PROMPTS):
        assert "【错误分类法" not in p, (
            f"FT_SYSTEM_PROMPTS[{i}] 混入了 taxonomy 段，与训练分布不符"
        )
