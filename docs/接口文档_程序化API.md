# 星衍放射质控软件 — 核心模块程序化接口文档

> 适用版本：`v3.0`（源码开发版 `COMMIT="dev"`)
> 适用对象：星衍系列其他系统（放射质控 / 病历 / 影像云）的开发者，通过 `import` 直接调用本引擎。
> 本文档描述的是**进程内 Python API**，不依赖网络；如需远程调用，见同目录《接口文档_HTTP_REST.md》。

---

## 1. 概述

质控引擎是一套**纯标准库、零外部依赖**的规则引擎（`engine.py`），配套三个本地模块：

| 模块 | 职责 | 依赖 |
|------|------|------|
| `engine` | 报告文本 → 分段 → NER → 规则校验 → 错误列表 + 多维评分 | 仅标准库 |
| `accounts` | 本地账号（工号+密码，PBKDF2 哈希），责任到人 | 仅标准库 + `sqlite3` |
| `samplelib` | 质控样本 SQLite 持久化，支撑统计/驾驶舱 | 仅标准库 + `sqlite3` |
| `ocr_provider` | 屏幕区域 OCR（RapidOCR 离线），文本回填 | `opencv` / `numpy` / `Pillow` / `rapidocr_onnxruntime`（可选） |

**集成方式**：将 `report_qc_app/src/` 加入 `sys.path` 后直接 `import engine / accounts / samplelib / ocr_provider`。

```python
import sys, os
sys.path.insert(0, "/path/to/report_qc_app/src")
import engine, accounts, samplelib, ocr_provider
```

---

## 2. `engine` 模块

### 2.1 数据模型

#### `Entity`（命名实体）
| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | str | 命中的原文片段 |
| `label` | str | 实体类型：`laterality` / `anatomy` / `gender_organ` / `measurement` / `bad_unit` |
| `start` | int | 在原文中的起始下标 |
| `end` | int | 结束下标（不含） |
| `section` | str | 所属段落：`findings` / `impression` / `meta` |
| `canonical` | Optional[str] | 解剖部位规范节点（如 `LUUL`），其它类型为空 |

#### `Finding`（质控发现 / 一条错误）
| 字段 | 类型 | 说明 |
|------|------|------|
| `rule_id` | str | 规则编号，如 `R1-GENDER`、`R8-TYPO`、`R14-NATURE` |
| `error_type` | str | 错误类型（中文，用于统计聚合） |
| `severity` | str | 严重度：`high` / `medium` / `low` |
| `message` | str | 人类可读的告警说明 |
| `snippet` | str | 命中原文片段（可能为空） |
| `span` | tuple[int,int] | 在原文中的区间 `(-1,-1)` 表示无法定位 |
| `suggestion` | str | **仅 R8 错别字**填充：建议修正词；其余规则为空，需人工判定 |

> 规则编号一览（持续扩充）：R1 性别矛盾（含原 R11 性别维度）、R2 左右混淆、R3 评分标准缺失、R4 单位非法、R5 描述-结论矛盾、R6 登记部位不符、R8 错别字、R9 显性冲突、R10 模板结构、R12 同一句逻辑错误、R14 前后文逻辑（描述↔结论：正常/定性/数量/左右）、R15 上下文逻辑（描述段内跨句：先正常后阳性/先见后无；R15-SIDE 已删并入 R2）、R17 逐部位精确比对、R18 检查部位器官漏写、R19 形近错字（R7/R11/R13/R20 已合并或预留）。

### 2.2 `RuleEngine`（规则引擎主类）

```python
class RuleEngine:
    def __init__(self) -> None
    def reload_rules(self) -> None
    def run(self, text: str, meta: dict) -> List[Finding]
    def auto_fix(self, text: str, findings: List[Finding]) -> tuple
```

#### `RuleEngine.run(text, meta) -> List[Finding]`
对一份报告做全量质控。

- **`text`** (`str`)：完整报告文本（含「检查所见/影像描述」与「诊断印象/结论」两段；单段也可，引擎按标题自动切分）。
- **`meta`** (`dict`)：元信息（来自 OCR 回填或人工录入）。全部为可选字符串键：
  - `gender`：性别，取值 `男/女/male/female`（空时引擎退回从正文解析）
  - `name` / `patient`：患者姓名（二选一，引擎做归一）
  - `age` / `modality` / `applied_site` / `laterality` / `exam_no`：年龄、检查手段、申请部位、侧别、影像号
