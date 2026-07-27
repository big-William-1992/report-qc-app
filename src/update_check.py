"""
update_check.py — 检查更新（GitHub Releases）
星衍放射质控软件 · 内测支撑模块（纯标准库，零依赖）

比对策略（仓库用滚动 'latest' tag，tag 名恒为 latest 无法比语义版本）：
  用本地 BUILD_TIME 与 Release 的 published_at 比对：
    - 云端 published_at 明显晚于本地构建时间 → 有新版
    - 否则 → 已是最新
    - 本地无 BUILD_TIME（源码/开发版）→ unknown（无法判断，仅展示信息）
网络请求放后台线程，失败静默，绝不阻塞启动。
"""
import json
import threading
import urllib.request
import datetime

import version

_REPO = "big-William-1992/report-qc-app"
RELEASE_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{_REPO}/releases/latest"
# 源码分发（BUILD_TIME 为空）时，退回比对云端 tarball 内 src/version.py 的 APP_VERSION
# 用 api.github.com 的 tarball 接口（raw.githubusercontent 在部分网络不可达）
_TARBALL_URL = f"https://api.github.com/repos/{_REPO}/tarball/latest"


def _parse_iso(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _fetch_latest(timeout=8):
    req = urllib.request.Request(
        RELEASE_API,
        headers={"User-Agent": "xingyan-qc-update",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _fetch_cloud_version(timeout=8):
    """从云端 tarball 内 src/version.py 解析 APP_VERSION（源码分发回退比对用）。

    用 api.github.com 的 tarball 接口而非 raw.githubusercontent，
    后者在部分网络/代理下不可达（实测 502）。tarball 仅数十 KB，内存解析即可。
    """
    import re
    import io
    import tarfile
    req = urllib.request.Request(
        _TARBALL_URL,
        headers={"User-Agent": "xingyan-qc-update",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    tf = tarfile.open(fileobj=io.BytesIO(data))
    for m in tf.getmembers():
        if m.name.endswith("src/version.py"):
            f = tf.extractfile(m)
            text = f.read().decode("utf-8", "ignore") if f else ""
            mm = re.search(r'APP_VERSION\s*=\s*["\']([0-9]+(?:\.[0-9]+)*)["\']', text)
            return mm.group(1) if mm else None
    return None


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0,)


def _ver_gt(a, b):
    """a > b（按点分版本号）。"""
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta > tb


def check_update_sync(timeout=8):
    """
    同步检查更新，返回 dict：
      {"status": "update"|"latest"|"unknown"|"error",
       "message": str, "url": RELEASE_PAGE, "published_at": str}
    """
    result = {"status": "error", "message": "",
              "url": RELEASE_PAGE, "published_at": ""}
    try:
        d = _fetch_latest(timeout=timeout)
    except Exception as e:
        result["message"] = f"检查更新失败（网络异常）：{e}"
        return result

    published = d.get("published_at", "")
    result["published_at"] = published
    local_bt = (getattr(version, "BUILD_TIME", "") or "").strip()

    if not local_bt:
        # 源码分发：BUILD_TIME 为空，退回比对云端 APP_VERSION
        try:
            cloud_ver = _fetch_cloud_version(timeout=timeout)
        except Exception:
            cloud_ver = None
        if cloud_ver and _ver_gt(cloud_ver, version.APP_VERSION):
            result["status"] = "update"
            result["message"] = (f"发现新版本 v{cloud_ver}！\n"
                                 f"你当前：v{version.APP_VERSION}\n\n"
                                 f"建议下载并更新。")
        elif cloud_ver:
            result["status"] = "latest"
            result["message"] = (f"已是最新版本（v{version.APP_VERSION}）。")
        else:
            result["status"] = "unknown"
            result["message"] = (f"当前为开发/源码版本（v{version.APP_VERSION}）。\n"
                                 f"云端最新发布于：{published or '未知'}")
        return result

    pub_dt = _parse_iso(published)
    loc_dt = _parse_iso(local_bt)
    if pub_dt and loc_dt and pub_dt > loc_dt + datetime.timedelta(minutes=1):
        result["status"] = "update"
        result["message"] = (f"发现新版本！\n"
                             f"云端发布时间：{published}\n"
                             f"你的版本构建于：{local_bt}\n\n"
                             f"建议下载最新版本。")
    else:
        result["status"] = "latest"
        result["message"] = f"已是最新版本（v{version.APP_VERSION}）。"
    return result


def check_update_async(callback, timeout=8):
    """后台线程检查更新，完成后以 result dict 调用 callback。

    注意：callback 在子线程执行，更新 UI 必须用 widget.after(0, ...) 切回主线程。
    """
    def _run():
        res = check_update_sync(timeout=timeout)
        try:
            callback(res)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
