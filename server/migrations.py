"""
server/migrations.py — 版本化数据库迁移（2026-09-30 审计新增）

## 为什么需要它

在这之前，schema 变更是**散落在 server/db.py 里的临时函数**（`_migrate_queue_hash`、
`_migrate_settings_uk` …），每次启动无条件跑一遍，靠"函数自己写得幂等"兜底：
- 没有任何地方记录"这个库升到第几版了"，出问题时无法判断；
- 新迁移只能继续往 `init_db()` 里堆，顺序与依赖靠人记；
- 医院场景是**就地升级、库里有真实数据**，一旦某次迁移改错，没有前置备份可退。

本模块给出最小可用的版本化迁移：`schema_migrations` 表 + 编号迁移 + 迁移前自动
快照 + 幂等与幂等失败留痕。**不引入 alembic**（SQLite 单体 + 单文件部署，
重量级框架收益有限、依赖成本高）。

## 用法（新增 schema 变更）

```python
@migration("0005_order_refund_reason", "orders 表补 refund_reason 列")
def _m0005(eng):
    add_column_if_missing(eng, "orders", "refund_reason", "VARCHAR(200)")
```

约定：
- 版本号 `NNNN_slug`，只增不改；已发布的迁移**不要修改**（改的是别人已经跑过的库）。
- 迁移函数必须**幂等**（用 `add_column_if_missing` / `PRAGMA` 自检），
  因为既有库会以"版本表为空"的状态首次接入本框架，所有迁移都会重跑一遍。
- 迁移失败**不阻断启动**（记 ERROR + 不写版本号，下次启动重试），避免把用户挡在门外；
  但会在日志里明确写出失败原因与"当前库可能处于半迁移状态"。
"""
import datetime
import glob
import os
import shutil
import sys
from typing import Callable, List, Tuple

_MIGRATIONS: List[Tuple[str, str, Callable]] = []
_TABLE = "schema_migrations"


def migration(version: str, description: str):
    """注册一个迁移（装饰器）。version 形如 "0002_queue_report_hash"。"""
    def deco(fn: Callable):
        _MIGRATIONS.append((version, description, fn))
        return fn
    return deco


def registered():
    """返回按版本号排序的 [(version, description)]（供测试/运维查看）。"""
    return [(v, d) for v, d, _fn in sorted(_MIGRATIONS, key=lambda x: x[0])]


def _log(msg: str, *args) -> None:
    try:
        try:
            from .log_utils import get_logger
        except ImportError:
            from log_utils import get_logger
        get_logger().info(msg, *args)
    except Exception:               # silent-except-ok: 观测层自身失败绝不能抛
        pass


def _log_error(msg: str, *args) -> None:
    try:
        try:
            from .log_utils import get_logger
        except ImportError:
            from log_utils import get_logger
        get_logger().error(msg, *args)
    except Exception:               # silent-except-ok: 观测层自身失败绝不能抛
        pass


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def ensure_table(eng) -> None:
    """建版本表（幂等）。用 exec_driver_sql：DDL 无需 SQLAlchemy 编译，
    也避免 `conn.execute("字符串")` 在 SQLAlchemy 2.0 下抛 ObjectNotExecutableError
    —— 那正是本仓库两个老迁移静默失效的原因（见 db.py 注释）。"""
    with eng.begin() as c:
        c.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "version VARCHAR(48) PRIMARY KEY, "
            "description VARCHAR(200), "
            "applied_at VARCHAR(32))")


def applied_versions(eng) -> set:
    ensure_table(eng)
    try:
        with eng.connect() as c:
            rows = c.exec_driver_sql(f"SELECT version FROM {_TABLE}").fetchall()
        return {r[0] for r in rows}
    except Exception:
        # 读版本表失败：按"未知"处理并留痕。注意不能静默——若在这里静默返回空集，
        # 上层会以为"所有迁移都没跑过"从而重放全部迁移。
        _log_error("读取 schema_migrations 失败，本次按未记录处理")
        return set()


def pending(eng) -> List[str]:
    done = applied_versions(eng)
    return [v for v, _d, _f in sorted(_MIGRATIONS, key=lambda x: x[0]) if v not in done]