- **返回**：`List[Finding]`，可能为空（表示未发现问题）。

```python
re = engine.RuleEngine()
report = "影像描述：右肺上叶见结节，边缘毛刺，考虑恶性。\n影像结论：右肺上叶结节，符合良性，建议复查。"
findings = re.run(report, {"gender": "男"})
for f in findings:
    print(f.rule_id, f.severity, f.message)
# -> R14-NATURE high 描述称恶性倾向，但结论称良性倾向，前后定性矛盾
```

#### `RuleEngine.auto_fix(text, findings) -> (fixed_text, n_fixed, n_manual, details)`
自动修正。仅**确定性错别字（R8）**会被安全替换；性别矛盾/左右混淆/描述-结论矛盾/部位不符等**无法判定正确值，不改文本**，计入 `n_manual` 待人工确认。

- 返回：
  - `fixed_text` (str)：修正后的原文
  - `n_fixed` (int)：已自动修正的错别字数
  - `n_manual` (int)：需人工确认的问题数
  - `details` (list[dict])：每条修正明细 `{start, end, wrong, correct, snippet, message}`，供前端逐条预览确认

#### `RuleEngine.reload_rules() -> None`
重新从 `assets/rules_config.json` 读取用户维护的规则（错别字词典/冲突词/忽略项/模板），即时生效。

### 2.3 评分函数（独立函数，进程级）

```python
engine.score(findings: List[Finding]) -> Dict[str, dict]
engine.score_summary(scores: Dict[str, dict]) -> Dict[str, int]
engine.error_type_counts(findings: List[Finding]) -> Dict[str, int]
```

- **`score(findings)`**：返回 **四维度评分明细** `{维度: {"score": int, "deductions": [{"rule","delta","reason"}]}}`，维度为 `准确性 / 完整性 / 规范性 / 及时性`。严重度扣分权重见模块内 `SEVERITY_WEIGHT`。
- **`score_summary(scores)`**：从 `score()` 的新结构提取 `{维度: 分数(int)}`，兼容旧消费方（驾驶舱/导出）。
- **`error_type_counts(findings)`**：按 `error_type` 聚合计数，供饼图/看板。

```python
scores = engine.score(findings)
summary = engine.score_summary(scores)      # {"准确性": 60, "完整性": 100, ...}
counts = engine.error_type_counts(findings)  # {"前后文逻辑错误-定性矛盾": 1, ...}
```

### 2.4 命名实体识别 `ChineseRadiologyNER`

```python
ner = engine.ChineseRadiologyNER()
ents: List[Entity] = ner.extract(text)
```

按「最长匹配 + 区间去重」抽取方位词、解剖同义词、性别相关器官、测量单位/非法单位，并标注所属段落。一般无需直接调用，`RuleEngine.run` 内部已使用。

### 2.5 元信息抽取（剪贴板/导入场景）

```python
engine.extract_meta(text: str) -> dict
engine.format_patient_ident(exam_no: str, name: str) -> str
```

- **`extract_meta(text)`**：从报告正文解析 `{patient, exam_no, gender, age, modality, applied_site, laterality}`，全部为中文字符串，**结果仅作提示，允许人工校正**（与 OCR 回填同源）。
- **`format_patient_ident(exam_no, name)`**：组合「影像号/姓名」显示值（纯函数，便于单测）。

### 2.6 规则配置（可维护词典 API）

```python
engine.RULES_CONFIG_PATH          # str：配置文件绝对路径（打包后落在 %APPDATA%/MedicalReportQC）
engine.load_rules_config(path=RULES_CONFIG_PATH) -> dict
engine.save_rules_config(cfg: dict, path=RULES_CONFIG_PATH) -> None
```

配置文件 `rules_config.json` 结构：

```json
{
  "typos":      { "前裂腺": "前列腺", "子官": "子宫", "...": "..." },  // 错别字 -> 正确词
  "conflicts":  [ ["A词", "B词"] ],   // 同一句不应共现的词对
  "ignores":    ["忽略的短语"],        // 不告警的特例
  "template":   { "required_sections": ["findings","impression"], "require_followup": true, ... }
}
```

- `load_rules_config()` 失败自动回退内置默认值，引擎始终可用。
- 增删错别字后调用 `re.reload_rules()`（或重启进程）即时生效。

### 2.7 常用常量（供上层复用）

