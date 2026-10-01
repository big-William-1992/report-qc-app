"""
test_repo_hygiene.py — 仓库卫生与版本一致性守卫（2026-09-30 审计新增）

守三件在本仓真实出过问题的事：
1. **版本号漂移**：单次审计里就出现过 4.3.3 / 4.3.4 / 4.3.6 三个版本号并存
   （version.py、CHANGELOG、RELEASE_CHECKLIST 各说各话）。现在以
   `src/version.py::APP_VERSION` 为唯一事实来源，其余位置必须与它一致。
2. **审查报告堆在根目录**：根目录一度累积 20 份 md，活文档与历史报告混在一起，
   新人/agent 分不清哪份还有效。历史报告统一进 `docs/reviews/`。
3. **统一自检入口可用**：`scripts/check_all.sh` 必须存在且语法正确
   （它把 pytest/ruff/bundle/基线/E2E 收成一条命令）。
"""
import os
import re
import shutil
import subprocess

import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import version  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import subprocess_env  # noqa: E402


# 允许留在根目录的"活文档"（新增前请先想清楚是否该进 docs/reviews/）
_LIVE_ROOT_DOCS = {
    "README.md", "AGENTS.md", "ARCHITECTURE.md", "CHANGELOG.md",
    "DEPLOYMENT.md", "DEVELOPMENT_GUIDE.md", "PRD.md", "TESTING_STRATEGY.md",
    "USER_GUIDE.md", "RELEASE_CHECKLIST.md", "VERSION_LIFECYCLE.md",
    "PRIVACY_POLICY.md", "TERMS_OF_SERVICE.md", "DATA_SECURITY.md",
    "LICENSE-HISTORY.md",   # 许可变更历史（事实声明，需与 LICENSE 同处根目录）
    "NOTICE.md",            # Required Notice + 第三方组件（PolyForm 要求随副本传递）
    "LLM质控层技术方案.md", "新Mac恢复步骤.md",
}


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_app_version_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.APP_VERSION), \
        f"APP_VERSION 应为 x.y.z：{version.APP_VERSION!r}"


def test_changelog_has_current_version_at_top():
    """CHANGELOG 必须**在最前面**有当前版本的条目（漏写=发布无记录）。"""
    heads = re.findall(r"^## v(\d+\.\d+\.\d+)", _read("CHANGELOG.md"), re.M)
    assert heads, "CHANGELOG 里找不到任何 `## vX.Y.Z` 标题"
    assert heads[0] == version.APP_VERSION, (
        f"CHANGELOG 顶部是 v{heads[0]}，而 version.APP_VERSION={version.APP_VERSION}；"
        "新版本要加在**最上面**")
    assert version.APP_VERSION in heads, "CHANGELOG 缺少当前版本的条目"


def test_release_checklist_mentions_current_version():
    txt = _read("RELEASE_CHECKLIST.md")
    assert f"v{version.APP_VERSION}" in txt, \
        f"RELEASE_CHECKLIST.md 未提及当前版本 v{version.APP_VERSION}"


def test_installer_version_is_injected_not_hardcoded():
    """安装包版本必须由 CI 注入（/DAppVersion=），不能与 version.py 双写。"""
    iss = os.path.join(_ROOT, "build", "setup.iss")
    if not os.path.isfile(iss):
        pytest.skip("无 Inno Setup 脚本")
    with open(iss, encoding="utf-8") as fh:
        txt = fh.read()
    assert "/DAppVersion" in _read(".github/workflows/build-windows.yml") or \
        "AppVersion" in txt, "安装包版本未从 CI 注入"
    # 若脚本里出现与本版本号不同的具体版本，说明有人写死了
    for found in set(re.findall(r'"(\d+\.\d+\.\d+)"', txt)):
        assert found == version.APP_VERSION or found == "1.0", \
            f"build/setup.iss 写死了版本 {found}，应改为 CI 注入"


