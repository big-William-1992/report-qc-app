# 星衍放射质控软件 · 院内交付硬化方案

> 适用版本：v4.3.x 系列（仓库 `report_qc_app`）
> 交付形态：医院内网自托管。桌面端 Windows exe（pywebview + 本地 FastAPI/uvicorn），
> 亦可"一台服务器 + 多医生浏览器访问"的多机部署。
> 本文随附 **可直接复制使用的示例配置**：`deploy/nginx-report-qc.conf`、`deploy/winsw-report-qc.xml`、
> `deploy/systemd-report-qc.service`、`deploy/backup.sh`（用法见 `deploy/README.md`）。

## 0. 事实基线（以代码为准，勿臆测）

### 0.1 服务启动与端口

| 项目 | 事实 | 来源 |
|------|------|------|
| 服务启动命令 | `uvicorn server.main:app --host 127.0.0.1 --port 8500` | `playwright.config.js`、`DEPLOYMENT.md` |
| 交付基线端口 | **8500** | 桌面壳默认 `XY_QC_PORT=8500`；`.bat` 提示"端口 8500 被占用" |
| `server.main` 默认端口 | `8000`（可用 `QC_PORT` 覆盖） | `server/main.py`（`--port` / `QC_PORT`） |
| 监听地址 | 默认 `127.0.0.1`（回环）；`QC_HOST` 可覆盖 | `server/main.py`、`ARCHITECTURE.md` |
| SPA 与 API | **同源**：静态资源由 FastAPI 挂载（`app.mount("/static", …)`），根路径 `spa_fallback` | `server/main.py` |

> ⚠️ **口径不一致（需修正）**：`DEPLOYMENT.md` 多处仍写端口 `8500`，而代码与 CI/启动脚本为 `8500`。
> 本文一律用 **8500**；建议信息科/研发统一文档口径，避免防火墙、反代、监控按错端口配置。
>
> ⚠️ **不要写 `PORT`**：服务端读的是 `QC_HOST` / `QC_PORT`（及 CLI 参数），桌面壳读 `XY_QC_PORT`。
> 全仓未发现服务端读取裸 `PORT` 环境变量（`PORT` 只作为局部变量出现）。若在服务模板里只写 `PORT=8500`，
> **不会生效**。

### 0.2 真实存在的关键环境变量

| 变量 | 作用 | 备注 |
|------|------|------|
| `QC_HOST` / `QC_PORT` | 服务监听地址/端口 | 默认 `127.0.0.1` / `8000`；交付基线端口 8500 |
| `QC_API_SECRET` | 令牌签名密钥 | **非本机监听未设置则启动即 `SystemExit`**；多节点须一致 |
| `QC_CORS_ORIGINS` | 追加跨域白名单（逗号分隔） | 默认仅放行本机 Origin；禁止 `*` |
| `QC_APPDATA` | 数据目录覆盖 | 默认见 §0.3；E2E 也用它隔离 |
| `DATABASE_URL` | 数据库位置（空则 SQLite） | `server/db.py` |
| `QC_DB_OVERRIDE` | 库位置覆盖（上层转 `DATABASE_URL`） | 测试/多实例 |
| `PUSH_API_KEY` | PACS/RIS 主动推送鉴权（`X-API-Key` 头） | `server/routes/route_push.py` |
| `QC_FLOATING_LICENSE` / `QC_FLOATING_SEATS` / `QC_FLOATING_DEPT_ID` / `QC_FLOATING_HEARTBEAT_DIR` | 浮动授权与座位心跳共享目录 | 多机部署 |
| `QC_UPDATE_LOCAL_DIR` | 离线更新包目录 | 见 §6 |
| `QC_BACKUP_ENABLED` / `QC_BACKUP_INTERVAL_DAYS` / `QC_BACKUP_KEEP_DAYS` / `QC_BACKUP_DIR` | 内置自动备份 | `src/backup.py` |
| `QC_API_TTL` / `QC_RATE_PER_MIN` / `QC_OCR_MAX_BYTES` / `QC_ENGINE` | 令牌时效 / 限流 / OCR 上传上限 / 引擎模式 | `.env.example`、`server/main.py` |

### 0.3 关键路径（交付/运维定位用）

| 内容 | Windows | Linux / macOS |
|------|---------|----------------|
| 数据目录（`user_data_dir()`，`QC_APPDATA` 可覆盖） | `%APPDATA%\MedicalReportQC` | `$XDG_DATA_HOME/MedicalReportQC`、`~/Library/Application Support/MedicalReportQC` |
| 日志目录（`log_user_dir()`） | `%LOCALAPPDATA%\星衍放射质控软件\logs` | `~/.local/share/星衍放射质控软件/logs`、`~/Library/Application Support/星衍放射质控软件/logs` |
| `qc.db`（账号/科室/样本/队列/设置/订单/审计） | 源码态 `<root>/assets/qc.db`；打包态用户数据目录 | 同左 |
| `feedback.db`（医生反馈） | 数据目录（无覆盖时与库同根） | 同左 |
| `rules_config.json` / `ocr_config.json` | 数据目录（首次从 `assets/` 复制） | 同左 |
| `license.dat` | `<安装根>/assets/license.dat` | 同左 |
| 内置备份默认目录 | `<日志目录>/backups`（可被 `QC_BACKUP_DIR` 覆盖） | 同左 |

### 0.4 已有安全能力（可依赖）

- 令牌鉴权：`server/security.py` 单一实现，HMAC-SHA256 签名、随机持久化密钥；角色 `admin` / `doctor`。
- 密码：PBKDF2 加盐；登录失败锁定与限流（`QC_RATE_PER_MIN` 默认 60/分钟）。
- CORS 白名单（**禁止通配 `*`**）；`X-Emp-Id` 本机兜底仅限 `127.0.0.1`/`::1`/`localhost`。
- 审计：`audit_log` 表 + 导出端点；登录、改密、角色、科室、规则保存、备份恢复等敏感操作落库。
  （交付说明称覆盖 21 个敏感端点；精确清单以 `server/main.py` 的 `log_audit(...)` 调用为准。）
- 离线授权：Ed25519 激活码 + 机器指纹；试用 90 天，且有多处冗余锚点，删除 `license.dat` 不能重置试用。
- 错误日志仅本地写入（`QC_ERROR_REPORT_URL` **未实现**，不联网上报）。

### 0.5 已知缺口（本方案要补的）

