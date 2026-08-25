"""样本导出模块 (2026-08-25 自 samplelib.py 拆出): CSV/DOCX/PDF 导出。

依赖 samplelib 的公共接口 db_path / list_samples_full (函数内延迟导入避免循环)。
"""
import os
import re
import json
import zipfile
import csv
import datetime

FIELDS = ["id", "ts", "patient", "gender", "age", "modality",
          "applied_site", "laterality", "user_id",
          "report_text", "findings_json", "scores_json"]


def _sdb():  # 延迟导入规避循环(双路径)
    try:
        from .samplelib import db_path, list_samples_full
    except ImportError:
        from samplelib import db_path, list_samples_full
    return db_path, list_samples_full


def export_samples(path: str = None, out_path: str = None, fmt: str = "csv",
                   user_id: str = None, anonymize: bool = False) -> str:
    _dbp, _lsf = _sdb()
    """导出样本库为 CSV（Excel 友好，utf-8-sig 带 BOM）/ JSON / DOCX / PDF。

    path      : 源库路径，默认 _dbp()
    out_path  : 输出文件，默认在源库同目录生成 samples_export_<时间戳>.<ext>
    fmt       : 'csv' | 'json' | 'docx' | 'pdf'
    user_id   : 非空时仅导出该责任人的样本（多用户隔离，2026-08-18）。
    anonymize : True 时剥离患者姓名/性别/年龄（医疗数据合规，2026-08-18）。
    返回输出文件路径。DOCX 用纯标准库生成（OOXML）；PDF 需要 reportlab（可选依赖，
    缺失时抛 RuntimeError 并给出安装提示）。
    """
    rows = _lsf(path, user_id=user_id)
    if anonymize:
        for r in rows:
            r["patient"] = "已脱敏"
            r["gender"] = ""
            r["age"] = ""
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.path.dirname(path or _dbp()),
                                f"samples_export_{stamp}.{fmt}")
    if fmt == "json":
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
    elif fmt == "docx":
        _write_docx(rows, out_path)
    elif fmt == "pdf":
        _write_pdf(rows, out_path)
    else:
        with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    return out_path


# ---------------------------------------------------------------------------
# 质控报告单导出（PDF / Word）：标题、检查部位、原报告、质控发现、建议修正
# 供科室留档、质控会议汇报、发给医生整改。
# ---------------------------------------------------------------------------
def export_report_docx(sample: dict, out_path: str = None) -> str:
    _dbp, _lsf = _sdb()
    """把单份样本导出为质控报告单 DOCX（Word）。

    sample : get_sample(sid) 返回的行（含 report_text / findings_json / scores_json）
    返回输出文件路径。
    """
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        sid = sample.get("id", 0)
        out_path = os.path.join(os.path.dirname(_dbp()),
                                f"质控报告_{sid}_{stamp}.docx")
    _write_docx([sample], out_path, single=True)
    return out_path


def export_report_pdf(sample: dict, out_path: str = None) -> str:
    _dbp, _lsf = _sdb()
    """把单份样本导出为质控报告单 PDF（需 reportlab）。"""
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        sid = sample.get("id", 0)
        out_path = os.path.join(os.path.dirname(_dbp()),
                                f"质控报告_{sid}_{stamp}.pdf")
    _write_pdf([sample], out_path, single=True)
    return out_path


def export_qc_report(report_text: str, meta: dict, findings: list,
                     scores: dict, fmt: str = "docx") -> str:
    _dbp, _lsf = _sdb()
    """把「一次质控结果」直接导出为质控报告单（无需入库）。

    findings : [Finding.__dict__] 或 [dict]，序列化后写入 findings_json。
    返回输出文件路径。
    """
    sample = {
        "id": "QC", "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "patient": (meta or {}).get("patient", ""),
        "gender": (meta or {}).get("gender", ""),
        "age": (meta or {}).get("age", ""),
        "modality": (meta or {}).get("modality", ""),
        "applied_site": (meta or {}).get("applied_site", ""),
        "laterality": (meta or {}).get("laterality", ""),
        "user_id": (meta or {}).get("user_id", ""),
        "report_text": report_text or "",
        "findings_json": json.dumps(
            [f.__dict__ if hasattr(f, "__dict__") else f for f in (findings or [])],
            ensure_ascii=False),
        "scores_json": json.dumps(scores or {}, ensure_ascii=False),
    }
    if fmt == "pdf":
        return export_report_pdf(sample)
    return export_report_docx(sample)


