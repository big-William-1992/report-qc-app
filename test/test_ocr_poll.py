"""OCR 监控调度逻辑测试：变化驱动 + 60s 兜底。

不依赖真实屏幕/ tkinter 主循环：用 FakeApp 绑定 ReportQcApp 的真实
_poll_ocr / _do_ocr_once 方法，monkeypatch ocr_provider 的截图/OCR/指纹接口。
"""
import sys, os, time as _time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from unittest import mock
from PIL import Image

import app as appmod
from app import ReportQcApp

FIXED_IMG = Image.new("RGB", (100, 40), (255, 255, 255))


class _Status:
    def __init__(self):
        self._v = ""
    def set(self, v):
        self._v = v
    def get(self):
        return self._v


class FakeApp:
    """最小替身：仅提供 _poll_ocr/_do_ocr_once 需要的属性与方法。"""
    def __init__(self):
        self.ocr_watch = True
        self.ocr_region = (0, 0, 100, 40)
        self._ocr_img_sig = None
        self._ocr_confirmed_key = None
        self._ocr_last_force_ts = 0.0
        self.ocr_meta = {}
        self._META_CN = {"patient": "姓名", "gender": "性别", "age": "年龄",
                         "modality": "影像", "applied_site": "检查部位",
                         "laterality": "侧别"}
        self.ocr_status = _Status()
        self._applied = []
        self._compared = []
        self._ocr_job = None

    def _apply_ocr_meta(self, meta, key):
        self._applied.append((meta, key))

    def _compare_ocr_clipboard(self, meta):
        self._compared.append(meta)

    def after(self, ms, cb):
        self._ocr_job = (ms, cb)
        return 1


def bind(fake):
    fake._poll_ocr = ReportQcApp._poll_ocr.__get__(fake)
    fake._do_ocr_once = ReportQcApp._do_ocr_once.__get__(fake)
    return fake


class PollScheduleTest(unittest.TestCase):
    def setUp(self):
        self.f = bind(FakeApp())
        self.patchers = []
        for target, fn in [
            ("availability", lambda: (True, "")),
            ("capture_region", lambda r: FIXED_IMG),
            ("ocr_image", lambda img: "姓名：张三\n性别：男\n年龄：40\n检查部位：头部CT"),
        ]:
            p = mock.patch.object(appmod.ocr_provider, target, fn)
            p.start()
            self.patchers.append(p)

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def _patch_sig(self, sig_val, changed):
        ps = mock.patch.object(appmod.ocr_provider, "image_signature", lambda i: sig_val)
        pc = mock.patch.object(appmod.ocr_provider, "signature_changed", lambda a, b: changed)
        ps.start(); pc.start()
        self.patchers += [ps, pc]

    def test_changed_runs_ocr(self):
        # 画面变化 → 立即跑 OCR 并回填
        self._patch_sig((1, 2, 3), True)
        self.f._ocr_last_force_ts = _time.time()
        self.f._poll_ocr(force=False)
        self.assertEqual(len(self.f._applied), 1, "变化时应跑 OCR 并回填")
        self.assertEqual(len(self.f._compared), 0)

    def test_unchanged_no_fallback_no_ocr(self):
        # 屏幕未变且未到 60s 兜底 → 不跑 OCR，仅做剪贴板核对
        self._patch_sig((1, 2, 3), False)
        self.f._ocr_last_force_ts = _time.time()  # 刚跑过
        self.f.ocr_meta = {"patient": "张三"}      # 模拟首轮已识别建立 meta
        self.f._poll_ocr(force=False)
        self.assertEqual(len(self.f._applied), 0, "未变化且未到兜底时不应跑 OCR")
        self.assertEqual(len(self.f._compared), 1, "应做剪贴板交叉核对")

    def test_unchanged_fallback_runs_ocr(self):
        # 屏幕未变但已超过 60s → 兜底强制跑 OCR
        self._patch_sig((1, 2, 3), False)
        self.f._ocr_last_force_ts = 0.0  # 很久没跑，远超 60s
        self.f._poll_ocr(force=False)
        self.assertEqual(len(self.f._applied), 1, "超 60s 兜底应强制跑 OCR")

    def test_force_runs_ocr(self):
        # 手动 force → 即使未变化也跑
        self._patch_sig((1, 2, 3), False)
        self.f._ocr_last_force_ts = _time.time()
        self.f._poll_ocr(force=True)
        self.assertEqual(len(self.f._applied), 1, "force 应跑 OCR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