def add_column_if_missing(eng, table: str, column: str, ddl_type: str) -> bool:
    """通用幂等加列（SQLite 的 ALTER TABLE 只能加列）。返回是否真的加了。"""
    with eng.begin() as c:
        cols = [r[1] for r in c.exec_driver_sql(
            f"PRAGMA table_info({table})").fetchall()]
        if not cols:                     # 表还不存在：交给 create_all
            return False
        if column in cols:
            return False
        # 说明：table/column/ddl_type 均来自本模块内硬编码的迁移定义，非外部输入
        c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        return True


def _db_file(eng) -> str:
    path = getattr(eng.url, "database", None)
    if not path or path == ":memory:":
        return ""
    return os.path.abspath(path)


def snapshot_before_migrate(eng, version: str, keep: int = 3) -> str:
    """迁移前快照：把数据库文件复制一份 `<db>.premigrate-<version>-<ts>.bak`。

    医院是就地升级：万一迁移把库改坏，这是唯一的现场。失败不阻断（只留痕）。
    """
    src = _db_file(eng)
    if not src or not os.path.isfile(src):
        return ""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = f"{src}.premigrate-{version}-{ts}.bak"
    try:
        shutil.copy2(src, dest)
    except Exception as e:
        _log_error("迁移前快照失败：%s: %s", dest, e)
        return ""
    # 保留最近 keep 份，避免磁盘被快照占满
    try:
        olds = sorted(glob.glob(src + ".premigrate-*.bak"))
        for old in olds[:-keep]:
            os.remove(old)
    except Exception:
        _log("清理旧迁移快照失败（不影响迁移本身）")
    _log("迁移前快照已生成：%s", dest)
    return dest


def run_migrations(eng, snapshot: bool = True) -> List[str]:
    """执行所有未应用的迁移，返回本次应用的版本号列表。

    幂等：已记录的版本直接跳过。失败：记 ERROR、不写版本号（下次重试）、继续后续迁移
    —— 保证应用仍能启动，用户能进界面导出数据。
    """
    ensure_table(eng)
    done = applied_versions(eng)
    applied: List[str] = []
    for version, description, fn in sorted(_MIGRATIONS, key=lambda x: x[0]):
        if version in done:
            continue
        if snapshot:
            snapshot_before_migrate(eng, version)
        try:
            fn(eng)
        except Exception as e:
            _log_error("迁移 %s（%s）失败，未记录版本、下次启动重试；"
                       "当前库可能处于半迁移状态：%s: %s", version, description,
                       type(e).__name__, e)
            continue
        try:
            with eng.begin() as c:
                c.exec_driver_sql(
                    f"INSERT OR REPLACE INTO {_TABLE} (version, description, applied_at) "
                    "VALUES (?, ?, ?)", (version, description, _now()))
        except Exception as e:
            _log_error("迁移 %s 已执行但版本记录失败：%s", version, e)
            continue
        applied.append(version)
        _log("已应用数据库迁移 %s：%s", version, description)
    return applied


# ─────────────────────────────────────────────────────────────────────────────
# 迁移清单
#
# 说明：0001 只是**登记基线**——表结构由 server/models.py + Base.metadata.create_all
# 负责；把它记进版本表，是为了让"老库首次接入本框架"也有一个明确起点。
# 0002/0003 把此前散落在 db.init_db() 里的临时迁移纳入版本管理
# （它们仍被 db.py 保留为函数，迁移体只是转调，避免逻辑双写）。
# ─────────────────────────────────────────────────────────────────────────────

@migration("0001_baseline", "基线：表结构由 models.py + create_all 建立（本版本仅登记）")
def _m0001(eng) -> None:
    return None


@migration("0002_queue_report_hash", "queue 表补 report_hash 列并回填（数据库层并发去重）")
def _m0002(eng) -> None:
    from server import db as _db
    _db._migrate_queue_hash(eng)


@migration("0003_settings_key_unique", "settings 唯一约束由 key 单列改为 (key, user_id) 复合")
def _m0003(eng) -> None:
    from server import db as _db
    _db._migrate_settings_uk(eng)


if __name__ == "__main__":  # pragma: no cover - 运维排查入口
    # 用法：python -c "from server import db, migrations; print(migrations.registered())"
    for v, d in registered():
        print(f"{v}\t{d}")
    print(f"共 {len(_MIGRATIONS)} 个迁移", file=sys.stderr)
