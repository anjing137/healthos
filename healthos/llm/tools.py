"""LLM 工具函数 — 给 LLM 提供"受控读"SQLite 的方式。

设计:
- 工具是 Python 函数,LLM 通过 function call 触发
- 每个工具调用都是受控 SQL,不带参数注入风险
- LLM 不能"读任意 SQL" — 它只能通过这些工具
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..db.conn import connect, DEFAULT_DB_PATH


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    return connect(db_path) if db_path else connect()


def read_today(log_date: str, db_path: Path | None = None) -> dict[str, Any]:
    """返回当日 meal / workout / sleep / knee_status / weight 摘要。

    用 query._recompute_meal 重算 kcal/protein(closed_question 真值替换 default grams),
    不用 raw meal.kcals — 那是 record 时按占位 default 写的。
    """
    from ..query import _recompute_meal

    conn = _conn(db_path)
    try:
        closed_q: dict[str, float] = {}
        for r in conn.execute(
            """SELECT food_name, resolved_grams, status
               FROM open_question WHERE log_date=?""",
            (log_date,),
        ):
            if r["status"] == "closed" and r["food_name"]:
                rg = r["resolved_grams"]
                closed_q[r["food_name"]] = rg if (rg is not None and rg != 0.0) else None

        meals: list[dict] = []
        total_kcal = total_prot = 0.0
        for r in conn.execute(
            """SELECT id, meal_slot, kcals, protein_g, fat_g, carb_g, raw_text, parsed_json
               FROM meal WHERE log_date=? ORDER BY id""",
            (log_date,),
        ):
            if r["parsed_json"]:
                defaults = {
                    it.get("name"): it.get("grams", 0.0)
                    for it in __import__("json").loads(r["parsed_json"] or "[]")
                }
                _items, k, p, _f, _c = _recompute_meal(r["parsed_json"], closed_q, defaults)
                meals.append({
                    "id": r["id"],
                    "meal_slot": r["meal_slot"],
                    "raw_text": r["raw_text"],
                    "kcals": round(k, 1),
                    "protein_g": round(p, 1),
                    "fat_g": round(_f, 1),
                    "carb_g": round(_c, 1),
                    "items": _items,
                })
                total_kcal += k
                total_prot += p
            else:
                meals.append({
                    "id": r["id"],
                    "meal_slot": r["meal_slot"],
                    "raw_text": r["raw_text"],
                    "kcals": r["kcals"],
                    "protein_g": r["protein_g"],
                    "fat_g": r["fat_g"],
                    "carb_g": r["carb_g"],
                })
                total_kcal += r["kcals"] or 0
                total_prot += r["protein_g"] or 0

        workouts: list[dict] = []
        for r in conn.execute(
            "SELECT duration_min, raw_text FROM workout WHERE log_date=? ORDER BY id",
            (log_date,),
        ):
            workouts.append(dict(r))
        sleep: list[dict] = []
        for r in conn.execute(
            "SELECT bedtime, wake_time, duration_min FROM sleep WHERE log_date=? ORDER BY id",
            (log_date,),
        ):
            sleep.append(dict(r))
        knees: list[dict] = []
        for r in conn.execute(
            "SELECT tightness, pain, swelling, notes, logged_at FROM knee_status WHERE log_date=? ORDER BY id",
            (log_date,),
        ):
            knees.append(dict(r))
        weights: list[dict] = []
        for r in conn.execute(
            "SELECT weight_kg, measured_at_hhmm FROM weight WHERE measured_at=? ORDER BY id",
            (log_date,),
        ):
            weights.append(dict(r))
        return {
            "log_date": log_date,
            "total_kcal": round(total_kcal, 1),
            "total_protein_g": round(total_prot, 1),
            "meals": meals,
            "workouts": workouts,
            "sleep": sleep,
            "knee_status": knees,
            "weights": weights,
        }
    finally:
        conn.close()


def get_recent_trend(window_days: int, db_path: Path | None = None) -> dict[str, Any]:
    """返回最近 N 天的体重 + 摄入(每行) + open_question closed 数。"""
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """SELECT measured_at, weight_kg FROM weight
               ORDER BY measured_at DESC LIMIT ?""",
            (window_days,),
        ).fetchall()
        weights = [dict(r) for r in rows]

        rows2 = conn.execute(
            """SELECT log_date, SUM(kcals) AS kcal, SUM(protein_g) AS prot
               FROM meal
               WHERE log_date >= date('now', ?)
               GROUP BY log_date ORDER BY log_date DESC""",
            (f"-{window_days} day",),
        ).fetchall()
        intake = [dict(r) for r in rows2]

        inbody = conn.execute(
            """SELECT test_date, weight_kg, body_fat_pct, basal_metabolic_rate_kcal
               FROM inbody ORDER BY test_date DESC LIMIT 1"""
        ).fetchone()
        latest_inbody = dict(inbody) if inbody else None
        return {
            "window_days": window_days,
            "weights": weights,
            "intake_by_day": intake,
            "latest_inbody": latest_inbody,
        }
    finally:
        conn.close()


def get_open_questions(log_date: str | None = None, db_path: Path | None = None) -> list[dict]:
    """返回 status='open' 的 open_question。LLM 用它来知道哪些数字还在猜。"""
    conn = _conn(db_path)
    try:
        if log_date:
            rows = conn.execute(
                """SELECT id, log_date, meal_slot, raw_item, food_name,
                          default_grams, default_kcals, default_protein_g, question
                   FROM open_question WHERE log_date=? AND status='open'
                   ORDER BY id""",
                (log_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, log_date, meal_slot, raw_item, food_name,
                          default_grams, default_kcals, default_protein_g, question
                   FROM open_question WHERE status='open'
                   ORDER BY id"""
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Tool spec for DeepSeek — OpenAI function calling format ────────


TOOLS_SPEC: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_today",
            "description": "读取某一天 (YYYY-MM-DD) 的所有 health 数据:饮食/训练/睡眠/膝盖/体重。LLM 用此事实核对用户说的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "log_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD 日期",
                    }
                },
                "required": ["log_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_trend",
            "description": "读取最近 N 天的体重 + 摄入 + 最新 InBody(趋势分析)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_days": {
                        "type": "integer",
                        "description": "窗口天数 (例如 7 / 14 / 30)",
                    }
                },
                "required": ["window_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_questions",
            "description": "列出所有 status='open' 的 open_question 项目(待回答 / 数字仍然在猜的)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "log_date": {
                        "type": "string",
                        "description": "可选,YYYY-MM-DD,只显示当天",
                    }
                },
            },
        },
    },
]


def dispatch(name: str, args: dict, db_path: Path | None = None) -> str:
    """调用 LLM 触发的 tool。返 JSON 字符串。"""
    try:
        if name == "read_today":
            return json.dumps(read_today(args["log_date"], db_path=db_path), ensure_ascii=False, default=str)
        if name == "get_recent_trend":
            return json.dumps(get_recent_trend(int(args["window_days"]), db_path=db_path), ensure_ascii=False, default=str)
        if name == "get_open_questions":
            return json.dumps(get_open_questions(args.get("log_date"), db_path=db_path), ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"error": f"unknown tool {name}"})
