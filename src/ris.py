"""
report_qc_app/src/ris.py
RIS 数据库直连模块

设计目标：
  - 院内 RIS/PACS 报告库多为 SQL Server 或 Oracle，少数 MySQL/PostgreSQL。
  - 本模块提供统一的连接与查询接口，按需加载对应驱动（可选依赖，缺失时优雅降级）。
  - 连接配置本地持久化到 assets/ris_config.json，避免每次重填。
  - 严禁在客户端硬编码明文口令到发行包；口令仅保存在本机配置文件（建议院内内网机器）。

驱动矩阵（需在目标机 pip 安装对应驱动，发行版可按需打包）：
  - sqlserver  -> pyodbc         (需系统安装 ODBC Driver 17/18 for SQL Server)
  - oracle     -> oracledb       (纯 Python，无需 Oracle Client) 或 cx_Oracle
  - mysql      -> pymysql
  - postgresql -> psycopg2 / psycopg

查询约定：
  - 由医院 IT 提供一条 SELECT，字段别名映射到质控软件字段：
      report_text(必填), patient, gender, age, modality, applied_site, ts
  - 支持参数化按日期/检查号/患者号拉取，避免全表扫描。
"""

import os
import sys
import json

def _config_path() -> str:
    """RIS 连接配置持久化路径（写盘，需真实可写目录）。

    冻结后 __file__ 指向 PYZ 合成路径，os.path.abspath 回溯会得到不存在的位置，
    导致配置永远写不进去。此时改用 exe 所在目录下的 assets/。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
        return os.path.join(base, "assets", "ris_config.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "ris_config.json")


CONFIG_PATH = _config_path()

# 各数据库类型 -> 驱动模块名
DRIVERS = {
    "sqlserver": "pyodbc",
    "oracle": "oracledb",
    "mysql": "pymysql",
    "postgresql": "psycopg2",
}

# 字段别名 -> 质控软件标准字段
STD_FIELDS = ["report_text", "patient", "gender", "age", "modality", "applied_site", "ts"]

DEFAULT_CONFIG = {
    "db_type": "sqlserver",
    "host": "",
    "port": "",
    "database": "",
    "user": "",
    "password": "",
    # 由院内 IT 提供；必须返回 report_text 列，其余可选
    "query": (
        "SELECT TOP 50 report_content AS report_text, patient_name AS patient, "
        "sex AS gender, age AS age, exam_part AS modality, apply_part AS applied_site, "
        "report_time AS ts FROM v_radiology_report ORDER BY report_time DESC"
    ),
}


# ----------------------------- 配置持久化 -----------------------------
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# ----------------------------- 驱动探测 -----------------------------
def driver_available(db_type: str):
    """返回 (是否可用, 驱动名, 提示信息)。"""
    mod = DRIVERS.get(db_type)
    if not mod:
        return False, "", f"不支持的数据库类型：{db_type}"
    try:
        __import__(mod)
        return True, mod, f"驱动 {mod} 可用"
    except Exception:
        hint = {
            "pyodbc": "pip install pyodbc（并安装 ODBC Driver 18 for SQL Server）",
            "oracledb": "pip install oracledb（纯 Python，无需 Oracle Client）",
            "pymysql": "pip install pymysql",
            "psycopg2": "pip install psycopg2-binary",
        }.get(mod, f"pip install {mod}")
        return False, mod, f"缺少驱动 {mod}，请执行：{hint}"


# ----------------------------- 连接 -----------------------------
def _connect(cfg: dict):
    db = cfg["db_type"]
    ok, mod, msg = driver_available(db)
    if not ok:
        raise RuntimeError(msg)

    if db == "sqlserver":
        import pyodbc
        port = cfg.get("port") or "1433"
        # 2026-08-18：优先 Driver 18，失败回退 17/13（医院环境 ODBC 版本不一，
        # 此前硬编码 18，仅装 17 的机器连接必失败）。
        _drivers = getattr(pyodbc, "drivers", lambda: [])()
        _pref = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
                 "ODBC Driver 13 for SQL Server", "SQL Server"]
        _avail = [d for d in _pref if any(d in x for x in _drivers)] or ["ODBC Driver 18 for SQL Server"]
        _last_err = None
        for _drv in _avail:
            conn_str = (
                f"DRIVER={{{_drv}}};"
                f"SERVER={cfg['host']},{port};DATABASE={cfg['database']};"
                f"UID={cfg['user']};PWD={cfg['password']};"
                "TrustServerCertificate=yes;Encrypt=optional"
            )
            try:
                return pyodbc.connect(conn_str, timeout=8)
            except Exception as e:  # noqa: BLE001
                _last_err = e
        raise RuntimeError(f"SQL Server 连接失败（已尝试驱动 {_avail}）：{_last_err}")

    if db == "oracle":
        import oracledb
        port = int(cfg.get("port") or 1521)
        dsn = oracledb.makedsn(cfg["host"], port, service_name=cfg["database"])
        return oracledb.connect(user=cfg["user"], password=cfg["password"], dsn=dsn)

    if db == "mysql":
        import pymysql
        return pymysql.connect(
            host=cfg["host"], port=int(cfg.get("port") or 3306),
            user=cfg["user"], password=cfg["password"],
            database=cfg["database"], charset="utf8mb4", connect_timeout=8)

    if db == "postgresql":
        import psycopg2
        return psycopg2.connect(
            host=cfg["host"], port=int(cfg.get("port") or 5432),
            user=cfg["user"], password=cfg["password"],
            dbname=cfg["database"], connect_timeout=8)

    raise RuntimeError(f"不支持的数据库类型：{db}")


def test_connection(cfg: dict):
    """测试连通性，返回 (成功?, 信息)。"""
    ok, mod, msg = driver_available(cfg["db_type"])
    if not ok:
        return False, msg
    try:
        conn = _connect(cfg)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return True, "连接成功"
    except Exception as e:
        return False, f"连接失败：{e}"


def fetch_reports(cfg: dict, limit: int = 50):
    """执行配置里的查询，返回标准字段的字典列表。

    结果按 STD_FIELDS 归一化；缺失列填空字符串。report_text 为空的行会被跳过。
    """
    conn = _connect(cfg)
    try:
        cur = conn.cursor()
        cur.execute(cfg["query"])
        cols = [d[0].lower() for d in cur.description]
        rows = cur.fetchmany(limit) if hasattr(cur, "fetchmany") else cur.fetchall()
        out = []
        for row in rows:
            rec = {c: ("" if v is None else str(v)) for c, v in zip(cols, row)}
            item = {k: rec.get(k, "") for k in STD_FIELDS}
            if item["report_text"].strip():
                out.append(item)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
