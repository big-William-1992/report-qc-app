"""
version.py — 星衍放射质控软件 版本信息（单一事实来源）

APP_VERSION  语义版本号，手动维护。
BUILD_TIME   由 CI 打包时写入 assets/build_info.json（UTC，ISO 格式）；
             本地源码运行时为空字符串，表示开发版。
COMMIT       构建对应的 git 短 sha；本地为 "dev"。
"""
import os
import json

APP_VERSION = "4.3.3"


def _load_build_info():
    """读取 CI 打包时写入的构建信息；本地源码运行返回默认值。"""
    # 2026-08-24：冻结态 __file__ 指向 PYZ 合成路径，回溯不到 assets/；
    # 统一走 app_paths.frozen_resource_dir 定位 _MEIPASS/assets/build_info.json。
    try:
        from app_paths import frozen_resource_dir
        candidates = [frozen_resource_dir("assets", "build_info.json")]
    except ImportError:
        candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        os.path.join(here, "..", "assets", "build_info.json"),
        os.path.join(here, "build_info.json"),
    ])
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("build_time", ""), d.get("commit", "dev")
        except Exception:
            continue
    return "", "dev"


BUILD_TIME, COMMIT = _load_build_info()
