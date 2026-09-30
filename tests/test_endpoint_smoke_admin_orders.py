"""
test_endpoint_smoke_admin_orders.py — 订单/导出/备份等"没人测过"端点的冒烟回归（2026-09-30）

## 背景：一批端点长期 100% 不可用，而全量测试从未覆盖

本轮路由拆分中，靠"静态悬空引用守卫 + 手工冒烟"发现并修复了两类**同源**缺陷：

1. **调用了不存在的会话入口** —— 5 个订单端点 + `/api/v1/export/data` 都写
   `db.get_session()`，而 `server/db.py` **没有这个函数**（只有 `SessionLocal`
   与 `get_db` 生成器）→ 每个请求都 `AttributeError`。
   后果：**订单管理（商业化功能）整块失效**，且无人发现。

2. **依赖缺失 / 名称写错** —— `/api/v1/export/data` 另有两处：`_json` 只在别的函数里
   被局部导入、`datetime` 未导入（写成 `datetime.datetime.now()`）。

共性：这些端点**没有任何测试**，失败又常被包装成业务响应（不是 500），
所以"全绿"并不代表可用。本文件为它们补上最小冒烟，防止回归。
"""
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="qc_admin_smoke_")
_APPDATA = os.path.join(_TMP, "appdata")
os.makedirs(_APPDATA, exist_ok=True)
os.environ["QC_DB_OVERRIDE"] = os.path.join(_TMP, "smoke.db")
os.environ["QC_APPDATA"] = _APPDATA
os.environ["QC_BACKUP_DIR"] = os.path.join(_TMP, "backups")     # 避免写用户目录（沙箱）

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "tests"), os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient      # noqa: E402
from conftest import temp_db                   # noqa: E402
from server import main as appmod              # noqa: E402


@pytest.fixture()
def admin_client():
    """建管理员账号并登录，返回 (client, headers)。用例内绑定临时库。"""
    with temp_db(os.path.join(_TMP, "smoke.db"), _APPDATA):
        c = TestClient(appmod.app, client=("127.0.0.1", 50000))
        c.post("/api/v1/license/disclaimer", json={})
        c.post("/api/v1/accounts", json={"emp_id": "smoke_admin",
                                         "password": "smoke-pass-1", "name": "冒烟管理员"})
        r = c.post("/api/v1/accounts/login",
                   json={"emp_id": "smoke_admin", "password": "smoke-pass-1"})
        token = (r.json().get("data") or {}).get("token") or ""
        assert token, f"登录未拿到 token：{r.text[:200]}"
        yield c, {"Authorization": f"Bearer {token}"}


def test_orders_endpoints_are_usable(admin_client):
    """订单四件套此前全部 AttributeError（db.get_session 不存在）→ 现在必须真能用。"""
    c, H = admin_client
    r = c.post("/api/v1/admin/orders/create",
               json={"customer_name": "冒烟测试医院", "amount": 59}, headers=H)
    assert r.status_code == 200, f"建单失败：{r.status_code} {r.text[:200]}"
    body = r.json()
    assert body.get("ok") and (body.get("data") or {}).get("order_no"), body
    assert c.get("/api/v1/admin/orders", headers=H).status_code == 200
    assert c.get("/api/v1/admin/orders/export", headers=H).status_code == 200


def test_export_data_endpoint_is_usable(admin_client):
    """/api/v1/export/data 此前三处缺陷叠加（会话入口、_json、datetime）→ 必须返回 JSON。

    注意：该端点用 `require_emp_local`，而**本机通道只认 X-Emp-Id、不认 Bearer**
    （见 server/security.require_emp_local 的本地分支）——这是既有的设计差异，
    故此处按本机用法传 X-Emp-Id（测试客户端来源为 127.0.0.1）。
    """
    c, _H = admin_client
    r = c.get("/api/v1/export/data", headers={"X-Emp-Id": "smoke_admin"})
    assert r.status_code == 200, f"导出失败：{r.status_code} {r.text[:200]}"
    assert '"exported_at"' in r.text and '"samples"' in r.text, r.text[:200]


def test_admin_read_endpoints_do_not_5xx(admin_client):
    """管理端只读端点不得因代码级错误（AttributeError/NameError）而 500。

    注：错误报告/备份状态会写用户数据目录，受限环境可能失败——这类**环境性**错误
    允许出现，但错误类型不得是 AttributeError/NameError（说明代码引用错了东西）。
    """
    c, H = admin_client
    for path in ("/api/v1/admin/audit-logs", "/api/v1/admin/license/status",
                 "/api/v1/admin/orders"):
        r = c.get(path, headers=H)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:160]}"
