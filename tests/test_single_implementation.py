"""
test_single_implementation.py — 「单一实现」守卫（2026-09-30 审查新增）

背景：本仓因多 Agent 并行开发，先后长出过两套「写了但从不生效」的并行实现，
都被静默架空却仍在被修改：

1. **第二套引擎**：`src/engine.py`（门面）+ `engine_types/helpers/config/ner/meta.py`
   + `rules_meta/typo/template/region/lesion.py`。由于 `import engine` 会优先命中
   `src/engine/` 包（Python 的「常规包优先于同名模块」规则），整个门面及其规则
   模块永远不会执行——但 v4.3.6 的 patch 仍在同时修改两份 `rules_typo.py`。
2. **第二套路由**：`server/routes/` 下 10 个模块从未被 `app.include_router`
   （main.py 只注册了 route_push），且内容比 main.py 陈旧（例如 route_qc 保存
   规则后缺少 `_reload_engine_rules()`）。

两者已于 2026-09-30 删除（可从 git 历史取回）。本文件确保它们不会悄悄回来。
"""
import collections
import glob
import importlib.util
import os
import re
import tempfile

# 与其他 API 测试一致的隔离：必须在 import server.main 之前设置。
_TMP = tempfile.mkdtemp(prefix="qc_single_impl_")
_APPDATA = os.path.join(_TMP, "appdata")
os.makedirs(_APPDATA, exist_ok=True)
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "single_impl.db")
os.environ["QC_APPDATA"] = _APPDATA

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_SERVER = os.path.join(_ROOT, "server")

# 已删除的旧引擎栈模块名：不允许再以同名文件出现
_LEGACY_ENGINE_MODULES = (
    "engine_types", "engine_helpers", "engine_config", "engine_ner", "engine_meta",
    "rules_meta", "rules_typo", "rules_template", "rules_region", "rules_lesion",
)


def test_engine_import_resolves_to_package():
    """`import engine` 必须命中 src/engine/ 包，而不是同名门面文件。"""
    import engine

    assert os.path.basename(os.path.dirname(os.path.abspath(engine.__file__))) == "engine", (
        f"import engine 命中了 {engine.__file__}，必须是 src/engine/ 包")
    assert os.path.exists(os.path.join(_SRC, "engine", "__init__.py"))
    assert not os.path.exists(os.path.join(_SRC, "engine.py")), (
        "src/engine.py 与 src/engine/ 包同名并存：包优先，门面文件会被静默架空，"
        "任何改在门面里的修复都不会生效。")
    # 门面必须真的对外可用（防止包拆分后符号缺失）
    for sym in ("RuleEngine", "Entity", "Finding", "load_rules_config",
                "learn_typo", "extract_meta_full", "score", "score_summary"):
        assert hasattr(engine, sym), f"engine 包对外缺少符号：{sym}"


def test_no_legacy_engine_modules():
    """旧引擎栈模块不允许重新出现（文件或可导入模块均算违规）。"""
    for name in _LEGACY_ENGINE_MODULES:
        p = os.path.join(_SRC, name + ".py")
        assert not os.path.exists(p), f"旧引擎栈模块重新出现：{p}"
        spec = importlib.util.find_spec(name)
        assert spec is None, f"{name} 不应可被导入（命中 {spec.origin}）"


def test_route_modules_are_all_registered():
    """server/routes/ 下每个模块都必须真的被 main.py include_router。"""
    main_src = open(os.path.join(_SERVER, "main.py"), encoding="utf-8").read()
    orphans = []
    for path in sorted(glob.glob(os.path.join(_SERVER, "routes", "*.py"))):
        name = os.path.basename(path)[:-3]
        if name == "__init__":
            continue
        if not re.search(
                rf"from\s+server\.routes\.{name}\s+import|import\s+server\.routes\.{name}\b",
                main_src):
            orphans.append(os.path.relpath(path, _ROOT))
    assert not orphans, (
        "以下路由模块从未被 main.py 注册（只写不挂 = 死代码，"
        "且会与 main.py 的同名端点形成两套实现）：" + ", ".join(orphans))


def _iter_api_routes(routes):
    """递归收集所有 /api/ 路由 (path, method)。

    ⚠️ 2026-09-30 修复：**必须递归**。本仓的 FastAPI 版本里 `app.include_router()`
    不会把子路由摊平进 `app.routes`，而是插入一个 `_IncludedRouter` 包装对象
    （实测：app.routes 90 项里有 3 个 _IncludedRouter，push/queue/stats 共 8 个端点
    **完全不可见**）。旧版守卫只遍历 `app.routes`，因此对"拆分到 routes/ 的端点被
    重复注册"这一**正是它要防的场景**是盲的 —— 等于门禁失效。
    """
    for route in routes:
        # 本仓 FastAPI 版本把 include_router 的结果包成 `_IncludedRouter`，
        # 该对象**没有** .routes/.path，只暴露 `original_router`（实测）——
        # 旧守卫因此对子路由完全失明。这里两条路径都跟。
        sub = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None)
        if sub:
            yield from _iter_api_routes(sub)
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not str(path).startswith("/api/"):
            continue
        for method in methods:
            yield (str(path), str(method).upper())


