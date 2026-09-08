# -*- coding: utf-8 -*-
"""星衍放射质控 — 数据库主动推送器（内网部署，无需改动 HIS/RIS 业务系统）

================================================================================
背景：质控软件支持「数据库主动推送」对接（POST /api/v1/push/report）。
本脚本作为中间推送器：定时查询医院报告库的只读视图，把新增/变更的报告
字段化推送到质控软件，替代「质控软件直连数据库轮询」旧模式。

特点：
  - 只读账号即可，脚本只执行 SELECT
  - 水位推进：按报告时间(或任意排序键)记住推到了哪一行，掉线不丢、重启不重推
  - 失败重试 3 次（指数退避），重试仍失败则中断本轮，下轮继续
  - 纯标准库 HTTP（urllib），无 requests 依赖；数据库驱动按库类型可选

用法：
  python3 tools/push_forwarder.py --config /etc/push_forwarder.json          # 常驻（默认每 60s）
  python3 tools/push_forwarder.py --config /etc/push_forwarder.json --once   # 只跑一轮（调试/任务计划）
  python3 tools/push_forwarder.py --config /etc/push_forwarder.json --dry-run  # 只打印不发送

配置模板（JSON）：
{
  "db_type": "sqlserver",            // sqlserver | oracle | mysql | postgresql
  "host": "192.168.1.100",
  "port": "",
  "database": "RIS_DB",
  "user": "readonly_user",
  "password": "******",
  "query": "SELECT TOP 200 patient_name AS patient, sex AS gender, age AS age, "
           "exam_part AS modality, apply_part AS applied_site, description AS findings_desc, "
           "diagnosis AS diagnosis, accession_no AS exam_id, "
           "CONVERT(varchar(19), report_time, 120) AS exam_date, report_time AS wm_ts "
           "FROM v_qc_push_report WHERE report_time > %WATERMARK% ORDER BY report_time",
  "watermark_col": "wm_ts",          // 排序/水位列；不需要水位可留空（每次都全量推）
  "push_url": "http://127.0.0.1:8500/api/v1/push/report",
  "api_key": "服务端PUSH_API_KEY的值",
  "source": "HIS推送",
  "state_file": ""                   // 水位持久化文件，留空默认 ~/.qc_push_forwarder_state.json
}

说明：
  - query 中 %WATERMARK% 占位符会被替换为上次水位（字符串字面量）。日期列若类型不匹配，
    请在视图/SELECT 里先转成 varchar/CHAR（如 SQL Server CONVERT(varchar(19),...,120)）。
  - 驱动：sqlserver→pip install pyodbc（另装 ODBC Driver 18）；oracle→pip install oracledb；
    mysql→pip install pymysql；postgresql→pip install psycopg2-binary
================================================================================
"""
import argparse
import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime

DEFAULT_INTERVAL = 60          # 常驻模式的轮询间隔（秒）
MAX_RETRY = 3                  # 单条推送失败重试次数
BACKOFF_BASE = 2.0             # 指数退避基数（秒）

DEFAULT_STATE = os.path.join(os.path.expanduser("~"), ".qc_push_forwarder_state.json")


# ── 配置 ─────────────────────────────────────────────────────────────
def load_config(path: str) -> dict:
    if not path or not os.path.exists(path):
        print("[配置] 未找到配置文件，请用 --config 指定；示例见脚本头部注释。")
        sys.exit(2)
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for key in ("db_type", "host", "database", "user", "password", "query",
                "push_url", "api_key"):
        if not cfg.get(key):
            print(f"[配置] 缺少必填项：{key}")
            sys.exit(2)
    if cfg["db_type"] not in ("sqlserver", "oracle", "mysql", "postgresql"):
        print(f"[配置] 不支持的 db_type：{cfg['db_type']}")
        sys.exit(2)
    cfg.setdefault("watermark_col", "wm_ts")
    cfg.setdefault("port", "")
    cfg.setdefault("source", "HIS推送")
    cfg.setdefault("state_file", DEFAULT_STATE)
    return cfg


def _fmt(ts) -> str:
    """把行里的时间/日期值转成 ISO 字符串，保证推送端可解析。"""
    if ts is None:
        return ""
    if isinstance(ts, (datetime,)):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


