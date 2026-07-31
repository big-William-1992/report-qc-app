#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UIA 真机自检脚本 —— 在 Windows 上验证「PACS 报告能否被 UIA 完整读取（无滚动漂移）」。

为什么需要它
------------
UIA 只对"标准文本控件"有效；若 PACS 报告区是自绘 canvas / OpenGL / DirectX 渲染，
UIA 读不到文本。本脚本是「🔎 UIA检测」按钮的命令行版，用于：
  1) 在 Windows 真机上一次性确认自家 PACS 是否支持 UIA；
  2) 产出可留存的证据文件（JSON + MD），作为录屏 / 自媒体验证的素材。

用法（在 Windows 机器上）
-------------------------
  1. 启动 PACS 工作站客户端，打开一份真实报告（含滚动内容更佳）。
  2. 保持该报告窗口为「当前焦点窗口」（用鼠标点一下它，使其置顶）。
  3. 命令行运行：  python tools/uia_selfcheck.py
  4. 按提示确认「是否无需滚动即读到完整报告」，脚本写入证据文件。

跨平台说明
----------
非 Windows 平台（macOS / Linux）import 本模块不会触发 comtypes；运行本脚本只打印
指引，不会报错。真实采集仅在 Windows + 已装 comtypes / pywinauto 时生效。
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from pathlib import Path

# 让脚本无论从哪里调用都能找到 src/uia_provider.py
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from uia_provider import UIAProvider  # noqa: E402


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    _hr("UIA 真机自检  |  星衍 AI 放射质控")
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"平台：{platform.system()} {platform.release()}  |  Python：{platform.python_version()}")

    if platform.system() != "Windows":
        _hr("非 Windows 平台 —— 仅打印指引")
        print("UIA 仅在 Windows 生效。请在 Windows 真机上执行本脚本。")
        print("前置：pip install comtypes   （或 pip install pywinauto）")
        print("然后打开 PACS 报告窗口并使其置顶，再运行本脚本。")
        return 0

    provider = UIAProvider()

    _hr("① 可用性检查")
    if not provider.is_available():
        print("❌ UIA 不可用：" + provider.unavailable_reason())
        print("→ 解决：pip install comtypes 后重启脚本。")
        _write_evidence({"uia_available": False,
                         "reason": provider.unavailable_reason()})
        return 1
    print("✅ UIA 运行时可用（comtypes / pywinauto 已加载）")

    _hr("② 诊断前景窗口文本控件（= GUI 的「🔎 UIA检测」）")
    print("⏳ 请确保 PACS 报告窗口是当前焦点窗口，3 秒后开始读取…")
    import time
    time.sleep(3)
    diag = provider.diagnose_foreground()
    print(diag)

    elems = provider.list_text_controls()
    control_count = len(elems)
    if control_count == 0:
        print("\n❌ 未找到可读文本控件。可能：焦点不在 PACS / 报告区是自绘渲染。")
        _write_evidence({"uia_available": True, "control_count": 0,
                         "captured": None})
        return 1
    print(f"\n✅ 找到 {control_count} 个文本控件")

    _hr("③ 读取报告全文（无滚动漂移验证）")
    text = provider.capture_text()
    if not text:
        print("❌ 读到的文本为空或过短（<8 字），无法质控。")
        _write_evidence({"uia_available": True, "control_count": control_count,
                         "captured_chars": 0, "captured": None})
        return 1

    print(f"✅ 成功读取，共 {len(text)} 字。前 120 字预览：")
    print("-" * 40)
    print(text[:120] + ("…" if len(text) > 120 else ""))
    print("-" * 40)

    # 让操作员人工确认"是否无需滚动即读到完整报告"
    _hr("④ 无漂移确认（人工判定）")
    print("请确认：读到的文本是否包含『检查所见』开头 与『诊断印象/签名』结尾，")
    print("        且全程【无需滚动】窗口即可获得完整内容？")
    try:
        ans = input("输入 y 表示确认无漂移 / n 表示仍有缺失：").strip().lower()
    except EOFError:
        ans = ""  # 非交互环境（如 CI）默认未确认
    no_drift = ans == "y"
    print("✅ 已确认无漂移" if no_drift else "⚠️ 未确认 / 存在缺失（请改用 OCR 或剪贴板方案）")

    evidence = {
        "uia_available": True,
        "control_count": control_count,
        "captured_chars": len(text),
        "captured_preview": text[:500],
        "no_drift_confirmed": no_drift,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    path = _write_evidence(evidence)

    _hr("⑤ 结论")
    if no_drift:
        print("🎉 验证通过：UIA 可完整读取该 PACS 报告，根治 OCR 滚动漂移。")
        print("   可直接在软件中「主采集（UIA）」跑质控，并据此录屏宣传。")
    else:
        print("⚠️ 验证未完全通过：该 PACS 报告区可能非标准文本控件。")
        print("   建议退回 OCR / 剪贴板方案，并在文档中如实标注。")
    print(f"📄 证据已写入：{path}")
    return 0 if no_drift else 2


def _write_evidence(evidence: dict) -> Path:
    out_dir = ROOT / "uia_evidence"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"uia_selfcheck_{ts}.json"
    md_path = out_dir / f"uia_selfcheck_{ts}.md"
    json_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_lines = [
        f"# UIA 真机自检证据  {ts}",
        "",
        f"- 平台: {platform.system()} {platform.release()}",
        f"- UIA 可用: {evidence.get('uia_available')}",
        f"- 文本控件数: {evidence.get('control_count')}",
        f"- 读取字数: {evidence.get('captured_chars')}",
        f"- 无漂移确认: {evidence.get('no_drift_confirmed')}",
        "",
        "## 文本预览",
        "```",
        evidence.get("captured_preview") or "(空)",
        "```",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path


if __name__ == "__main__":
    sys.exit(main())
