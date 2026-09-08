# PACS/RIS 数据库主动推送对接指南

> 目标读者：医院信息科 / HIS·RIS·PACS 对接工程师。
> 一句话：**让数据库侧把新报告"推"给质控软件，而不是质控软件来"拉"。**
> 配套脚本：`tools/push_forwarder.py`（内网部署，无需改动业务系统）。

---

## 1. 总体架构

```
医院数据库(RIS/HIS/PACS)
   │  ① 报告审核/签发（或定时扫描视图，见 §4）
   ▼
   HTTP POST  /api/v1/push/report   (X-API-Key 鉴权)
   ▼
星衍质控软件
   ├─ 自动质控（错别字/术语/模板/部位一致性…）
   ├─ 入库样本库（含影像描述/影像诊断独立字段）
   └─ 进「待质控队列」→ 医生逐份复核、针对性修改
```

质控软件**不要求**直连你们的内网数据库（原来的直连/轮询模式保留为兼容，不作为主线）。
你们只需把**审核签发后的报告**（或视图里的增量数据）POST 到推送端点。

---

## 2. 接收端点规格

| 项 | 值 |
|----|----|
| 地址 | `POST http://<质控软件IP>:8500/api/v1/push/report` |
| 鉴权 | 请求头 `X-API-Key: <PUSH_API_KEY>`（值由质控软件端 `.env` 提供给你们） |
| 格式 | `Content-Type: application/json` 或 `application/xml` |
| 字符集 | UTF-8（数据库取数时统一转 UTF-8，防止中文乱码） |

> 说明：`8500` 是桌面端默认端口。如果质控软件部署为内网服务（`server.main:app`），默认 `8000`，以实际为准。
> 鉴权失败：无 Key 返回 `401`，Key 错误返回 `403`；密钥写错会导致推送被拒，日志在质控软件 `crash.log`。

### 2.1 JSON 字段（推荐）

```json
{
  "patient": "张三",
  "gender": "男",
  "age": "45",
  "modality": "CT",
  "applied_site": "胸部",
  "findings_desc": "双肺纹理清晰，未见实质性病变。",
  "diagnosis": "胸部 CT 未见明显异常。",
  "exam_id": "202608210001",
  "exam_date": "2026-08-21",
  "source": "HIS签发推送"
}
```

| 字段 | 必填 | 说明 | 数据库常见列 |
|------|------|------|-------------|
| `patient` | 否 | 患者姓名 | `patient_name` |
| `gender` | 否 | 性别（男/女） | `sex` |
| `age` | 否 | 年龄 | `age` |
| `modality` | 否 | 检查方式（CT/MR/DR/…） | `exam_part` / `modality` |
| `applied_site` | 否 | 检查部位（胸部/头颅/腰椎…） | `apply_part` / `body_part` |
| `findings_desc` | 二选一 | **影像描述** | `description` / `findings` |
| `diagnosis` | 二选一 | **影像诊断** | `diagnosis` / `impression` |
| `report_text` | 二选一 | 整段正文（旧格式兼容，无描述/诊断分栏时用） | `report_content` |
| `exam_id` | 否 | 检查号/影像号（建议传，便于核对） | `accession_no` |
| `exam_date` | 否 | 检查日期（ISO：`2026-08-21` 或 `2026-08-21 10:30:00`） | `report_time` |
| `source` | 否 | 来源标识，默认 `PACS推送` | — |

**字段规则**：
- `findings_desc` / `diagnosis` 至少传一个，或传 `report_text`。全空返回 `400`。
- 描述/诊断会作为**独立字段**进入待质控队列：医生在质控工作区看到「影像描述」「影像诊断」两个编辑区，可逐字段修改后重新质控入库。
- 别名兼容：`findings`/`description`/`desc` → 影像描述；`impression`/`conclusion` → 影像诊断。你们不用改字段名也行。

### 2.2 XML 格式（老系统可选）

