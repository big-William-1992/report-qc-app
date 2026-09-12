# 星衍放射质控软件 · 科室多机部署手册

> 版本：v4.3.5+ | 2026-09-12
> 适用：放射科 2-10 台工作站部署，支持单机浮动授权、离线更新、自动备份、集中运维

---

## 目录

1. [部署架构](#1-部署架构)
2. [单机部署（推荐起步）](#2-单机部署推荐起步)
3. [多机部署（浮动授权）](#3-多机部署浮动授权)
4. [离线更新通道](#4-离线更新通道)
5. [自动备份与恢复](#5-自动备份与恢复)
6. [集中审计日志](#6-集中审计日志)
7. [健康检查与监控](#7-健康检查与监控)
8. [配置参考表](#8-配置参考表)
9. [故障排查](#9-故障排查)

---

## 1. 部署架构

### 单机模式（1 台工作站）

```
[放射科工作站] ── 独立运行 ── SQLite 本地库 ── 单机授权
```

- 零外部依赖，离线运行
- 本地 SQLite 存储所有数据
- 单机激活码绑定硬件指纹

### 浮动授权模式（2-10 台工作站）

```
[RIS/科室网关] ── PostgreSQL ── [授权主机]
      │                            │
      ├── [工作站 A] ───────────────┤
      ├── [工作站 B] ───────────────┤
      ├── [工作站 C] ───────────────┤
      └── [工作站 N] ───────────────┘
```

- PostgreSQL 集中存储，多机共享数据
- 一个激活码覆盖整个科室，按座位数计费
- 心跳共享目录控制并发

---

## 2. 单机部署（推荐起步）

### macOS

```bash
# 1. 下载并解压
tar xzf report-qc-macos.tar.gz
cd report-qc-app

# 2. 启动
./启动星衍质控软件.command

# 3. 浏览器自动打开 http://localhost:8377
```

### Windows

```bash
# 1. 下载并解压
# 双击解压 report-qc-portable.zip
cd report-qc-app

# 2. 启动
.\报告质控软件.exe

# 3. 浏览器自动打开 http://localhost:8377
```

### 首次启动检查清单

- [ ] 端口 8377 未被占用（`lsof -i :8377` / `netstat -ano | findstr 8377`）
- [ ] 试用期 90 天内可正常使用
- [ ] 管理员账号已创建（自助注册，强制 doctor 角色）
- [ ] 自动备份已启动（`/api/v1/admin/backup/status`）

---

## 3. 多机部署（浮动授权）

### 3.1 准备 PostgreSQL 数据库

```sql
-- 在 PostgreSQL 服务器创建数据库
CREATE DATABASE xingyan_qc
    WITH ENCODING = 'UTF8'
    LC_COLLATE = 'zh_CN.UTF-8'
    LC_CTYPE = 'zh_CN.UTF-8';

CREATE USER xingyan_qc WITH PASSWORD '强密码';
GRANT ALL PRIVILEGES ON DATABASE xingyan_qc TO xingyan_qc;
```

### 3.2 配置环境变量

每台工作站需设置（macOS/Linux）：

```bash
# ~/.bashrc 或 ~/.zshrc
export DATABASE_URL="postgresql://xingyan_qc:强密码@pg-server:5432/xingyan_qc"
export QC_API_SECRET="随机生成的长字符串"  # 非本机监听必须设置
export QC_FLOATING_LICENSE=true
export QC_FLOATING_SEATS=5    # 科室最大并发座位数
export QC_FLOATING_DEPT_ID="我的科室ID"   # 激活码对应的部门标识
export QC_FLOATING_HEARTBEAT_DIR="/Volumes/Shared/xc-heartbeat"  # 共享目录
```

Windows 环境（系统属性 → 环境变量）：

```
DATABASE_URL    = postgresql://xingyan_qc:强密码@pg-server:5432/xingyan_qc
QC_API_SECRET   = 随机生成的长字符串
QC_FLOATING_LICENSE = true
QC_FLOATING_SEATS   = 5
QC_FLOATING_DEPT_ID = 我的科室ID
QC_FLOATING_HEARTBEAT_DIR = \\server\shared\heartbeat
```

### 3.3 生成浮动授权激活码

```bash
# 开发者在发卡机上运行
python gen_activation_code.py --department "我的科室ID" --seats 5

# 输出激活码，部署到每台工作站的激活对话框
```

### 3.4 共享目录说明

心跳目录（`QC_FLOATING_HEARTBEAT_DIR`）需要：

| 特性 | 要求 |
|------|------|
| 协议 | NFS / SMB / 共享盘（所有工作站可读可写） |
| 权限 | 755（所有工作站可读写） |
| 容量 | 极小（每台机器一个 JSON 文件，约 100 字节） |
| 过期 | 心跳文件 30 分钟无更新自动失效 |

### 3.5 浮动授权行为说明

- **首次激活**：在一台机器输入激活码，检查座位数 → 写心跳文件
- **持续运行**：每次 API 请求检查时刷新心跳
- **机器离线**：30 分钟后心跳过期，座位自动释放
- **座位已满**：新机器激活时返回"座位已满"，已运行机器不受影响
- **信任模式**：未设置心跳目录时，仅验证激活码签名，不检查座位数

---

## 4. 离线更新通道

适用于医院内网、无法访问 GitHub 的场景。

### 4.1 部署方式

```
[更新分发主机]
  ├── latest.tar.gz          (macOS 更新包)
  ├── latest.zip             (Windows 更新包)
  ├── latest.tar.gz.sha256   (可选：完整性校验)
  └── latest.zip.sha256      (可选)

[RIS 网络共享]
  └── /shared/updates/       (指向上述目录)
```

### 4.2 工作站配置

```bash
# 设置本地更新目录路径
export QC_UPDATE_LOCAL_DIR="/Volumes/Shared/updates"
# Windows:
# QC_UPDATE_LOCAL_DIR = \\server\shared\updates
```

### 4.3 更新流程

1. **分发主机**：将新版本更新包放入共享目录
2. **工作站**：访问 `/api/v1/update/check` → 返回本地更新包信息
3. **工作站**：访问 `/api/v1/update/download` → 复制更新包到缓存
4. **工作站**：主程序退出 → 安装脚本自动替换文件 → 重新启动

### 4.4 完整性校验

```bash
# 分发主机：生成校验文件
sha256sum latest.tar.gz > latest.tar.gz.sha256
sha256sum latest.zip > latest.zip.sha256
```

工作站自动校验，不匹配则拒绝安装。

---

## 5. 自动备份与恢复

### 5.1 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `QC_BACKUP_ENABLED` | `true` | 是否启用自动备份 |
| `QC_BACKUP_INTERVAL_DAYS` | `1` | 备份间隔（天） |
| `QC_BACKUP_KEEP_DAYS` | `7,30,90` | 保留策略（三级轮转） |
| `QC_BACKUP_DIR` | `~/.local/share/xingyan_qc/backups` | 备份目录 |

### 5.2 备份内容

- **数据库**：samples.db、qc.db、accounts.db（SQLite VACUUM INTO，不锁库）
- **配置**：license.dat、rules_config.json、ris_config.json

### 5.3 API 操作

```bash
# 查看备份状态
curl http://localhost:8377/api/v1/admin/backup/status

# 手动触发备份
curl -X POST http://localhost:8377/api/v1/admin/backup/run

# 查看备份列表并恢复
curl http://localhost:8377/api/v1/admin/backup/status | jq '.backup_files'

# 恢复指定备份（需登录管理员）
curl -X POST -d '{"name":"samples.db.20260912_103000"}' \
  http://localhost:8377/api/v1/admin/backup/restore
```

### 5.4 集中备份策略（多机）

```bash
# 将备份目录设为共享盘路径，自动同步到服务器
export QC_BACKUP_DIR="/Volumes/RIS_Backup/xingyan_qc"
```

---

## 6. 集中审计日志

### 6.1 导出审计日志

```bash
# JSON 格式导出（全量）
curl "http://localhost:8377/api/v1/admin/audit-logs/export?format=json" \
  -o audit_$(date +%Y%m%d).json

# CSV 格式导出（带筛选）
curl "http://localhost:8377/api/v1/admin/audit-logs/export?format=csv&action=login_success&start=2026-09-01T00:00:00" \
  -o audit_login_$(date +%Y%m%d).csv
```

### 6.2 多机合并归档

```bash
# 各工作站分别导出 → 上传到中心服务器
for host in ws1 ws2 ws3 ws4 ws5; do
  curl "http://${host}:8377/api/v1/admin/audit-logs/export?format=json" \
    -o "audit_${host}_$(date +%Y%m%d).json"
done

# 合并为单一文件（按时间排序）
jq -s 'sort_by(.ts) | .items[]' audit_*.json > audit_merged.json
```

### 6.3 审计日志字段说明

| 字段 | 说明 |
|------|------|
| `id` | 唯一 ID |
| `ts` | 操作时间（ISO 格式） |
| `emp_id` | 操作工号 |
| `action` | 操作类型（login_success, rules_config_saved, …） |
| `detail` | 详情 JSON（密码变更、角色变更等） |
| `ip` | 来源 IP |

---

## 7. 健康检查与监控

### 7.1 健康检查端点

```bash
curl http://localhost:8377/api/v1/health
```

返回结构：

```json
{
  "status": "ok",
  "version": "4.3.5",
  "db": {"ok": true, "path": "..."},
  "disk": {"total": "500GB", "free": "320GB", "percent": 64},
  "active_sessions": {"login_fail_count": 0},
  "license": {"status": "activated", "data": ""},
  "init_warning": ""
}
```

### 7.2 监控脚本（可选）

```bash
# /usr/local/bin/qc-healthcheck.sh
#!/bin/bash
URL="http://localhost:8377/api/v1/health"
RESP=$(curl -s --max-time 5 "$URL")
STATUS=$(echo "$RESP" | jq -r '.status')
if [ "$STATUS" != "ok" ]; then
  echo "[ALERT] QC health check failed: $STATUS" | logger -t xingyan-qc
  # 可选：触发短信/邮件告警
fi
```

配合 crontab：
```
*/5 * * * * /usr/local/bin/qc-healthcheck.sh
```

### 7.3 授权状态查询

```bash
curl http://localhost:8377/api/v1/admin/license/status
```

返回浮动授权座位使用情况。

---

## 8. 配置参考表

### 核心配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DATABASE_URL` | (空) | PostgreSQL 连接串，空则使用 SQLite |
| `QC_API_SECRET` | (自动生成) | 非本机监听必须显式设置 |
| `PORT` | `8377` | 服务监听端口 |

### 授权配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `QC_FLOATING_LICENSE` | `false` | 启用浮动授权模式 |
| `QC_FLOATING_SEATS` | `5` | 最大并发座位数 |
| `QC_FLOATING_DEPT_ID` | (从 license.dat 读) | 部门标识 |
| `QC_FLOATING_HEARTBEAT_DIR` | (空) | 共享目录（空则信任模式） |

### 更新配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `QC_UPDATE_LOCAL_DIR` | (空) | 离线更新目录（空则在线更新） |

### 备份配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `QC_BACKUP_ENABLED` | `true` | 启用自动备份 |
| `QC_BACKUP_INTERVAL_DAYS` | `1` | 备份间隔天数 |
| `QC_BACKUP_KEEP_DAYS` | `7,30,90` | 保留策略 |
| `QC_BACKUP_DIR` | `~/.local/share/xingyan_qc/backups` | 备份目录 |

---

## 9. 故障排查

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 端口占用 | 8377 已被使用 | 修改 PORT 或 kill 占用进程 |
| 数据库连接失败 | DATABASE_URL 配置错误 | 检查 pg 服务器地址/端口/密码 |
| 授权过期 | 试用期 90 天用完 | 输入激活码激活 |
| 浮动授权座位已满 | 共享目录心跳文件过多 | 清理过期心跳或删除不用的 workstation |
| 离线更新包找不到 | QC_UPDATE_LOCAL_DIR 路径错误 | 确认目录中有 latest.zip / latest.tar.gz |
| 自动备份未执行 | QC_BACKUP_ENABLED=false | 检查环境变量 |
| 日志文件为空 | 路径权限问题 | 检查 ~/.local/share/xingyan_qc/logs/ 可写 |

### 诊断包导出

```bash
# 生成诊断包（包含日志 + 系统信息 + 授权状态）
python -m src.log_utils   # 或直接调用 API
```

### 日志位置

| 平台 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/星衍放射质控软件/logs/app.log` |
| Windows | `%LOCALAPPDATA%\星衍放射质控软件\logs\app.log` |
| Linux | `~/.local/share/星衍放射质控软件/logs/app.log` |

### 紧急恢复

```bash
# 1. 停止服务
kill <pid>   # macOS/Linux
taskkill /PID <pid> /F   # Windows

# 2. 从备份恢复数据库
cp ~/.local/share/xingyan_qc/backups/qc.db.20260912_* assets/qc.db

# 3. 重新启动
```