| 编号 | 缺口 | 影响 | 对应章节 |
|------|------|------|----------|
| G1 | 应用无内置 TLS | 内网明文传输，测评不过 | §2 |
| G2 | 无 SSO，仅本地账号 | 账号需手工维护，离职回收靠人工 | §3 |
| G3 | 内置备份不加密且**不含 `feedback.db`** | 备份介质一旦泄露=报告正文泄露；反馈数据无备份 | §5 |
| G4 | 无异地/定时备份与恢复演练 | 单点故障无兜底，恢复能力未验证 | §5 |
| G5 | 无 Windows 服务化官方步骤 | 服务器部署靠人工开着窗口，重启不自恢复 | §4 |
| G6 | 审计无医务科现成报表 | 合规检查临时拼数据 | §7、§9 |
| G7 | 离线升级包**无签名**（`.sig`） | 共享盘被篡改可投毒；离线模式仅校验 `.sha256` | §6 |
| G8 | 反代同机时会命中"本机放行" | **鉴权被绕过**（详见 §2.3） | §2 |
| G9 | 单机 SQLite 的并发上限 | 多机/高并发下写锁竞争 | §10 |

---

## 1. 部署形态对比

| 维度 | A. 单机桌面 | B. 单服务器 + 多浏览器 | C. 院内虚拟化（VM/超融合） |
|------|-------------|------------------------|---------------------------|
| 形态 | 每台医生机跑 exe（pywebview + 本地 uvicorn，默认 `127.0.0.1:8500`） | 1 台服务器跑后端，医生用浏览器经反代访问 | 同 B，但宿主为院内虚拟化平台，可快照/迁移/容灾 |
| 数据 | 各自本地 SQLite | **单库集中**（SQLite 单文件或 PostgreSQL） | 同 B（库放独立数据盘/块存储） |
| 授权 | 单机激活码绑定硬件指纹 | 浮动授权（`QC_FLOATING_LICENSE` + 心跳共享目录计座位） | 同 B |
| 运维成本 | 低（但 N 台要 N 次升级） | 中（升级一次，全科室受益） | 中高（需虚拟化平台管理能力） |
| 可用性 | 单机故障只影响一台 | 服务器故障=全科停摆，**必须有备份+快照** | 最高（HA/快照/在线迁移） |
| 安全面 | 小（仅本机回环） | 需 TLS/反代/防火墙/审计（本文重点） | 同 B + 平台层加固 |
| 适用前提 | 1–2 台；无集中运维诉求 | 院内网可达；有反代与证书；有备份介质 | 有虚拟化平台；信息科愿纳管 |
| 推荐场景 | 起步/单机试点、诊室独立使用 | **放射科 2–10 人日常主力形态（推荐）** | 三级医院、需合规与容灾的正式生产 |

**选择建议**

1. **≤2 台工作站**：选 A，零运维，但必须逐台配置自动备份到共享盘。
2. **3–10 人、要"数据一份、升级一次"**：选 B，并按本文 §2/§4/§5 硬化。
3. **正式生产、要容灾/审计/测评**：选 C，在 B 的硬化基础上叠加虚拟机快照与独立数据盘。

> **需院内信息科确认**：服务器放置位置、是否纳入院内备份体系统一纳管、是否要求等保/院内安全测评。

---

## 2. 反向代理与 TLS

### 2.1 为什么必须加反代

应用**无内置 TLS**，且多机形态下若直接 `--host 0.0.0.0` 会把明文服务暴露到内网。反代承担：
HTTPS 终结、证书、HSTS、内网网段白名单、上传体积与超时、真实 IP 透传、版本号隐藏。

### 2.2 nginx 与 IIS 配置要点对照

完整可复制模板见 `deploy/nginx-report-qc.conf`（nginx）。IIS 要点见 §2.2.2。

| 要点 | nginx | IIS（ARR + URL Rewrite） |
|------|-------|--------------------------|
| HTTPS 终结 | `listen 443 ssl;` + `ssl_certificate` / `ssl_certificate_key`（含中间证书的 fullchain） | 站点绑定 443 + 导入证书（pfx）；绑定勾选 SNI |
| 协议版本 | `ssl_protocols TLSv1.2 TLSv1.3;` | 用 IISCrypto/组策略禁用 SSL3/TLS1.0/1.1，仅留 1.2/1.3 |
| HSTS | `add_header Strict-Transport-Security "max-age=31536000" always;`（先短后长） | `web.config` 的 `outboundRules` 加 `Strict-Transport-Security` |
| 版本号隐藏 | `server_tokens off;` | 移除响应头 `Server`（`outboundRules` `removeServerHeader="true"`）+ 移除 `X-Powered-By` |
| 上传体积 | `client_max_body_size 25m;`（应用上限 20MB，留余量） | `Request Filtering` → `maxAllowedContentLength=26214400`（25MB） |
| 超时 | `proxy_read_timeout 180s;`（LLM 质控可能 30s+ 重试） | ARR `ProxyTimeout`（秒）；`proxy` 节点设 `timeout=180` |
| 真实 IP | `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`（多层时用 `real_ip` 模块） | ARR 默认追加 `X-Forwarded-For`；需在 `allowedServerVariables` 放行 |
| 内网限制 | `allow 10.0.0.0/8; … deny all;` | 站点绑定 + 防火墙；或 `ipSecurity` 的 `allowUnlisted=false` + 允许网段 |
| 转发目标 | `proxy_pass http://127.0.0.1:8500;` | ARR 反向代理规则 → `http://127.0.0.1:8500/` |
| 访问日志 | `access_log` 记真实 IP | IIS 日志默认记真实 IP（`X-Forwarded-For` 内的由应用解析） |

#### 2.2.1 nginx 关键片段（完整见模板）

```nginx
upstream report_qc_backend { server 127.0.0.1:8500; keepalive 16; }

server {
    listen 443 ssl; http2 on;
    server_name qc.example.hospital.local;      # ← 替换
    ssl_certificate     /etc/ssl/report-qc/fullchain.pem;
    ssl_certificate_key /etc/ssl/report-qc/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    server_tokens off;
    add_header Strict-Transport-Security "max-age=31536000" always;

    allow 10.0.0.0/8; allow 172.16.0.0/12; allow 192.168.0.0/16;  # ← 院内网段
    deny  all;

    client_max_body_size 25m;
    proxy_connect_timeout 10s; proxy_send_timeout 120s; proxy_read_timeout 180s; proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    location / { proxy_pass http://report_qc_backend; }
}
```

