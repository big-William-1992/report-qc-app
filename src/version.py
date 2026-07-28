"""
version.py — 星衍放射质控软件 版本信息（单一事实来源）

APP_VERSION  语义版本号，手动维护。
BUILD_TIME   由 CI 打包时写入 assets/build_info.json（UTC，ISO 格式）；
             本地源码运行时为空字符串，表示开发版。
COMMIT       构建对应的 git 短 sha；本地为 "dev"。
"""
import os
import json

APP_VERSION = "2.4.0"


def _load_build_info():
    """读取 CI 打包时写入的构建信息；本地源码运行返回默认值。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(here, "..", "assets", "build_info.json"),
        os.path.join(here, "build_info.json"),
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("build_time", ""), d.get("commit", "dev")
        except Exception:
            continue
    return "", "dev"


BUILD_TIME, COMMIT = _load_build_info()
