"""
LLM 质控引擎（Qwen 系列 / 通义千问 API 兼容）。

对接方式：
  QC_ENGINE=api     → 走通义千问云端 API（qwen-turbo / qwen-plus）
  QC_ENGINE=local   → 走本地 ollama / llama.cpp 兼容接口（OpenAI 格式）

输出格式与现有 engine.RuleEngine 的 run() 完全一致（List[Finding]），
可在 server/deps.py:_run_qc() 中通过开关切换。

依赖：
  pip install httpx    # 轻量 HTTP 客户端，无 async 要求
"""

import json
import os
import re
import time
from typing import List, Optional

# 从现有引擎复用 Finding dataclass
from engine import Finding

# ---------- .env 自动加载（优先级：环境变量 > .env 文件 > 默认值） ----------
def _load_env():
    """从项目根目录加载 .env 文件（不存在则静默跳过）。"""
    import pathlib
    for p in (pathlib.Path(__file__).resolve().parent.parent / ".env",
              pathlib.Path.cwd() / ".env"):
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break

_load_env()

# ---------- 环境变量 ----------
QC_ENGINE = os.environ.get("QC_ENGINE", "rule")         # rule | api | local
QC_LLM_API_KEY = os.environ.get("QC_LLM_API_KEY", "")   # 通义千问 API Key
QC_LLM_BASE_URL = os.environ.get("QC_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QC_LLM_MODEL = os.environ.get("QC_LLM_MODEL", "qwen-turbo-1101")  # 或 qwen-plus / qwen2.5-7b-instruct
QC_LLM_TIMEOUT = int(os.environ.get("QC_LLM_TIMEOUT", "30"))
QC_LLM_MAX_RETRIES = int(os.environ.get("QC_LLM_MAX_RETRIES", "2"))
QC_LLM_FALLBACK = os.environ.get("QC_LLM_FALLBACK", "1")  # "1"=LLM 失败时降级到规则引擎

# ---------- Prompt 模板 ----------
_SYSTEM_PROMPT = """你是放射科报告质控专家。检查报告质控缺陷，输出 JSON。

## 规则清单

R1-GENDER 性别矛盾 — 描述与患者性别不符（如男性报告中出现"子宫"）
R2-LATERALITY 侧别矛盾 — 左右描述混乱（如"右肺"与"左肺"混用无依据）
R3-SCORE 评分缺失 — 应有评分但未给出（如 BI-RADS 分类）
R4-UNIT 单位错误 — 测量值单位错误或缺失（mm/cm 混用）
R5-CONSISTENCY 描述与结论矛盾 — 描述段与诊断印象互相矛盾
R6-SITE 部位漏写 — 扫描范围内应描述但未提及的器官/部位
R7-INTERNAL 内部逻辑矛盾 — 同一报告内自相矛盾（如"未见异常"但又描述病灶）
R8-TYPO 错别字 — 明显错别字（如"费炎"→"肺炎"）
R9-CONFLICT 互斥诊断 — 互斥诊断同时出现（如"良性"与"恶性"）
R10-TEMPLATE 模板缺失 — 报告缺少必需段落（如缺"检查所见"或"诊断印象"）
R11-ABNORMAL 异常描述模糊 — 异常发现描述不够具体（如仅"异常"无细节）
R11-SIDE 侧别遗漏 — 双侧病变但只描述一侧
R11-GENDER 性别相关异常 — 性别特异性发现缺失
R12-SENTENCE 语句不规范 — 语句不通、用词不当、口语化
R14-COUNT 数量描述矛盾 — 病灶数量前后不一致
R14-NATURE 性质描述矛盾 — 病灶性质描述前后不一致
R14-NORMAL 正常异常矛盾 — 同一部位既描述正常又描述异常
R14-SIDE 侧别描述矛盾 — 侧别描述前后不一致
R15-NORMAL 正常描述缺失 — 应提及的正常结构未提及
R15-PRESENCE 存在性矛盾 — "有"与"无"对同一结构的描述矛盾
R15-SIDE 侧别存在矛盾 — 侧别存在性描述矛盾
R16-FOLLOWUP 随访缺失 — 阳性发现未给出随访/复查建议
R17-PERREGION 逐部位比对 — 报告未覆盖所有相关解剖区域
R18-COVERAGE 结构不完整 — 报告整体结构不完整（缺段落）
R19-HOMOPHONE 同音错字 — 读音相近的错别字（如"部明显"→"不明显"）
R19-WHITELIST 非标准词组 — 不在医学词表中的可疑片段
R20-TEMPLATE 模板不规范 — 报告模板格式不规范
R21-GENDER 性别相关部位 — 性别不相关的解剖部位出现在报告中
R22-SIZE 病灶大小缺失 — 描述病灶但未给出具体大小
R22-UNIT 病灶大小单位 — 病灶大小单位不规范

## 输出格式

严格输出 JSON：
{"findings": [
  {
    "rule_id": "规则编码",
    "error_type": "矛盾|漏写|误写|错误|不规范|随访|不一致|重复|歧义|模板缺失|模糊|缺陷",
    "severity": "high|medium|low",
    "message": "问题描述",
    "snippet": "报告原文片段",
    "span": [起始字符偏移, 结束字符偏移],
    "suggestion": "修改建议"
  }
]}

无缺陷时输出 {"findings": []}。只输出 JSON，不要其他文字。"""


def _build_user_prompt(report: str, meta: dict) -> str:
    """构建用户输入，将报告和元数据一起传给模型。"""
    parts = [f"患者信息：{meta.get('patient', '未知')}，{meta.get('gender', '未知')}，{meta.get('age', '未知')}岁"]
    parts.append(f"检查方式：{meta.get('modality', '未知')}")
    parts.append(f"检查部位：{meta.get('applied_site', '未知')}")
    parts.append("")
    parts.append("报告正文：")
    parts.append(report)
    return "\n".join(parts)


def _call_llm_api(messages: List[dict], model: str = None) -> Optional[str]:
    """调用通义千问 API（兼容 OpenAI 格式）。"""
    import httpx

    base_url = QC_LLM_BASE_URL.rstrip("/")
    api_key = QC_LLM_API_KEY
    if not api_key:
        return None

    url = f"{base_url}/chat/completions"
    payload = {
        "model": model or QC_LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(QC_LLM_MAX_RETRIES + 1):
        try:
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=QC_LLM_TIMEOUT,
            )
            if resp.status_code == 429:
                time.sleep(1.5 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip()
        except Exception as e:
            if attempt < QC_LLM_MAX_RETRIES:
                time.sleep(1.0)
                continue
            return None
    return None


def _call_ollama(messages: List[dict], model: str = None) -> Optional[str]:
    """调用本地 ollama 兼容接口（OpenAI 格式）。"""
    import httpx

    base_url = QC_LLM_BASE_URL.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    # 如果用户没设 base_url，默认用 ollama 地址
    if "dashscope" in base_url:
        base_url = "http://localhost:11434/v1"

    url = f"{base_url}/chat/completions"
    payload = {
        "model": model or QC_LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
        "stream": False,
    }

    try:
        resp = httpx.post(url, json=payload, timeout=QC_LLM_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _parse_findings(raw: str) -> Optional[List[Finding]]:
    """解析 LLM 的 JSON 输出为 List[Finding]，含校验和兜底。"""
    if not raw:
        return None

    # 尝试提取 JSON 部分（模型可能输出多余文字）
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return None

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        # 尝试修复常见 JSON 问题
        fixed = raw.strip()
        # 去掉首尾非 JSON 字符
        fixed = re.sub(r"^[^{]*", "", fixed)
        fixed = re.sub(r"[^}]*$", "", fixed)
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            return None

    findings = data.get("findings", [])
    if not isinstance(findings, list):
        return None

    result = []
    ALLOWED_RULE_IDS = {
        "R1-GENDER", "R2-LATERALITY", "R3-SCORE", "R4-UNIT",
        "R5-CONSISTENCY", "R6-SITE", "R7-INTERNAL", "R8-TYPO",
        "R9-CONFLICT", "R10-TEMPLATE", "R11-ABNORMAL", "R11-SIDE",
        "R11-GENDER", "R12-SENTENCE", "R14-COUNT", "R14-NATURE",
        "R14-NORMAL", "R14-SIDE", "R15-NORMAL", "R15-PRESENCE",
        "R15-SIDE", "R16-FOLLOWUP", "R17-PERREGION", "R18-COVERAGE",
        "R19-HOMOPHONE", "R19-WHITELIST", "R20-TEMPLATE",
        "R21-GENDER", "R22-SIZE", "R22-UNIT",
    }
    ALLOWED_ERROR_TYPES = {"矛盾", "漏写", "误写", "错误", "不规范", "随访",
                           "不一致", "重复", "歧义", "模板缺失", "模糊", "缺陷"}

    for f in findings:
        if not isinstance(f, dict):
            continue
        rule_id = str(f.get("rule_id", "")).strip()
        if rule_id not in ALLOWED_RULE_IDS:
            continue
        error_type = str(f.get("error_type", "")).strip()
        if error_type not in ALLOWED_ERROR_TYPES:
            continue
        severity = str(f.get("severity", "medium")).strip()
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        message = str(f.get("message", "")).strip()
        snippet = str(f.get("snippet", "")).strip()
        span_raw = f.get("span", [-1, -1])
        if isinstance(span_raw, list) and len(span_raw) == 2:
            span = [int(span_raw[0]), int(span_raw[1])]
        else:
            span = [-1, -1]
        suggestion = str(f.get("suggestion", "")).strip()

        result.append(Finding(
            rule_id=rule_id,
            error_type=error_type,
            severity=severity,
            message=message,
            snippet=snippet,
            span=span,
            suggestion=suggestion,
        ))

    return result


class LLMEngine:
    """LLM 质控引擎，与 RuleEngine 接口对齐。"""

    def __init__(self):
        self._rule_engine = None  # 兜底用

    def _get_fallback(self):
        """懒加载规则引擎兜底。"""
        if self._rule_engine is None:
            from engine import RuleEngine
            self._rule_engine = RuleEngine()
        return self._rule_engine

    def run(self, text: str, meta: dict) -> List[Finding]:
        """
        主入口：用 LLM 做质控返回 List[Finding]。
        与 RuleEngine.run() 签名完全一致。
        """
        engine_mode = QC_ENGINE
        findings = []

        # 1) LLM 推理
        if engine_mode == "api":
            findings = self._run_api(text, meta)
        elif engine_mode == "local":
            findings = self._run_local(text, meta)
        else:
            # 未知模式，降级到规则引擎
            return self._get_fallback().run(text, meta)

        # 2) LLM 输出为空或解析失败 → 降级
        if findings is None or len(findings) == 0:
            # 空 findings 可能是真的无缺陷，也可能是 LLM 没输出
            # 回退到规则引擎做一次确定性检查
            if QC_LLM_FALLBACK == "1":
                return self._get_fallback().run(text, meta)
            return []

        # 3) 去重（同一 rule_id + 同一 snippet 只保留一条）
        seen = set()
        deduped = []
        for f in findings:
            key = (f.rule_id, f.snippet)
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        # 4) 合并规则引擎的确定性结果（错别字/侧别等规则强项）
        if QC_LLM_FALLBACK == "1":
            rule_findings = self._get_fallback().run(text, meta)
            # 保留规则引擎的确定性错误（R8-TYPO, R19-HOMOPHONE, R2-LATERALITY 等）
            # 规则引擎的同类发现覆盖 LLM 的
            rule_keyed = {}
            for rf in rule_findings:
                key = (rf.rule_id, rf.snippet)
                rule_keyed[key] = rf

            merged = []
            deduped_keyed = {}
            for df in deduped:
                key = (df.rule_id, df.snippet)
                deduped_keyed[key] = df

            # 先加入规则引擎的确定性结果
            for key, rf in rule_keyed.items():
                merged.append(rf)

            # 再加入 LLM 独有的发现（规则引擎没报的）
            for key, df in deduped_keyed.items():
                if key not in rule_keyed:
                    merged.append(df)

            # 再次去重
            seen2 = set()
            final = []
            for f in merged:
                key = (f.rule_id, f.snippet)
                if key not in seen2:
                    seen2.add(key)
                    final.append(f)
            return final

        return deduped

    def _run_api(self, text: str, meta: dict) -> Optional[List[Finding]]:
        """调用通义千问云端 API。"""
        if not QC_LLM_API_KEY:
            return None

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(text, meta)},
        ]

        raw = _call_llm_api(messages)
        if not raw:
            return None
        return _parse_findings(raw)

    def _run_local(self, text: str, meta: dict) -> Optional[List[Finding]]:
        """调用本地 ollama / llama.cpp 兼容接口。"""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(text, meta)},
        ]

        raw = _call_ollama(messages)
        if not raw:
            return None
        return _parse_findings(raw)

    def auto_fix(self, text: str, findings: List[Finding]):
        """
        自动修正。
        LLM 引擎的 auto_fix 策略与规则引擎不同：
        - 确定性错误（R8-TYPO, R19-HOMOPHONE）→ 按规则引擎替换
        - 语义类错误 → 不自动改，给出建议
        """
        # 规则引擎的 auto_fix 处理确定性错误
        return self._get_fallback().auto_fix(text, findings)

    def reload_rules(self):
        """接口对齐，LLM 模式下无意义。"""
        pass


# ---------- 快捷测试 ----------
if __name__ == "__main__":
    import sys

    report = sys.argv[1] if len(sys.argv) > 1 else (
        "双肺纹理清晰，走行自然，未见明显实质性病变。"
        "双侧肺门不大，纵隔居中，未见肿大淋巴结。"
        "心脏大小形态正常。双侧胸膜未见增厚，胸腔未见积液。"
        "所示骨骼未见异常。"
        "肝内见类圆形低密度灶，边界模糊，增强后未见明显强化，考虑囊肿可能。"
        "\n\n诊断意见：右肺下叶结节，建议定期复查。"
    )
    meta = {"patient": "张三", "gender": "男", "age": "45",
            "modality": "CT", "applied_site": "胸部"}

    engine = LLMEngine()
    findings = engine.run(report, meta)
    print(f"Findings: {len(findings)}")
    for f in findings:
        print(f"  [{f.severity}] {f.rule_id}: {f.message}")
        if f.suggestion:
            print(f"    → {f.suggestion}")