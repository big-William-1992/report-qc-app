"""跨冻结/源码态的只读资源目录定位（2026-08-23 Windows 打包隐患修复）。

PyInstaller 6 onedir 布局下，datas 资源位于 <app>/_internal/（即 sys._MEIPASS），
而 os.path.dirname(sys.executable) 指向 <app>/——按 exe 目录拼接 assets/ 等资源
路径会落空。统一从这里取资源根目录：
  - 冻结态：优先 sys._MEIPASS；无该属性（老版 PyInstaller/特殊布局）时回退 exe 目录。
  - 非冻结：src/ 的上一级（项目根），与各调用方历史 dirname(__file__)/.. 行为完全一致。
注意：可写数据（crash.log、update 缓存、用户数据库）不适用本模块，仍应按
exe 所在目录 / 用户目录解析。
"""
import os
import sys


def frozen_resource_dir(*parts: str) -> str:
    """返回打包资源根目录，可选拼接子路径 parts。

    冻结（PyInstaller）：sys._MEIPASS（onedir 下为 <app>/_internal，datas 实际所在），
    缺失时回退 exe 所在目录。非冻结：项目根（src/app_paths.py 的上一级）。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(base, *parts)) if parts else os.path.normpath(base)
