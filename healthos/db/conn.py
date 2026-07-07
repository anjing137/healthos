"""SQLite 连接 + migration 应用。

约定:
- 数据库文件路径: data/healthos.db(第一版只支持这一个 db)
- 新 schema 改一个文件加一个: healthos/db/migrations/v00N_xxx.sql
- 每次开连接跑 init():确保所有已注册的 migration 都跑过,且只跑一次
- SQLite 没有 ADD COLUMN IF NOT EXISTS。处理方案:在 init() 里检测 PRAGMA table_info
  的实际列,有则跳过 ALTER,没有则执行 ALTER。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "healthos.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with project-wide conventions."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


# ALTER TABLE <name> ADD COLUMN <col>
_ALTER_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
    flags=re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """把 SQL 文件里 '--' 起头的整行注释清掉(整行)。"""
    out: list[str] = []
    for ln in sql.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("--"):
            continue
        # 行内注释:仅在该行不含引号,且 '--' 出现在 SQL 部分的右侧时丢弃
        if "--" in ln:
            # 简化:若 ';' 出现在 '--' 之前,只保留 ';' 前
            i = ln.find("--")
            if i >= 0:
                ln = ln[:i]
        out.append(ln)
    return "\n".join(out)


def _split_sql(sql: str) -> list[str]:
    """按 ';' 切 SQL,返非空语句。先剥离注释,再切。"""
    cleaned = _strip_sql_comments(sql)
    return [s.strip() for s in cleaned.split(";") if s.strip()]  # noqa: E741


def init(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    """Run all migrations in order. Idempotent on ALTER TABLE ADD COLUMN.

    Algorithm:
    1. 读 v*.sql 文件,按 ';' 切(注释跳过)
    2. 对每条 stmt:
       - 若 ALTER TABLE ADD COLUMN  → 检查 col 已存在则跳过,否则 execute 单条
       - 若 CREATE / 其他          → 整段 executescript,容忍 duplicate column 警告
    3. 全部成功 → 在 schema_migrations 写一行
    """
    mdir = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    already: set[str] = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations")
    }
    for sql_file in sorted(mdir.glob("v*.sql")):
        version = sql_file.stem  # v001_init
        if version in already:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        for stmt in _split_sql(sql):
            m = _ALTER_RE.match(stmt)
            if m:
                tbl, col = m.group(1), m.group(2)
                if col in _table_columns(conn, tbl):
                    continue  # 已存在,跳过
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" in str(exc).lower():
                        continue
                    raise
            else:
                try:
                    conn.executescript(stmt + ";")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" in str(exc).lower():
                        continue
                    raise
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))",
            (version,),
        )
        conn.commit()


def upsert_daily_log(conn: sqlite3.Connection, log_date: str) -> None:
    """Ensure a daily_log row exists for the given date."""
    conn.execute(
        """
        INSERT INTO daily_log(log_date, created_at, updated_at)
        VALUES(?, datetime('now'), datetime('now'))
        ON CONFLICT(log_date) DO UPDATE SET updated_at = datetime('now')
        """,
        (log_date,),
    )
