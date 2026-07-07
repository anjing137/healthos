"""P0: PROFILE.md 加载 + 注入测试。

3 个 case:
 1. PROFILE.md 存在 → profile_block_for_prompt() 返回非空 + 包 <user_profile> 标记
 2. PROFILE.md 不存在 → 返回空串(向后兼容,chat 仍能跑)
 3. profile 内容确实被拼到 CHAT_SYSTEM — 修改 PROFILE 后 run_chat 的 system prompt 含新内容
    (用 mock 验证 prompt 内容,不必真调 LLM)
"""

from __future__ import annotations

import inspect
from pathlib import Path

from healthos.llm.agent import run_chat
from healthos.llm.profile import (
    PROFILE_PATH,
    load_profile_text,
    profile_block_for_prompt,
)


def test_profile_md_exists_and_loads():
    """PROFILE.md 应该在仓库里、能读出来。"""
    assert PROFILE_PATH.exists(), "PROFILE.md 必须在 healthos/llm/ 下"
    text = load_profile_text()
    assert text.strip(), "PROFILE.md 不应该为空"
    # 必须包含核心字段 — 不然 LLM 看不到关键不变量
    assert "weight_kg" in text
    assert "bmr_kcal" in text
    assert "protein_target_g" in text


def test_profile_block_format():
    """profile_block_for_prompt 返回 <user_profile>...</user_profile> 包裹的块。"""
    block = profile_block_for_prompt()
    assert "<user_profile>" in block
    assert "</user_profile>" in block
    # 至少有 markdown 内容(原 PROFILE.md 全文)
    assert "anjing137" in block  # user_id 在 frontmatter


def test_profile_block_empty_when_missing(tmp_path, monkeypatch):
    """PROFILE.md 不存在时返空串 — 不能炸。"""
    from healthos.llm import profile
    fake = tmp_path / "no_such.md"
    block = profile_block_for_prompt(path=fake)
    assert block == ""


def test_run_chat_injects_profile_into_system_prompt(monkeypatch, tmp_path):
    """run_chat 必须把 profile 拼到 CHAT_SYSTEM。

    mock 掉 chat() 抓 system 参数,断言 profile 内容在里面。
    用 tmp_path 给 P1 chat_memory 一个干净 db,避免污染 dev db。
    """
    from healthos.llm.client import LLMResponse
    from healthos.db import conn as db_conn

    # 给 chat_memory 一个 tmp db — 同时跑过 migration
    fake_db = tmp_path / "test.db"
    c = db_conn.connect(fake_db)
    db_conn.init(c)
    c.close()

    # 把 connect 默认路径改成我们的 fake(影响 chat_memory)
    monkeypatch.setattr(
        "healthos.llm.chat_memory.connect",
        lambda db_path=None: db_conn.connect(fake_db) if db_path is None else db_conn.connect(db_path),
    )
    # 把 session_id 文件路径也改成 tmp,避免读 dev db 的文件
    monkeypatch.setattr(
        "healthos.llm.chat_memory._SESSION_ID_FILE",
        tmp_path / ".chat_session_id",
    )

    captured = {}

    def fake_chat(req):
        captured["system"] = req.system
        return LLMResponse(text="ok", tool_calls=[])

    from healthos.llm import agent as agent_mod
    monkeypatch.setattr(agent_mod, "chat", fake_chat)

    run_chat("今天怎么样", today_iso="2026-07-08", yesterday_iso="2026-07-07")

    sys_prompt = captured["system"]
    assert "<user_profile>" in sys_prompt, "system prompt 必须含 <user_profile> 块"
    assert "anjing137" in sys_prompt
    assert "100.9" in sys_prompt, "weight_kg 必须注入"


def test_run_chat_does_not_inject_profile_when_missing(monkeypatch, tmp_path):
    """当 PROFILE.md 不存在时,run_chat 的 system prompt 不应含 <user_profile>。"""
    from healthos.llm import profile
    from healthos.llm.client import LLMResponse
    from healthos.db import conn as db_conn

    fake_db = tmp_path / "test.db"
    c = db_conn.connect(fake_db)
    db_conn.init(c)
    c.close()
    monkeypatch.setattr(
        "healthos.llm.chat_memory.connect",
        lambda db_path=None: db_conn.connect(fake_db) if db_path is None else db_conn.connect(db_path),
    )
    monkeypatch.setattr(
        "healthos.llm.chat_memory._SESSION_ID_FILE",
        tmp_path / ".chat_session_id",
    )

    captured = {}

    def fake_chat(req):
        captured["system"] = req.system
        return LLMResponse(text="ok", tool_calls=[])

    # 临时把默认路径指向不存在文件
    fake_profile = tmp_path / "no.md"
    monkeypatch.setattr(profile, "_PROFILE_PATH", fake_profile)
    from healthos.llm import agent as agent_mod
    monkeypatch.setattr(agent_mod, "chat", fake_chat)

    run_chat("test", today_iso="2026-07-08", yesterday_iso="2026-07-07")

    sys_prompt = captured["system"]
    assert "<user_profile>" not in sys_prompt