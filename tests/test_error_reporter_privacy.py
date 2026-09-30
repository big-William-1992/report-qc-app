"""
test_error_reporter_privacy.py — 错误/信息日志的脱敏回归（2026-09-30 审计新增）

背景（真实缺陷）：两个写入口的脱敏口径不一致——
    report_exception: 只按 key 名剔除 patient / report_text 两个键
    report_info     : **完全不剔**，原样落盘
而 /api/v1/user-feedback 走的是 report_info，并把用户反馈正文写进 context。
该入口用户常粘贴报告正文 → 患者内容可能落进本地错误日志（并由管理员接口回显）。

本文件锁死修复后的口径：
1. context 只允许白名单键（未知键一律丢弃）；
2. 两个入口共用同一套擦洗（标签后内容、长数字串）；
3. 未实现的远程上报必须如实标注。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import error_reporter as er          # noqa: E402


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    """把日志目录钉到临时目录，返回读取函数。"""
    d = tmp_path / "errors"
    d.mkdir()
    monkeypatch.setattr(er, "_local_dir", lambda: str(d))

    def _read():
        rows = []
        for fn in sorted(os.listdir(d)):
            with open(d / fn, encoding="utf-8") as fh:
                rows += [json.loads(x) for x in fh if x.strip()]
        return rows

    return _read


def test_exception_context_whitelist_drops_unknown_keys(reports):
    try:
        raise ValueError("boom")
    except ValueError as exc:
        er.report_exception(exc, where="t", emp_id="1001",
                            report_text="患者男，58岁，右肺上叶见结节。",
                            meta={"patient": "张三"}, rule_id="R1-GENDER",
                            patient_name="张三")
    entry = reports()[-1]
    ctx = entry["context"]
    assert ctx.get("emp_id") == "1001"
    assert ctx.get("rule_id") == "R1-GENDER"
    for leak in ("report_text", "meta", "patient_name", "patient"):
        assert leak not in ctx, f"未知键 {leak} 未被丢弃（PHI 落盘风险）"


def test_info_applies_same_scrubbing(reports):
    """report_info 此前一个键都不过滤，现在必须与 report_exception 同口径。"""
    er.report_info("用户反馈", where="feedback.submit", emp_id="1001",
                   report_text="患者女，31岁。检查所见：低回升结节。",
                   feedback_text="编号 123456789 的报告有问题")
    entry = reports()[-1]
    ctx = entry["context"]
    assert "report_text" not in ctx, "未知键未丢弃（PHI 落盘风险）"
    txt = ctx.get("feedback_text", "")
    assert "123456789" not in txt and "数字已抹除" in txt, f"长数字未抹除：{txt!r}"


def test_label_cut_removes_everything_after_label(reports):
    """命中「患者/姓名/住院号」等标签后，其后的整段内容都要抹掉。"""
    er.report_info("用户反馈", where="feedback.submit",
                   feedback_text="患者王某某，右肺上叶见结节，建议随访")
    txt = reports()[-1]["context"]["feedback_text"]
    assert "王某某" not in txt and "右肺上叶" not in txt, f"标签后内容未抹除：{txt!r}"
    assert "已抹除" in txt


def test_label_and_digit_scrubbing_in_message(reports):
    try:
        raise RuntimeError("患者姓名：张三 身份证 110101199001011234 解析失败")
    except RuntimeError as exc:
        er.report_exception(exc, where="t")
    msg = reports()[-1]["error_msg"]
    assert "张三" not in msg and "110101199001011234" not in msg, msg


def test_message_truncated(reports):
    er.report_info("x" * 5000, where="t")
    assert len(reports()[-1]["message"]) <= 500


def test_remote_upload_is_marked_not_implemented(reports, monkeypatch):
    """QC_ERROR_REPORT_URL 曾被文档说成可用上传地址，实际未实现——必须如实标注。"""
    monkeypatch.setattr(er, "_REMOTE_URL", "https://example.invalid/collect")
    st = er.get_stats()
    assert st["remote_upload"] == "not_implemented"
    assert st["local_only"] is True

    monkeypatch.setattr(er, "_REMOTE_URL", "")
    assert er.get_stats()["remote_upload"] == "disabled"


def test_reporter_never_uploads(tmp_path, monkeypatch):
    """本模块不得发起任何网络请求（合规：数据不出院）。"""
    src = open(os.path.join(_ROOT, "src", "error_reporter.py"), encoding="utf-8").read()
    for bad in ("urlopen", "requests.", "httpx.", "socket."):
        assert bad not in src, f"error_reporter 出现网络调用 {bad}（应保持纯本地）"
