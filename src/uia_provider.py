"""
report_qc_app/src/uia_provider.py
Windows UI Automation（UIA）报告采集提供器 —— 替代「屏幕区域 OCR」，根治滚动漂移。

原理对比：
    - OCR 是「看像素」：固定像素坐标框 → 截图 → 识别。报告一滚动，框里像素就变了，
      必然错位（用户遇到的「屏幕上下拖动后识别区域偏差」）。
    - UIA 是「读控件」：Windows 给每个标准 UI 控件维护一颗自动化树，报告文本框在树里
      是一个节点，其文字存在于控件内存，**与滚动/分辨率/DPI/字号完全无关**。
      即使报告下半截在屏幕外，也能一次性读出完整文本 —— 这才是「完整报告」的正确来源。

适用：原生 Windows PACS 客户端（联影/东软/飞利浦/GE 等使用标准 Win32/WPF/.NET 控件）。
不适用：报告区是自绘 canvas / OpenGL 渲染（UIA 读不到文本），此时应退回 OCR / 剪贴板 / DICOM SR。

合规：UIA 仅读取当前已激活、处于用户焦点下的窗口文本，不抓端口、不解密、不触网，
符合医疗数据不出域原则（与 OCR 同级）。

跨平台：本模块在 macOS / Linux 仅做「可用性探测」与接口占位，绝不实际 import comtypes；
真实采集仅在 Windows 上触发。便于在任意平台做单元/集成测试（通过注入 window_text_fn / elements_fn）。
"""

from __future__ import annotations

import platform

