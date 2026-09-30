#!/usr/bin/env bash
# =============================================================================
# 星衍放射质控软件 —— 备份 / 校验 / 可选加密 / 保留策略 脚本
# =============================================================================
# 目标：把「内置自动备份」之外的**可加密、可异地、可演练**的备份补齐（见交付硬化方案 §5）。
#
# 备份范围（与项目真实路径一致，见 src/paths.py / src/backup.py）：
#   - qc.db          账号/科室/样本/队列/设置/订单/审计日志（SQLite，VACUUM INTO 在线备份）
#   - feedback.db    医生反馈（**含报告正文**；内置备份不含它，本脚本补上）
#   - rules_config.json / ocr_config.json / ris_config.json   规则/OCR 区域/RIS 配置
#   - license.dat    离线授权
#
# 用法：
#   ./backup.sh                  # 正常执行一次备份
#   ./backup.sh --dry-run        # 只打印将备份什么，不落盘
#   ./backup.sh --verify-only    # 只对最近一次备份做结构/清单校验（演练用）
#   QC_BACKUP_ENCRYPT=age QC_BACKUP_AGE_RECIPIENT=age1... ./backup.sh
#
# 建议 crontab（每天 02:30，输出留痕）：
#   30 2 * * * /opt/report-qc/deploy/backup.sh >> /var/log/report-qc-backup.log 2>&1
#
# ⚠️ 未加密的备份包含**完整报告正文与账号数据**，属敏感数据：
#    必须落到受控介质（加密盘/受控共享目录），并按院内隐私与数据分级要求审批。
#
# 依赖：sqlite3（必需）；加密时另需 age / gpg / 7z（按所选方式）。
# 兼容：Linux / macOS / git-bash（不使用 GNU find -printf 等专属语法）。
# =============================================================================

set -Eeuo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# 配置区：按院内实际改这里即可（也可用同名环境变量覆盖）
# ---------------------------------------------------------------------------

# 备份根目录（建议：独立数据盘或受控共享目录；异地副本由同步任务负责搬运）
BACKUP_ROOT="${QC_BACKUP_ROOT:-/var/backups/report-qc}"

# 应用数据目录（默认与 src/paths.py::user_data_dir() 一致）；
# 若服务用 QC_APPDATA 覆盖过，这里必须填同一个值，或用同名环境变量传入。
DATA_DIR="${QC_APPDATA:-}"

# 源码/安装根目录：默认取本脚本上级目录（deploy/ 的上一级），用于找 assets/qc.db、license.dat
APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# 需要 VACUUM INTO 备份的 SQLite 库（按名称在数据目录中查找）
DB_NAMES=(qc.db feedback.db)

# 需要原样复制的配置文件（按名称在数据目录/安装目录中查找）
CONFIG_NAMES=(rules_config.json ocr_config.json ris_config.json license.dat)

# 保留策略（GFS 三级）：daily 保留最近 N 份；weekly/monthly 各保留 N 份
KEEP_DAILY="${QC_BACKUP_KEEP_DAILY:-14}"
KEEP_WEEKLY="${QC_BACKUP_KEEP_WEEKLY:-8}"
KEEP_MONTHLY="${QC_BACKUP_KEEP_MONTHLY:-12}"

# 加密方式：none | age | gpg | 7z
#   - age/gpg：公钥加密，**不需要在本机保存解密口令**，推荐
#   - 7z：对称口令，简单但口令要托管；且口令会短暂出现在进程列表，安全性最低
ENCRYPT_MODE="${QC_BACKUP_ENCRYPT:-none}"

# age 收件人公钥（age -r 的值，形如 age1...）
AGE_RECIPIENT="${QC_BACKUP_AGE_RECIPIENT:-}"
# GPG 收件人（密钥 ID / 邮箱），需事先 import 公钥
GPG_RECIPIENT="${QC_BACKUP_GPG_RECIPIENT:-}"
# 7z 口令文件（**不要**把口令直接写进本脚本；文件权限 0600）
SEVENZIP_PASSWORD_FILE="${QC_BACKUP_7Z_PASSWORD_FILE:-}"

# sqlite3 路径（不在 PATH 中时显式指定）
SQLITE3_BIN="${SQLITE3_BIN:-sqlite3}"

# ---------------------------------------------------------------------------
# 运行时
# ---------------------------------------------------------------------------
DRY_RUN=0
VERIFY_ONLY=0
TS="$(date +%Y%m%d_%H%M%S)"

# 工作区：明文数据只在 $WORK/payload 与 $WORK/*.tar 中短暂存在，退出时整体清理
WORK="$(mktemp -d "${TMPDIR:-/tmp}/report-qc-backup.XXXXXX")"
STAGE="$WORK/payload"
mkdir -p "$STAGE"
ARTIFACT=""

