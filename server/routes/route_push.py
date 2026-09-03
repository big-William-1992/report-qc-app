"""
route_push.py — 星衍放射质控 API 路由：推送接收

PACS/RIS 系统主动推送报告到本端点，替代传统轮询模式。
支持 JSON 与 XML 两种格式，通过 X-API-Key 头鉴权。
"""

import os
import json
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List
from io import StringIO

from fastapi import APIRouter, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.deps import _envelope, _run_qc, require_emp_local, _queue_add_text
import samplelib
import engine

router = APIRouter(tags=["push"])

# ── 推送鉴权 ─────────────────────────────────────────────────────────

_PUSH_API_KEY = os.environ.get("PUSH_API_KEY", "").strip()


def _require_push_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> str:
    """验证推送来源的 API Key。"""
    if not _PUSH_API_KEY:
        raise HTTPException(503, "推送服务未配置：请设置 PUSH_API_KEY 环境变量")
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key 头")
    # 恒定时间比较防时序攻击
    if len(x_api_key) != len(_PUSH_API_KEY) or not _constant_time_compare(x_api_key, _PUSH_API_KEY):
        raise HTTPException(403, "X-API-Key 无效")
    return x_api_key


def _constant_time_compare(a: str, b: str) -> bool:
    """简单恒定时间比较，防止时序侧信道攻击。"""
    if len(a) != len(b):
        return False
    result = 0
    for ca, cb in zip(a, b):
        result |= ord(ca) ^ ord(cb)
    return result == 0


# ── 数据模型（JSON 格式） ────────────────────────────────────────────────

class PushReportJSON(BaseModel):
    """PACS/RIS 推送报告 JSON 请求体。"""
    report_text: str = Field(..., description="报告正文（纯文本）")
    patient: str = Field(default="", description="患者姓名")
    gender: str = Field(default="", description="性别")
    age: str = Field(default="", description="年龄")
    modality: str = Field(default="", description="成像方式（CT/MR/DR/…）")
    applied_site: str = Field(default="", description="检查部位")
    exam_id: str = Field(default="", description="检查号/影像号")
    exam_date: str = Field(default="", description="检查日期（ISO 格式）")
    source: str = Field(default="PACS推送", description="来源标识")


# ── XML 辅助解析 ─────────────────────────────────────────────────────

def _parse_xml_report(body: str) -> dict:
    """将 XML 报告体解析为统一的 dict 结构。

    支持两种格式自动适配：
    1. 扁平元素：<report><report_text>...</report_text><patient>...</patient>...</report>
    2. 嵌套结构：<Report><Header><PatientName>...</PatientName>...</Header>
               <Body><ReportContent>...</ReportContent></Body></Report>
    """
    root = ET.fromstring(body)

    # 归一化标签名（去命名空间、转小写）
    def _norm_tag(tag: str) -> str:
        # 去掉 {namespace} 前缀
        idx = tag.rfind("}")
        return tag[idx + 1:].lower() if idx >= 0 else tag.lower()

    def _text_of(elem, *tags):
        """从元素中取第一个匹配标签的文本，支持多标签名备选。"""
        for tag in tags:
            found = elem.find(f".//{tag}")
            if found is not None and found.text:
                return found.text.strip()
        return ""

    # 探测结构：如果根元素下直接包含 <report_text> 或 <ReportText> 则按扁平处理
    children = {_norm_tag(c.tag) for c in root}
    is_flat = "report_text" in children or "reporttext" in children

    if is_flat:
        # 扁平结构直接映射
        def _val(*tags):
            for tag in tags:
                el = root.find(f".//{tag}")
                if el is not None and el.text:
                    return el.text.strip()
                ltag = tag.lower()
                el2 = root.find(f".//{ltag}")
                if el2 is not None and el2.text:
                    return el2.text.strip()
            return ""

        return {
            "report_text": _val("report_text", "reportText", "ReportText", "ReportText"),
            "patient": _val("patient", "Patient", "PatientName", "patient_name", "PatientName"),
            "gender": _val("gender", "Gender", "Sex", "sex"),
            "age": _val("age", "Age"),
            "modality": _val("modality", "Modality", "ModalityType"),
            "applied_site": _val("applied_site", "appliedSite", "AppliedSite", "bodypart",
                                 "BodyPart", "StudyDescription", "study_description"),
            "exam_id": _val("exam_id", "examId", "ExamId", "AccessionNumber",
                            "accession_number", "StudyInstanceUID", "study_uid"),
            "exam_date": _val("exam_date", "examDate", "ExamDate", "StudyDate", "study_date"),
        }
    else:
        # 嵌套结构：常见 DICOM SR / HL7 风格
        return {
            "report_text": (_text_of(root, "ReportContent", "report_content", "ReportText",
                                     "report_text", "DiagnosticReport", "diagnostic_report")
                            or _text_of(root, "Body", "body", "Content", "content")),
            "patient": (_text_of(root, "PatientName", "patient_name", "Patient",
                                 "patient") or ""),
            "gender": (_text_of(root, "Gender", "gender", "Sex", "sex") or ""),
            "age": (_text_of(root, "Age", "age") or ""),
            "modality": (_text_of(root, "Modality", "modality", "ModalityType",
                                  "modality_type") or ""),
            "applied_site": (_text_of(root, "BodyPart", "bodypart", "AppliedSite",
                                      "applied_site", "StudyDescription", "study_description")
                             or ""),
            "exam_id": (_text_of(root, "AccessionNumber", "accession_number", "ExamId",
                                 "exam_id", "StudyInstanceUID", "study_uid") or ""),
            "exam_date": (_text_of(root, "StudyDate", "study_date", "ExamDate",
                                   "exam_date") or ""),
        }


