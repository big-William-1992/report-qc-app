# deploy/ —— 交付硬化示例配置

本目录是 `docs/DELIVERY_HARDENING.md`（院内交付硬化方案）的配套模板，
**面向真实投产**：复制出去、替换占位符即可使用。

> ⚠️ 所有文件中的私钥、密码、网段、域名、路径均为**占位符**，投产前必须替换。
> **切勿**把真实密钥/口令提交进代码仓库。

## 文件清单与用途

| 文件 | 用途 | 目标位置（示例） |
|------|------|------------------|
| `nginx-report-qc.conf` | nginx HTTPS 反向代理：TLS、HSTS、内网网段限制、上传体积、超时、真实 IP 透传、隐藏版本号 | `/etc/nginx/conf.d/report-qc.conf` |
| `winsw-report-qc.xml` | Windows 服务模板（WinSW）：开机自启、崩溃重启、日志滚动、专用服务账号 | 与 `report-qc-service.exe` 同目录，同名 |
| `systemd-report-qc.service` | Linux systemd 服务模板：专用低权限账号、`EnvironmentFile` 注入密钥、自动重启、基础加固 | `/etc/systemd/system/report-qc.service` |
| `backup.sh` | 备份 `qc.db`/`feedback.db`/配置/授权 + 完整性校验 + 可选加密 + GFS 保留策略 | `/opt/report-qc/deploy/backup.sh`（或任意受控路径） |
| `README.md` | 本文件 | —— |

## 通用前置（三个模板都依赖）

1. **服务端按"源码 + venv"部署**（`winsw` / `systemd` 模板假定如此）：
   ```
   /opt/report-qc/app/            # 项目根（server/ 与 src/ 的上一级）
   /opt/report-qc/app/.venv/      # Python 虚拟环境
   ```
   打包版 exe 为 pywebview 桌面壳，默认只绑 `127.0.0.1` 并弹窗；**其服务化方式需向研发确认**。

2. **端口基线为 8500**（服务端读 `QC_HOST` / `QC_PORT`）。**不要写裸 `PORT`**——服务端不读它。

3. **非本机监听必须设置 `QC_API_SECRET`**，否则应用启动即退出；多节点须保持一致。

4. 反代与应用**同机**时，务必给 uvicorn 加 `--proxy-headers --forwarded-allow-ips 127.0.0.1`。
   否则应用会把所有访问者当成 `127.0.0.1`，命中"本机放行"逻辑 → **鉴权可被绕过**，且审计 IP 全错。
   （原理见 `docs/DELIVERY_HARDENING.md` §2.4）

---

## 1. nginx 反代

```bash
sudo cp nginx-report-qc.conf /etc/nginx/conf.d/report-qc.conf
sudo vi /etc/nginx/conf.d/report-qc.conf     # 替换：域名、证书路径、院内网段
sudo nginx -t && sudo systemctl reload nginx
```

检查点：
- `server_name`、`ssl_certificate`、`ssl_certificate_key` 已替换；
- `allow ...; deny all;` 改为院内真实网段；
- 应用按 §「通用前置」第 4 条启动了代理头信任；
- `client_max_body_size 25m` ≥ 应用上限 20MB。

IIS（ARR + URL Rewrite）版本的关键片段见 `docs/DELIVERY_HARDENING.md` §2.2.2。

## 2. Windows 服务（WinSW）

```powershell
# 1) 放置：把 WinSW 重命名为 report-qc-service.exe，与本 xml 放同一目录
# 2) 本文件重命名为 report-qc-service.xml（必须与 exe 同名，仅扩展名不同）
# 3) 修改 xml 中的路径 / 环境变量 / 服务账号（占位符见文件内注释）
# 4) 管理员 PowerShell：
.\report-qc-service.exe install
.\report-qc-service.exe start
.\report-qc-service.exe status
# 卸载：
.\report-qc-service.exe stop
.\report-qc-service.exe uninstall
```

用 NSSM 的等价命令见 `docs/DELIVERY_HARDENING.md` §4.1。

## 3. Linux 服务（systemd）

```bash
# 1) 专用低权限账号
sudo useradd --system --create-home --home-dir /opt/report-qc --shell /usr/sbin/nologin svc-reportqc
sudo chown -R svc-reportqc:svc-reportqc /opt/report-qc

# 2) 密钥文件（0600，内容不入库）
sudo install -d -m 700 /etc/report-qc
sudo tee /etc/report-qc/env >/dev/null <<'EOF'
QC_API_SECRET=<强随机串，64+ 字符>
PUSH_API_KEY=<强随机串，启用推送时填写>
QC_CORS_ORIGINS=https://qc.example.hospital.local
EOF
sudo chmod 600 /etc/report-qc/env

# 3) 安装 unit
sudo cp systemd-report-qc.service /etc/systemd/system/report-qc.service
sudo vi /etc/systemd/system/report-qc.service   # 核对路径/账号/环境变量
sudo systemctl daemon-reload
sudo systemctl enable --now report-qc
systemctl status report-qc
journalctl -u report-qc -f
```

## 4. 备份脚本（backup.sh）

```bash
chmod +x backup.sh
# 先预演，确认会备份哪些文件（不写盘）
./backup.sh --dry-run

# 正式执行（默认不加密）
./backup.sh

# 启用 age 公钥加密（推荐）
QC_BACKUP_ENCRYPT=age \
QC_BACKUP_AGE_RECIPIENT='age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' \
./backup.sh

# 只校验最近一次备份（恢复演练第一步）
./backup.sh --verify-only
```

**脚本内的配置区**（改这几项即可直接用）：

| 变量 | 含义 | 默认 |
|------|------|------|
| `QC_BACKUP_ROOT` | 备份根目录 | `/var/backups/report-qc` |
| `QC_APPDATA` | 应用数据目录（须与服务实际一致） | 按平台默认 |
| `APP_ROOT` | 源码/安装根（找 `assets/qc.db`、`license.dat`） | 脚本上级目录 |
| `QC_BACKUP_KEEP_DAILY/WEEKLY/MONTHLY` | GFS 保留份数 | `14 / 8 / 12` |
| `QC_BACKUP_ENCRYPT` | `none` / `age` / `gpg` / `7z` | `none` |
| `QC_BACKUP_AGE_RECIPIENT` / `QC_BACKUP_GPG_RECIPIENT` / `QC_BACKUP_7Z_PASSWORD_FILE` | 加密收件人/口令文件 | 空 |

crontab 示例：

```cron
# 每天 02:30 备份，输出留痕
30 2 * * * /opt/report-qc/deploy/backup.sh >> /var/log/report-qc-backup.log 2>&1

# 每周一 03:30 校验最近一次备份（演练）
30 3 * * 1 /opt/report-qc/deploy/backup.sh --verify-only >> /var/log/report-qc-backup.log 2>&1
```

> 脚本依赖 `sqlite3` 命令行（`VACUUM INTO` + `PRAGMA integrity_check`）；加密按所选方式需 `age` / `gpg` / `7z`。
> **备份含报告正文，务必落在加密盘/受控介质并定期做恢复演练**（步骤见方案 §5.4）。

## 5. 上线前

请对照 `docs/DELIVERY_HARDENING.md` §7 的检查清单逐条勾选，尤其是：
TLS、内网限制、`QC_API_SECRET`、备份**包含 `feedback.db`**、恢复演练、审计真实 IP、时间同步。
