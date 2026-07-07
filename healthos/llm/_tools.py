"""LLM 触发的写工具 — 都走 audit_log,无 SQL 注入风险。

设计契约:
1. 每个写工具先 snapshot before,行不存在就返回 error,**不写 audit**。
2. 执行实际 UPDATE。
3. snapshot after。
4. 写 audit_log 一行(source='llm-agent')。

为什么不直接给 LLM run_sql:
- 防止 LLM 写错 UPDATE(WHERE 漏 / 多改 / 改错字段)
- 防止 LLM 被 prompt 注入干危险操作
- 每个写工具的 schema 边界是 Python 代码层面维护的(不是 prompt)

每个写工具在 tools.py 内是普通函数,在 dispatch() 注册,在 TOOLS_SPEC 暴露。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _audit_write(
    conn: sqlite3.Connection,
    *,
    action: str,
    table_name: str,
    row_id: int,
    before: dict,
    after: dict,
    notes: str,
    source: str = "llm-agent",
) -> int:
    """写 audit_log 一行。source 默认 'llm-agent'。

    Returns:
      写入的 audit_log.id
    """
    cur = conn.execute(
        """INSERT INTO audit_log(created_at, action, table_name, row_id,
                                  source, before_json, after_json, notes)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            action,
            table_name,
            row_id,
            source,
            json.dumps(before, ensure_ascii=False, default=str),
            json.dumps(after, ensure_ascii=False, default=str),
            notes,
        ),
    )
    return int(cur.lastrowid)


def close_question(
    qid: int,
    resolved_grams: float,
    notes: str,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """关闭一条 open_question。

    Args:
      qid: open_question.id
      resolved_grams: 用户给的真实克数(可以 0.0 表示没吃)
      notes: LLM 必须说明为什么这样关(进 audit_log notes)

    Returns:
      {"ok": True, "qid": ..., "audit_id": ..., "resolved_grams": ...} 或
      {"ok": False, "error": ...}
    """
    conn = connect(db_path) if db_path else connect()
    try:
        # 1) before snapshot
        row = conn.execute(
            "SELECT * FROM open_question WHERE id=?", (qid,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"open_question #{qid} 不存在"}
        before = dict(row)

        # 2) UPDATE
        conn.execute(
            """UPDATE open_question
               SET resolved_grams=?, status='closed'
               WHERE id=?""",
            (resolved_grams, qid),
        )

        # 3) after snapshot
        row2 = conn.execute(
            "SELECT * FROM open_question WHERE id=?", (qid,)
        ).fetchone()
        after = dict(row2)

        # 4) audit
        audit_id = _audit_write(
            conn,
            action="resolve_q",
            table_name="open_question",
            row_id=qid,
            before=before,
            after=after,
            notes=notes,
        )
        conn.commit()
        return {
            "ok": True,
            "qid": qid,
            "audit_id": audit_id,
            "resolved_grams": resolved_grams,
            "status": "closed",
        }
    finally:
        conn.close()


def set_workout_kcal(
    workout_id: int,
    kcal: float,
    notes: str,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """手动校准一条 workout 的 kcal_burned(method='manual', confidence=1.0)。

    与 healthos fix-workout CLI 等价,但走 audit_log,source='llm-agent'。

    Args:
      workout_id: workout.id
      kcal: 真实 kcal
      notes: LLM 必须说明为什么这样校准(进 audit_log)

    Returns:
      {"ok": True, "workout_id": ..., "audit_id": ..., "kcal_burned": ...} 或
      {"ok": False, "error": ...}
    """
    conn = connect(db_path) if db_path else connect()
    try:
        # 1) before snapshot
        row = conn.execute(
            "SELECT * FROM workout WHERE id=?", (workout_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"workout #{workout_id} 不存在"}
        before = dict(row)

        # 2) UPDATE
        conn.execute(
            """UPDATE workout
               SET kcal_burned=?, kcal_method='manual', confidence=1.0
               WHERE id=?""",
            (kcal, workout_id),
        )

        # 3) after snapshot
        row2 = conn.execute(
            "SELECT * FROM workout WHERE id=?", (workout_id,)
        ).fetchone()
        after = dict(row2)

        # 4) audit
        audit_id = _audit_write(
            conn,
            action="manual_kcal",
            table_name="workout",
            row_id=workout_id,
            before=before,
            after=after,
            notes=notes,
        )
        conn.commit()
        return {
            "ok": True,
            "workout_id": workout_id,
            "audit_id": audit_id,
            "kcal_burned": kcal,
            "kcal_method": "manual",
            "confidence": 1.0,
        }
    finally:
        conn.close()


def reparse_meal(
    meal_id: int,
    new_raw_text: str,
    notes: str,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """替换一条 meal 的 raw_text 并按新 raw 重新算 kcal / protein_g / fat_g / carb_g。

    实现策略(v1,简化):不重跑 parser+quantify,只重算 kcals 字段 ——
    用 build_today 同款 _recompute_meal 真值逻辑(CLAUDE.md 第 4 条)。
    即如果 meal 的 parsed_json 里每条 item,按当前 open_question 的 resolved_grams
    重算真值;否则保持原 parsed_json 不动。

    这是受控的"重新 SUM"动作,不会改 raw_text 之外的字段名/类型。
    """
    from ..query import _recompute_meal

    conn = connect(db_path) if db_path else connect()
    try:
        row = conn.execute(
            "SELECT * FROM meal WHERE id=?", (meal_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"meal #{meal_id} 不存在"}
        before = dict(row)

        # closed_question 真值表
        closed_q: dict[str, float] = {}
        for r in conn.execute(
            """SELECT food_name, resolved_grams FROM open_question
               WHERE log_date=? AND status='closed'""",
            (before["log_date"],),
        ):
            if r["food_name"]:
                rg = r["resolved_grams"]
                closed_q[r["food_name"]] = rg if rg is not None else 0.0

        defaults: dict[str, float] = {}
        if before["parsed_json"]:
            for it in json.loads(before["parsed_json"]):
                defaults[it.get("name")] = it.get("grams", 0.0)

        _items, k, p, f, c = _recompute_meal(
            before["parsed_json"] or "[]", closed_q, defaults
        )

        # UPDATE — 只动 raw_text + 数字字段,不动 slot/log_date/parsed_json(避免破坏结构)
        conn.execute(
            """UPDATE meal
               SET raw_text=?, kcals=?, protein_g=?, fat_g=?, carb_g=?
               WHERE id=?""",
            (new_raw_text, round(k, 1), round(p, 1), round(f, 1), round(c, 1), meal_id),
        )

        row2 = conn.execute("SELECT * FROM meal WHERE id=?", (meal_id,)).fetchone()
        after = dict(row2)

        audit_id = _audit_write(
            conn,
            action="reparse",
            table_name="meal",
            row_id=meal_id,
            before=before,
            after=after,
            notes=notes,
        )
        conn.commit()
        return {
            "ok": True,
            "meal_id": meal_id,
            "audit_id": audit_id,
            "kcals": round(k, 1),
            "protein_g": round(p, 1),
        }
    finally:
        conn.close()


# ─── 从 tools.py 顶部 import 一下 connect,避免重新写 ──────────────
# 注意这个文件 _tools.py 是单独新建的。它从 healthos.db.conn 拿 connect,
# 不从 healthos.llm.tools import — 避免循环引用。
from ..db.conn import connect  # noqa: E402