log()  { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%F %T')" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

cleanup() {
  # 清理明文暂存区（未加密数据不留在临时目录）
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

for arg in "$@"; do
  case "$arg" in
    --dry-run)     DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    -h|--help)     sed -n '2,45p' "$0"; exit 0 ;;
    *)             die "未知参数：${arg}（支持 --dry-run / --verify-only / --help）" ;;
  esac
done

# 解析数据目录（与 src/paths.py::user_data_dir 保持一致）
resolve_data_dir() {
  if [[ -n "$DATA_DIR" ]]; then
    [[ -d "$DATA_DIR" ]] || die "QC_APPDATA 指向的目录不存在：$DATA_DIR"
    printf '%s\n' "$DATA_DIR"; return
  fi
  case "$(uname -s)" in
    Darwin) printf '%s\n' "$HOME/Library/Application Support/MedicalReportQC" ;;
    Linux)  printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/MedicalReportQC" ;;
    MINGW*|MSYS*|CYGWIN*)
            : "${APPDATA:?请设置 APPDATA 或显式传入 QC_APPDATA}"
            printf '%s\n' "$(cygpath -u "$APPDATA" 2>/dev/null || printf '%s' "$APPDATA")/MedicalReportQC" ;;
    *)      printf '%s\n' "$HOME/.local/share/MedicalReportQC" ;;
  esac
}

# 在候选目录中定位文件，输出第一个存在的路径
locate_file() {
  local name="$1" d
  for d in "$(resolve_data_dir)" "$APP_ROOT/assets" "$APP_ROOT"; do
    if [[ -f "$d/$name" ]]; then printf '%s\n' "$d/$name"; return 0; fi
  done
  return 1
}

# VACUUM INTO 在线备份 + 完整性校验（不锁库、WAL 安全）
sqlite_backup() {
  local src="$1" dest="$2"
  command -v "$SQLITE3_BIN" >/dev/null 2>&1 \
    || die "未找到 sqlite3：请安装（或设置 SQLITE3_BIN=/path/to/sqlite3）"
  [[ -e "$dest" ]] && die "目标已存在，拒绝覆盖：$dest"
  # VACUUM INTO 要求目标文件不存在；它读取一致性快照，不会长时间锁库
  "$SQLITE3_BIN" "$src" "VACUUM INTO '$dest'" || die "VACUUM INTO 失败：$src"
  local ic
  ic="$("$SQLITE3_BIN" "$dest" 'PRAGMA integrity_check;' || true)"
  [[ "$ic" == "ok" ]] || die "备份库完整性校验失败（${dest}）：${ic}"
  log "  ✓ $(basename "$src") → $(basename "$dest")（integrity_check=ok，$(du -h "$dest" | cut -f1)）"
}

copy_config() {
  local src="$1" dest_dir="$2"
  cp -p "$src" "$dest_dir/" && log "  ✓ $(basename "$src")（配置）"
}

encrypt_artifact() {
  local plain="$1" out="$2"
  case "$ENCRYPT_MODE" in
    none)
      cp -p "$plain" "$out"; return ;;
    age)
      command -v age >/dev/null 2>&1 || die "未安装 age（或改用 QC_BACKUP_ENCRYPT=none）"
      [[ -n "$AGE_RECIPIENT" ]] || die "age 加密需要 QC_BACKUP_AGE_RECIPIENT（公钥）"
      age -r "$AGE_RECIPIENT" -o "$out" "$plain"
      log "  ✓ age 加密完成（公钥前缀：${AGE_RECIPIENT:0:12}...）" ;;
    gpg)
      command -v gpg >/dev/null 2>&1 || die "未安装 gpg（或改用 QC_BACKUP_ENCRYPT=none）"
      [[ -n "$GPG_RECIPIENT" ]] || die "gpg 加密需要 QC_BACKUP_GPG_RECIPIENT"
      gpg --batch --yes --trust-model always -e -r "$GPG_RECIPIENT" -o "$out" "$plain"
      log "  ✓ gpg 加密完成（收件人：${GPG_RECIPIENT}）" ;;
    7z)
      command -v 7z >/dev/null 2>&1 || die "未安装 7z（或改用 QC_BACKUP_ENCRYPT=none）"
      [[ -n "$SEVENZIP_PASSWORD_FILE" && -f "$SEVENZIP_PASSWORD_FILE" ]] \
        || die "7z 加密需要 QC_BACKUP_7Z_PASSWORD_FILE（0600 权限的口令文件）"
      # 注意：口令会短暂出现在进程列表中；高安全要求请改用 age/gpg
      7z a -t7z -mhe=on -p"$(<"$SEVENZIP_PASSWORD_FILE")" "$out" "$plain" >/dev/null
      log "  ✓ 7z 加密完成（口令来自文件）" ;;
    *) die "未知 QC_BACKUP_ENCRYPT=${ENCRYPT_MODE}（可选 none/age/gpg/7z）" ;;
  esac
}