# ── 数据库连接 ───────────────────────────────────────────────────────
def _connect(cfg: dict):
    db = cfg["db_type"]
    if db == "sqlserver":
        import pyodbc
        port = cfg.get("port") or "1433"
        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={cfg['host']},{port};DATABASE={cfg['database']};"
            f"UID={cfg['user']};PWD={cfg['password']};"
            "TrustServerCertificate=yes;Encrypt=optional"
        )
        return pyodbc.connect(conn_str, timeout=15)
    if db == "oracle":
        import oracledb
        port = int(cfg.get("port") or 1521)
        dsn = oracledb.makedsn(cfg["host"], port, service_name=cfg["database"])
        return oracledb.connect(user=cfg["user"], password=cfg["password"], dsn=dsn)
    if db == "mysql":
        import pymysql
        port = int(cfg.get("port") or 3306)
        return pymysql.connect(host=cfg["host"], port=port, user=cfg["user"],
                               password=cfg["password"], database=cfg["database"],
                               cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    if db == "postgresql":
        import psycopg2
        port = cfg.get("port") or "5432"
        return psycopg2.connect(host=cfg["host"], port=port, database=cfg["database"],
                                user=cfg["user"], password=cfg["password"])
    raise RuntimeError(f"不支持的数据库类型：{db}")


def _rows(conn, cfg: dict, watermark: str):
    """执行查询并返回 dict 行列表。%WATERMARK% 替换为水位字符串。"""
    sql = cfg["query"]
    if "%WATERMARK%" in sql:
        safe = watermark.replace("'", "''") if watermark else ""
        sql = sql.replace("%WATERMARK%", f"'{safe}'")
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    out = []
    for r in cur.fetchall():
        if isinstance(r, dict):
            out.append({k.lower(): v for k, v in r.items()})
        else:
            out.append(dict(zip(cols, r)))
    cur.close()
    return out


# ── 状态（水位） ─────────────────────────────────────────────────────
def load_state(cfg: dict) -> str:
    try:
        with open(cfg["state_file"], encoding="utf-8") as fh:
            st = json.load(fh)
        return st.get("watermark", "")
    except Exception:
        return ""


def save_state(cfg: dict, watermark: str) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cfg["state_file"])), exist_ok=True)
        with open(cfg["state_file"], "w", encoding="utf-8") as fh:
            json.dump({"watermark": watermark, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[状态] 水位保存失败：{exc}")


# ── HTTP 推送 ────────────────────────────────────────────────────────
def push_one(cfg: dict, row: dict) -> str:
    """推送一行报告，返回应答 message；失败抛异常（由调用方重试）。"""
    exam_date = _fmt(row.get("exam_date") or row.get("report_time") or "")
    payload = {
        "patient": row.get("patient", ""),
        "gender": row.get("gender", ""),
        "age": row.get("age", ""),
        "modality": row.get("modality", ""),
        "applied_site": row.get("applied_site", ""),
        "findings_desc": row.get("findings_desc", ""),
        "diagnosis": row.get("diagnosis", ""),
        "exam_id": row.get("exam_id", ""),
        "exam_date": exam_date,
        "source": cfg.get("source", "HIS推送"),
    }
    # 整段式兼容：无描述/诊断分栏时退回 report_text
    if not payload["findings_desc"] and not payload["diagnosis"]:
        payload["report_text"] = row.get("report_text", "")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["push_url"], data=data, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": cfg["api_key"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body[:200]}")
    try:
        d = json.loads(body)
    except Exception:
        raise RuntimeError(f"应答非 JSON：{body[:200]}")
    if not d.get("ok"):
        raise RuntimeError(d.get("message") or "推送端返回失败")
    return d.get("message") or "OK"


# ── 主流程 ───────────────────────────────────────────────────────────
def run_once(cfg: dict, dry_run: bool = False):
    watermark = load_state(cfg)
    print(f"[{time.strftime('%H:%M:%S')}] 开始一轮推送，水位={watermark or '（无）'}")
    conn = _connect(cfg)
    try:
        rows = _rows(conn, cfg, watermark)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        print(f"[{time.strftime('%H:%M:%S')}] 无新增报告。")
        return 0

    sent = 0
    wm_col = cfg.get("watermark_col") or ""
    for i, row in enumerate(rows, 1):
        exam = row.get("exam_id") or row.get("patient") or f"第{i}行"
        if dry_run:
            print(f"[试推] {exam} | 描述:{str(row.get('findings_desc') or '')[:20]} "
                  f"诊断:{str(row.get('diagnosis') or '')[:20]}")
            sent += 1
            continue
        # 重试：指数退避
        ok = False
        for attempt in range(1, MAX_RETRY + 1):
            try:
                msg = push_one(cfg, row)
                print(f"[推送] {exam} -> {msg}")
                ok = True
                break
            except Exception as exc:
                print(f"[重试] {exam} 第{attempt}/{MAX_RETRY}次失败：{exc}")
                if attempt < MAX_RETRY:
                    time.sleep(BACKOFF_BASE ** attempt)
        if not ok:
            # 停在失败行：不越过该行推进水位，下轮继续从原水位开始（不丢数据）
            print(f"[警告] {exam} 推送失败，本轮中断；水位保持 {watermark or '（无）'}")
            return sent
        if wm_col and wm_col in row:
            watermark = _fmt(row[wm_col])
            save_state(cfg, watermark)
        sent += 1
    print(f"[{time.strftime('%H:%M:%S')}] 本轮完成：推送 {sent} 条，水位={watermark or '（无）'}")
    return sent


def main():
    ap = argparse.ArgumentParser(description="星衍质控 · 数据库报告主动推送器")
    ap.add_argument("--config", required=True, help="配置文件路径（JSON）")
    ap.add_argument("--once", action="store_true", help="只跑一轮后退出")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="轮询间隔秒")
    ap.add_argument("--dry-run", action="store_true", help="试推：只打印不发送")
    args = ap.parse_args()

    cfg = load_config(args.config)
    while True:
        try:
            run_once(cfg, dry_run=args.dry_run)
        except Exception:
            print("[错误] 本轮流失败：")
            traceback.print_exc()
        if args.once:
            break
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
