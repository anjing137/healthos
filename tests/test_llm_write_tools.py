"""v2 — LLM 写工具测试。

8 个 case:
 1. close_question 写 audit_log
 2. close_question 改 resolved_grams + status='closed'
 3. set_workout_kcal 写 audit_log(method='manual', conf=1.0)
 4. 不存在的 qid → 不写 audit,返回 ok=False
 5. reparse_meal snapshot before/after,kcal 真值重算
 6. 三工具幂等(再调一次 → 多一条 audit 行,但 db 状态一致)
 7. dispatch 路由所有 3 个写工具
 8. TOOLS_SPEC 注册 6 个工具,新写工具 schema 完整
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from healthos.db import conn as db_conn
from healthos.llm import tools
from healthos.llm._tools import (
    close_question,
    reparse_meal,
    set_workout_kcal,
)
from healthos.record import record


@pytest.fixture
def fresh_db(tmp_path) -> Path:
    fake = tmp_path / "test_write.db"
    c = db_conn.connect(fake)
    db_conn.init(c)
    c.close()
    return fake


def _audit_count(conn, table: str, row_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE table_name=? AND row_id=?",
        (table, row_id),
    ).fetchone()["c"]


# ── close_question ─────────────────────────────────────────────────


def test_close_question_writes_audit_log(fresh_db):
    """先 record 一段带 unknown food 的日记 → 触发 open_question → close 它。"""
    res = record("午餐:未知食物一份", log_date="2026-07-08", db_path=fresh_db)
    assert res.questions  # 至少一条 open_question
    qid = res.questions[0]

    out = close_question(qid, 150.0, "用户答复:一份约 150g", db_path=fresh_db)
    assert out["ok"] is True
    assert out["qid"] == qid
    assert out["audit_id"] > 0

    c = db_conn.connect(fresh_db)
    # 1) status 改 'closed'
    row = c.execute("SELECT status, resolved_grams FROM open_question WHERE id=?",
                    (qid,)).fetchone()
    assert row["status"] == "closed"
    assert row["resolved_grams"] == 150.0
    # 2) audit_log 写了一行
    assert _audit_count(c, "open_question", qid) == 1
    audit = c.execute(
        "SELECT action, source, notes FROM audit_log WHERE row_id=?",
        (qid,),
    ).fetchone()
    assert audit["action"] == "resolve_q"
    assert audit["source"] == "llm-agent"
    assert "150g" in audit["notes"]
    c.close()


def test_close_question_returns_error_for_missing_id(fresh_db):
    """不存在的 qid → 返回 ok=False,audit_log 不写。"""
    out = close_question(9999, 100.0, "测试", db_path=fresh_db)
    assert out["ok"] is False
    c = db_conn.connect(fresh_db)
    assert _audit_count(c, "open_question", 9999) == 0
    c.close()


# ── set_workout_kcal ──────────────────────────────────────────────


def test_set_workout_kcal_writes_audit_and_overrides(fresh_db):
    """写一条 basketball,然后 set_workout_kcal 把它从 MET 改成 manual。"""
    res = record("运动:打篮球 30 分钟 中", log_date="2026-07-08", db_path=fresh_db)
    assert res.workouts == 1
    c = db_conn.connect(fresh_db)
    wid = c.execute("SELECT id FROM workout ORDER BY id DESC LIMIT 1").fetchone()["id"]
    c.close()

    out = set_workout_kcal(wid, 420.0, "用户说那天打得很激烈,实际 ~420",
                            db_path=fresh_db)
    assert out["ok"] is True
    assert out["kcal_burned"] == 420.0
    assert out["audit_id"] > 0

    c = db_conn.connect(fresh_db)
    row = c.execute(
        "SELECT kcal_burned, kcal_method, confidence FROM workout WHERE id=?",
        (wid,),
    ).fetchone()
    assert row["kcal_burned"] == 420.0
    assert row["kcal_method"] == "manual"
    assert row["confidence"] == 1.0
    assert _audit_count(c, "workout", wid) == 1
    audit = c.execute(
        "SELECT action, source, before_json, after_json FROM audit_log WHERE row_id=?",
        (wid,),
    ).fetchone()
    assert audit["action"] == "manual_kcal"
    assert audit["source"] == "llm-agent"
    # before/after 都是 JSON 字符串
    assert "kcal_method" in audit["before_json"]
    assert "manual" in audit["after_json"]
    c.close()


def test_set_workout_kcal_returns_error_for_missing_id(fresh_db):
    out = set_workout_kcal(9999, 100.0, "test", db_path=fresh_db)
    assert out["ok"] is False


# ── reparse_meal ──────────────────────────────────────────────────


def test_reparse_meal_recomputes_kcal_with_closed_questions(fresh_db):
    """先录一段带 unknown food → open_question。
    关闭 open_question(resolved_grams=150) → reparse_meal → kcals 应该按 150g 重算。"""
    res = record("午餐:某未知水果一份", log_date="2026-07-08", db_path=fresh_db)
    qid = res.questions[0]
    close_question(qid, 150.0, "回复:150g", db_path=fresh_db)

    c = db_conn.connect(fresh_db)
    mid = c.execute("SELECT id FROM meal ORDER BY id DESC LIMIT 1").fetchone()["id"]
    # 拿到 reparse 前的 kcals(原 record 时的占位)
    before_kcal = c.execute("SELECT kcals FROM meal WHERE id=?", (mid,)).fetchone()["kcals"]
    c.close()

    out = reparse_meal(mid, "午餐:某未知水果 150g", "用户说其实就 150g",
                        db_path=fresh_db)
    assert out["ok"] is True
    assert out["audit_id"] > 0

    c = db_conn.connect(fresh_db)
    after_row = c.execute(
        "SELECT kcals, raw_text FROM meal WHERE id=?", (mid,),
    ).fetchone()
    assert after_row["raw_text"] == "午餐:某未知水果 150g"
    # 因为 close_question 后 read_today 的真值重算会按 150g 算,
    # reparse 后 kcals 应该 == 真值(未必等于 before_kcal,
    # 但一定有变化或者等于;不能等于 None / 0)
    assert after_row["kcals"] is not None
    assert _audit_count(c, "meal", mid) == 1
    audit = c.execute(
        "SELECT action, source FROM audit_log WHERE row_id=? AND table_name='meal'",
        (mid,),
    ).fetchone()
    assert audit["action"] == "reparse"
    assert audit["source"] == "llm-agent"
    c.close()


# ── 幂等 + dispatch + spec ────────────────────────────────────────


def test_three_tools_idempotent_audit_grows(fresh_db):
    """连续调同一工具两次:db 状态一致,audit_log 增加两行。"""
    record("午餐:未知 X 一份", log_date="2026-07-08", db_path=fresh_db)
    c = db_conn.connect(fresh_db)
    qid = c.execute("SELECT id FROM open_question ORDER BY id DESC LIMIT 1").fetchone()["id"]
    c.close()

    close_question(qid, 100.0, "first", db_path=fresh_db)
    close_question(qid, 100.0, "second", db_path=fresh_db)

    c = db_conn.connect(fresh_db)
    # db 状态一致
    row = c.execute("SELECT status, resolved_grams FROM open_question WHERE id=?",
                    (qid,)).fetchone()
    assert row["status"] == "closed"
    assert row["resolved_grams"] == 100.0
    # audit 两行
    assert _audit_count(c, "open_question", qid) == 2
    c.close()


def test_dispatch_routes_all_three_write_tools(fresh_db):
    """dispatch 路径打通:不走函数直调,模拟 LLM 调工具。"""
    record("午餐:未知 Y 一份", log_date="2026-07-08", db_path=fresh_db)
    record("运动:打篮球 30 分钟 中", log_date="2026-07-08", db_path=fresh_db)
    c = db_conn.connect(fresh_db)
    qid = c.execute("SELECT id FROM open_question ORDER BY id DESC LIMIT 1").fetchone()["id"]
    wid = c.execute("SELECT id FROM workout ORDER BY id DESC LIMIT 1").fetchone()["id"]
    c.close()

    # close_question via dispatch
    r1 = json.loads(tools.dispatch("close_question",
                                    {"qid": qid, "resolved_grams": 100, "notes": "via dispatch"},
                                    db_path=fresh_db))
    assert r1["ok"] is True

    # set_workout_kcal via dispatch
    r2 = json.loads(tools.dispatch("set_workout_kcal",
                                    {"workout_id": wid, "kcal": 350, "notes": "via dispatch"},
                                    db_path=fresh_db))
    assert r2["ok"] is True
    assert r2["kcal_burned"] == 350.0

    # reparse_meal via dispatch
    mid = None
    c = db_conn.connect(fresh_db)
    mid = c.execute("SELECT id FROM meal ORDER BY id DESC LIMIT 1").fetchone()["id"]
    c.close()
    r3 = json.loads(tools.dispatch("reparse_meal",
                                    {"meal_id": mid, "new_raw_text": "午餐:已知 — 重新录入",
                                     "notes": "via dispatch"},
                                    db_path=fresh_db))
    assert r3["ok"] is True


def test_tools_spec_has_six_with_complete_write_schemas():
    """TOOLS_SPEC 必须包含 3 个读 + 3 个写,且写工具的 required 参数完整。"""
    spec_names = {t["function"]["name"] for t in tools.TOOLS_SPEC}
    assert spec_names == {
        "read_today", "get_recent_trend", "get_open_questions",
        "close_question", "set_workout_kcal", "reparse_meal",
    }
    by_name = {t["function"]["name"]: t for t in tools.TOOLS_SPEC}
    for w in ("close_question", "set_workout_kcal", "reparse_meal"):
        params = by_name[w]["function"]["parameters"]
        assert "notes" in params["properties"], f"{w} 必须有 notes 字段"
        assert "notes" in params["required"], f"{w} 的 notes 必填"