# GFS 保留：只统计备份**产物**（report-qc-*），按修改时间从新到旧，保留最近 keep 个
# 产物被清理时，其同时间戳的 SHA256SUMS.<stamp> 一并删除，避免留下无主清单
prune_keep() {
  local dir="$1" keep="$2" label="$3"
  [[ -d "$dir" ]] || return 0
  local i=0 f st
  # ls -1t：按 mtime 从新到旧（POSIX 可用，避免 GNU find -printf）
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    i=$(( i + 1 ))
    if (( i > keep )); then
      st="$(stamp_of "$f")"
      rm -f -- "$f" && log "  - 清理旧备份[$label]：$(basename "$f")"
      [[ -n "$st" ]] && rm -f -- "$dir/SHA256SUMS.${st}"
    fi
  done < <(ls -1t "$dir"/report-qc-* 2>/dev/null || true)
}

# 从产物文件名解析时间戳：report-qc-<YYYYmmdd_HHMMSS>.<ext...>
stamp_of() {
  basename "$1" | sed -E 's/^report-qc-([0-9]{8}_[0-9]{6})\..*$/\1/'
}

verify_artifact() {
  local file="$1" stamp
  [[ -f "$file" ]] || die "备份产物不存在：$file"
  stamp="$(stamp_of "$file")"
  log "校验备份：$file"
  case "$file" in
    *.tar)     tar -tf  "$file" >/dev/null && log "  ✓ tar 结构完整" ;;
    *.tar.gz)  tar -tzf "$file" >/dev/null && log "  ✓ tar.gz 结构完整" ;;
    *.age)     log "  · age 密文（内容校验需私钥，恢复演练时解密后再校验）" ;;
    *.gpg)     log "  · gpg 密文（内容校验需私钥，恢复演练时解密后再校验）" ;;
    *.7z)      log "  · 7z 密文（内容校验需口令，恢复演练时解密后再校验）" ;;
  esac
  # 产物级 SHA256 清单（文件名带同一时间戳）
  local sum="$BACKUP_ROOT/daily/SHA256SUMS.${stamp}"
  if [[ -f "$sum" ]]; then
    ( cd "$BACKUP_ROOT/daily" && sha256sum -c "SHA256SUMS.${stamp}" ) \
      && log "  ✓ 产物 SHA256 校验通过"
  else
    warn "未找到 ${sum}，跳过产物哈希复核"
  fi
}

# ---------------------------------------------------------------------------
# --verify-only：只校验最近一次备份（恢复演练第一步）
# ---------------------------------------------------------------------------
if (( VERIFY_ONLY )); then
  latest=""
  for d in daily weekly monthly; do
    f="$(ls -1t "$BACKUP_ROOT/$d"/report-qc-* 2>/dev/null | head -n1 || true)"
    if [[ -n "$f" ]]; then latest="$f"; break; fi
  done
  [[ -n "$latest" ]] || die "在 $BACKUP_ROOT 下未找到任何备份产物"
  verify_artifact "$latest"
  exit 0
fi

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
D="$(resolve_data_dir)"
log "备份开始：数据目录=$D  安装根=$APP_ROOT  备份根=$BACKUP_ROOT  加密=$ENCRYPT_MODE"
if (( DRY_RUN )); then log "（--dry-run：仅预演，不写入任何文件）"; fi

# 1) 落盘目录
if (( ! DRY_RUN )); then
  mkdir -p "$BACKUP_ROOT/daily" "$BACKUP_ROOT/weekly" "$BACKUP_ROOT/monthly"
  chmod 700 "$BACKUP_ROOT" 2>/dev/null || true
fi

# 2) 数据库：VACUUM INTO（一致性快照）
found_db=0
for name in "${DB_NAMES[@]}"; do
  if src="$(locate_file "$name")"; then
    found_db=1
    if (( DRY_RUN )); then log "  · 将备份 $name ← $src"; continue; fi
    sqlite_backup "$src" "$STAGE/$name"
  else
    # feedback.db 不存在属正常（尚无反馈数据）；qc.db 缺失则必须报错
    if [[ "$name" == "qc.db" ]]; then die "未找到 qc.db（检查 QC_APPDATA / APP_ROOT：${D}）"; fi
    warn "跳过不存在的库：$name"
  fi
