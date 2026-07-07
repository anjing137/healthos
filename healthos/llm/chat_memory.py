"""chat session 持久化 + Markdown 落盘。

P1 设计:
- session_id 在 REPL/CLI 启动时确定,持久化到 data/.chat_session_id
  下次启动读这个文件 — 同一进程树内(REPL)继承;重启新 session
- 每次 chat 调用:同时写 chat_log(SQLite 索引)+ chat_history/<session_id>.md(真源)
- 历史召回:取最近 N 条同 session_id 的 chat_log,拼成 messages 喂给 LLM

为什么 Markdown 落盘:
- 可读、可 grep、可 git 友好
- 给未来接 EverOS / MemOS 留接口
- 出问题能 cat 直接看,不用 SQL query
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..db.conn import DEFAULT_DB_PATH, connect


# ── session_id 管理 ────────────────────────────────────────────────

_SESSION_ID_FILE = DEFAULT_DB_PATH.parent / ".chat_session_id"


def get_or_create_session_id() -> str:
    """读或创建当前 chat session id。

    持久化在 data/.chat_session_id(跟 db 同目录)。
    同一文件存在 → 复用;不存在 → 新 UUID。
    """
    try:
        if _SESSION_ID_FILE.exists():
            text = _SESSION_ID_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception:
        pass
    new_id = f"chat-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    try:
        _SESSION_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_ID_FILE.write_text(new_id, encoding="utf-8")
    except Exception:
        pass
    return new_id


def reset_session_id() -> str:
    """强制开新 session — 写新 UUID 到文件,返回新 id。"""
    if _SESSION_ID_FILE.exists():
        try:
            _SESSION_ID_FILE.unlink()
        except Exception:
            pass
    return get_or_create_session_id()


# ── Markdown 落盘 ─────────────────────────────────────────────────

_HISTORY_DIR = DEFAULT_DB_PATH.parent / "chat_history"


def _md_path(session_id: str) -> Path:
    return _HISTORY_DIR / f"{session_id}.md"


def append_to_markdown(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
) -> None:
    """追加一条消息到 data/chat_history/<session_id>.md(真源)。"""
    md = _md_path(session_id)
    try:
        md.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else ""
        line = f"\n## [{ts}] {role}\n{content}\n"
        if meta_str:
            line += f"\n<!-- meta: {meta_str} -->\n"
        if not md.exists():
            md.write_text(
                f"# chat session {session_id}\n"
                f"started {ts}\n"
                f"\n---\n",
                encoding="utf-8",
            )
        with md.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Markdown 落盘失败不影响 chat 本身(降级)
        pass


# ── SQLite 索引(给 chat_log 表)───────────────────────────────────


def write_chat_log(
    session_id: str,
    role: str,
    content: str,
    log_date: str,
    metadata: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> int:
    """写一行 chat_log。返 id。"""
    conn = connect(db_path) if db_path else connect()
    try:
        cur = conn.execute(
            """INSERT INTO chat_log(log_date, created_at, speaker, content, source,
                                    session_id, role, metadata)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_date,
                datetime.now().isoformat(timespec="seconds"),
                role,           # 用 role 当 speaker(让 v004 老 schema 兼容)
                content,
                "memory",       # source
                session_id,
                role,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_recent_history(
    session_id: str,
    limit: int = 10,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """读最近 N 条同 session_id 的 chat_log。返 [{role, content, created_at}]。"""
    conn = connect(db_path) if db_path else connect()
    try:
        rows = conn.execute(
            """SELECT role, content, created_at, metadata
               FROM chat_log
               WHERE session_id=? AND role IN ('user','agent','tool_call','tool_result')
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        # 反转成时间正序
        rows = list(reversed(rows))
        out = []
        for r in rows:
            out.append({
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            })
        return out
    finally:
        conn.close()