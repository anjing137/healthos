"""P1: chat memory 持久化 + Markdown + session_id 测试。

7 个 case:
 1. session_id 创建 + 复用(读文件)
 2. session_id 文件不存在 → 新 UUID 写文件
 3. append_to_markdown 创建新文件 + 追加格式正确
 4. write_chat_log 写一行 + 列填好
 5. get_recent_history 取最近 N 条 + 时间正序
 6. 同 session 多轮写入能召回
 7. run_chat 写 user+agent 到 chat_log + Markdown
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from healthos.db import conn as db_conn
from healthos.llm import agent
from healthos.llm import chat_memory
from healthos.llm.client import LLMResponse
from healthos.llm.chat_memory import (
    append_to_markdown,
    get_or_create_session_id,
    get_recent_history,
    reset_session_id,
    write_chat_log,
)


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    """给 chat_memory 一切路径切到 tmp_path — 隔离 dev db。"""
    fake_db = tmp_path / "test.db"
    c = db_conn.connect(fake_db)
    db_conn.init(c)
    c.close()

    monkeypatch.setattr(
        "healthos.llm.chat_memory.connect",
        lambda db_path=None: db_conn.connect(fake_db) if db_path is None else db_conn.connect(db_path),
    )
    monkeypatch.setattr(chat_memory, "_SESSION_ID_FILE", tmp_path / ".chat_session_id")
    monkeypatch.setattr(chat_memory, "_HISTORY_DIR", tmp_path / "chat_history")
    return tmp_path


# ── session_id 管理 ────────────────────────────────────────────────


def test_session_id_creates_when_missing(tmp_workspace):
    sid = get_or_create_session_id()
    assert sid.startswith("chat-")
    assert (tmp_workspace / ".chat_session_id").read_text(encoding="utf-8").strip() == sid


def test_session_id_reuses_existing(tmp_workspace):
    (tmp_workspace / ".chat_session_id").write_text("chat-existing-abc", encoding="utf-8")
    sid = get_or_create_session_id()
    assert sid == "chat-existing-abc"


def test_reset_session_id_creates_new(tmp_workspace):
    (tmp_workspace / ".chat_session_id").write_text("chat-old-xyz", encoding="utf-8")
    new_sid = reset_session_id()
    assert new_sid != "chat-old-xyz"
    assert new_sid.startswith("chat-")


# ── Markdown 落盘 ──────────────────────────────────────────────────


def test_append_markdown_creates_file(tmp_workspace):
    sid = "chat-test-1"
    append_to_markdown(sid, "user", "你好")
    append_to_markdown(sid, "agent", "你好,有什么能帮你的?")

    md_path = tmp_workspace / "chat_history" / f"{sid}.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert f"# chat session {sid}" in text
    # 角色 + 内容都在文件里
    assert "user\n你好" in text
    assert "agent\n你好,有什么能帮你的?" in text


def test_append_markdown_with_metadata(tmp_workspace):
    sid = "chat-test-meta"
    append_to_markdown(sid, "agent", "调了 read_today",
                        metadata={"tool_calls": ["read_today"]})
    text = (tmp_workspace / "chat_history" / f"{sid}.md").read_text(encoding="utf-8")
    assert "<!-- meta:" in text
    assert "read_today" in text


# ── SQLite 索引 ────────────────────────────────────────────────────


def test_write_chat_log_persists(tmp_workspace):
    sid = "chat-db-test"
    write_chat_log(sid, "user", "test msg", log_date="2026-07-08",
                    metadata={"k": "v"})

    rows = get_recent_history(sid, limit=10)
    assert len(rows) == 1
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "test msg"
    assert rows[0]["metadata"] == {"k": "v"}


def test_get_recent_history_order_and_limit(tmp_workspace):
    sid = "chat-order-test"
    for i in range(5):
        write_chat_log(sid, "user" if i % 2 == 0 else "agent",
                        f"msg-{i}", log_date="2026-07-08")
    rows = get_recent_history(sid, limit=3)
    assert len(rows) == 3
    # 应该按时间正序:msg-2, msg-3, msg-4
    assert rows[0]["content"] == "msg-2"
    assert rows[2]["content"] == "msg-4"


def test_get_recent_history_filters_other_sessions(tmp_workspace):
    sid_a = "chat-a"
    sid_b = "chat-b"
    write_chat_log(sid_a, "user", "A 的话", log_date="2026-07-08")
    write_chat_log(sid_b, "user", "B 的话", log_date="2026-07-08")
    rows = get_recent_history(sid_a, limit=10)
    assert len(rows) == 1
    assert rows[0]["content"] == "A 的话"


# ── run_chat 集成 ──────────────────────────────────────────────────


def test_run_chat_writes_user_and_agent(tmp_workspace, monkeypatch):
    """run_chat 一轮 → chat_log 有 user + agent 两行,Markdown 也有。"""
    captured = {}

    def fake_chat(req):
        captured["system"] = req.system
        return LLMResponse(text="这是 LLM 回复", tool_calls=[])

    monkeypatch.setattr(agent, "chat", fake_chat)

    out = agent.run_chat("我问", today_iso="2026-07-08", yesterday_iso="2026-07-07")
    assert out == "这是 LLM 回复"

    sid = get_or_create_session_id()
    rows = get_recent_history(sid, limit=10)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "我问"
    assert rows[1]["role"] == "agent"
    assert rows[1]["content"] == "这是 LLM 回复"

    # Markdown 也应该有两段
    md_path = tmp_workspace / "chat_history" / f"{sid}.md"
    text = md_path.read_text(encoding="utf-8")
    assert "user\n我问" in text
    assert "agent\n这是 LLM 回复" in text


def test_run_chat_injects_history_into_next_call(tmp_workspace, monkeypatch):
    """第二轮 chat 的 system prompt 必须含 <chat_history>...</chat_history>。"""
    captured_list = []

    def fake_chat(req):
        captured_list.append(req.system)
        return LLMResponse(text="ok", tool_calls=[])

    monkeypatch.setattr(agent, "chat", fake_chat)

    agent.run_chat("第一句", today_iso="2026-07-08", yesterday_iso="2026-07-07")
    agent.run_chat("第二句", today_iso="2026-07-08", yesterday_iso="2026-07-07")

    second_sys = captured_list[1]
    assert "<chat_history>" in second_sys, "第二轮应该看到第一轮历史"
    assert "第一句" in second_sys