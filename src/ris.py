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
import re


def _restrict_file_access(path: str) -> None:
    """限制敏感文件仅当前用户可读写（跨平台）。"""
    try:
        if sys.platform.startswith("win"):
            import subprocess
            user = os.environ.get("USERNAME") or os.getlogin()
            subprocess.run(
                ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(F)"],
                capture_output=True, timeout=5,
            )
        else:
            os.chmod(path, 0o600)
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)

def _config_path() -> str:
    """RIS 连接配置持久化路径（写盘，需真实可写目录）。

    2026-08-18 M6：冻结后统一落用户可写数据目录（与 samplelib._appdata_db / db.py
    同口径），不再写 sys._MEIPASS（临时目录，退出即丢；Program Files 只读则写失败）。
    """
    if getattr(sys, "frozen", False):
        import platform as _plt
        if _plt.system() == "Windows":
            base = os.path.expandvars("%APPDATA%")
        elif _plt.system() == "Darwin":
            base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        else:
            base = os.path.expanduser("~")
        return os.path.join(base, "MedicalReportQC", "ris_config.json")
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
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)
    return cfg


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    try:
        _restrict_file_access(CONFIG_PATH)  # 2026-08-18 M6：含医院库口令，限本账号读取
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


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
            database=cfg["database"], charset="utf8mb4", connect_timeout=8,
            read_timeout=15, write_timeout=15)  # 2026-08-18 M9：查询级超时（pymysql 需连接参数）

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


# 只读 SQL 校验（2026-08-18 H5 修复）：Web 端点把客户端提交的 query 原样交给
# fetch_reports 执行，键名打通后即成为「任意登录用户对自填主机执行任意 SQL」的口子。
# 这里强制只读约束（SELECT/WITH 开头、禁分号、禁 DDL/DML），拒之门外。
_SQL_FORBIDDEN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|MERGE|REPLACE|ATTACH|DETACH|VACUUM|COPY)\b", re.I)


def _assert_readonly_sql(sql: str) -> None:
    """校验查询 SQL 为只读单条 SELECT/WITH；不合法抛 ValueError。"""
    q = (sql or "").strip()
    if not q:
        raise ValueError("查询 SQL 为空")
    if len(q) > 20000:
        raise ValueError("查询 SQL 过长（上限 20000 字符）")
    if not re.match(r"(?is)^(SELECT|WITH)\b", q):
        raise ValueError("只允许 SELECT/WITH 只读查询")
    if ";" in q:
        raise ValueError("查询中不允许分号（仅允许单条只读查询）")
    if _SQL_FORBIDDEN.search(q):
        raise ValueError("查询包含被禁止的关键字（仅允许只读 SELECT）")


def _apply_query_timeout(conn, db_type: str, seconds: int = 15) -> None:
    """设置查询级超时（2026-08-18 M9 修复）：连接超时之外，慢查询会无限阻塞
    RIS 轮询线程/GUI。逐驱动设查询超时；pymysql 的 read_timeout 需连接参数，
    已在 _connect 的 mysql 分支设置。"""
    try:
        if db_type == "sqlserver":        # pyodbc
            conn.timeout = seconds
        elif db_type == "oracle":          # oracledb（毫秒）
            if hasattr(conn, "call_timeout"):
                conn.call_timeout = seconds * 1000
        elif db_type == "postgresql":      # psycopg2
            cur = conn.cursor()
            cur.execute("SET statement_timeout = %d" % (seconds * 1000))
            cur.close()
    except Exception:
        try:
            from .log_utils import log_quiet
        except ImportError:
            from log_utils import log_quiet
        log_quiet(__name__)


def fetch_reports(cfg: dict, limit: int = 50):
    """执行配置里的查询，返回标准字段的字典列表。

    结果按 STD_FIELDS 归一化；缺失列填空字符串。report_text 为空的行会被跳过。
    2026-08-18 H5/M9：查询只读校验 + 查询级超时。
    """
    sql = cfg.get("query", "")
    _assert_readonly_sql(sql)
    conn = _connect(cfg)
    _apply_query_timeout(conn, cfg.get("db_type", ""))
    try:
        cur = conn.cursor()
        cur.execute(sql)
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
            try:
                from .log_utils import log_quiet
            except ImportError:
                from log_utils import log_quiet
            log_quiet(__name__)