`LATERALITY` / `GENDER_ORGANS` / `ANATOMY_SYNONYMS` / `SITE_NORM` / `SITE_CANON` / `VALID_UNITS` / `MODALITY_SCORE` 等词典均在 `engine` 顶层导出，可按需引用（如把 `SITE_CANON` 用于界面部位归一显示）。

---

## 3. `accounts` 模块（本地账号 / 责任到人）

完全离线，账号数据仅存本机 SQLite（`assets/accounts.db`），不上传网络。密码以 **PBKDF2-HMAC-SHA256（10 万次迭代 + 随机盐）** 存储，明文不落盘。

```python
import accounts

# 创建第一个账号（工号即登录名，唯一；密码 ≥6 位）
ok, msg = accounts.create_account("10086", "rad12345", name="谢俊")
print(ok, msg)                       # True 创建成功

# 校验登录
if accounts.verify_account("10086", "rad12345"):
    accounts.set_session("10086")    # 记录当前登录工号到 session.json

# 查询
accounts.count_accounts()            # -> int
accounts.get_name("10086")           # -> "谢俊"
accounts.list_accounts()             # -> [("10086","谢俊"), ...]
accounts.account_exists("10086")     # -> True
accounts.get_session()               # -> "10086"（重启预填用）
accounts.clear_session()             # 退出登录
```

| 函数 | 签名 | 返回 | 说明 |
|------|------|------|------|
| `create_account` | `(emp_id, password, name="") -> (bool, str)` | 成功/失败 + 信息 | 工号空或密码<6 位返回失败；工号重复 `IntegrityError` 返回失败 |
| `verify_account` | `(emp_id, password) -> bool` | 是否匹配 | 工号不存在或密码错均返回 `False` |
| `account_exists` | `(emp_id) -> bool` | 是否存在 | — |
| `count_accounts` | `() -> int` | 账号数 | 用于「首次运行强制建号」判断 |
| `get_name` | `(emp_id) -> str` | 显示姓名 | 无则空串 |
| `list_accounts` | `() -> list[tuple]` | `[(emp_id, name), ...]` | 按工号排序 |
| `set_session` | `(emp_id) -> None` | — | 写入 `session.json` |
| `get_session` | `() -> str` | 当前工号 | 读不到返回空串 |
| `clear_session` | `() -> None` | — | 删除会话 |
| `init_db` | `(path=None) -> None` | — | 显式建表（一般无需调用，各函数内部自动建） |

> **测试可注入路径**：模块顶部 `_DB_OVERRIDE` 可设为测试库路径，避免污染真实 `assets/accounts.db`。

---

## 4. `samplelib` 模块（样本库 / 统计）

质控结果持久化到 SQLite（`assets/samples.db`，打包后落 `%APPDATA%/MedicalReportQC/samples.db`）。表 `samples` 含 `id/ts/patient/gender/age/modality/applied_site/laterality/user_id/report_text/findings_json/scores_json`。`laterality`、`user_id` 为向后兼容追加列。

```python
import samplelib, json

sid = samplelib.save_sample(
    report=report_text,
    meta={"patient": "张某", "gender": "男", "age": "56", "modality": "CT",
          "applied_site": "胸部", "laterality": "右"},
    findings=findings,                                   # List[Finding]
    scores=engine.score_summary(engine.score(findings)), # dict
    anonymize=False,        # True 时 patient 入库为 "已脱敏"
    user_id="10086",        # 质控责任人工号（来自 accounts.get_session()）
)
rows = samplelib.list_samples()                  # 概览：id/ts/patient/gender/modality/applied_site/user_id
full = samplelib.get_sample(sid)                 # 全部字段，含 report_text/findings_json/scores_json
samplelib.delete_sample(sid)
err_counts = samplelib.stats_by_error_type()     # {"错别字": 12, ...}
trend = samplelib.stats_by_date()                # {"2026-07-28": {"n": 3, "avg_acc": 88.3}, ...}
```

| 函数 | 签名 | 返回 | 说明 |
|------|------|------|------|
| `save_sample` | `(report, meta, findings, scores, path=None, anonymize=False, user_id=None) -> int` | 新行 `id` | `findings` 以 `__dict__` 序列化入 `findings_json`；`scores` 序列化入 `scores_json` |
| `list_samples` | `(path=None) -> list[dict]` | 概览行 | 按 `id DESC` |
| `get_sample` | `(sid, path=None) -> dict` | 全字段 | 含 `report_text/findings_json/scores_json`；无则 `{}` |
| `list_samples_full` | `(path=None) -> list[dict]` | 全字段列表 | 供导出报表 |
| `delete_sample` | `(sid, path=None) -> None` | — | 物理删除 |
| `stats_by_error_type` | `(path=None) -> dict` | `{error_type: count}` | 饼图数据源 |
| `stats_by_date` | `(path=None) -> dict` | `{date: {n, avg_acc}}` | 趋势图数据源 |
| `init_db` / `db_path` | — | — | 建表 / 取库路径 |

