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
