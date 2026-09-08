"""crash.log 轮转（desktop_app._rotate_crash_log）单测。"""
import os
import tempfile

from desktop_app import _rotate_crash_log, _CRASH_LOG_MAX_BYTES


def test_below_threshold_no_rotate():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("short\n")
        f.flush()
        path = f.name
    try:
        assert _rotate_crash_log(path, max_bytes=1000) is False
        assert os.path.exists(path)
        assert not os.path.exists(path + ".1")
    finally:
        os.unlink(path)


def test_above_threshold_rotates():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("x" * 200)
        f.flush()
        path = f.name
    try:
        # create stale .1 to verify it gets overwritten
        with open(path + ".1", "w") as f1:
            f1.write("old .1")
        assert _rotate_crash_log(path, max_bytes=100) is True
        assert not os.path.exists(path)
        assert os.path.exists(path + ".1")
        with open(path + ".1") as f1:
            assert f1.read() == "x" * 200
    finally:
        for p in (path, path + ".1"):
            if os.path.exists(p):
                os.unlink(p)


def test_missing_file_no_crash():
    assert _rotate_crash_log("/tmp/_nonexistent_crash_test_.log", max_bytes=10) is False


def test_default_threshold():
    assert _CRASH_LOG_MAX_BYTES == 5 * 1024 * 1024
