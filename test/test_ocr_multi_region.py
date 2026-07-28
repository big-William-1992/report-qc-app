"""多区域 OCR + 识别并质控 测试。

不依赖真实屏幕 / tkinter 主循环：FakeApp 绑定 ReportQcApp 的真实
_select_ocr_region 之外的纯逻辑方法（_capture_and_qc / _compose_report /
_append_region_qc / _update_region_status），monkeypatch ocr_provider 与
engine.extract_meta。
"""
import sys, os, difflib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from unittest import mock

# 本地无 opencv/numpy 环境：ocr_provider 顶层仅依赖 cv2/numpy，且被测逻辑
# 全程 monkeypatch 其截图/OCR 接口，故用 MagicMock 占位即可（RapidOCR 为懒加载）。
for _m in ("cv2", "numpy"):
    if _m not in sys.modules:
        sys.modules[_m] = mock.MagicMock()

import app as appmod
from app import ReportQcApp, OCR_ROLE_CN


class _Status:
    def __init__(self):
        self._v = ""
    def set(self, v):
        self._v = v
    def get(self):
        return self._v


class _FakeText:
    def __init__(self):
        self.report = ""
        self.lines = []
    def delete(self, a, b):
        self.report = ""
    def insert(self, idx, text):
        if idx == "1.0":
            self.report = text
        else:
            self.lines.append(text)


class FakeApp:
    """最小替身：提供多区域 OCR 方法所需的属性与方法。"""
    def __init__(self, regions=None):
        self.ocr_regions = dict(regions or {})
        self.ocr_region = self.ocr_regions.get("basic")
        self.vars = {k: _Status() for k in
                     ["exam_no", "name", "gender", "age",
                      "modality", "applied_site", "laterality"]}
        self.ocr_regions_status = _Status()
        self.out = _FakeText()
        self.findings_txt = _FakeText()
        self.impression_txt = _FakeText()
        self._META_CN = {"name": "姓名", "exam_no": "影像号", "gender": "性别",
                         "age": "年龄", "modality": "成像方式", "applied_site": "检查部位",
                         "laterality": "侧别"}
        self.ocr_status = _Status()
        self._ran = False
        self._msg = None

    def _ocr_status(self, state, text):
        self.ocr_status.set(text)

    def _compare_ocr_clipboard(self, ocr_meta):
        # 测试中仅需存在该方法（身份交叉核对逻辑在 app 真实实现里已覆盖）
        pass

    def _run(self):
        self._ran = True

    def _similar(self, a, b):
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()


def bind(fake):
    fake._capture_and_qc = ReportQcApp._capture_and_qc.__get__(fake)
    fake._compose_report = ReportQcApp._compose_report.__get__(fake)
    fake._append_region_qc = ReportQcApp._append_region_qc.__get__(fake)
    fake._update_region_status = ReportQcApp._update_region_status.__get__(fake)
    return fake


class TestComposeReport(unittest.TestCase):
    def setUp(self):
        self.app = bind(FakeApp())

    def test_compose_includes_sections(self):
        meta = {"name": "张三", "exam_no": "CT123", "gender": "男", "age": "54", "modality": "胸部"}
        r = self.app._compose_report(meta, "肺纹理增多", "支气管炎")
        self.assertIn("【患者信息】", r)
        self.assertIn("姓名：张三", r)
        self.assertIn("影像号：CT123", r)
        self.assertIn("检查所见：", r)
        self.assertIn("肺纹理增多", r)
        self.assertIn("诊断印象：", r)
        self.assertIn("支气管炎", r)

    def test_compose_no_meta_header(self):
        r = self.app._compose_report({}, "描述内容", "结论内容")
        self.assertNotIn("【患者信息】", r)
        self.assertIn("检查所见：", r)
        self.assertIn("诊断印象：", r)


class TestUpdateRegionStatus(unittest.TestCase):
    def test_all_set(self):
        app = bind(FakeApp({"basic": (1, 1, 10, 10),
                             "findings": (2, 2, 20, 20),
                             "impression": (3, 3, 30, 30)}))
        app._update_region_status()
        self.assertIn("基础信息已设", app.ocr_regions_status.get())
        self.assertIn("影像描述已设", app.ocr_regions_status.get())
        self.assertIn("影像结论已设", app.ocr_regions_status.get())
        self.assertEqual(app.ocr_region, (1, 1, 10, 10))

    def test_partial_set(self):
        app = bind(FakeApp({"basic": (1, 1, 10, 10)}))
        app._update_region_status()
        self.assertIn("基础信息已设", app.ocr_regions_status.get())
        self.assertIn("影像描述未设", app.ocr_regions_status.get())
        self.assertIn("影像结论未设", app.ocr_regions_status.get())


class TestCaptureAndQc(unittest.TestCase):
    def setUp(self):
        self.regions = {"basic": (1, 1, 10, 10),
                        "findings": (2, 2, 20, 20),
                        "impression": (3, 3, 30, 30)}
        self.app = bind(FakeApp(self.regions))
        # 每个区域截图返回其 rect 作为 tag，OCR 按 tag 返回不同文本
        self.ocr_map = {
            (1, 1, 10, 10): "张三 男 54岁 胸部",
            (2, 2, 20, 20): "双肺纹理增多，未见实质性病灶",
            (3, 3, 30, 30): "支气管炎",
        }
        self.patcher_cap = mock.patch.object(
            appmod.ocr_provider, "capture_region", side_effect=lambda r: r)
        self.patcher_ocr = mock.patch.object(
            appmod.ocr_provider, "ocr_image",
            side_effect=lambda img: self.ocr_map.get(img, ""))
        self.patcher_avail = mock.patch.object(
            appmod.ocr_provider, "availability", return_value=(True, ""))
        self.patcher_meta = mock.patch.object(
            appmod.engine, "extract_meta",
            side_effect=lambda t: {"patient": "张三", "gender": "男",
                                   "age": "54", "modality": "胸部"} if "张三" in t else {})
        self.patcher_msg = mock.patch.object(appmod.messagebox, "showinfo")
        self.patcher_warn = mock.patch.object(appmod.messagebox, "showwarning")
        self.patcher_err = mock.patch.object(appmod.messagebox, "showerror")
        for p in (self.patcher_cap, self.patcher_ocr, self.patcher_avail,
                  self.patcher_meta, self.patcher_msg, self.patcher_warn, self.patcher_err):
            p.start()
        self.addCleanup(mock.patch.stopall)

    def test_capture_fills_meta_and_report(self):
        self.app._capture_and_qc()
        self.assertTrue(self.app._ran)
        self.assertEqual(self.app.vars["name"].get(), "张三")
        self.assertEqual(self.app.vars["modality"].get(), "胸部")
        self.assertIn("双肺纹理增多", self.app.findings_txt.report)
        self.assertIn("支气管炎", self.app.impression_txt.report)
        # 分区域状态写入结果区
        self.assertIn("分区域识别状态", "".join(self.app.out.lines))

    def test_impression_copied_from_findings_flagged(self):
        # 描述与结论雷同 → 追加照抄警告
        self.ocr_map[(2, 2, 20, 20)] = "双肺未见异常"
        self.ocr_map[(3, 3, 30, 30)] = "双肺未见异常"
        self.app._capture_and_qc()
        self.assertIn("照抄", "".join(self.app.out.lines))

    def test_missing_region_guards(self):
        # 缺 impression 区 → 不应运行质控，应弹提示
        self.app.ocr_regions.pop("impression")
        self.app._capture_and_qc()
        self.assertFalse(self.app._ran)
        appmod.messagebox.showinfo.assert_called()


if __name__ == "__main__":
    unittest.main()
