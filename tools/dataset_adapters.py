"""英文放射报告错误集 → 本项目 SFT 训练格式（alpaca）适配器。

把公开英文数据集（CorBenchX / ReXErr / llm4proofreading）的标注，
映射成本项目统一的错误分类（R*/L1 taxonomy）与训练样本结构，
作为「方法种子」参与中文训练集构造（详见 docs/放射科报告质控公开数据集调研与中文迁移方案.md）。

每条产出与 llm_prompt / llm_qc / train/dataset_info.json 完全对齐：
  instruction : 质控专家指令
  input       : 含错误的报告文本
  output      : JSON 数组字符串（error_type/location/severity/confidence/rationale）
"""
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

INSTRUCTION = ("你是资深放射科质控专家，审查下面的放射诊断报告，"
               "找出语义/语境类错误（含规则覆盖的硬错误），"
               "仅输出一个 JSON 数组，每条含 error_type/location/severity/confidence/rationale；"
               "无问题输出 []。")

# 英文数据集错误类型 → 本项目 taxonomy
ERR_MAP = {
    # CorBenchX
    "omission": "R15-PRESENCE", "insertion": "R12-SENTENCE",
    "spelling error": "R8-TYPO", "side confusion": "R2-LATERALITY", "other": "L1-OTHER",
    # llm4proofreading
    "negation": "R5-CONSISTENCY", "left/right": "R2-LATERALITY",
    # interval change（间期对比变化）：R13 已废弃为预留编号、引擎无对应规则，
    # R16-FOLLOWUP 仅覆盖随访建议时限缺失，语义不符，故归入 L1-OTHER。
    "interval change": "L1-OTHER", "transcription": "R8-TYPO",
}


def map_err(t: str) -> str:
    return ERR_MAP.get((t or "").strip().lower(), "L1-OTHER")


def _rec(inp: str, out_list: list) -> dict:
    return {"instruction": INSTRUCTION, "input": inp,
            "output": json.dumps(out_list, ensure_ascii=False)}


def _quote(txt: str):
    m = re.search(r'[""]([^""]+)[""]', txt or "")
    return m.group(1) if m else ""


def corbenchx_records(items) -> list:
    """CorBenchX：items 为 train.json 的 list（input_report/output_report/error_type/error_description）。"""
    recs = []
    for it in items:
        inp = it.get("input_report") or it.get("output_report", "")
        if not inp:
            continue
        out = [{
            "error_type": map_err(it.get("error_type")),
            "location": _quote(it.get("error_description", "")),
            "severity": "medium", "confidence": 1.0,
            "rationale": it.get("error_description", ""),
        }]
        recs.append(_rec(inp, out))
    return recs


def _parse_json_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else [v]
        except Exception:
            return []
    return []


def rexerr_records(rows) -> list:
    """ReXErr：rows 为 report-level CSV 的 dict 列表（error_report / errors_sampled）。"""
    recs = []
    for r in rows:
        err_report = r.get("error_report") or ""
        if not err_report:
            continue
        errs = _parse_json_list(r.get("errors_sampled"))
        outs = []
        for e in errs:
            if isinstance(e, dict):
                t = e.get("error_type") or e.get("error") or ""
                desc = e.get("error_description") or json.dumps(e, ensure_ascii=False)
            else:
                t, desc = str(e), str(e)
            outs.append({
                "error_type": map_err(t), "location": "",
                "severity": "medium", "confidence": 1.0, "rationale": desc,
            })
        if not outs:
            outs = [{"error_type": "L1-OTHER", "location": "", "severity": "low",
                     "confidence": 1.0, "rationale": "GPT-4o 注入错误"}]
        recs.append(_rec(err_report, outs))
    return recs


def llm4proofreading_records(items) -> list:
    """llm4proofreading：items 为含 report / error_type（negation/left-right/interval change/transcription）的记录。"""
    recs = []
    for it in items:
        inp = it.get("report") or it.get("error_report") or ""
        if not inp:
            continue
        t = it.get("error_type", "")
        out = [{
            "error_type": map_err(t),
            "location": it.get("error_text") or it.get("location") or "",
            "severity": "medium", "confidence": 1.0,
            "rationale": f"{t} 类错误",
        }]
        recs.append(_rec(inp, out))
    return recs


def load_any(path: str) -> list:
    """按文件名/扩展名自动分发到对应适配器，返回 alpaca 记录列表。"""
    name = os.path.basename(path).lower()
    if name.endswith(".csv"):
        with open(path, encoding="utf-8", errors="ignore") as fh:
            rows = list(csv.DictReader(fh))
        if "rexerr" in name:
            return rexerr_records(rows)
        if "corbench" in name:  # CSV 形态的 CorBenchX（2026-08-23 修复：原为 if False 死代码）
            return corbenchx_records(rows)
        return []
    # JSON（CorBenchX / llm4proofreading 多为 json）
    with open(path, encoding="utf-8", errors="ignore") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("data") or data.get("records") or []
    if "corbench" in name:
        return corbenchx_records(data)
    if "proofread" in name or "llm4" in name:
        return llm4proofreading_records(data)
    # 兜底：尝试按字段猜测
    if data and isinstance(data[0], dict) and "input_report" in data[0]:
        return corbenchx_records(data)
    if data and isinstance(data[0], dict) and "error_report" in data[0]:
        return rexerr_records(data)
    return llm4proofreading_records(data)
