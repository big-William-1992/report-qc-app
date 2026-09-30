"""
test_r10_template_detection.py — R10 模板合规的段落判定（2026-09-30 新增）

真实缺陷（用语义评测集工具的演示时撞出来的）：
  R10 的结论段判定写成 `(?m)^\\s*(?:诊断印象|…)\\s*[:：]` —— **锚定行首**。
  于是**单行报告**（从 HIS/PACS 复制粘贴、换行被压掉）即便写着
  "……诊断印象：胸部未见明显异常。" 也被判「报告缺少『诊断印象/结论』段」。
  而"粘贴即查"正是本软件的主用法 → 这类报告会被**每条都误报一次**。

修复要点（两者都要保住）：
  ① 单行/多行都要能识别结论段（按段边界判定，而不是按行首）；
  ② 2026-08-18 的修复意图不能退回去：正文里的
     『临床初步结论』『结论尚待』等词**不能**被当成结论段（否则真缺段时漏报）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine import RuleEngine, extract_meta      # noqa: E402

_MULTI = ("患者男，45岁。\n"
          "检查所见：双肺纹理清晰，未见实质性病变，纵隔居中。\n"
          "诊断印象：胸部未见明显异常。")
_ONE_LINE = ("患者男，45岁。检查所见：双肺纹理清晰，未见实质性病变，纵隔居中。"
             "诊断印象：胸部未见明显异常。")
_NO_IMPRESSION = "患者男，45岁。检查所见：双肺纹理清晰，未见实质性病变，纵隔居中。"


def _r10_types(text):
    """返回 R10 命中的结构化 error_type 列表。

    断言用 Finding.error_type（"模板缺失-描述段"/"模板缺失-结论段"）而不是 message 子串：
    消息里写的是"缺少『诊断印象/结论』段"，用 "结论段" 去搜会**假通过**
    （本文件第一版就踩了这个坑——前两个用例其实是空断言）。
    """
    eng = RuleEngine()
    return [getattr(f, "error_type", "") for f in eng.run(text, extract_meta(text))
            if getattr(f, "rule_id", "") == "R10-TEMPLATE"]


def test_single_line_report_with_impression_is_not_flagged():
    """单行报告（换行被压掉）不应被误判为"缺少结论段"。"""
    got = _r10_types(_ONE_LINE)
    assert "模板缺失-结论段" not in got, f"单行报告误报：{got}"


def test_multi_line_report_with_impression_is_not_flagged():
    got = _r10_types(_MULTI)
    assert "模板缺失-结论段" not in got, f"多行报告误报：{got}"


def test_really_missing_impression_is_still_flagged():
    """真缺结论段时**必须**仍然报出来（别把误报修成漏报）。"""
    got = _r10_types(_NO_IMPRESSION)
    assert "模板缺失-结论段" in got, f"真缺结论段未检出：{got}"


@pytest.mark.parametrize("prose", [
    "检查所见：双肺纹理增多。临床初步结论尚未明确，需结合病史。",   # 『初步结论』非标题
    "检查所见：右肺上叶见结节。结论尚待病理证实。",                 # 『结论尚待』非标题
])
def test_prose_words_are_not_treated_as_section(prose):
    """正文里的"结论"字样不得被当成结论段（保留 2026-08-18 的修复意图）。"""
    got = _r10_types(prose)
    assert "模板缺失-结论段" in got, f"正文含『结论』二字时漏报了缺段：{got}"


@pytest.mark.parametrize("title", ["诊断印象", "影像诊断", "诊断意见", "影像结论", "结论"])
def test_common_impression_titles_are_recognized(title):
    """常见结论段标题都要认（且单行也要认）——标题集合来自 textsplit 单一数据源。"""
    text = f"患者男，45岁。检查所见：双肺纹理清晰。{title}：胸部未见明显异常。"
    got = _r10_types(text)
    assert "模板缺失-结论段" not in got, f"标题『{title}』未被识别：{got}"


def test_findings_titles_include_modal_specific_forms():
    """描述段标题应覆盖『CT所见/MRI所见/超声所见』等（共享标题集合的好处）。"""
    for title in ("检查所见", "影像描述", "CT所见", "MRI所见", "超声所见"):
        text = f"患者男，45岁。{title}：双肺纹理清晰。诊断印象：胸部未见明显异常。"
        got = _r10_types(text)
        assert "模板缺失-描述段" not in got, f"描述段标题『{title}』未被识别：{got}"
