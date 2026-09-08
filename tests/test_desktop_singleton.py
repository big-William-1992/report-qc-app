"""desktop_app 单实例锁平台分支回归（2026-08-23 Windows 隐患修复）。

背景：Windows 的 os.kill(pid, 0) 语义是 TerminateProcess——旧锁文件探活会把
正在运行的上一实例直接杀掉。修复后 Windows 改「端口绑定」判定；macOS/Linux
保留锁文件 + os.kill 探活（POSIX sig=0 安全）。

本文件全部用 mock/临时目录，不真正绑定 8500、不杀任何进程。
"""
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop_app  # noqa: E402


class _FakeSock:
    def __init__(self, bind_err=None):
        self._bind_err = bind_err
        self.bound = None
        self.closed = False

    def bind(self, addr):
        if self._bind_err is not None:
            raise self._bind_err
        self.bound = addr

    def close(self):
        self.closed = True


def _free_port():
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def no_lockfile(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_app, "SINGLETON_LOCK",
                        str(tmp_path / "singleton_test.lock"))
    return str(tmp_path / "singleton_test.lock")


# ---------- Windows 分支：端口绑定 ----------

def test_win_bind_ok_returns_true(monkeypatch, no_lockfile):
    monkeypatch.setattr(sys, "platform", "win32")
    port = _free_port()
    sock = _FakeSock()

    class FakeSocketModule:
        AF_INET = 2
        SOCK_STREAM = 1

        @staticmethod
        def socket(*a, **k):
            return sock

    monkeypatch.setattr(desktop_app, "socket", FakeSocketModule)
    monkeypatch.setattr(desktop_app, "PREFERRED_PORT", port)
    killed = []
    monkeypatch.setattr(os, "kill", lambda *a, **k: killed.append(a))

    assert desktop_app._acquire_singleton() is True
    assert sock.bound == ("127.0.0.1", port)
    assert sock.closed, "bind 成功也必须立即释放 socket"
    assert not os.path.exists(no_lockfile), "Windows 分支不得创建/触碰锁文件"
    assert killed == [], "Windows 分支绝不允许 os.kill（会 TerminateProcess 对方实例）"


def test_win_bind_conflict_returns_false(monkeypatch, no_lockfile):
    monkeypatch.setattr(sys, "platform", "win32")
    port = _free_port()

    class FakeSocketModule:
        AF_INET = 2
        SOCK_STREAM = 1

        @staticmethod
        def socket(*a, **k):
            return _FakeSock(bind_err=OSError(10048, "port in use"))

    monkeypatch.setattr(desktop_app, "socket", FakeSocketModule)
    monkeypatch.setattr(desktop_app, "PREFERRED_PORT", port)
    killed = []
    monkeypatch.setattr(os, "kill", lambda *a, **k: killed.append(a))

    assert desktop_app._acquire_singleton() is False
    assert not os.path.exists(no_lockfile)
    assert killed == []


def test_win_branch_ignores_posix_lockfile(monkeypatch, no_lockfile):
    """Windows 判定只看端口：即使残留锁文件写着活 PID 也按端口结论走。"""
    with open(no_lockfile, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(desktop_app, "PREFERRED_PORT", _free_port())

    class FakeSocketModule:
        AF_INET = 2
        SOCK_STREAM = 1

        @staticmethod
        def socket(*a, **k):
            return _FakeSock()

    monkeypatch.setattr(desktop_app, "socket", FakeSocketModule)
    assert desktop_app._acquire_singleton() is True


# ---------- macOS/Linux 分支：锁文件 + PID 探活（原逻辑不回归）----------

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX 锁分支")
def test_posix_first_acquire_true_and_writes_pid(monkeypatch, no_lockfile):
    assert desktop_app._acquire_singleton() is True
    with open(no_lockfile, encoding="utf-8") as f:
        assert int(f.read().strip()) == os.getpid()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX 锁分支")
def test_posix_live_pid_blocks_second_instance(monkeypatch, tmp_path):
    lock = str(tmp_path / "s.lock")
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    monkeypatch.setattr(desktop_app, "SINGLETON_LOCK", lock)
    assert desktop_app._acquire_singleton() is False


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX 锁分支")
def test_posix_stale_pid_taken_over(monkeypatch, tmp_path):
    lock = str(tmp_path / "s.lock")
    dead = _dead_pid()
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(dead))
    monkeypatch.setattr(desktop_app, "SINGLETON_LOCK", lock)
    assert desktop_app._acquire_singleton() is True
    with open(lock, encoding="utf-8") as f:
        assert int(f.read().strip()) == os.getpid()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX 锁分支")
def test_posix_corrupt_lockfile_takeover(monkeypatch, tmp_path):
    lock = str(tmp_path / "s.lock")
    with open(lock, "w", encoding="utf-8") as f:
        f.write("not-a-pid")
    monkeypatch.setattr(desktop_app, "SINGLETON_LOCK", lock)
    assert desktop_app._acquire_singleton() is True


# ---------- app_paths.frozen_resource_dir ----------

def test_frozen_resource_dir_dev_mode_matches_legacy():
    import app_paths
    d = app_paths.frozen_resource_dir("assets")
    legacy = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(app_paths.__file__)), "..", "assets"))
    assert not getattr(sys, "frozen", False)
    assert d == legacy