```xml
<Report>
  <PatientName>张三</PatientName>
  <Gender>男</Gender>
  <Age>45</Age>
  <Modality>CT</Modality>
  <BodyPart>胸部</BodyPart>
  <FindingsDescription>双肺纹理清晰，未见实质性病变。</FindingsDescription>
  <Diagnosis>胸部 CT 未见明显异常。</Diagnosis>
  <ExamId>202608210001</ExamId>
  <ExamDate>2026-08-21</ExamDate>
</Report>
```

支持扁平与嵌套（Header/Body）两种结构，标签名大小写不敏感，自动适配
（如 `Impression`、`Conclusion`、`ReportContent`、`AccessionNumber` 等常见命名）。

### 2.3 响应与去重

```json
{ "ok": true, "code": "PUSH_OK", "data": { "qc": { ... 质控结果 ... }, "fields": { ... 回显字段 ... }, "warnings": [] } }
```

- `ok:true` = 已接收并完成质控、入库、入队。
- **幂等去重**：质控软件按正文 MD5 去重——同一内容重复推送，队列里不会出现两条
  （返回 `code:PUSH_OK`，但队列侧判重跳过）。建议你们仍按水位/状态标记避免无谓流量（见 §4.2）。
- `warnings` 非空不表示失败：入库/入队失败时仍返回 200，并附带原因，建议你们记日志。
- **报告改后重推**：正文变化 → MD5 变化 → 会作为新版本进入队列，医生能看到更新后的内容。这是预期行为。

---

## 3. 三条改造路线（按推荐排序）

| 路线 | 改什么 | 工作量 | 适用 | 推荐 |
|------|--------|--------|------|------|
| A. 应用层改造 | HIS/RIS 的报告审核/签发/打印代码里加一次 HTTP POST | 小（几行） | 你们能改 HIS/RIS 源码 | ★★★ |
| B. 中间推送器 | 内网一台服务器/PC 跑 `tools/push_forwarder.py`，定时扫视图推增量 | 小（配一个查询） | 不能改 HIS，只给只读账号 | ★★★ |
| C. 数据库触发器/作业 | 触发器里调 HTTP（SQLCLR / utl_http） | 大、风险高 | 一般不建议 | ★ |

---

### 路线 A：HIS/RIS 应用层改造（最直接）

在报告**审核/签发/打印**的成功分支里调用一次推送即可，伪代码：

```csharp
// 报告签发成功时（C# 示例，其他语言同理）
using var client = new HttpClient();
client.DefaultRequestHeaders.Add("X-API-Key", "你们拿到的Key");
var body = new {
    patient = "张三", gender = "男", age = "45",
    modality = "CT", applied_site = "胸部",
    findings_desc = "双肺纹理清晰…", diagnosis = "胸部CT未见异常…",
    exam_id = "202608210001", exam_date = "2026-08-21",
    source = "HIS签发"
};
var resp = await client.PostAsJsonAsync("http://质控IP:8500/api/v1/push/report", body);
```

要点：
- 在**签发成功后**、且**不阻塞主流程**地调用（放后台任务/线程，失败记日志，不要影响出报告）。
- 客户端地址写质控软件所在机的**内网 IP**，不要写 `localhost`。
- 医院内网一般无准入，如果开了防火墙，需放行质控软件端口入站。
- 每次调用拿 `exam_id` 记一条自己的推送日志（成功/失败/应答），便于排查。

---

### 路线 B：中间推送器（不改业务系统）

仓库已带 `tools/push_forwarder.py`：

- 部署到内网任意一台能同时访问「报告库」和「质控软件」的机器（Windows/Linux/macOS 均可）。
- 给一个**只读账号**即可，脚本只做 SELECT。
- 定时(默认 60s)查询你提供的视图；按**水位列**（如 `report_time`）只推新增/变更的行。
- 失败自动重试 3 次、指数退避，异常记入自己的日志文件，不会丢报告（下轮继续）。

```bash
# 1. 配置（推荐用 JSON 配置文件，避免命令行暴露密码）
python3 tools/push_forwarder.py --config /etc/push_forwarder.json

# 2. 直接跑一次（调试用）
python3 tools/push_forwarder.py --config /etc/push_forwarder.json --once

# 3. 注册为服务常驻（Windows: 任务计划 / Linux: systemd + --loop --interval 60）
```

