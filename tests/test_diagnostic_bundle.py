"""
test_diagnostic_bundle.py — 诊断包导出回归（2026-09-30 审计新增）

背景（两个真实缺陷）：
1. `feedback.db` 的写入原本落在 `with ZipFile(...)` **块外** —— 归档已关闭，
   必然抛 ValueError 又被 except 吞掉，于是 CHANGELOG 里"诊断包纳入 feedback.db"
   从来没有生效；
2. `DEPLOYMENT.md` 给的命令是 `python -m src.log_utils`，但 src/ 不是包
   （无 __init__.py）且模块没有 CLI 入口 → 命令必然失败；该函数在全仓也**无人调用**。

修复后：包内含 README 说明、默认排除含患者正文的 feedback.db（显式开关才纳入）、
并提供可用的 CLI。
"""
import os
import subprocess
import sys
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _bundle(tmp_path, monkeypatch, include_patient_data=False):
    import log_utils as lu
    # 日志目录与反馈库都指到临时位置，避免碰真实数据
    logdir = tmp_path / "logs"
    logdir.mkdir(exist_ok=True)
    (logdir / "app.log").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(lu, "log_dir", lambda: str(logdir))
    monkeypatch.setattr(lu, "_system_info", lambda: {"app": "test"})
    if include_patient_data:
        dbdir = tmp_path / "db"
        dbdir.mkdir(exist_ok=True)
        (dbdir / "feedback.db").write_bytes(b"SQLite format 3\x00fake")
        import samplelib
        monkeypatch.setattr(samplelib, "db_path", lambda: str(dbdir / "qc.db"))
    return lu.export_diagnostic_bundle(str(tmp_path / "out"),
                                       include_patient_data=include_patient_data)


def test_bundle_has_readme_and_logs(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "system_info.json" in names
        assert any(n.startswith("logs/") for n in names)
        assert "README_诊断包.txt" in names
        txt = zf.read("README_诊断包.txt").decode("utf-8")
        assert "隐私" in txt and "feedback.db" in txt


def test_bundle_excludes_patient_data_by_default(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch, include_patient_data=False)
    with zipfile.ZipFile(z) as zf:
        assert "feedback.db" not in zf.namelist(), \
            "默认不得把含报告正文的 feedback.db 打进诊断包"
        assert "未包含 feedback.db" in zf.read("README_诊断包.txt").decode("utf-8")


def test_bundle_includes_patient_data_when_explicit(tmp_path, monkeypatch):
    """显式开关时必须真的入包——这正是此前"写在 with 块外"导致从来没生效的地方。"""
    z = _bundle(tmp_path, monkeypatch, include_patient_data=True)
    with zipfile.ZipFile(z) as zf:
        assert "feedback.db" in zf.namelist(), "显式要求却仍未入包（zip 块外写入回归）"
        assert "已包含 feedback.db" in zf.read("README_诊断包.txt").decode("utf-8")


def test_cli_is_runnable(tmp_path):
    """DEPLOYMENT.md 的命令必须真的能跑（不能再用 `python -m src.log_utils`）。"""
    out = tmp_path / "cli_out"
    r = subprocess.run([sys.executable, os.path.join(_SRC, "log_utils.py"),
                        "--dest", str(out)],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "QC_APPDATA": str(tmp_path / "appdata")})
    assert r.returncode == 0, f"CLI 退出码 {r.returncode}: {r.stderr[-500:]}"
    zips = list(out.glob("*.zip"))
    assert zips, f"CLI 未产出诊断包：{r.stdout[-300:]}"


def test_docs_use_runnable_command():
    src = open(os.path.join(_ROOT, "DEPLOYMENT.md"), encoding="utf-8").read()
    assert "python -m src.log_utils" not in src, "文档仍在写不可用的 `-m src.log_utils`"
    assert "python src/log_utils.py" in src
