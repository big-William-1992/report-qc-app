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
        self._ocr_focus_paused = False
        self._ocr_light_state = None   # 记录状态灯语义色（off/monitoring/ok/empty/alert/error）
        self.ocr_dot = object()
        self._ocr_dot_id = 1

    def _ocr_status(self, state, text):
        self._ocr_light_state = state
        self.ocr_status.set(text)

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
    fake._on_ocr_activate = ReportQcApp._on_ocr_activate.__get__(fake)
    fake._on_ocr_deactivate = ReportQcApp._on_ocr_deactivate.__get__(fake)
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

    def test_focus_paused_skips_ocr(self):
        # 本软件聚焦（用户正在操作）→ _on_ocr_activate 暂停并灯转灰；
        # 之后轮询跳过截图，不跑 OCR，但维持调度
        self._patch_sig((1, 2, 3), True)
        self.f._on_ocr_activate()
        self.f._poll_ocr(force=False)
        self.assertEqual(len(self.f._applied), 0, "聚焦暂停时不应跑 OCR")
        self.assertIsNotNone(self.f._ocr_job, "聚焦暂停时仍应维持调度")
        self.assertEqual(self.f._ocr_light_state, "off", "聚焦暂停状态灯应为灰色 off")

    def test_deactivate_resumes_ocr(self):
        # 本软件失去前台（用户切去 PACS）→ _on_ocr_deactivate 恢复并立即 force 一次
        self._patch_sig((1, 2, 3), True)
        self.f._ocr_focus_paused = True
        self.f._on_ocr_deactivate()
        self.assertEqual(self.f._ocr_focus_paused, False, "失焦应解除暂停")
        self.assertIn(self.f._ocr_light_state, ("monitoring", "ok"),
                      "恢复监控灯应转青(monitoring)或识别成功转绿(ok)")
        self.assertEqual(len(self.f._applied), 1, "恢复时应立即 force 跑一次 OCR")

    def test_light_ok_when_recognized(self):
        # 画面变化且识别出患者信息 → 状态灯转绿 ok
        self._patch_sig((1, 2, 3), True)
        self.f._ocr_last_force_ts = _time.time()
        self.f._poll_ocr(force=False)
        self.assertEqual(self.f._ocr_light_state, "ok", "识别成功状态灯应为绿色 ok")

    def test_light_empty_when_no_patient(self):
        # 识别到文字但解析不出患者字段（框错区域）→ 状态灯转黄 empty
        self._patch_sig((1, 2, 3), True)
        self.f._ocr_last_force_ts = _time.time()
        with mock.patch.object(appmod.ocr_provider, "ocr_image",
                                lambda img: "PACS 工作站 工具栏 放大"):
            self.f._poll_ocr(force=False)
        self.assertEqual(self.f._ocr_light_state, "empty", "无患者信息状态灯应为黄色 empty")
        self.assertEqual(len(self.f._applied), 0, "空区域不应回填")


if __name__ == "__main__":
    unittest.main(verbosity=2)
