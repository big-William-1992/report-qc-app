"""
test_trial_anchor.py — 试用起点防重置回归（2026-09-30 审计新增）

背景（真实缺陷）：试用起点只存在 license.dat 里，而它在用户可写目录 →
    rm license.dat  →  check_trial 认为"首次运行"  →  白送满 TRIAL_DAYS。
HMAC 只防"改日期"，完全不防"删文件"。

修复：把同一份带签名的起点日期冗余写到多个锚点，取**最早的有效日期**为准，
任一锚点尚存，试用就不会被重置。

隔离：本文件 monkeypatch `license_utils._LICENSE_FILE` 到 tmp_path；由于它不等于
导入时记录的默认路径，`_using_default_license()` 为假 → 只用「同目录锚点」，
不会碰用户数据目录或注册表（保证测试之间互不污染）。
"""
import datetime
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import license_utils as lu          # noqa: E402


def _today_minus(days: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


@pytest.fixture()
def lic_path(tmp_path, monkeypatch):
    p = tmp_path / "assets" / "license.dat"
    p.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lu, "_LICENSE_FILE", str(p))
    assert not lu._using_default_license(), "测试必须用非默认许可路径，否则锚点会落到真实目录"
    return str(p)


def _write_lic(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _anchor_path(lic: str) -> str:
    return os.path.join(os.path.dirname(lic), lu._ANCHOR_NAME)


def test_first_run_writes_signed_anchor(lic_path):
    st, rest = lu.check_trial()
    assert st == "trial" and rest == lu.TRIAL_DAYS
    ap = _anchor_path(lic_path)
    assert os.path.exists(ap), "首次运行必须落锚点"
    d = json.load(open(ap, encoding="utf-8"))
    assert d.get("date") and lu._trial_verify(d["date"], d.get("sig", ""))


def test_deleting_license_does_not_reset_trial(lic_path):
    start = _today_minus(10)
    _write_lic(lic_path, {"first_run": {"date": start, "sig": lu._trial_sign(start)}})
    assert lu.check_trial() == ("trial", lu.TRIAL_DAYS - 10)

    os.remove(lic_path)                      # 用户"删文件重置试用"
    st, rest = lu.check_trial()
    assert st == "trial", f"删 license.dat 后试用被重置：{st}"
    assert rest == lu.TRIAL_DAYS - 10, f"剩余天数被重置：{rest}（应为 {lu.TRIAL_DAYS - 10}）"


def test_anchor_wins_when_license_start_is_later(lic_path):
    """把 license.dat 换成"起点更晚"的合法签名也无效——以锚点为准。"""
    old, new = _today_minus(20), _today_minus(1)
    _write_lic(lic_path, {"first_run": {"date": old, "sig": lu._trial_sign(old)}})
    assert lu.check_trial() == ("trial", lu.TRIAL_DAYS - 20)

    _write_lic(lic_path, {"first_run": {"date": new, "sig": lu._trial_sign(new)}})
    st, rest = lu.check_trial()
    assert (st, rest) == ("trial", lu.TRIAL_DAYS - 20), f"锚点未生效：{st},{rest}"


def test_expired_stays_expired_without_license(lic_path):
    start = _today_minus(lu.TRIAL_DAYS + 5)
    _write_lic(lic_path, {"first_run": {"date": start, "sig": lu._trial_sign(start)}})
    assert lu.check_trial()[0] == "expired"
    os.remove(lic_path)
    assert lu.check_trial()[0] == "expired", "删文件后不该复活试用"


def test_anchor_with_bad_signature_is_ignored(lic_path):
    """锚点必须是可信签名才有用（不能靠伪造锚点把试用往前推或往后拉）。"""
    ap = _anchor_path(lic_path)
    with open(ap, "w", encoding="utf-8") as f:
        json.dump({"date": _today_minus(999), "sig": "DEADBEEF"}, f)
    st, rest = lu.check_trial()
    assert st == "trial" and rest == lu.TRIAL_DAYS, f"伪造锚点被采信了：{st},{rest}"


def test_tampered_license_still_expired_with_anchor(lic_path):
    """篡改 license.dat 的签名仍判过期（不因锚点而放宽）。"""
    _write_lic(lic_path, {"first_run": {"date": _today_minus(1), "sig": "DEADBEEF"}})
    assert lu.check_trial()[0] == "expired"