> **脱敏语义**：`anonymize=True` 时仅 `patient` 被替换为「已脱敏」；`user_id`（责任人工号）**永不脱敏**，以保证质控可追溯。

---

## 5. `ocr_provider` 模块（屏幕区域 OCR，可选依赖）

全部本地离线，符合医疗数据不出域。需安装 `opencv-python` / `numpy` / `Pillow` / `rapidocr-onnxruntime`；未安装时 `availability()` 返回 `False` 并给出安装提示，不影响 `engine` 主流程。

```python
import ocr_provider

ok, why = ocr_provider.availability()     # (True, "") 或 (False, "请先安装...")
if ok:
    # bbox = (x, y, w, h) 逻辑像素（macOS 自动按 Retina 缩放，需屏幕录制权限）
    text = ocr_provider.region_to_text((100, 200, 600, 60))
    meta = engine.extract_meta(text)       # 与剪贴板解析同源，回填输入框
```

| 函数 | 签名 | 返回 | 说明 |
|------|------|------|------|
| `availability` | `() -> (bool, str)` | 是否可用 + 原因 | 首次检测会懒加载 RapidOCR 并缓存失败原因 |
| `ocr_image` | `(img, min_score=MIN_SCORE) -> str` | 识别文本 | `img` 可为 PIL.Image 或 numpy；低于 `min_score`(0.55) 的行丢弃，`0` 关闭过滤 |
| `region_to_text` | `(bbox) -> str` | 区域识别文本 | 区域监控主入口：截图 + OCR |
| `capture_region` | `(bbox) -> PIL.Image` | 截图 | 仅截图，供预览/调试 |
| `preprocess_for_ocr` | `(img) -> np.ndarray` | 预处理后数组 | 灰度 + CLAHE；矮条小字区域(高<`SMALL_REGION_H`)先 2x 放大 |
| `image_signature` | `(img) -> tuple` | 64×64 灰度指纹 | 帧间快速比较 |
| `signature_changed` | `(sig_a, sig_b, tolerance, pixel_diff) -> bool` | 是否实质变化 | 区域没变就不重跑 OCR，省 CPU |

> 关键常量：`MIN_SCORE=0.55`（置信度阈值）、`PIXEL_DIFF=8` / `CHANGE_TOLERANCE=0.002`（变化检测）、`SMALL_REGION_H=96`（矮条小字放大阈值）。

---

## 6. 端到端集成示例

```python
import sys
sys.path.insert(0, "/path/to/report_qc_app/src")
import engine, accounts, samplelib

# 1) 责任到人：从会话取当前工号
emp_id = accounts.get_session() or "unknown"

# 2) 质控
re = engine.RuleEngine()
report = open("report.txt", encoding="utf-8").read()
meta = engine.extract_meta(report)          # 或来自 OCR / 人工录入
findings = re.run(report, meta)

# 3) 评分
summary = engine.score_summary(engine.score(findings))

# 4) 持久化（入库）
sid = samplelib.save_sample(report, meta, findings, summary, user_id=emp_id)

print(f"样本 {sid} 已存，发现 {len(findings)} 处问题；准确性 {summary.get('准确性')}")
```

---

## 7. 注意事项

1. **线程安全**：`engine` 无全局可变状态，可多线程并发；`accounts`/`samplelib` 基于 SQLite 连接，建议每线程独立调用（SQLite 连接非跨线程共享）。
2. **路径**：打包态（`sys.frozen`）下数据库/规则配置自动重定向到用户可写目录（`%APPDATA%/MedicalReportQC`），开发态在 `assets/`。
3. **版本**：接口随版本演进，调用方应锁定 `APP_VERSION`（`import version; version.APP_VERSION`）。本文档对应 `3.0`。
4. **合规性**：引擎与样本库均本地存储、不联网；对外提供网络服务时，由调用方在 HTTP 层落实鉴权与审计（见 REST 文档）。
