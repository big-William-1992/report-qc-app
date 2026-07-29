"""
test/test_uia_provider.py
Windows UI Automation 采集提供器（uia_provider）合成回归与单元测试。

说明：
- 这些测试均不依赖真实 Windows / comtypes：通过注入 window_text_fn / elements_fn 模拟
  前景 PACS 窗口的文本控件，验证 UIA 采集的「无滚动漂移」核心语义与 QC 数据流。
- 真实 Windows 采集代码路径（comtypes UIAutomation）仅在 Windows 上执行，此处不覆盖，
  但会被 uia_provider 的可用性探测与接口签名保护。

合规边界：
  模拟文本均为合成样例，不含任何真实患者数据。
"""

import os
import sys

# 将 src/ 加入导入路径（与既有测试一致）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import uia_provider
from uia_provider import UIAProvider
from engine import RuleEngine


SAMPLE_REPORT = """患者信息：张三  男  56岁  检查部位：胸部正侧位
检查所见：左肺下叶见斑片状模糊影，边界不清。
诊断印象：右肺下叶炎症，建议抗炎后复查。"""


def _has_r14_side(findings):
    return any(f.rule_id == "R14-SIDE" for f in findings)


# ---------------- 可用性与降级 ----------------
def test_not_available_on_non_windows(monkeypatch):
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider()
    assert p.is_available() is False
    assert "Windows" in p.unavailable_reason()


def test_available_with_injected_fn_on_any_platform(monkeypatch):
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider(window_text_fn=lambda: SAMPLE_REPORT)
    assert p.is_available() is True
    assert p.unavailable_reason() == ""


def test_capture_text_none_when_fn_returns_none(monkeypatch):
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider(window_text_fn=lambda: None)
    assert p.capture_text() is None


# ---------------- 核心：无滚动漂移（一次读出完整报告） ----------------
def test_capture_text_returns_full_report_no_drift(monkeypatch):
    """模拟『屏幕只显示上半截、但 UIA 读控件内存』——capture_text 必须返回整份报告，
    不会像 OCR 那样因滚动而只截到局部。"""
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    long_report = SAMPLE_REPORT + "\n（以下为屏幕外下半截）双侧胸腔积液，心影增大。"
    p = UIAProvider(window_text_fn=lambda: long_report)
    out = p.capture_text()
    assert out is not None
    # 完整报告（含屏幕外部分）都被读出
    assert "左肺下叶" in out and "右肺下叶炎症" in out
    assert "双侧胸腔积液" in out and "心影增大" in out


def test_capture_text_strips_whitespace(monkeypatch):
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider(window_text_fn=lambda: "  \n  " + SAMPLE_REPORT + "  \n ")
    assert p.capture_text() == SAMPLE_REPORT


# ---------------- 诊断：列出文本控件 ----------------
def test_list_text_controls_diagnostic(monkeypatch):
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    elems = [("Document", "左肺下叶见斑片状模糊影"), ("Edit", "右肺下叶炎症"), ("Button", "保存")]
    p = UIAProvider(elements_fn=lambda: elems)
    got = p.list_text_controls()
    assert ("Document", "左肺下叶见斑片状模糊影") in got
    diag = p.diagnose_foreground()
    assert "2 个文本控件" in diag or "文本控件" in diag


# ---------------- 端到端：UIA 文本流经 QC 引擎 ----------------
def test_uia_text_flows_through_qc_r14_conflict(monkeypatch):
    """UIA 读出的整段报告，经引擎分段后，左右矛盾（R14-SIDE）应被检出——
    与手工输入 / OCR 文本走完全相同的 QC 路径。"""
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider(window_text_fn=lambda: SAMPLE_REPORT)
    text = p.capture_text()
    assert text == SAMPLE_REPORT
    findings = RuleEngine().run(text, {})
    assert _has_r14_side(findings), "UIA 文本未触发预期的 R14-SIDE 左右矛盾"


def test_uia_split_into_findings_impression(monkeypatch):
    """复刻 GUI _capture_via_uia 的分段逻辑：UIA 整段文本应正确切分为 检查所见/诊断印象。"""
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    p = UIAProvider(window_text_fn=lambda: SAMPLE_REPORT)
    text = p.capture_text()
    secs = RuleEngine._split_for_r5(text)
    assert "左肺下叶见斑片状" in secs["findings"]
    assert "右肺下叶炎症" in secs["impression"]


def test_uia_normal_report_no_false_positive(monkeypatch):
    """正常同侧报告不应误报左右矛盾。"""
    monkeypatch.setattr(uia_provider.platform, "system", lambda: "Darwin")
    normal = """患者信息：李四  女  48岁
检查所见：右肺上叶见一小结节。
诊断印象：右肺上叶结节，建议随访。"""
    p = UIAProvider(window_text_fn=lambda: normal)
    text = p.capture_text()
    findings = RuleEngine().run(text, {})
    assert not _has_r14_side(findings)
