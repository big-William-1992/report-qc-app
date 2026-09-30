"""
test_local_trust_hardening.py — 「本机免令牌」通道的安全加固（2026-09-30 新增）

真实绕过路径（交付硬化复核时发现，高危）：
  `require_emp_local` 原先只判断 `request.client.host` 是不是回环地址，
  而**应用绑 127.0.0.1 + 反向代理同机**这种常见院内多机部署下，
  所有内网用户的 client.host 都是 127.0.0.1 → 任何人只要带
  `X-Emp-Id: <任意工号>` 就被当成可信本机，可**免令牌冒充他人**；
  审计日志里来源 IP 也全变成 127.0.0.1，事后无法追溯。

加固：本机通道必须同时满足「未显式关闭(QC_LOCAL_TRUST≠0)」+「回环」+「不带代理转发头」。
真实桌面端直连不带这些头，因此便利性保留；经反代来的请求一律走 Bearer token。
"""
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="qc_localtru_")
_APPDATA = os.path.join(_TMP, "appdata")
os.makedirs(_APPDATA, exist_ok=True)
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "lt.db")
os.environ["QC_APPDATA"] = _APPDATA
os.environ["QC_API_SECRET"] = "local-trust-test-secret"
os.environ.pop("QC_LOCAL_TRUST", None)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient      # noqa: E402
from conftest import temp_db                   # noqa: E402
from server import main as appmod              # noqa: E402
from server import security                    # noqa: E402

_EMP, _PWD = "lt_admin", "local-trust-123"


@pytest.fixture(autouse=True)
def _bound_db():
    """在本模块**测试执行时**绑定临时库并播种账号，结束后还原绑定。

    为什么不在 import 时绑定：pytest 会先把所有测试模块 import 完再执行，
    而 `set_test_db` 改的是**全局** engine/SessionLocal —— 在 import 时绑定会让
    本模块"抢走"其它模块的库绑定（实测会把 test_data_layer_unified /
    test_health_endpoint 一起搞挂）。这是仓库已知的测试隔离脆弱点（AGENTS §10），
    新写的测试一律"用时绑定 + 用完还原"。
    """
    with temp_db(os.path.join(_TMP, "lt.db"), _APPDATA):
        c = TestClient(appmod.app, client=("127.0.0.1", 50000))
        c.post("/api/v1/license/disclaimer", json={})
        c.post("/api/v1/accounts",
               json={"emp_id": _EMP, "password": _PWD, "name": "本机信任测试"})
        yield


def _client(host="127.0.0.1"):
    return TestClient(appmod.app, client=(host, 50000))


def test_loopback_without_proxy_headers_still_convenient():
    """桌面端/本机脚本直连：不带代理头 → 便利通道保留。"""
    c = _client()
    r = c.get("/api/v1/queue", headers={"X-Emp-Id": _EMP})
    assert r.status_code == 200, r.text


def test_loopback_with_forwarded_for_is_not_trusted():
    """经反向代理（带 X-Forwarded-For）→ 不得再拿 X-Emp-Id 冒充本机。"""
    c = _client()
    r = c.get("/api/v1/queue", headers={"X-Emp-Id": _EMP,
                                        "X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 401, f"反代来的请求不应被当成本机：{r.status_code}"


def test_loopback_with_real_ip_header_is_not_trusted():
    c = _client()
    r = c.get("/api/v1/queue", headers={"X-Emp-Id": _EMP, "X-Real-IP": "10.1.2.3"})
    assert r.status_code == 401, r.text


def test_local_channel_can_be_disabled_explicitly(monkeypatch):
    """多机部署时可用 QC_LOCAL_TRUST=0 彻底关掉本机通道。"""
    monkeypatch.setenv("QC_LOCAL_TRUST", "0")
    c = _client()
    r = c.get("/api/v1/queue", headers={"X-Emp-Id": _EMP})
    assert r.status_code == 401, "QC_LOCAL_TRUST=0 时不应继续信任本机头"


def test_remote_client_never_gets_local_trust():
    c = _client(host="10.9.9.9")
    r = c.get("/api/v1/queue", headers={"X-Emp-Id": _EMP})
    assert r.status_code == 401, "非回环来源绝不能走本机通道"


def test_bearer_token_still_works_through_proxy():
    """加固不能把正常路径堵死：反代场景下 Bearer token 必须照常可用。"""
    c = _client()
    login = c.post("/api/v1/accounts/login", json={"emp_id": _EMP, "password": _PWD})
    assert login.status_code == 200, login.text
    data = login.json().get("data") or {}
    token = data.get("token") or data.get("access_token") or ""
    assert token, f"登录未返回 token：{data}"
    r = c.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}",
                                        "X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 200, r.text


def test_local_trust_helper_matrix():
    """直接对判定函数做矩阵断言（防止有人把条件改宽）。"""
    class _Req:
        def __init__(self, host, headers):
            self.client = type("C", (), {"host": host})()
            self.headers = headers

    assert security.local_trust_allowed(_Req("127.0.0.1", {})) is True
    assert security.local_trust_allowed(_Req("::1", {})) is True
    assert security.local_trust_allowed(_Req("127.0.0.1", {"x-forwarded-for": "1.2.3.4"})) is False
    assert security.local_trust_allowed(_Req("127.0.0.1", {"x-real-ip": "1.2.3.4"})) is False
    assert security.local_trust_allowed(_Req("127.0.0.1", {"forwarded": "for=1.2.3.4"})) is False
    assert security.local_trust_allowed(_Req("10.1.1.1", {})) is False