配置模板见脚本头部的 `DEFAULT_CONFIG` 注释。数据库驱动需要：
`sqlserver→pyodbc`、`oracle→oracledb`、`mysql→pymysql`、`postgresql→psycopg2`。

---

### 路线 C：数据库触发器（一般不推荐）

| 数据库 | 可行性 | 说明 |
|--------|--------|------|
| Oracle | 可以 | `utl_http` 可直接发 POST，注意 ACL 授权 |
| SQL Server | 麻烦 | 需开 CLR 集成或 `sp_OACreate`，性能/安全代价高，建议走路线 A/B |
| PostgreSQL | 可以 | `pgaudit`/`NOTIFY` + 监听器，或 `http` 扩展（需编译） |
| MySQL/MariaDB | 不行 | 无原生 HTTP，必须外面套层 |

触发器方案**不建议作为首选**：数据库里跑 HTTP 调用，慢、易阻塞事务、难排查。
如果一定要用，务必 `PRAGMA/异步` + 失败不阻塞 + 单独记录推送表。

---

## 4. 你们需要准备的 SQL 视图

无论走 A（应用层直接取内存对象）还是 B（推送器取视图），推荐先在库里建一个
**只读视图**（临时表/物化视图也可），把报告库字段映射到质控软件字段：

```sql
-- SQL Server 示例
CREATE OR ALTER VIEW v_qc_push_report AS
SELECT TOP (200) WITH TIES
  patient_name          AS patient,          -- 患者姓名
  sex                   AS gender,           -- 性别
  age                   AS age,              -- 年龄
  exam_part             AS modality,         -- 检查方式: CT/MR/DR/钼靶…
  apply_part            AS applied_site,     -- 检查部位: 胸部/头颅/腰椎…
  description           AS findings_desc,    -- 影像描述
  diagnosis             AS diagnosis,        -- 影像诊断
  accession_no          AS exam_id,          -- 检查号
  CONVERT(varchar(19), report_time, 120) AS exam_date,  -- 检查时间(ISO)
  report_time           AS wm_ts             -- 水位列（推送器用）
FROM v_radiology_report
WHERE report_time > '2026-01-01';            -- 推送器会追加水位条件
```

Oracle：函数 `TO_CHAR(report_time,'YYYY-MM-DD HH24:MI:SS')` 转 `exam_date`；
MySQL/PostgreSQL：`DATE_FORMAT` / `to_char` 同理。列名**全部小写**最省事。

---

## 5. 常见问题

- **Key 从哪拿？** 质控软件 `.env` 里 `PUSH_API_KEY`，由你们管理员从部署机读取并通知对接方。
- **中文乱码？** 两端统一 UTF-8；odbc 连接串建议加 `ClientCharset=UTF-8`（Oracle 场景）。
- **推送失败会丢报告吗？** 路线 B 不会（水位推进在成功后）；路线 A 建议你们自己记推送日志。
- **重复推送怎么办？** 质控端按正文 MD5 去重；建议你们用水位/`exam_id` 状态再加一道保底。
- **报告退改/追改？** 推送新版本即可，质控端当作新版本处理，医生复核时覆盖旧内容。
- **https？** 内网明文即可；若要 https，FastAPI 前面加反向代理（nginx/caddy），地址相应改。
- **下班后推送？** 无限制，24h 接收；医生上班时在「待质控队列」复核即可。
- **怎么验证通没通？** 用 curl 手动推一条测试报告，返回 `ok:true` 且界面「待质控队列」出现即成功：

```bash
curl -X POST http://质控IP:8500/api/v1/push/report \
  -H "X-API-Key: KEY" -H "Content-Type: application/json" \
  -d '{"patient":"测试","gender":"男","age":"30","modality":"DR","applied_site":"胸部",
       "findings_desc":"测试所见。","diagnosis":"测试诊断。"}'
```
