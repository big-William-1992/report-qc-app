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


def test_no_duplicate_registered_routes():
    """同一「路径 + 方法」不得注册两次——后注册的那份永远不生效。"""
    from server.main import app

    seen = collections.Counter()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not str(path).startswith("/api/"):
            continue
        for method in methods:
            seen[(path, method)] += 1
    dups = {f"{m} {p}": n for (p, m), n in seen.items() if n > 1}
    assert not dups, f"同一路径+方法被注册多次（隐式覆盖，后者永不生效）：{dups}"