# 仅用于检测：Windows 且可导入 comtypes / pywinauto 才视为可用。
def _uia_runtime_available() -> bool:
    if platform.system() != "Windows":
        return False
    for mod in ("comtypes", "pywinauto"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


class UIAProvider:
    """从前景（聚焦）PACS 窗口读取报告全文，或列出其文本控件用于诊断。

    window_text_fn: 测试/注入用，返回整窗报告文本；设置后 capture_text 优先用它。
    elements_fn:     测试/注入用，返回 [(控件类型, 文本), ...]；设置后诊断优先用它。
    """

    def __init__(self, window_text_fn=None, elements_fn=None):
        self._window_text_fn = window_text_fn
        self._elements_fn = elements_fn
        self._real_err = None

    # ---------------- 可用性 ----------------
    def is_available(self) -> bool:
        if self._window_text_fn is not None or self._elements_fn is not None:
            return True
        return _uia_runtime_available()

    def unavailable_reason(self) -> str:
        if self.is_available():
            return ""
        if platform.system() != "Windows":
            return "UIA 仅在 Windows 生效；当前为非 Windows 平台（macOS/Linux 无法调用 UI Automation）。"
        return self._real_err or "未检测到 comtypes / pywinauto（请 pip install comtypes 或 pywinauto）。"

    # ---------------- 主入口：读取报告全文（无漂移） ----------------
    def capture_text(self) -> str | None:
        """返回前景窗口的报告全文；无法读取时返回 None。

        返回的整段文本交由引擎（ChineseRadiologyNER / _split_for_r5）按标题切分
        为 检查所见 / 诊断印象，完全等价于手工输入或 OCR 得到的文本流。
        """
        if self._window_text_fn is not None:
            try:
                t = self._window_text_fn()
                return t.strip() if isinstance(t, str) else None
            except Exception:
                return None
        if not self.is_available():
            return None
        try:
            elems = self._uia_walk_foreground()
        except Exception as e:  # 真实 Windows 采集异常 → 安全降级
            self._real_err = f"UIA 读取失败：{e}"
            return None
        # 过滤掉过短/纯 UI 标签（按钮、菜单项等），只保留有意义的文本块
        blocks = [t.strip() for ctype, t in elems if t and t.strip() and len(t.strip()) >= 8]
        if not blocks:
            return None
        # 去重（相邻重复块，常见于分栏重复渲染）
        dedup = []
        for b in blocks:
            if not dedup or b != dedup[-1]:
                dedup.append(b)
        return "\n".join(dedup)

    # ---------------- 诊断：列出前景窗口的文本控件（供用户验证 PACS 是否支持 UIA） ----------------
    def list_text_controls(self) -> list:
        """返回 [(控件类型, 文本片段), ...]，用于「UIA 检测」按钮。

        用户可用「检测」确认自家 PACS 报告区是否为标准文本控件（能读到报告原文即可行）。
        """
        if self._elements_fn is not None:
            try:
                return list(self._elements_fn())
            except Exception:
                return []
        if not self.is_available():
            return []
        try:
            return [(ctype, (t or "").strip()) for ctype, t in self._uia_walk_foreground()]
        except Exception as e:
            self._real_err = f"UIA 诊断失败：{e}"
            return []

    def diagnose_foreground(self) -> str:
        """人类可读的诊断摘要，供 GUI 弹窗展示。"""
        if not self.is_available():
            return "UIA 不可用：" + self.unavailable_reason()
        elems = self.list_text_controls()
        if not elems:
            return ("在当前前景窗口未找到可读取的文本控件。\n"
                    "可能原因：① 焦点不在 PACS 报告窗口；② 报告区是自绘 canvas/OpenGL（UIA 读不到）。")
        lines = [f"前景窗口找到 {len(elems)} 个文本控件："]
        for ctype, t in elems[:12]:
            snippet = (t[:40] + "…") if len(t) > 40 else t
            lines.append(f"  · [{ctype}] {snippet}")
        if len(elems) > 12:
            lines.append(f"  …（其余 {len(elems) - 12} 个省略）")
        return "\n".join(lines)

    # ---------------- Windows 真实实现（懒加载，非 Windows 永不执行） ----------------
    def _uia_walk_foreground(self) -> list:
        """遍历前景窗口的文本控件（Document / Edit / Text），返回 [(类型名, 文本), ...]。"""
        import ctypes
        import comtypes
        from comtypes.gen import UIAutomationClient

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return []

        comtypes.CoInitialize()
        try:
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=UIAutomationClient.IUIAutomation)
            root = uia.ElementFromHandle(hwnd)

            doc_id = UIAutomationClient.UIA_DocumentControlTypeId
            edit_id = UIAutomationClient.UIA_EditControlTypeId
            text_id = UIAutomationClient.UIA_TextControlTypeId
            ctype_name = {doc_id: "Document", edit_id: "Edit", text_id: "Text"}

            cond = uia.CreateOrCondition(
                uia.CreateOrCondition(
                    uia.CreatePropertyCondition(UIAutomationClient.UIA_ControlTypePropertyId, doc_id),
                    uia.CreatePropertyCondition(UIAutomationClient.UIA_ControlTypePropertyId, edit_id)),
                uia.CreatePropertyCondition(UIAutomationClient.UIA_ControlTypePropertyId, text_id))

            walker = uia.CreateTreeWalker(cond)
            out = []
            el = walker.GetFirstChildElement(root)
            while el:
                ctype = ctype_name.get(el.CurrentControlType, "Control")
                txt = self._element_text(el, uia, UIAutomationClient)
                if txt:
                    out.append((ctype, txt))
                el = walker.GetNextSiblingElement(el)
            return out
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    @staticmethod
    def _element_text(el, uia, UIAutomationClient) -> str:
        """按优先级读取单个控件文本：TextPattern → ValuePattern → Name。"""
        # TextPattern（Document / Edit 的多行富文本）
        try:
            tp = el.GetCurrentPattern(UIAutomationClient.UIA_TextPatternId)
            if tp:
                t = tp.DocumentRange.GetText(-1)
                if isinstance(t, str) and t.strip():
                    return t
        except Exception:
            pass
        # ValuePattern（单行/带值的 Edit）
        try:
            vp = el.GetCurrentPattern(UIAutomationClient.UIA_ValuePatternId)
            if vp:
                t = vp.CurrentValue
                if isinstance(t, str) and t.strip():
                    return t
        except Exception:
            pass
        # Name 兜底
        try:
            n = el.CurrentName
            if isinstance(n, str) and n.strip():
                return n
        except Exception:
            pass
        return ""
