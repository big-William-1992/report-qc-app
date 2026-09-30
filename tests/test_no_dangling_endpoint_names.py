"""
test_no_dangling_endpoint_names.py — 端点处理器不得引用未定义的名字（2026-09-30 新增）

## 为什么需要（真实事故）

路由拆分 S3 用"文本标记区间"删除 main.py 的一大段代码时，**连带删掉了
`_RIS_POLL_LOCK` 的定义**，而 `/api/v1/ris/poll-now` 仍在引用它。
后果：调用该端点返回

    {'ok': False, 'code': 'POLL_ERR', 'data': {'error': 'NameError'},
     'message': "轮询失败：name '_RIS_POLL_LOCK' is not defined"}

更糟的是**全量测试（514 例）+ 前端 e2e 全绿**都没发现它：
该端点把自己的异常转成了 `ok=False` 的业务响应（不是 500），且当时没有任何测试打到它。

## 这个守卫做什么

对每个已注册的 API 端点处理器，静态检查其**引入的名字**（co_names）是否都能在其
定义模块的全局命名空间（或 builtins）里解析。不需要真正调用端点，因此能覆盖
"平时没人调"的路径——这正是上一条事故的盲区。

只做"是否定义"这一层，不判断类型/取值，误报率低（内置名与闭包变量都放行）。
"""
import builtins
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _iter_endpoints(routes):
    """递归收集 (path, methods, handler)，兼容本仓 include_router 的 _IncludedRouter 包装。"""
    for route in routes:
        sub = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None)
        if sub:
            yield from _iter_endpoints(sub)
            continue
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if not path or endpoint is None or not str(path).startswith("/api/"):
            continue
        yield str(path), sorted(getattr(route, "methods", []) or []), endpoint


def _module_globals(fn):
    mod = sys.modules.get(getattr(fn, "__module__", ""))
    return getattr(mod, "__dict__", {})


def _unresolved(fn):
    """返回该函数**以全局方式加载**、但在模块全局与 builtins 里都不存在的名字。

    ⚠️ 只能用 `dis` 取 LOAD_GLOBAL：`co_names` 同时包含**属性名**（get/close/count）
    与函数体内的局部 import（csv/io），直接用它会产生大量误报（首版即因此报废）。
    """
    import dis
    g = _module_globals(fn)
    builtin_names = set(dir(builtins))
    missing = set()
    try:
        code = fn.__code__
    except AttributeError:
        return []
    for instr in dis.get_instructions(code):
        if instr.opname in ("LOAD_GLOBAL", "STORE_GLOBAL", "DELETE_GLOBAL"):
            name = instr.argval
            if name in builtin_names or name in g:
                continue
            missing.add(name)
    return sorted(missing)


def test_no_endpoint_references_missing_globals():
    """真实启动 app，逐个端点做静态名字解析检查。"""
    from server import main  # noqa: F401  导入即注册全部路由

    problems = {}
    for path, methods, fn in _iter_endpoints(main.app.routes):
        mod = getattr(fn, "__module__", "")
        # 只检查属于本仓的处理器（第三方/Starlette 内部跳过）
        if not (mod.startswith("server") or mod.startswith("routes")):
            continue
        miss = _unresolved(fn)
        if miss:
            problems[f"{'/'.join(methods)} {path} ({fn.__qualname__})"] = miss

    assert not problems, (
        "以下端点引用了未定义的全局名字（调用时会 NameError，且往往被包装成业务错误而静默）：\n"
        + "\n".join(f"  - {k}: {v}" for k, v in sorted(problems.items()))
        + "\n  常见原因：用'区间删除'搬代码时连带删掉了被其它端点引用的定义。")


def test_ris_poll_now_does_not_name_error():
    """回归：/ris/poll-now 曾因 _RIS_POLL_LOCK 被误删而返回 NameError。"""
    from fastapi.testclient import TestClient
    from server import main

    c = TestClient(main.app, client=("127.0.0.1", 50000))
    r = c.post("/api/v1/ris/poll-now")
    body = r.json()
    err = str((body.get("data") or {}).get("error") or "")
    assert "NameError" not in err, f"/ris/poll-now 出现 NameError（定义被误删？）：{body}"
    # 允许业务失败（未配置 RIS 连接等），但不得是代码级错误
    assert err not in ("AttributeError", "ImportError"), f"/ris/poll-now 代码级错误：{body}"
