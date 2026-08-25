# 原 engine.py 顶部词表导入的兼容层: 保证 `from engine import XXX` 对外不变
from _lexicons import *  # noqa: F401,F403
from _utils import *     # noqa: F401,F403
from engine_text import *  # noqa: F401,F403


def _pull_symbols(*module_names):
    """将兄弟模块全部符号(含下划线私有)注入调用方 globals; 先到先得不覆盖。"""
    import importlib
    import inspect
    frame = inspect.currentframe().f_back
    g = frame.f_globals
    pkg = g.get("__package__")
    for name in module_names:
        m = importlib.import_module("." + name, package=pkg)
        for k, v in list(vars(m).items()):
            if k.startswith("__"):
                continue
            if k not in g:
                g[k] = v