done
(( found_db )) || die "没有可备份的数据库"

# 3) 配置文件
for name in "${CONFIG_NAMES[@]}"; do
  if src="$(locate_file "$name")"; then
    if (( DRY_RUN )); then log "  · 将备份 $name ← $src"; continue; fi
    copy_config "$src" "$STAGE"
  else
    warn "跳过不存在的配置文件：$name"
  fi
done

if (( DRY_RUN )); then log "预演结束（未写入）"; exit 0; fi

# 4) 清单：MANIFEST 记来源与时间；SHA256SUMS 记内容哈希（随包，供解包后核对）
{
  echo "application=星衍放射质控软件"
  echo "backup_time=$TS"
  echo "hostname=$(hostname 2>/dev/null || echo unknown)"
  echo "data_dir=$D"
  echo "encrypt_mode=$ENCRYPT_MODE"
  echo "--- 文件清单 ---"
  ( cd "$STAGE" && sha256sum ./* 2>/dev/null )
} > "$STAGE/MANIFEST.txt"
( cd "$STAGE" && sha256sum ./* > SHA256SUMS )

# 5) 打包（明文 tar 只存在于 WORK，随后按策略加密）
PLAIN_TAR="$WORK/report-qc-${TS}.tar"
tar -C "$STAGE" -cf "$PLAIN_TAR" .

case "$ENCRYPT_MODE" in
  none) ARTIFACT="$BACKUP_ROOT/daily/report-qc-${TS}.tar" ;;
  age)  ARTIFACT="$BACKUP_ROOT/daily/report-qc-${TS}.tar.age" ;;
  gpg)  ARTIFACT="$BACKUP_ROOT/daily/report-qc-${TS}.tar.gpg" ;;
  7z)   ARTIFACT="$BACKUP_ROOT/daily/report-qc-${TS}.tar.7z" ;;
esac
encrypt_artifact "$PLAIN_TAR" "$ARTIFACT"
[[ -f "$ARTIFACT" ]] || die "备份产物未生成：$ARTIFACT"
chmod 600 "$ARTIFACT" 2>/dev/null || true   # 备份是敏感数据：默认仅属主可读

# 6) 产物哈希清单 + 校验
( cd "$BACKUP_ROOT/daily" && sha256sum "$(basename "$ARTIFACT")" > "SHA256SUMS.${TS}" )
log "备份产物：${ARTIFACT}（$(du -h "$ARTIFACT" | cut -f1)）"
verify_artifact "$ARTIFACT"

# 7) GFS 分层（按周/按月另存一份，供更长期保留）
dow="$(date +%u)"; dom="$(date +%d)"
if [[ "$dow" == "7" ]]; then cp -p "$ARTIFACT" "$BACKUP_ROOT/weekly/" && log "已归档 weekly"; fi
if [[ "$dom" == "01" ]]; then cp -p "$ARTIFACT" "$BACKUP_ROOT/monthly/" && log "已归档 monthly"; fi

# 8) 保留策略
prune_keep "$BACKUP_ROOT/daily"   "$KEEP_DAILY"   daily
prune_keep "$BACKUP_ROOT/weekly"  "$KEEP_WEEKLY"  weekly
prune_keep "$BACKUP_ROOT/monthly" "$KEEP_MONTHLY" monthly

log "备份完成。若为异地备份，请确认同步/拷贝任务已把产物搬到离线介质，并定期做恢复演练。"
cat <<'EOF'

── 恢复（演练/应急）───────────────────────────────────────────────
1) 停止服务：            systemctl stop report-qc        # Windows: net stop ReportQC
2) 解密（按加密方式选一条）：
     age -d -i key.txt report-qc-<TS>.tar.age > report-qc-<TS>.tar
     gpg -d report-qc-<TS>.tar.gpg > report-qc-<TS>.tar
     7z x report-qc-<TS>.tar.7z        # 需口令
3) 校验清单：            mkdir -p ./restore && tar -xf report-qc-<TS>.tar -C ./restore \
                           && (cd ./restore && sha256sum -c SHA256SUMS)
4) 校验数据库：          sqlite3 ./restore/qc.db 'PRAGMA integrity_check;'   # 期望输出 ok
5) 写回（先备份现状）：   cp ./restore/qc.db "<数据目录>/qc.db"
                         cp ./restore/feedback.db "<数据目录>/feedback.db"
                         cp ./restore/license.dat "<安装根>/assets/license.dat"
6) 启动服务并验收：      systemctl start report-qc
     验收标准：/api/v1/health 返回 status=ok；能登录；样本数/审计条数与备份时点一致。
──────────────────────────────────────────────────────────────────
EOF
