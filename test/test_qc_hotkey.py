"""「识别并质控」快捷键 测试。

覆盖：keysym→VK 映射、显示名、Tk 绑定序列、应用/清除快捷键的持久化与状态、
触发防抖。不依赖真实屏幕 / tkinter 主循环（FakeApp + 绑定真实方法）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from unittest import mock

for _m in ("cv2", "numpy"):
    if _m not in sys.modules:
        sys.modules[_m] = mock.MagicMock()

import app as appmod
from app import (ReportQcApp, hotkey_vk, hotkey_display, hotkey_tk_sequence,
                 HOTKEY_MOD_FLAGS)


class _Status:
    def __init__(self, v=""):
        self._v = v
    def set(self, v):
        self._v = v
    def get(self):
        return self._v


class FakeApp:
    """最小替身：提供快捷键方法所需属性；bind_all/unbind_all/after 记录调用。"""
    def __init__(self, hotkey=None):
        self.qc_hotkey = hotkey
        self.qc_hotkey_status = _Status()
        self._hotkey_thread_id = None
        self._hotkey_tk_seq = None
        self._hotkey_busy = False
        self._pynput_listener = None
        self._last_hotkey_ts = 0.0
        self._pynput_started = None
        self.saved_cfg = None
        self.bound = {}
        self.qc_calls = 0

    def _save_ocr_config(self):
        self.saved_cfg = {"hotkey": self.qc_hotkey}

    def bind_all(self, seq, cb):
        self.bound[seq] = cb

    def unbind_all(self, seq):
        self.bound.pop(seq, None)

    def after(self, ms, cb=None):
        if cb:
            cb()

    def _capture_and_qc(self):
        self.qc_calls += 1

    def _start_pynput_listener(self, label):
        # 测试替身：不真正启动 OS 级全局监听（需辅助功能权限/会起后台线程），
        # 仅记录被调用并刷新状态，使快捷键注册/清除的绑定与持久化断言可独立验证。
        self._pynput_started = label
        self.qc_hotkey_status.set(label + "（后台全局监听已启用）")


def bind(fake):
    for name in ("_apply_qc_hotkey", "_register_qc_hotkey",
                 "_unregister_qc_hotkey", "_on_qc_hotkey"):
        setattr(fake, name, getattr(ReportQcApp, name).__get__(fake))
    return fake


class TestHotkeyVk(unittest.TestCase):
    def test_letters_digits(self):
        self.assertEqual(hotkey_vk("q"), ord("Q"))
        self.assertEqual(hotkey_vk("Q"), ord("Q"))
        self.assertEqual(hotkey_vk("5"), ord("5"))

    def test_function_keys(self):
        self.assertEqual(hotkey_vk("F1"), 0x70)
        self.assertEqual(hotkey_vk("F9"), 0x78)
        self.assertEqual(hotkey_vk("F12"), 0x7B)

    def test_special_and_keypad(self):
        self.assertEqual(hotkey_vk("space"), 0x20)
        self.assertEqual(hotkey_vk("Home"), 0x24)
        self.assertEqual(hotkey_vk("KP_5"), 0x65)

    def test_unsupported_returns_none(self):
        self.assertIsNone(hotkey_vk("Muhenkan"))
        self.assertIsNone(hotkey_vk(""))
        self.assertIsNone(hotkey_vk(None))


class TestHotkeyDisplayAndSeq(unittest.TestCase):
    def test_display(self):
        self.assertEqual(hotkey_display(None), "未设置")
        self.assertEqual(hotkey_display({"mods": [], "key": "F9"}), "F9")
        self.assertEqual(
            hotkey_display({"mods": ["alt", "ctrl"], "key": "q"}), "Ctrl+Alt+Q")

    def test_tk_sequence(self):
        self.assertIsNone(hotkey_tk_sequence(None))
        self.assertEqual(hotkey_tk_sequence({"mods": [], "key": "F9"}), "<F9>")
        self.assertEqual(
            hotkey_tk_sequence({"mods": ["ctrl", "alt"], "key": "q"}),
            "<Control-Alt-q>")
        # 带 shift 的字母用大写形式
        self.assertEqual(
            hotkey_tk_sequence({"mods": ["ctrl", "shift"], "key": "q"}),
            "<Control-Shift-Q>")

    def test_mod_flags(self):
        self.assertEqual(HOTKEY_MOD_FLAGS["ctrl"], 0x0002)
        self.assertEqual(HOTKEY_MOD_FLAGS["alt"], 0x0001)


class TestApplyHotkey(unittest.TestCase):
    def test_apply_sets_persists_and_binds(self):
        app = bind(FakeApp())
        hk = {"mods": ["ctrl", "alt"], "key": "q"}
        app._apply_qc_hotkey(hk)
        self.assertEqual(app.qc_hotkey, hk)
        self.assertEqual(app.saved_cfg, {"hotkey": hk})           # 已持久化
        self.assertIn("<Control-Alt-q>", app.bound)               # 应用内绑定
        self.assertIn("Ctrl+Alt+Q", app.qc_hotkey_status.get())   # 状态文本

    def test_clear_hotkey(self):
        app = bind(FakeApp({"mods": [], "key": "F9"}))
        app._hotkey_tk_seq = "<F9>"
        app.bound["<F9>"] = lambda e: None
        app._apply_qc_hotkey(None)
        self.assertIsNone(app.qc_hotkey)
        self.assertEqual(app.saved_cfg, {"hotkey": None})
        self.assertNotIn("<F9>", app.bound)                       # 已解绑
        self.assertEqual(app.qc_hotkey_status.get(), "快捷键：未设置")

    def test_reapply_unbinds_old(self):
        app = bind(FakeApp())
        app._apply_qc_hotkey({"mods": [], "key": "F9"})
        self.assertIn("<F9>", app.bound)
        app._apply_qc_hotkey({"mods": ["ctrl"], "key": "g"})
        self.assertNotIn("<F9>", app.bound)                       # 旧的解绑
        self.assertIn("<Control-g>", app.bound)


class TestHotkeyTrigger(unittest.TestCase):
    def test_trigger_runs_capture(self):
        app = bind(FakeApp())
        app._on_qc_hotkey()
        self.assertEqual(app.qc_calls, 1)
        self.assertFalse(app._hotkey_busy)                        # 执行完复位

    def test_debounce_when_busy(self):
        app = bind(FakeApp())
        app._hotkey_busy = True
        app._on_qc_hotkey()
        self.assertEqual(app.qc_calls, 0)                         # 忙时忽略

    def test_busy_reset_on_exception(self):
        app = bind(FakeApp())
        def boom():
            raise RuntimeError("x")
        app._capture_and_qc = boom
        with self.assertRaises(RuntimeError):
            app._on_qc_hotkey()
        self.assertFalse(app._hotkey_busy)                        # 异常也复位


if __name__ == "__main__":
    unittest.main()