def test_no_duplicate_registered_routes():
    """同一「路径 + 方法」不得注册两次——后注册的那份永远不生效。"""
    from server.main import app

    seen = collections.Counter(_iter_api_routes(app.routes))
    dups = {f"{m} {p}": n for (p, m), n in seen.items() if n > 1}
    assert not dups, f"同一路径+方法被注册多次（隐式覆盖，后者永不生效）：{dups}"


def test_route_collector_sees_included_routers():
    """反证守卫不是"空跑"：必须能看到 include_router 注册进来的端点。

    这是上一条守卫的前提条件——如果收集器看不到子路由，重复注册永远测不出来。
    这里断言"能看到 route_push 的推送端点"（它一直是经 include_router 注册的）。
    """
    from server.main import app
    routes = set(_iter_api_routes(app.routes))
    assert any(p == "/api/v1/push/report" for p, _m in routes), \
        f"收集器看不到 include_router 注册的端点，守卫形同虚设（当前 {len(routes)} 条）"
    # 拆分后的模块端点也必须在（S1：queue/stats）
    assert any(p == "/api/v1/queue" for p, _m in routes), "看不到 route_queue 的端点"
    assert any(p == "/api/v1/stats/trend" for p, _m in routes), "看不到 route_stats 的端点"


def test_auth_deps_have_single_source():
    """鉴权依赖与令牌逻辑只能有 security.py 一份实现（deps.py 只再导出）。

    历史问题：deps.py 里另有一整套更弱的并行鉴权栈——缺省密钥是字面量
    "change-me-in-prod"、工号存在性校验写成 `import src.accounts`（src 不是包，
    该 ImportError 被吞掉 → 校验是死代码）、本地来源不校验账号。而
    ARCHITECTURE.md 恰把 deps.py 描述为"依赖注入"，按文档 import 就拿到弱鉴权。
    """
    from server import deps, security

    for name in ("require_emp", "require_emp_local", "require_admin",
                 "make_token", "verify_token", "_emp_from_auth", "SECRET"):
        assert getattr(deps, name) is getattr(security, name), (
            f"server/deps.{name} 与 server/security.{name} 不是同一个对象——"
            "鉴权/令牌逻辑又分叉了（deps 只能 re-export）")


def test_no_guessable_default_secret():
    """不允许再出现可猜测的默认 API 密钥（只看代码，不看注释）。"""
    for rel in ("server/security.py", "server/deps.py", "server/main.py"):
        src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        assert "change-me-in-prod" not in code, (
            f"{rel} 出现可猜测的默认密钥；缺省必须走 _load_or_create_secret()")


def test_token_logic_defined_in_one_place():
    """make_token / verify_token 只允许在 server/security.py 里定义。"""
    offenders = []
    for rel in ("server/deps.py", "server/main.py", "server/core.py",
                "server/routes/route_push.py"):
        p = os.path.join(_ROOT, rel)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        if re.search(r"^def\s+(make_token|verify_token)\b", src, re.M):
            offenders.append(rel)
    assert not offenders, f"以下文件重复定义了令牌逻辑：{offenders}"


def test_no_duplicate_rule_implementations():
    """同一规则方法不得在多个 mixin 模块里各写一份。

    2026-09-30 实例：`_r10_template` 在 rules_sentence.py 与 rules_template.py
    各有一份**逐字相同**的实现，而 RuleEngine 的多继承只会用 MRO 靠前的那份
    → 另一份是永不执行的死代码；改错那份会"改了没反应"（与历史上"双引擎/双路由"
    同类）。修 R10 单行报告误报时顺手去重，这里加断言防止再长出来。
    """
    import glob
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    owners = {}
    for path in glob.glob(os.path.join(root, "src", "engine", "rules_*.py")):
        src = open(path, encoding="utf-8").read()
        for name in re.findall(r"^\s*def (_r\d+[a-z_]*)", src, re.M):
            owners.setdefault(name, []).append(os.path.basename(path))
    dup = {k: v for k, v in owners.items() if len(v) > 1}
    assert not dup, f"规则实现重复（MRO 只生效一个，另一个是死代码）：{dup}"
