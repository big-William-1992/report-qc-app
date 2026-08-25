"""
report_qc_app/server/schemas.py
星衍放射质控软件 — 路由请求/响应模型（Pydantic）

2026-08-21 架构收敛（task T56 第一步）：把 server/main.py 内联的 27 个
Pydantic 模型统一抽离到本模块。纯数据类、零行为、零外部依赖，仅依赖
pydantic 与 typing —— 迁移后 main.py 只需 `from server.schemas import *`
即可，不改变任何路由签名与 FastAPI 请求/响应校验语义。

后续若进一步拆分 routes/ 子包，本模块即各 route 模块共享的唯一模型真相源，
避免「每个路由文件各写一份同名模型」造成的字段口径分叉。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class FindingOut(BaseModel):
    rule_id: str
    error_type: str
    severity: str
    message: str
    snippet: str = ""
    span: list = [-1, -1]
    suggestion: str = ""


class CheckReq(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class FeedbackReq(BaseModel):
    """医生反馈(badcase 回流, 2026-08-25 P1-4)。"""
    feedback_type: str          # false_positive | missed | wrong_type | other
    report_text: str
    rule_id: str = ""
    engine_source: str = ""     # rules | llm | fused
    severity: str = ""
    message: str = ""
    snippet: str = ""
    suggestion: str = ""
    user_note: str = ""


class BatchItem(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    auto_fix: bool = False


class BatchReq(BaseModel):
    items: List[BatchItem]


class AccountCreate(BaseModel):
    emp_id: str
    password: str
    name: str = ""


class LoginReq(BaseModel):
    emp_id: str
    password: str


class SampleCreate(BaseModel):
    report: str
    meta: Dict[str, str] = {}
    findings: List[dict] = []
    score: Dict[str, Any] = {}
    anonymize: bool = False
    user_id: Optional[str] = None


class OCRB64(BaseModel):
    image_base64: str


class RisConfigReq(BaseModel):
    db_type: str = "sqlserver"
    host: str = ""
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    query_sql: str = ""


class SampleExportReq(BaseModel):
    path: str = ""
    fmt: str = "csv"
    anonymize: bool = False  # 导出脱敏：剥离患者姓名/性别/年龄


class SampleImportReq(BaseModel):
    path: str = ""


class RisPollConfigReq(BaseModel):
    enabled: Optional[bool] = None
    interval_min: Optional[int] = None
    limit: Optional[int] = None
    auto_qc: Optional[bool] = None
    auto_enqueue: Optional[bool] = None


class RoleReq(BaseModel):
    role: str


class PwdReq(BaseModel):
    password: str


class DeptReq(BaseModel):
    dept_id: Optional[int] = None   # 传 null/空可清除科室归属


class DeptCreateReq(BaseModel):
    name: str


class ActivateReq(BaseModel):
    code: str


class SampleReportExportReq(BaseModel):
    """单份质控报告单导出请求。fmt: docx | pdf"""
    fmt: str = "docx"


class QcReportExportReq(BaseModel):
    """当前工作区质控结果直接导出报告单（无需入库）。"""
    report: str = ""
    meta: Dict[str, str] = {}
    findings: List[dict] = []
    scores: Dict[str, Any] = {}
    fmt: str = "docx"


class QueueItemReq(BaseModel):
    text: str
    patient: str = ""
    site: str = ""
    source: str = "手动"
    meta: Dict[str, str] = {}


class ScreenRegion(BaseModel):
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


class ScreenOCRReq(BaseModel):
    regions: Dict[str, ScreenRegion] = {}
    refresh: bool = False       # True=识别前重新抓屏（画面已变动时用）
    dynamic: bool = False       # True=动态语义识别：整屏OCR后按标题切分，滚动不变形
    dynamic_region: Optional[ScreenRegion] = None  # 动态模式限定 OCR 范围（三区外接矩形）


class OCRMetaReq(BaseModel):
    """图片模式下，前端已对三区分别 OCR，把三区文本送来后端做结构化抽取。"""
    basic: str = ""
    findings: str = ""
    impression: str = ""


class LearnTypoReq(BaseModel):
    wrong: str = ""
    correct: str = ""


class TypoItemReq(BaseModel):
    wrong: str = ""
    correct: str = ""


class TypoBatchImportReq(BaseModel):
    """批量导入：items 为 [[错词, 正确词], ...] 或 [{wrong, correct}, ...]"""
    items: list = []


__all__ = [
    "FindingOut", "CheckReq", "FeedbackReq", "BatchItem", "BatchReq",
    "AccountCreate", "LoginReq", "SampleCreate", "OCRB64",
    "RisConfigReq", "SampleExportReq", "SampleImportReq", "RisPollConfigReq",
    "RoleReq", "PwdReq", "DeptReq", "DeptCreateReq", "ActivateReq",
    "SampleReportExportReq", "QcReportExportReq", "QueueItemReq",
    "ScreenRegion", "ScreenOCRReq", "OCRMetaReq",
    "LearnTypoReq", "TypoItemReq", "TypoBatchImportReq",
]