#### 2.2.2 IIS（ARR + URL Rewrite）要点

1. 安装 `Application Request Routing`(ARR) + `URL Rewrite`，ARR 全局设置启用 proxy。
2. 建反向代理规则：匹配 `(.*)` → `http://127.0.0.1:8500/{R:1}`，勾选"保留 HTTP 主机头"。
3. 允许真实 IP 服务器变量（否则 `X-Forwarded-For` 不被传递）：
   `configuration/system.webServer/rewrite/allowedServerVariables` 加入 `HTTP_X_FORWARDED_FOR`、`HTTP_X_FORWARDED_PROTO`。
4. `web.config` 关键片段（放在站点根）：

```xml
<configuration>
  <system.webServer>
    <rewrite>
      <allowedServerVariables>
        <add name="HTTP_X_FORWARDED_FOR" />
        <add name="HTTP_X_FORWARDED_PROTO" />
      </allowedServerVariables>
      <rules>
        <rule name="ReportQC Reverse Proxy" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:8500/{R:1}" />
          <serverVariables>
            <set name="HTTP_X_FORWARDED_PROTO" value="https" />
          </serverVariables>
        </rule>
      </rules>
    </rewrite>
    <security>
      <requestFiltering>
        <!-- 25MB，须大于应用 20MB 上限 -->
        <requestLimits maxAllowedContentLength="26214400" />
      </requestFiltering>
    </security>
    <!-- 移除版本号 -->
    <httpProtocol>
      <customHeaders>
        <remove name="X-Powered-By" />
        <add name="Strict-Transport-Security" value="max-age=31536000" />
        <add name="X-Content-Type-Options" value="nosniff" />
      </customHeaders>
    </httpProtocol>
    <rewrite>
      <!-- 注：同一 <rewrite> 段不要重复出现；此处仅为示意，实际合并到上面一个 rewrite 节点 -->
    </rewrite>
  </system.webServer>
</configuration>
```

> 实际部署时请把上面两处 `<rewrite>` 合并为一个节点（XML 不允许重复元素）。

### 2.3 WebSocket / SSE 是否需要？

**不需要。** 全仓未发现 WebSocket、`EventSource`、`text/event-stream` 或 `StreamingResponse` 的实际使用
（仅 eslint 里声明了一个未使用的 `WebSocket` 全局）。前端为普通 REST 轮询。
因此反代**无需** `Upgrade`/`Connection: upgrade` 配置，也无需为长连接调栈。

> 若未来引入实时推送，再放开 proxy 的 `Upgrade` 头即可（模板中已注释保留位置）。

### 2.4 FastAPI / uvicorn 侧必须配合的头部与参数

| 应用侧动作 | 原因 | 做法 |
|-----------|------|------|
| 信任代理头 | 应用用 `request.client.host` 记审计 IP、并做"本机放行"判定；不处理则**所有远程用户都被当成 127.0.0.1** | `uvicorn … --proxy-headers --forwarded-allow-ips="127.0.0.1"`（反代异机则填反代内网 IP） |
| `X-Forwarded-For` | 审计日志的 `ip` 字段来源 | 反代注入；应用经 uvicorn 代理头还原 |
| `X-Forwarded-Proto` | 应用生成绝对链接/判断 https | 反代注入 `https` |
| `Host` | 应用的 Origin 白名单与 Host 校验 | 反代透传 `$host` |
| `QC_API_SECRET` | 非回环监听**必须**显式设置，否则启动即退出 | 环境变量/`EnvironmentFile`（勿入库） |
| `QC_CORS_ORIGINS` | 若医生可能用其它 Origin 直连 | 追加 `https://qc.example.hospital.local`；**统一单入口时同源，无需设置** |

> 🔒 **G8 高危（务必处理）**：`server/security.py::require_emp_local` 与 `server/main.py` 的建号引导逻辑
> 在 `request.client.host ∈ {127.0.0.1, ::1, localhost}` 时接受 `X-Emp-Id` 头、并在"无账号"时放行 `"local"`。
> 若应用绑 `127.0.0.1` 且反代同机、**又不启用 `--proxy-headers`**，则所有外部请求的 `client.host` 都是 `127.0.0.1`
> → 等于任何内网用户都能免令牌冒充任意工号。**两种正确姿势任一**：
> 1. 启用 `--proxy-headers --forwarded-allow-ips=127.0.0.1`（推荐，同时修好审计 IP）；或
> 2. 让应用监听内网地址（如 `10.0.0.5:8500`），使反代连接源不是回环，从根上避免"本机"判定。
>
> 建议同时把反代与应用之间的通道限制住（只允许反代 IP 访问应用端口），并保留 `QC_API_SECRET`。

---

## 3. 账号与 SSO

| 路线 | 做法 | 改造成本 | 风险 | 推荐顺序 |
|------|------|----------|------|----------|
| **A. 本地账号（现状）** | 用内置注册/管理；admin 维护账号 | 0（已具备） | 账号为孤立体系；离职回收靠人工；密码策略依赖管理员执行 | ① 立即采用 |
| **B. LDAP / AD 绑定** | 后端新增 LDAP 认证模块，登录时向院内 AD 校验（LDAPS 636） | 中（研发改造 + AD 服务账号 + 联调；**需研发排期**） | 需处理 AD 不可达时的降级、口令不经应用留存、证书信任；改错会影响登录 | ② 有条件时做 |
| **C. 反代层 SSO 头 + 应用信任头** | 反代用 IIS Windows 认证 / `auth_request` / oauth2-proxy 完成认证，注入 `X-Remote-User`，应用信任该头 | 低（应用侧小改）但**风险最高** | **头伪造**：只要应用可被直连，任何人伪造该头即等于任意登录 | ③ 仅在能保证"应用仅反代可达"后做 |

**风险说明（C）**：信任头方案的安全完全依赖网络隔离——应用端口必须做到"除反代外不可达"
（绑回环 + 防火墙 + 只允许反代 IP）。这与 §2.4 的 G8 是同一类问题：**信任边界不能靠请求内容判断**。

**推荐顺序**：先做 A 的规范化（强口令、及时停用离职账号、admin 最小化），再评估 B；
C 只在已有成熟反代 SSO 且能落实隔离时采用。