def test_no_audit_reports_piling_up_in_root():
    """审查/修复报告必须进 docs/reviews/，不要堆在仓库根目录。"""
    root_mds = {f for f in os.listdir(_ROOT) if f.endswith(".md")}
    extra = root_mds - _LIVE_ROOT_DOCS
    assert not extra, (
        f"根目录出现未登记的文档：{sorted(extra)}\n"
        "  活文档请加进本测试的 _LIVE_ROOT_DOCS；审查/修复报告请放 docs/reviews/")
    assert os.path.isdir(os.path.join(_ROOT, "docs", "reviews")), \
        "docs/reviews/ 应存在（历史审查报告归档处）"


def test_check_all_script_exists_and_parses():
    """统一自检入口必须在且语法正确（否则等于没有门禁）。"""
    sh = os.path.join(_ROOT, "scripts", "check_all.sh")
    assert os.path.isfile(sh), "缺少 scripts/check_all.sh（统一自检入口）"
    if shutil.which("bash") is None:
        pytest.skip("无 bash")
    r = subprocess.run(["bash", "-n", sh], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"check_all.sh 语法错误：{r.stderr[:300]}"


def test_ci_has_frontend_e2e_gate():
    """前端 e2e 必须真的接进 CI —— 白屏事故就是这样漏出去的。"""
    wf_dir = os.path.join(_ROOT, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        pytest.skip("无 workflows")
    hits = []
    for fn in os.listdir(wf_dir):
        if not fn.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(wf_dir, fn), encoding="utf-8") as fh:
            txt = fh.read()
        if "playwright test" in txt:
            hits.append(fn)
    assert hits, ("没有任何 workflow 跑 `playwright test` —— 前端界面行为无 CI 门禁"
                  "（2026-09-30 白屏事故的直接原因）")


# ── 许可一致性（2026-09-30 D2 决策：PolyForm Noncommercial 1.0.0）─────────────
# 这些断言的目的：防止"改了 LICENSE 但对外文案/发布流程没跟上"，
# 以及防止后人把许可"顺手"改回宽松许可（那会让"商业需授权"整套商业模型失效）。

def test_license_is_polyform_noncommercial():
    lic = _read("LICENSE")
    assert "PolyForm Noncommercial License 1.0.0" in lic, \
        "LICENSE 应为 PolyForm Noncommercial 1.0.0（D2 决策；不要改回 MIT/Apache）"
    assert "MIT License" not in lic.split("PolyForm")[0], "LICENSE 顶部不应残留 MIT 文本"


def test_license_notice_contains_required_notice():
    """PolyForm 的 Notices 章节要求随副本传递 `Required Notice:` 行。"""
    for f in ("LICENSE", "NOTICE.md"):
        assert "Required Notice:" in _read(f), f"{f} 缺少 Required Notice 行"


def test_mit_history_is_archived():
    """旧 MIT 文本必须留档：已按 MIT 取得权利的人需要能核对来源。"""
    assert os.path.isfile(os.path.join(_ROOT, "LICENSE-MIT")), \
        "缺少 LICENSE-MIT（旧 MIT 文本存档）"
    hist = _read("LICENSE-HISTORY.md")
    assert "MIT" in hist and "PolyForm" in hist, "许可变更历史必须同时记录 MIT 与 PolyForm 的分界"


def test_public_docs_match_license():
    """对外文档必须与新许可口径一致（不能还写着"MIT 免费可再分发"）。"""
    for rel in ("README.md", "docs/DISCLAIMER.md", "docs/index.html",
                "TERMS_OF_SERVICE.md"):
        txt = _read(rel)
        assert "PolyForm" in txt, f"{rel} 未说明当前许可为 PolyForm"
        assert "可自由使用、修改与再分发" not in txt, \
            f"{rel} 仍残留 MIT 时期的“可自由再分发”表述（与非商业许可冲突）"


def test_ci_bundle_carries_license():
    """发布物必须随附许可（PolyForm 的硬性义务）——CI 需把许可复制进 dist。"""
    wf = _read(".github/workflows/build-windows.yml")
    assert "cp LICENSE" in wf and "dist/报告质控软件" in wf, \
        "CI 未把 LICENSE 复制进打包目录（绿色版与安装包都从该目录取文件）"
    assert 'grep -q "Required Notice:"' in wf, "CI 未校验 Required Notice 行存在"