# ── 推送接收端点 ─────────────────────────────────────────────────────

@router.post("/api/v1/push/report")
async def push_report(
    request: Request,
    api_key: str = Depends(_require_push_api_key),
):
    """接收 PACS/RIS 推送的报告，自动完成质控、入库、入待质控队列。

    **请求格式**：
    - `Content-Type: application/json` — JSON 请求体，字段见 PushReportJSON 模型
    - `Content-Type: application/xml` — XML 请求体，支持扁平/嵌套两种结构

    **鉴权**：`X-API-Key` 头，值由服务端 `PUSH_API_KEY` 环境变量配置。

    **返回**：质控结果（含 findings / score 等）及入库状态。
    """
    content_type = request.headers.get("content-type", "").lower()

    try:
        raw_body = await request.body()
        body_str = raw_body.decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(400, f"无法读取请求体：{exc}")

    # ── 解析请求体 ────────────────────────────────────────────────
    if "xml" in content_type or body_str.strip().startswith("<"):
        # XML 解析
        try:
            data = _parse_xml_report(body_str)
        except ET.ParseError as exc:
            raise HTTPException(400, f"XML 解析失败：{exc}")
    elif "json" in content_type or body_str.strip().startswith("{"):
        # JSON 解析
        try:
            parsed = json.loads(body_str)
            data = PushReportJSON(**parsed).model_dump()
        except Exception as exc:
            raise HTTPException(400, f"JSON 解析失败：{exc}")
    else:
        raise HTTPException(400, "不支持的 Content-Type，请使用 application/json 或 application/xml")

    # ── 校验必要字段 ──────────────────────────────────────────────
    report_text = (data.get("report_text") or "").strip()
    if not report_text:
        raise HTTPException(400, "report_text 不能为空")

    # ── 构建元数据 ─────────────────────────────────────────────────
    meta = {
        "patient": data.get("patient", ""),
        "gender": data.get("gender", ""),
        "age": data.get("age", ""),
        "modality": data.get("modality", ""),
        "applied_site": data.get("applied_site", ""),
    }
    source = data.get("source", "PACS推送")

    # ── 执行质控 ──────────────────────────────────────────────────
    try:
        qc_result = _run_qc(report_text, meta, auto_fix=False)
    except Exception as exc:
        raise HTTPException(500, f"质控执行失败：{exc}")

    # ── 入库 samplelib ────────────────────────────────────────────
    try:
        findings = []
        for f in qc_result.get("findings") or []:
            findings.append(engine.Finding(
                rule_id=f.get("rule_id", ""),
                error_type=f.get("error_type", ""),
                severity=f.get("severity", "low"),
                message=f.get("message", ""),
                snippet=f.get("snippet", ""),
                span=tuple(f.get("span", (-1, -1))),
                suggestion=f.get("suggestion", ""),
            ))
        samplelib.save_sample(
            report_text, meta, findings, qc_result.get("score") or {},
            anonymize=False, user_id="push-api",
        )
    except Exception as exc:
        # 入库失败不丢报告，返回警告但保持 200
        qc_result["_warnings"] = qc_result.get("_warnings", []) + [f"入库失败：{exc}"]

    # ── 入待质控队列 ──────────────────────────────────────────────
    try:
        _queue_add_text(report_text, meta, source=source)
    except Exception as exc:
        qc_result["_warnings"] = qc_result.get("_warnings", []) + [f"入队失败：{exc}"]

    return _envelope(True, "PUSH_OK", {
        "qc": qc_result,
        "meta": meta,
        "source": source,
        "warnings": qc_result.get("_warnings", []),
    }, "报告已接收并完成质控")