> **若只做一件**：**加固路线 A，并把应用锁在反代之后（回环监听 + 防火墙 + `QC_API_SECRET` + 内网白名单）。**
> 这一步零研发成本，却同时消除了"无 TLS"和"本机放行可被绕过"两个最大风险；SSO 属体验/合规加分项，可后置。

> **需院内信息科确认**：AD 域控版本与 LDAPS 证书、是否允许应用使用服务账号查询目录、账号标识（工号/域账号）映射规则。

---

## 4. 服务化与开机自启

> **前提（重要）**：以下模板按 **Python 源码 + venv** 的服务端部署编写。
> 打包版 exe 是 pywebview 桌面壳，默认**只绑定 `127.0.0.1` 并弹窗**，未见官方"无头服务"参数。
> **需向研发确认**打包 exe 是否支持无头方式对外提供多机访问；若否，多机服务端请用源码 + venv 部署。

### 4.1 Windows：WinSW（推荐）与 NSSM

| 方案 | 优点 | 配置方式 |
|------|------|----------|
| **WinSW** | 声明式 XML、日志滚动、失败重启、服务账号，运维友好 | `deploy/winsw-report-qc.xml` |
| NSSM | 命令式、无需写 XML，图形界面可调 | 见下方命令 |
| `sc.exe` | 系统自带 | 无法直接托管非服务程序，需配合 `srvany` 类工具，**不推荐** |

**WinSW 步骤**：下载 WinSW → 重命名为 `report-qc-service.exe` → 同目录放同名 `.xml`（用 `deploy/winsw-report-qc.xml` 改路径与密钥）→
管理员执行 `.\report-qc-service.exe install` / `start`。

**NSSM 等价命令**（把源码部署做成服务）：

```powershell
# 需管理员 PowerShell；<...> 处按实际替换
nssm install ReportQC "C:\ReportQC\app\.venv\Scripts\python.exe" `
  "-m uvicorn server.main:app --host 127.0.0.1 --port 8500 --proxy-headers --forwarded-allow-ips 127.0.0.1"
nssm set ReportQC AppDirectory "C:\ReportQC\app"
nssm set ReportQC AppEnvironmentExtra `
  QC_HOST=127.0.0.1 QC_PORT=8500 "QC_APPDATA=D:\ReportQC\data" QC_API_SECRET=<强随机串>
nssm set ReportQC AppStdout "D:\ReportQC\logs\app.out.log"
nssm set ReportQC AppStderr "D:\ReportQC\logs\app.err.log"
nssm set ReportQC AppRotateFiles 1
nssm set ReportQC AppRotateBytes 10485760
nssm set ReportQC Start SERVICE_AUTO_START
nssm set ReportQC AppExit Default Restart
nssm set ReportQC AppRestartDelay 10000
nssm set ReportQC ObjectName "DOMAIN\svc_reportqc" "<密码>"
nssm start ReportQC
```

**要点**：工作目录必须是项目根（`server/` 与 `src/` 的上级）；日志重定向到受控目录并开滚动；
失败自动重启；用**专用低权限账号**（不要 `SYSTEM`/`Administrator`）；密钥不要写进提交到仓库的 XML。

### 4.2 Linux：systemd

完整模板 `deploy/systemd-report-qc.service`。关键项：

| 项 | 值/说明 |
|----|---------|
| `User` / `Group` | 专用系统账号 `svc-reportqc`（无登录 shell） |
| `WorkingDirectory` | 项目根（如 `/opt/report-qc/app`） |
| `EnvironmentFile` | `/etc/report-qc/env`（`0600`，放 `QC_API_SECRET`、`PUSH_API_KEY`） |
| `Environment` | `QC_HOST` / `QC_PORT` / `QC_APPDATA` / `QC_BACKUP_*` / `QC_UPDATE_LOCAL_DIR` |
| `ExecStart` | venv 的 `python -m uvicorn server.main:app --host … --port 8500 --proxy-headers --forwarded-allow-ips 127.0.0.1` |
| `Restart` | `always` + `RestartSec=5s` |
| 日志 | `StandardOutput=journal`（`journalctl -u report-qc -f`），**不要**再自行重定向到文件（避免与内置日志双写） |
| 加固 | `NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=full` / `ReadWritePaths=/var/lib/report-qc` |

启用：`sudo systemctl daemon-reload && sudo systemctl enable --now report-qc`。

### 4.3 Windows 服务化 + 反代组合

若同时用 IIS 反代，给 WinSW/NSSM 服务加依赖 `W3SVC`（WinSW 的 `<depend>W3SVC</depend>`，
NSSM 的 `nssm set ReportQC DependOnService W3SVC`），保证反代先起、服务后停。

---

## 5. 备份与灾备

### 5.1 备份范围与频率

