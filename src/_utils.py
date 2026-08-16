"""质控引擎纯辅助函数模块（从 engine.py 渐进式抽取，2026-08-16）。

仅包含无引擎依赖的纯函数（不引用 RuleEngine/NER/_KG），可用 _lexicons 词表或标准库。
engine.py 通过 `from _utils import *` 引入，保持 `from engine import XXX` 对外兼容。
"""


def _r5_fam(canonical):
    """从 canonical 提取器官族（用于 R5 描述-结论一致性归组）。

    canonical 编码规则：
      - 『L-kidney』/『R-femoral-head』等以 L-/R- 开头 → 取连字符后部分为器官族。
      - 肺叶编码『LUL/RUL/LUUL/RUUL/LLL/RLL 等』（以 UL/LL 结尾）→ 统一归为 lung。
    其余原样返回。返回 None 表示无法归组（跳过该实体）。
    """
    if not canonical:
        return None
    if canonical.startswith(("L-", "R-")):
        return canonical.split("-", 1)[-1]
    if canonical.endswith(("UL", "LL")):
        return "lung"
    return canonical


__all__ = ["_r5_fam"]
