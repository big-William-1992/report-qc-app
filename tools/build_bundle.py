#!/usr/bin/env python3
"""
tools/build_bundle.py — 由 modules/*.js 生成经典脚本 app.bundle.js

为什么需要它（2026-09-30 事故）：
  index.html 用 **经典脚本** 方式加载 `js/app.bundle.js`（无 type="module"），
  因为桌面应用有 file:// 与 http:// 两条加载路径，ES 模块在 file:// 下会被 CORS 拦。
  v4.3.6 的 d73e2b9 重新生成 bundle 时**忘了剥掉顶层 import/export**，
  于是 app.bundle.js 在浏览器里直接 SyntaxError（`import` 在脚本里非法）——
  整个 SPA 的 JS 一行都不执行，界面全废，且没有任何测试能发现（HTTP 仍是 200）。
  本脚本把这一步固化成命令，配套 tests/test_frontend_bundle.py 守着。

用法：
    python3 tools/build_bundle.py            # 生成
    python3 tools/build_bundle.py --check    # 只校验是否已同步（CI/测试用）
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "web", "static", "js")
MODULES_DIR = os.path.join(JS_DIR, "modules")
BUNDLE = os.path.join(JS_DIR, "app.bundle.js")

# 拼接顺序 = 依赖顺序（先 core，再各业务模块），与既有 bundle 一致
ORDER = ["core.js", "shell.js", "qc.js", "rules.js", "data.js",
         "ocr.js", "settings.js", "ris.js", "feedback.js", "auth.js"]

# 顶层 import / export 必须剥掉：经典脚本里 `import` 非法，`export` 同样非法；
# 模块之间的符号共享由各模块的 Object.assign(window, {...}) 承担。
_IMPORT_RE = re.compile(r"^import\s.*?;?\s*$", re.M)
_EXPORT_RE = re.compile(r"^export\s.*?;?\s*$", re.M)


def strip_module_code(src: str) -> str:
    """去掉顶层的 import / export 语句（不碰缩进内部与字符串中的同名文本）。"""
    out = _IMPORT_RE.sub("", src)
    out = _EXPORT_RE.sub("", out)
    return out.strip("\n")


def build_bundle_text() -> str:
    parts = []
    for name in ORDER:
        path = os.path.join(MODULES_DIR, name)
        if not os.path.isfile(path):
            raise SystemExit(f"缺少模块文件：{path}")
        with open(path, encoding="utf-8") as fh:
            code = strip_module_code(fh.read())
        parts.append(f"// ====== module: {name} ======\n\n{code}\n")
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只校验 app.bundle.js 是否与 modules/ 同步且无 import/export")
    args = ap.parse_args()

    want = build_bundle_text()
    cur = ""
    if os.path.isfile(BUNDLE):
        with open(BUNDLE, encoding="utf-8") as fh:
            cur = fh.read()

    if args.check:
        bad = []
        if re.search(r"^(?:import|export)\s", cur, re.M):
            bad.append("app.bundle.js 仍含顶层 import/export（经典脚本会 SyntaxError）")
        # 按非空行比较，忽略空行差异
        norm = lambda s: [l.rstrip() for l in s.splitlines() if l.strip()]
        if norm(cur) != norm(want):
            bad.append("app.bundle.js 与 modules/*.js 不同步（改了模块没重新打包）")
        if bad:
            for b in bad:
                print("✗ " + b, file=sys.stderr)
            print("  修复：python3 tools/build_bundle.py", file=sys.stderr)
            return 1
        print("✓ app.bundle.js 与 modules/ 同步，且为合法经典脚本")
        return 0

    with open(BUNDLE, "w", encoding="utf-8") as fh:
        fh.write(want)
    print(f"✓ 已生成 {os.path.relpath(BUNDLE, ROOT)}"
          f"（{len(want.splitlines())} 行，{len(ORDER)} 个模块）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