def _scores_of(r: dict) -> dict:
    """解析样本行 scores_json 为 dict。

    2026-08-18 修复：CSV 导入且无 scores 列时 _import_rows 默认写 "[]"，
    json.loads 得 list，下游 scores.items() 抛 AttributeError（导出报告单 500）。
    """
    raw = r.get("scores_json") or "{}"
    try:
        v = json.loads(raw)
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}


_XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_escape(s: str) -> str:
    if s is None:
        return ""
    # 剥离 XML 1.0 非法控制字符（\x00-\x08\x0b\x0c\x0e-\x1f）：
    # 报告文本含这些字符时直接写入 document.xml 会导致 Word 报"文档损坏"（2026-08-18）
    return (_XML_ILLEGAL_RE.sub("", str(s))
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _docx_para(text: str, bold: bool = False, size: int = 22,
               color: str = None, align: str = "left") -> str:
    """构造一个 docx paragraph 的 XML 片段。"""
    color_xml = f'<w:color w:val="{color}"/>' if color else ""
    b = "<w:b/>" if bold else ""
    align_map = {"left": "left", "center": "center", "right": "right"}
    return (f'<w:p><w:pPr><w:jc w:val="{align_map.get(align, "left")}"/></w:pPr>'
            f'<w:r><w:rPr><w:rFonts w:eastAsia="宋体"/>{b}'
            f'<w:sz w:val="{size}"/>{color_xml}</w:rPr>'
            f'<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')


def _docx_table_row(cells, header=False) -> str:
    """构造一行表格 XML（用于发现列表）。"""
    style = ('<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="E8EDF5"/>'
             '<w:tcW w:w="0" w:type="auto"/></w:tcPr>'
             if header else '<w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>')
    tcs = "".join(f"<w:tc>{style}<w:p><w:r><w:rPr><w:rFonts w:eastAsia=\"宋体\"/>"
                  f'<w:sz w:val="18"/>{("<w:b/>" if header else "")}</w:rPr>'
                  f'<w:t xml:space="preserve">{_xml_escape(c)}</w:t></w:r></w:p></w:tc>'
                  for c in cells)
    return f"<w:tr>{tcs}</w:tr>"


def _write_docx(rows: list, out_path: str, single: bool = False) -> None:
    """纯标准库生成 .docx（OOXML + zipfile），Word/WPS 均可打开。

    single=True 表示单份质控报告单版式；否则为样本列表汇总版式。
    """
    body = [_docx_para("星衍 · 放射质控报告单", bold=True, size=32,
                       color="1F4E79", align="center")] if single else []
    if single:
        r = rows[0]
        scores = _scores_of(r)
        body.append(_docx_para("", size=8))
        meta_lines = [
            f"报告 ID：{r.get('id', '')}", f"检查部位：{r.get('applied_site') or '—'}",
            f"患者：{r.get('patient') or '—'}    性别：{r.get('gender') or '—'}    年龄：{r.get('age') or '—'}",
            f"成像方式：{r.get('modality') or '—'}    检查时间：{(r.get('ts') or '')[0:16]}",
        ]
        for ln in meta_lines:
            body.append(_docx_para(ln, size=20))
        body.append(_docx_para("", size=8))
        body.append(_docx_para("一、原报告", bold=True, size=24, color="1F4E79"))
        for sec in ("影像描述：", "影像结论："):
            pass
        text = (r.get("report_text") or "").strip()
        body.append(_docx_para(text if text else "（无正文）", size=20))
        body.append(_docx_para("", size=8))
        body.append(_docx_para("二、质控发现", bold=True, size=24, color="1F4E79"))
        findings = json.loads(r.get("findings_json") or "[]")
        sev_cn = {"high": "严重", "medium": "警告", "low": "提示"}
        if not findings:
            body.append(_docx_para("✓ 未检出问题", size=20))
        else:
            body.append(_docx_table_row(["级别", "类型", "问题描述", "建议修正"], header=True))
            for f in findings:
                body.append(_docx_table_row([
                    sev_cn.get(f.get("severity", ""), f.get("severity", "—")),
                    f.get("error_type", "—"),
                    f.get("message", "—"),
                    f.get("suggestion") or "需人工确认",
                ]))
        body.append(_docx_para("", size=8))
        body.append(_docx_para("三、多维评分", bold=True, size=24, color="1F4E79"))
        score_cn = {"准确性": "准确性 Accuracy", "完整性": "完整性 Completeness",
                    "规范性": "规范性 Normalization", "及时性": "及时性 Timeliness"}
        for k, v in scores.items():
            val = v.get("score", 100) if isinstance(v, dict) else v
            body.append(_docx_para(f"· {score_cn.get(k, k)}：{val} 分", size=20))
        body.append(_docx_para("", size=8))
        body.append(_docx_para("四、质控结论", bold=True, size=24, color="1F4E79"))
        critical = sum(1 for f in findings if f.get("severity") == "high")
        warning = sum(1 for f in findings if f.get("severity") == "medium")
        info = sum(1 for f in findings if f.get("severity") == "low")
        if critical:
            conclusion = f"发现 {critical} 项严重问题、{warning} 项警告、{info} 项提示，建议复核后修改报告。"
        elif warning:
            conclusion = f"发现 {warning} 项警告、{info} 项提示，建议按建议修正文本完善报告。"
        else:
            conclusion = "未发现严重质控问题，报告质量良好。" if not info \
                else f"仅有 {info} 项提示性建议，可选择性完善。"
        body.append(_docx_para(conclusion, size=20, color="C00000" if critical else "375623"))
        body.append(_docx_para("", size=8))
        body.append(_docx_para("—— 本报告由星衍AI放射质控系统自动生成，供质控参考 ——",
                               size=16, color="808080", align="center"))
    else:
        body.append(_docx_para(f"样本库导出（共 {len(rows)} 条）", bold=True, size=28,
                               color="1F4E79", align="center"))
        body.append(_docx_para("导出时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                               size=18, color="808080", align="center"))
        body.append(_docx_para("", size=8))
        body.append(_docx_table_row(["ID", "时间", "患者", "性别", "年龄",
                                     "模态", "部位", "发现数"], header=True))
        for r in rows:
            n_find = len(json.loads(r.get("findings_json") or "[]"))
            body.append(_docx_table_row([
                str(r.get("id", "")), (r.get("ts") or "")[0:16], r.get("patient") or "—",
                r.get("gender") or "—", str(r.get("age") or "—"),
                r.get("modality") or "—", r.get("applied_site") or "—", str(n_find),
            ]))
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(body) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" '
        'w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)


def _write_pdf(rows: list, out_path: str, single: bool = False) -> None:
    """用 reportlab 生成 PDF（可选依赖）。未安装时给出明确提示而非静默失败。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
    except ImportError:
        raise RuntimeError(
            "导出 PDF 需要 reportlab，请执行：pip install reportlab "
            "（或改用 Word/DOCX 导出，无需额外依赖）")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="星衍 · 放射质控报告单" if single else "样本库导出")
    title_style = ParagraphStyle("title", fontName="STSong-Light" if _pdf_font() else "Helvetica",
                                 fontSize=18, leading=24, alignment=1,
                                 textColor=colors.HexColor("#1F4E79"))
    h2 = ParagraphStyle("h2", fontName=_pdf_font() or "Helvetica", fontSize=13,
                        leading=18, textColor=colors.HexColor("#1F4E79"),
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", fontName=_pdf_font() or "Helvetica", fontSize=10,
                          leading=16)
    small = ParagraphStyle("small", fontName=_pdf_font() or "Helvetica", fontSize=8,
                           leading=12, textColor=colors.HexColor("#808080"),
                           alignment=1)
    story = []
    if single:
        r = rows[0]
        scores = _scores_of(r)
        findings = json.loads(r.get("findings_json") or "[]")
        sev_cn = {"high": "严重", "medium": "警告", "low": "提示"}
        story.append(Paragraph("星衍 · 放射质控报告单", title_style))
        story.append(Spacer(1, 4 * mm))
        meta_lines = [
            f"报告 ID：{r.get('id', '')}    检查部位：{r.get('applied_site') or '—'}",
            f"患者：{r.get('patient') or '—'}    性别：{r.get('gender') or '—'}    年龄：{r.get('age') or '—'}",
            f"成像方式：{r.get('modality') or '—'}    检查时间：{(r.get('ts') or '')[0:16]}",
        ]
        for ln in meta_lines:
            story.append(Paragraph(ln, body))
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#1F4E79")))
        story.append(Paragraph("一、原报告", h2))
        story.append(Paragraph((r.get("report_text") or "（无正文）").replace("\n", "<br/>"), body))
        story.append(Paragraph("二、质控发现", h2))
        if not findings:
            story.append(Paragraph("✓ 未检出问题", body))
        else:
            data = [["级别", "类型", "问题描述", "建议修正"]]
            for f in findings:
                data.append([sev_cn.get(f.get("severity", ""), f.get("severity", "—")),
                             f.get("error_type", "—"),
                             (f.get("message", "") or "").replace("\n", "<br/>"),
                             (f.get("suggestion") or "需人工确认").replace("\n", "<br/>")])
            t = Table(data, colWidths=[14 * mm, 26 * mm, 70 * mm, 50 * mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D8")),
                ("FONTNAME", (0, 0), (-1, 0), _pdf_font() or "Helvetica"),
                ("FONTNAME", (0, 1), (-1, -1), _pdf_font() or "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)
        story.append(Paragraph("三、多维评分", h2))
        score_cn = {"准确性": "准确性 Accuracy", "完整性": "完整性 Completeness",
                    "规范性": "规范性 Normalization", "及时性": "及时性 Timeliness"}
        for k, v in scores.items():
            val = v.get("score", 100) if isinstance(v, dict) else v
            story.append(Paragraph(f"· {score_cn.get(k, k)}：{val} 分", body))
        story.append(Paragraph("四、质控结论", h2))
        critical = sum(1 for f in findings if f.get("severity") == "high")
        warning = sum(1 for f in findings if f.get("severity") == "medium")
        info = sum(1 for f in findings if f.get("severity") == "low")
        if critical:
            conclusion = f"发现 {critical} 项严重问题、{warning} 项警告、{info} 项提示，建议复核后修改报告。"
        elif warning:
            conclusion = f"发现 {warning} 项警告、{info} 项提示，建议按建议修正文本完善报告。"
        else:
            conclusion = "未发现严重质控问题，报告质量良好。" if not info \
                else f"仅有 {info} 项提示性建议，可选择性完善。"
        c_style = ParagraphStyle("concl", parent=body, textColor=colors.HexColor(
            "#C00000" if critical else "#375623"), fontName=_pdf_font() or "Helvetica")
        story.append(Paragraph(conclusion, c_style))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("—— 本报告由星衍AI放射质控系统自动生成，供质控参考 ——", small))
    else:
        story.append(Paragraph("样本库导出", title_style))
        story.append(Paragraph("导出时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), small))
        story.append(Spacer(1, 4 * mm))
        data = [["ID", "时间", "患者", "性别", "年龄", "模态", "部位", "发现数"]]
        for r in rows:
            n_find = len(json.loads(r.get("findings_json") or "[]"))
            data.append([str(r.get("id", "")), (r.get("ts") or "")[0:16],
                         r.get("patient") or "—", r.get("gender") or "—",
                         str(r.get("age") or "—"), r.get("modality") or "—",
                         r.get("applied_site") or "—", str(n_find)])
        t = Table(data, colWidths=[16 * mm, 32 * mm, 26 * mm, 18 * mm, 16 * mm,
                                   18 * mm, 24 * mm, 20 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D8")),
            ("FONTNAME", (0, 0), (-1, -1), _pdf_font() or "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    doc.build(story)


_pdf_font_cache = None


def _pdf_font():
    """探测可用的中文字体名（仅一次）。reportlab 内置 STSong-Light，无需字体文件。"""
    global _pdf_font_cache
    if _pdf_font_cache is None:
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            _pdf_font_cache = "STSong-Light"
        except Exception:
            _pdf_font_cache = ""
    return _pdf_font_cache


