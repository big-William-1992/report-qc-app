"""
test_updater_verification.py — 自动更新完整性校验回归（2026-09-30 审计新增）

背景（真实缺陷）：客户端会去取「<下载URL>.sha256」并强制校验，但**发布流程
从不生成**该文件 → 校验永远走「无校验文件：跳过（宽容）」分支，
2026-08-18 那次"防供应链投毒"加固等于没生效。

本文件锁两件事：
1. 校验逻辑本身正确（哈希不匹配 → 删除并抛错；匹配 → 放行）；
2. 发布流程确实会生成 .sha256（用静态断言把「客户端要校验」与「流水线会产出」
   钉在一起——这是此前静默断裂的那一环）。
"""
import hashlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import auto_updater as au          # noqa: E402


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_checksum(monkeypatch, body: bytes):
    monkeypatch.setattr(au.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(body))


def test_matching_checksum_passes(tmp_path, monkeypatch):
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"original-bytes")
    good = hashlib.sha256(b"original-bytes").hexdigest()
    _patch_checksum(monkeypatch, f"{good}  pkg.zip\n".encode())

    au._verify_download_sha256(str(dest))     # 不应抛错
    assert dest.exists()


def test_mismatching_checksum_removes_and_raises(tmp_path, monkeypatch):
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"tampered-bytes")
    other = hashlib.sha256(b"original-bytes").hexdigest()
    _patch_checksum(monkeypatch, other.encode())

    with pytest.raises(RuntimeError):
        au._verify_download_sha256(str(dest))
    assert not dest.exists(), "校验失败必须删除被篡改的更新包"


def test_missing_checksum_is_tolerated_but_documented(tmp_path, monkeypatch):
    """无校验文件时保持宽容（GitHub 动态 tarball 无法附带），但要清楚这是兜底而非保护。"""
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"anything")

    def _boom(*a, **k):
        raise OSError("404")

    monkeypatch.setattr(au.urllib.request, "urlopen", _boom)
    au._verify_download_sha256(str(dest))     # 不抛错
    assert dest.exists()


def test_client_expects_sha256_sidecar():
    """客户端必须仍是"取 <URL>.sha256"的契约（改这里就要同步改发布流程）。"""
    src = open(os.path.join(_ROOT, "src", "auto_updater.py"), encoding="utf-8").read()
    assert '_download_url() + ".sha256"' in src


def test_release_pipeline_generates_sha256():
    """发布流程必须生成 .sha256 —— 否则上面的校验永远不会执行（本次修的缺陷）。"""
    wf = open(os.path.join(_ROOT, ".github", "workflows", "build-windows.yml"),
              encoding="utf-8").read()
    assert "sha256sum" in wf, "发布流程缺少 sha256sum 生成步骤"
    assert ".sha256" in wf, "发布流程未把 .sha256 一起发布"
    # 且必须排在发布之前
    assert wf.index("sha256sum") < wf.index("softprops/action-gh-release"), \
        "校验文件必须在 Publish Release 之前生成"