| 对象 | 内容 | 是否内置备份覆盖 | 建议频率 | 备注 |
|------|------|------------------|----------|------|
| `qc.db` | 账号/科室/样本/队列/设置/订单/**审计日志** | ✅（`VACUUM INTO`） | 每日 + 每次升级前 | 单文件，是恢复的核心 |
| `feedback.db` | 医生反馈（**含报告正文**） | ❌ **内置备份不含** | 每日 | 必须由 `deploy/backup.sh` 补上 |
| `rules_config.json` | 质控规则配置 | ✅ | 每日 / 变更后 | |
| `ocr_config.json` | OCR 区域配置 | ✅（位于数据目录） | 每日 / 变更后 | 重画成本高，勿漏 |
| `ris_config.json` | RIS/PACS 集成配置 | ✅ | 每日 / 变更后 | |
| `license.dat` | 离线授权 | ✅ | 每日 / 变更后 | 丢失需重新激活 |

### 5.2 `VACUUM INTO` 的正确用法与一致性

内置 `src/backup.py` 与 `deploy/backup.sh` 都用：

```bash
sqlite3 "<数据目录>/qc.db" "VACUUM INTO '/目标/qc.db'"
sqlite3 "<数据目录>/feedback.db" "VACUUM INTO '/目标/feedback.db'"
```

要点：

- `VACUUM INTO` 是在线一致性快照，**不长时间锁库**，WAL 模式下也安全；目标是新文件，**必须不存在**（否则报错）。
- 备份后立刻校验：`sqlite3 '/目标/qc.db' 'PRAGMA integrity_check;'`，期望输出 `ok`。
- 不要用 `cp qc.db` 代替：库处于 WAL 时，直接复制会漏掉 `-wal` 中的已提交事务，得到**不一致或丢数据**的副本。
- 运行库已启用 `PRAGMA journal_mode=WAL`、`BEGIN IMMEDIATE`、`busy_timeout=30000`（见 §10），与 `VACUUM INTO` 兼容。

### 5.3 加密与介质对比

| 方案 | 类型 | 密钥管理 | 优点 | 缺点 | 适用 |
|------|------|----------|------|------|------|
| **不加密** | — | 无 | 简单、可 grep | **备份含报告正文，泄露=隐私事故** | 仅在加密盘/受控介质内，且经审批 |
| **age** | 公钥（X25519） | 只需公钥加密；私钥离线保管 | 命令极简、现代、无口令 | 需引入 `age` 组件 | **推荐** |
| **GPG** | 公钥 | 公钥加密，私钥离线 | 生态成熟、院内可能已有 | 配置繁琐、子密钥/信任模型易踩坑 | 已有 GPG 体系时 |
| **7z（AES-256）** | 对称口令 | 口令需托管 | 无需额外公钥、跨平台 | 口令易泄露；`-p` 会短暂出现在进程列表 | 过渡方案 |

**介质建议**：本地受控盘（当日）→ 院内受控共享/备份一体机（近线）→ **离线介质（移动硬盘/磁带，异地存放）**。
离线副本是抵御勒索软件的关键，务必"备份完成后拔离/离线"。

> **需院内信息科确认**：备份是否需要加密、采用哪种加密、密钥托管方式、离线介质保管责任人、是否纳入院内统一备份体系。

### 5.4 恢复演练步骤与验收标准

**演练频率**：上线前必做一次；此后**每季度一次**，并记录留档。

| 步骤 | 操作 | 期望结果 |
|------|------|----------|
| 1 | 取最近一次备份，解密到隔离环境（**不要在生产直接覆盖**） | 解密成功，得到 `.tar` |
| 2 | 解包并校验清单：`tar -xf … -C ./restore && (cd ./restore && sha256sum -c SHA256SUMS)` | 全部 `OK` |
| 3 | `sqlite3 ./restore/qc.db 'PRAGMA integrity_check;'` | 输出 `ok` |
| 4 | 停生产服务，先备份现状库，再写回 `qc.db` / `feedback.db` / 配置 / `license.dat` | 文件属主与权限正确，服务账号可读 |
| 5 | 启动服务，`curl http://127.0.0.1:8500/api/v1/health` | `status=ok`，`db.ok=true` |
| 6 | 登录并抽查：账号数、科室、样本数、审计条数与备份时点一致 | 数据一致，无缺表/422 |
| 7 | 记录演练用时（即实测 RTO）与问题清单 | 归档，形成改进项 |

**验收标准**：① 解密与校验全通过；② 库完整性 `ok`；③ 服务可启动、健康检查通过；④ 关键业务数据条数与备份时点一致；
⑤ 实测 RTO ≤ 约定值；⑥ 演练记录归档（含执行人、时间、结论）。

### 5.5 RPO / RTO 建议

| 场景 | RPO（可容忍数据丢失） | RTO（恢复时长） | 达成手段 |
|------|----------------------|-----------------|----------|
| 单机桌面 | ≤ 24h | ≤ 2h | 内置每日备份 + 共享盘副本 |
| 院内服务器（推荐） | ≤ 24h（重要变更前额外备份） | ≤ 4h | `deploy/backup.sh` 每日 + 离线副本 + 季度演练 |
| 正式生产（虚拟化） | ≤ 1h | ≤ 1h | 每日逻辑备份 + 虚拟机快照 + 独立数据盘快照 |

> 以上为**工程建议值**，最终 RPO/RTO 需与科室/信息科共同确认并写入运维 SLA。

---

## 6. 离线升级与完整性

### 6.1 离线包目录结构（以 `QC_UPDATE_LOCAL_DIR` 为例）

```text
\\fileserver\ReportQC\updates\        # ← QC_UPDATE_LOCAL_DIR 指向这里
├── latest.zip                        # Windows 更新包（macOS 为 latest.tar.gz）
├── latest.zip.sha256                 # 已可用：归档 SHA-256（客户端强制校验）
├── latest.zip.sig                    # 待补齐：Ed25519 对 SHA-256 摘要的签名（base64）
├── latest.tar.gz                     # macOS 更新包
├── latest.tar.gz.sha256
├── latest.tar.gz.sig
└── RELEASE_NOTES.md                  # 版本说明（可选，便于科室沟通）
```

应用侧路径定义见 `src/auto_updater.py`：离线模式取 `latest.zip`（Windows）/ `latest.tar.gz`（macOS）。

### 6.2 现状与缺口

| 能力 | 状态 | 说明 |
|------|------|------|
| `.sha256` | ✅ **已可用** | 2026-09-30 起 CI 为每个产物生成同名 `.sha256`；客户端 `_verify_download_sha256` / 离线 `_verify_local_checksum` 会强制校验 |
| `.sig`（在线更新） | ⚠️ 代码就绪、**CI 未生成** | `src/auto_updater.py::_verify_update_signature` 已支持 Ed25519 验签；缺 GitHub Secret 里的私钥 |
| `.sig`（离线更新） | ❌ **未接入** | 离线分支只调用 `_verify_local_checksum`（仅校验 `.sha256`）；`.sig` 的读取走的是在线 URL 分支 |

### 6.3 补齐 `.sig`：GitHub Actions 新增签名步骤

**第一步：生成密钥对（在离线/受控机器上执行一次，私钥绝不入库）**

```bash
openssl genpkey -algorithm ED25519 -out update_signing_key.pem   # 私钥，离线保管
openssl pkey -in update_signing_key.pem -pubout -out update_public_key.pem   # 公钥，内嵌客户端
```

**第二步：把公钥内嵌客户端**：用 `update_public_key.pem` 的内容替换
`src/auto_updater.py` 中 `_UPDATE_PUBLIC_KEY_PEM` 的占位 PEM（当前为占位公钥，必须替换为与私钥配对的真实公钥）。

**第三步：Secret 管理**

- GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，
  名称 `UPDATE_SIGNING_KEY`，值为 `update_signing_key.pem` 的**完整 PEM 文本**（含首尾行）。
- 高安全要求可用 **Environment secret** 并设置 required reviewers，发布前需人工批准。
- **私钥绝不入库、绝不写进工作流明文、绝不写进任何日志**；本地私钥文件权限 `600`，最好离线保管。

**第四步：在 `publish` job 的"Generate checksums"之后追加签名步骤**

> 客户端验签口径：`pub.verify(sig, sha256(archive).digest())` —— 即对**归档 SHA-256 摘要的原始 32 字节**做
> Ed25519 签名，结果 **base64** 写入 `<产物>.sig`。下面的写法与之严格一致。

```yaml
      - name: Sign release assets (.sig, Ed25519)
        # 未配置 Secret 时跳过（不影响发布，但客户端会退回"无 .sig → 宽容跳过"）
        if: ${{ secrets.UPDATE_SIGNING_KEY != '' }}
        shell: bash
        env:
          UPDATE_SIGNING_KEY: ${{ secrets.UPDATE_SIGNING_KEY }}
        run: |
          set -euo pipefail
          umask 077
          KEY="$(mktemp)"; trap 'rm -f "$KEY" digest.bin sig.bin' EXIT
          printf '%s\n' "$UPDATE_SIGNING_KEY" > "$KEY"
          # 自检：确认私钥可用且与客户端公钥配对（配对校验见下方 verify 步骤）
          openssl pkey -in "$KEY" -noout || { echo "::error::签名私钥不可用"; exit 1; }

          IFS=',' read -ra arr <<< "${RELEASE_FILES}"
          extra=""
          for f in "${arr[@]}"; do
            case "$f" in *.sha256|*.sig) continue;; esac   # 只对发布产物本身签名
            [ -f "$f" ] || continue
            # 与客户端一致：对归档 SHA-256 的原始摘要（32 字节）签名
            openssl dgst -sha256 -binary "$f" > digest.bin
            openssl pkeyutl -sign -inkey "$KEY" -rawin -in digest.bin -out sig.bin
            base64 -w0 sig.bin > "$f.sig"
            echo "  $f.sig <- $(head -c 16 "$f.sig")…"
            extra="$extra,$f.sig"
          done
          [ -n "$extra" ] || { echo "::error::未生成任何 .sig"; exit 1; }
          echo "RELEASE_FILES=${RELEASE_FILES}${extra}" >> "$GITHUB_ENV"
```

**第五步（可选但强烈建议）：发布前用公钥自校验**，确保签名与内嵌公钥配对，避免"发了签但客户端验不过"。

```yaml
      - name: Verify signatures with embedded public key
        if: ${{ secrets.UPDATE_SIGNING_KEY != '' }}
        shell: bash
        run: |
          set -euo pipefail
          # 从源码提取内嵌公钥，逐一验签（与 auto_updater 的验签逻辑同源）
          sed -n '/-----BEGIN PUBLIC KEY-----/,/-----END PUBLIC KEY-----/p' src/auto_updater.py > pub.pem
          IFS=',' read -ra arr <<< "${RELEASE_FILES}"
          for f in "${arr[@]}"; do
            case "$f" in *.sig) ;; *) continue;; esac
            art="${f%.sig}"
            openssl dgst -sha256 -binary "$art" > digest.bin
            base64 -d "$f" > sig.bin
            openssl pkeyutl -verify -pubin -inkey pub.pem -rawin -in digest.bin -sigfile sig.bin \
              || { echo "::error::$art 签名与内嵌公钥不匹配"; exit 1; }
          done
```

**离线包同步**：把 `latest.zip` 与 `latest.zip.sha256`、`latest.zip.sig` **一起**拷到 `QC_UPDATE_LOCAL_DIR`。

### 6.4 补齐离线 `.sig` 校验（应用侧小改，需研发排期）

当前离线分支只校验 `.sha256`。若要离线也验签，把 `_verify_local_checksum(dest, src)` 扩展为同样读取
`src + ".sig"` 并按 `_UPDATE_PUBLIC_KEY_PEM` 验签（可复用 `_verify_update_signature` 的校验逻辑）。
**在完成该改造前，离线升级的安全性以 `.sha256` 为准，请务必保证共享目录本身受控**。

> **需院内信息科确认**：离线更新目录由谁维护、写权限谁有、是否要求签名验签后再分发。

---

## 7. 上线前检查清单

> 逐条勾选；不适用项注明原因。`□` 表示待办。

### 7.1 网络 / 端口 / 防火墙
- [ ] 服务器网段与医生机网段互通，DNS 名称可解析（如 `qc.example.hospital.local`）
- [ ] 应用端口（8500）**仅**允许反代所在主机访问，禁止医生机直连
- [ ] 反代端口（443）仅放行院内网段，80 仅用于跳转
- [ ] 已用 `netstat -ano | findstr 8500` / `ss -lntp | grep 8500` 确认端口未被占用
- [ ] 防火墙策略已由信息科备案并留存规则截图

### 7.2 TLS
- [ ] 证书为院内 CA 或受信 CA 签发，**含完整中间证书**，有效期 >6 个月
- [ ] 仅启用 TLSv1.2/1.3；已禁用 SSL3/TLS1.0/1.1
- [ ] HSTS 已启用（初期 `max-age` 从小值观察）
- [ ] 响应头已隐藏服务器版本号（nginx `server_tokens off` / IIS 移除 `Server`、`X-Powered-By`）
- [ ] 用 `openssl s_client -connect host:443` 或院内扫描工具复核协议与证书链

### 7.3 账号与最小权限
- [ ] `QC_API_SECRET` 为强随机串（≥32 字节），多节点一致，**未入库**
- [ ] `PUSH_API_KEY`（若启用推送）为强随机串，**未入库**
- [ ] 管理员账号数量最小化；医生账号按实名/工号建立
- [ ] 服务进程使用专用低权限账号，**非** `root`/`SYSTEM`/`Administrator`
- [ ] 已明确离职/转岗账号的停用流程与责任人

### 7.4 备份可用性验证
- [ ] `deploy/backup.sh` 已按院内路径改好并可成功执行一次
- [ ] 备份**包含 `feedback.db`**（内置备份不含，必须脚本补齐）
- [ ] 备份已按策略加密（或落在加密盘/受控介质）
- [ ] 备份产物权限为 `600`、属主为运维/服务账号
- [ ] 已完成一次**完整恢复演练**并通过 §5.4 验收标准
- [ ] 已有异地/离线副本，并确认搬运责任人与周期

### 7.5 日志与审计
- [ ] 服务日志可写、已滚动、保留周期明确（如 90 天）
- [ ] 审计日志可导出（`/api/v1/admin/audit-logs/export`），且**导出件含真实 IP**
- [ ] 已确认反代 `X-Forwarded-For` 正确透传（审计 `ip` 为医生机 IP，非反代 IP）
- [ ] 已给医务科/信息科提供审计导出与归档流程说明（见 §9）

### 7.6 时间同步
- [ ] 服务器与医生机均接入院内 NTP，`timedatectl`/`w32tm` 状态正常
- [ ] 时区统一（建议 `Asia/Shanghai`），否则审计时间戳对不上

### 7.7 磁盘空间与保留策略
- [ ] 数据盘剩余空间 ≥ 30%，并配置阈值告警
- [ ] 日志、备份、更新缓存目录有保留策略（备份 GFS、日志轮转）
- [ ] 已确认 `qc.db` 增长预期与磁盘容量规划

### 7.8 隐私告知与院内审批
- [ ] 已向科室提供隐私告知/数据使用说明（仓库 `PRIVACY_POLICY.md`）
- [ ] 系统部署方案已通过院内信息科/安全评审（**需确认**是否需等保备案）
- [ ] 备份含报告正文的事实已告知，并落实加密与访问控制
- [ ] 数据出网行为已确认：错误日志**仅本地**写入、不上报

### 7.9 回滚方案
- [ ] 已保存上一版本安装包与升级前的 `qc.db` 备份
- [ ] 已确认本次升级是否含**不可逆**的数据库变更（见 §8）
- [ ] 回滚步骤已文档化并演练过

---

## 8. 回滚预案

### 8.1 版本回滚步骤

| 步骤 | 操作 | 备注 |
|------|------|------|
| 1 | 记录现场：当前版本、`qc.db` 大小与修改时间、健康检查输出 | 便于复盘 |
| 2 | 停止服务（`systemctl stop report-qc` / `net stop ReportQC`） | 确保无写入 |
| 3 | **备份现状**：`python src/log_utils.py --dest <DIR>` + `deploy/backup.sh` | 回滚失败还能再回来 |
| 4 | 判断数据库兼容性（见 §8.2） | 决定能否直接回滚 |
| 5 | 若兼容：还原上一版本程序文件（覆盖安装包/解压旧版），保留现有 `qc.db` | |
| 6 | 若不兼容：同时还原升级前的 `qc.db` | **会丢失升级后新增数据**，需科室确认 |
| 7 | 启动服务并验证：`/api/v1/health` → 登录 → 抽查业务 | 验收同 §5.4 |
| 8 | 归档：回滚原因、影响范围、数据损失量 | 形成复盘记录 |

### 8.2 "升级后无法回滚"的判定条件

满足**任一**即视为不可直接回滚，回滚将丢数据：

1. 新版本对 `qc.db` 执行了**破坏性 schema 变更**（删列/改类型/重建表且未保留旧数据）。
2. 发布了**数据迁移脚本**且旧版本不认识新表/新列（旧代码读到未知结构可能报错或写坏数据）。
3. 升级后已产生**仅新版本可解析**的数据（如新的审计动作、新的配置项、新的订单/队列字段）。
4. 升级同时更换了 `license.dat` 或授权机制版本。

> **工程建议**：每次升级前固定执行"停止服务 → `deploy/backup.sh` → 再升级"，把 §8.2 的风险降到"最多丢一次升级窗口的数据"。
> **需研发确认**：每个版本的迁移脚本是否幂等、是否可逆；建议在 `CHANGELOG.md` 中为每个版本标注"数据库变更：可逆/不可逆"。

---

## 9. 运维手册要点

### 9.1 日常巡检项（建议每日/每周）

| 频率 | 检查项 | 命令/入口 | 正常判据 |
|------|--------|-----------|----------|
| 每日 | 服务健康 | `curl -s http://127.0.0.1:8500/api/v1/health` | `status=ok`、`db.ok=true` |
| 每日 | 备份是否成功 | 查看 `deploy/backup.sh` 日志 / 内置 `/api/v1/admin/backup/status` | 当日有产物、`integrity_check=ok` |
| 每日 | 磁盘空间 | `df -h` / `Get-PSDrive` | 剩余 ≥ 30% |
| 每周 | 授权状态 | `/api/v1/admin/license/status` | 未过期、座位未超 |
| 每周 | 审计导出归档 | `/api/v1/admin/audit-logs/export?format=csv` | 可导出、条数合理、IP 为真实客户端 |
| 每周 | 日志错误 | 日志目录（§0.3） | 无持续报错 |
| 每月 | 离线副本 | 检查异地介质 | 副本存在且可读 |

### 9.2 常见故障定位

| 现象 | 可能原因 | 定位与处理 |
|------|----------|------------|
| 服务起不来 | `QC_API_SECRET` 未设（非回环监听会 `SystemExit`）；工作目录不对导致模块导入失败；密钥文件为空 | 看服务日志首屏；确认工作目录=项目根；设置 `QC_API_SECRET` |
| 端口被占用 | 8500 已被占用（残留进程/第二个实例） | `netstat -ano \| findstr 8500` / `ss -lntp \| grep 8500`；结束占用进程或改 `QC_PORT`（同时改反代） |
| 数据库被锁 | 多实例并发写、长事务、共享盘上跑 SQLite | 确认只有一个服务实例；不要把 `qc.db` 放共享盘；核对 WAL 与 `busy_timeout`（§10） |
| OCR 模型缺失 | `assets/ocr_models/*.onnx` 未随包/被 LFS 指针替换 | 按**字节数**核对（`2432880 / 10690752 / 585532`），不能只看文件名 |
| 授权到期 | 试用 90 天到期 / 浮动座位已满 | 输入激活码；`/api/v1/admin/license/status` 看座位；清理过期心跳 |
| 医生端审计 IP 全是反代 IP | 未启用 uvicorn 代理头 | 加 `--proxy-headers --forwarded-allow-ips`；见 §2.4 |
| 上传报 413 | 反代体积小于应用上限 | 反代调到 25MB（应用上限 20MB） |

### 9.3 日志位置与诊断包导出

**日志位置**（`log_user_dir()`）：

| 平台 | 路径 |
|------|------|
| Windows | `%LOCALAPPDATA%\星衍放射质控软件\logs\app.log` |
| Linux | `~/.local/share/星衍放射质控软件/logs/app.log` |
| macOS | `~/Library/Application Support/星衍放射质控软件/logs/app.log` |

**诊断包导出（真实命令）**：

```bash
# 生成诊断包（日志 + 系统信息 + 授权状态），默认**不含**患者数据
python src/log_utils.py --dest ~/Desktop

# 确需连同 feedback.db（内含报告正文）一起打包时，须显式声明并走隐私审批
python src/log_utils.py --dest ~/Desktop --include-patient-data
```

> ⚠️ **不要写 `python -m src.log_utils`**：`src/` **不是包**（无 `__init__.py`），该形式会失败。
> 正确写法就是上面的文件路径形式。包内会附 `README_诊断包.txt` 说明所含内容与隐私提示。

### 9.4 审计日志交付给医务科

现状**没有**针对医务科的现成报表。可用的做法：

1. 用导出端点产出 CSV/JSON：
   ```bash
   curl "http://127.0.0.1:8500/api/v1/admin/audit-logs/export?format=csv&start=2026-09-01T00:00:00" \
        -o audit_$(date +%Y%m%d).csv
   ```
2. 多机场景逐台导出后合并（按 `ts` 排序）。
3. 用表格工具做成"月度操作汇总"（登录、改密、角色变更、规则保存、备份恢复等）。

> **G6 改进建议**：向研发提需求，增加"医务科月报"一键导出（固定字段、固定周期、含导出人与时间签名）。

---

## 10. 多机部署注意（SQLite 并发限制与换库阈值）

### 10.1 当前数据层行为

`server/db.py` 对 SQLite 已做三项加固：

| 机制 | 值 | 作用 |
|------|----|------|
| `PRAGMA journal_mode=WAL` | 库级持久 | 读不阻塞写，桌面壳线程与 uvicorn 并发不再互锁 |
| `BEGIN IMMEDIATE` | 所有事务以写锁启动 | 把"判空→插入"等复合操作串行化，避免并发竞态 |
| `PRAGMA busy_timeout` | **30000ms（30s）** | 写锁最多等 30s 再报 `database is locked` |

> SQLite **只有一个写者**：写请求串行。WAL 只是让"读"不被写阻塞，并不能让"写"并行。

### 10.2 必须注意的部署约束

- **一个库只能有一个服务实例写入**。不要把同一 `qc.db` 放在共享盘上让多台机器同时跑服务——跨主机的文件锁不可靠，极易损坏。
- 多机形态应统一为"**1 个服务端进程 + N 个浏览器客户端**"，而不是"N 个服务进程共享一个库文件"。
- 浮动授权的共享目录里放的是**心跳 JSON**（约 100 字节/台，30 分钟过期），**不是**数据库文件。
- 定期 `VACUUM` 可回收空间，但需在低峰期做，且会短暂持有写锁。

### 10.3 何时必须换 PostgreSQL（判断阈值建议）

| 信号 | 阈值建议 | 说明 |
|------|----------|------|
| 数据库文件大小 | **> 2–4 GB** | 备份/恢复窗口显著变长，写放大明显 |
| 并发在线医生数 | **> 10 人**同时操作 | 写请求排队，偶发等锁 |
| 出现 `database is locked` | **每周 ≥ 1 次**（非人为/非备份引起） | 说明写入已超 SQLite 承受范围 |
| 日均写入量 | **> 数万次事务/天** | 多为高频队列写入/推送入库 |
| 单次查询延迟 | P95 **> 1s** | 大表扫描开始拖慢体验 |
| 需要多服务实例/高可用 | 出现该需求即换 | SQLite 无法安全多实例写入 |

**换库路径**：`server/db.py` 已支持 `DATABASE_URL`；配置 PostgreSQL 连接串即可切换。
迁移步骤（**需研发支持并提供迁移脚本**）：

1. 在 PG 建库（`UTF8`，字符集/排序规则按院内规范）；
2. 用 SQLAlchemy `create_all` 建表；
3. 停写窗口内用数据迁移脚本把 SQLite 数据导入 PG；
4. 校验行数与关键业务数据一致；
5. 切 `DATABASE_URL`，保留 SQLite 只读副本一段时间作为回退；
6. 更新 `ARCHITECTURE.md` / `DEPLOYMENT.md` 部署说明。

> ⚠️ 注意：`feedback.db` 是**独立库且刻意与 `qc.db` 分离**（`src/badcase_store.py` 用裸 `sqlite3`），
> 换 PostgreSQL 时需**单独**规划其落位（迁移或保留为本地库）。

> **需院内信息科确认**：是否已有 PostgreSQL 实例可复用、备份是否纳入院内统一数据库备份、版本与字符集要求。

---

## 附录 A. 待确认事项汇总（需院内信息科 / 测评机构 / 研发确认）

| # | 事项 | 归属 | 影响 |
|---|------|------|------|
| 1 | 是否需等保备案/测评，等级与范围 | 信息科/测评机构 | 决定加固深度与文档要求 |
| 2 | 证书类型与签发方式、域名规划 | 信息科 | §2 |
| 3 | AD/LDAP 是否可用、服务账号与 LDAPS 证书 | 信息科 | §3 路线 B |
| 4 | 备份加密方式与密钥托管、离线介质责任人 | 信息科 | §5 |
| 5 | 打包 exe 是否支持无头服务模式 | 研发 | §4（决定服务化方式） |
| 6 | 各版本数据库变更是否可逆 | 研发 | §8 |
| 7 | 离线更新目录的写入权限与签名验签要求 | 信息科/研发 | §6 |
| 8 | RPO/RTO 与运维 SLA 的具体数值 | 科室/信息科 | §5.5 |
| 9 | 文档端口口径统一（`DEPLOYMENT.md` 的 8500 → 8500） | 研发 | §0.1 |

## 附录 B. 示例文件索引

| 文件 | 用途 |
|------|------|
| `deploy/nginx-report-qc.conf` | nginx HTTPS 反代（内网限制 + 真实 IP 透传） |
| `deploy/winsw-report-qc.xml` | Windows 服务（WinSW）模板 |
| `deploy/systemd-report-qc.service` | Linux systemd 服务模板 |
| `deploy/backup.sh` | 备份 + 校验 + 可选加密 + GFS 保留策略 |
| `deploy/README.md` | 各文件用途与使用步骤 |

> 示例配置中的私钥、密码、网段、域名、路径均为**占位符**，投产前必须替换，且**不得**把真实密钥提交到代码仓库。
