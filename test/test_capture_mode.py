"""采集方式（capture_mode）选择与 _dispatch_qc 分派逻辑的测试。

仅测纯逻辑（不创建 Tk 窗口、不弹 messagebox）：通过 __new__ 跳过 ReportQcApp.__init__，
用 mock 替换采集方法与模块级 messagebox / ocr_provider。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import app as app_module
from unittest import mock


def _make_app():
    """构造一个不初始化 Tk 的 ReportQcApp 空壳，仅设 _dispatch_qc 依赖的属性。"""
    a = app_module.ReportQcApp.__new__(app_module.ReportQcApp)
    a.uia = mock.Mock()
    a.uia.is_available.return_value = True
    a.ocr_regions = {}
    a.capture_mode = "auto"
    a.ocr_cfg = {}
    a.capture_mode_var = mock.Mock()  # 供 _on_capture_mode_change 使用（本测试不触发）
    return a


def test_norm_capture_mode_legal():
    f = app_module.ReportQcApp._norm_capture_mode
    assert f("auto") == "auto"
    assert f("uia") == "uia"
    assert f("ocr") == "ocr"
    assert f("ask") == "ask"


def test_norm_capture_mode_illegal_falls_back_auto():
    f = app_module.ReportQcApp._norm_capture_mode
    assert f("BOGUS") == "auto"
    assert f(None) == "auto"
    assert f(123) == "auto"
    assert f("") == "auto"


def test_dispatch_uia_mode():
    a = _make_app()
    a.capture_mode = "uia"
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr, \
         mock.patch.object(a, "_ask_capture_mode") as m_ask:
        a._dispatch_qc()
        m_uia.assert_called_once()
        m_ocr.assert_not_called()
        m_ask.assert_not_called()


def test_dispatch_ocr_mode_with_regions():
    a = _make_app()
    a.capture_mode = "ocr"
    a.ocr_regions = {"basic": (0, 0, 1, 1)}
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr, \
         mock.patch.object(app_module, "ocr_provider") as mp, \
         mock.patch.object(app_module, "messagebox"):
        mp.availability.return_value = (True, "ok")
        a._dispatch_qc()
        m_ocr.assert_called_once()
        m_uia.assert_not_called()


def test_dispatch_ocr_mode_without_regions_shows_info():
    a = _make_app()
    a.capture_mode = "ocr"
    a.ocr_regions = {}
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr, \
         mock.patch.object(app_module, "messagebox") as mb:
        a._dispatch_qc()
        m_ocr.assert_not_called()
        m_uia.assert_not_called()
        mb.showinfo.assert_called_once()


def test_dispatch_auto_uia_available():
    a = _make_app()
    a.capture_mode = "auto"
    a.uia.is_available.return_value = True
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr:
        a._dispatch_qc()
        m_uia.assert_called_once()
        m_ocr.assert_not_called()


def test_dispatch_auto_fallback_ocr():
    a = _make_app()
    a.capture_mode = "auto"
    a.uia.is_available.return_value = False
    a.ocr_regions = {"basic": (0, 0, 1, 1)}
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr, \
         mock.patch.object(app_module, "ocr_provider") as mp, \
         mock.patch.object(app_module, "messagebox"):
        mp.availability.return_value = (True, "ok")
        a._dispatch_qc()
        m_ocr.assert_called_once()
        m_uia.assert_not_called()


def test_dispatch_auto_no_region_shows_info():
    a = _make_app()
    a.capture_mode = "auto"
    a.uia.is_available.return_value = False
    a.ocr_regions = {}
    with mock.patch.object(a, "_capture_via_uia") as m_uia, \
         mock.patch.object(a, "_capture_and_qc") as m_ocr, \
         mock.patch.object(app_module, "messagebox") as mb:
        a._dispatch_qc()
        m_ocr.assert_not_called()
        m_uia.assert_not_called()
        mb.showinfo.assert_called_once()


def test_dispatch_ask_opens_menu():
    a = _make_app()
    a.capture_mode = "ask"
    with mock.patch.object(a, "_ask_capture_mode") as m_ask:
        a._dispatch_qc()
        m_ask.assert_called_once()
