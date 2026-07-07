"""D4.5 C — query 层。

设计:
- 不重写 meal 行;只在 query 时用 parsed_json + open_question(closed + resolved_grams)做"真值合计"
- 当 parsed_json 里某条 food 命中 closed_question,就用 resolved_grams 替代 grams;
  否则用 record 时的 default grams
- 输出 TodayReport 与 OpenQuestions 两个 dataclass, CLI 用它们打印
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from ..db.conn import connect
from ..nutrition.foods import lookup


@dataclass
class MealRow:
    meal_slot: str
    raw_text: str
    kcals: float
    protein_g: float
    fat_g: float
    carb_g: float
    items: list[dict]  # 每条 ParsedQuantity 投影


@dataclass
class TodayReport:
    log_date: str
    meals: list[MealRow] = field(default_factory=list)
    workout_minutes: int = 0
    # v1 — workout kcal 估算合并
    workout_kcal: float = 0.0
    workout_kcal_estimated_count: int = 0
    workout_kcal_manual_count: int = 0
    workout_pending_count: int = 0
    sleep_duration_min: Optional[float] = None
    knee_tightness: Optional[int] = None
    kcals: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carb_g: float = 0.0
    open_questions: list[dict] = field(default_factory=list)


def _recompute_meal(
    parsed_json: str,
    resolved: dict[str, float],
    defaults: dict[str, float],
) -> tuple[list[dict], float, float, float, float]:
    """从 parsed_json 拆出每条,按 resolved/default 替换 grams,算出真营养。

    resolved: food_name -> resolved_grams(closed 的)
    defaults: food_name -> default_grams(未 closed 的 food 当前 grams —— 这次用)
    """
    items = json.loads(parsed_json or "[]")
    new_items: list[dict] = []
    k = p = f = c = 0.0
    for it in items:
        name = it.get("name")
        default_grams = it.get("grams", 0.0)
        hit = lookup(name) if name else None
        # resolved value 可能是 None(closed-with-zero 表示"未答")或 0.x 真值。
        # None → fallback 到 record 时 default;有数值(含 0)→ 用真值(0 = 用户说没吃)。
        resolved_val = resolved.get(name or "")
        grams = resolved_val if resolved_val is not None else default_grams
        if hit and grams > 0:
            kcal = grams * hit.macros.kcals / 100.0
            prot = grams * hit.macros.protein_g / 100.0
            fat = grams * hit.macros.fat_g / 100.0
            carb = grams * hit.macros.carb_g / 100.0
        elif hit and grams == 0:
            # User 答 0g / 没吃
            kcal = prot = fat = carb = 0.0
        else:
            kcal = it.get("kcals", 0.0)
            prot = it.get("protein_g", 0.0)
            fat = 0.0
            carb = 0.0
        k += kcal
        p += prot
        f += fat
        c += carb
        new_items.append(
            {
                **it,
                "resolved_grams": grams,
                "resolved_kcals": round(kcal, 1),
                "resolved_protein_g": round(prot, 1),
            }
        )
    return new_items, k, p, f, c


def build_today(log_date: str, db_path: Optional[Path] = None) -> TodayReport:
    conn = connect(db_path) if db_path else connect()
    try:
        rep = TodayReport(log_date=log_date)

        # 收集 open_question(仅 open)+ 已 closed
        # closed_q: food_name -> resolved_grams(if valid;0/None → fall back to default grams)
        closed_q: dict[str, float] = {}
        all_open: list[dict] = []
        for r in conn.execute(
            """SELECT id, log_date, meal_slot, raw_item, food_name,
                      default_grams, resolved_grams, default_kcals,
                      default_protein_g, question, status
               FROM open_question WHERE log_date=?""",
            (log_date,),
        ):
            d = dict(r)
            if d["status"] == "closed" and d["food_name"]:
                rg = d["resolved_grams"]
                # resolved_grams=None 或 0 → 当作"未答",让 _recompute_meal fallback
                # 这是因为 learn 早期 closed-and-zero 占位 (例如 sklearn-style 'skip' 走 0g)
                # 在那之后才换 settle 机制。
                if rg is None or rg == 0.0:
                    rg = None  # signal fallback
                closed_q[d["food_name"]] = rg
            if d["status"] == "open":
                all_open.append(d)

        # meal
        for r in conn.execute(
            """SELECT meal_slot, raw_text, parsed_json, kcals, protein_g
               FROM meal WHERE log_date=?
               ORDER BY id""",
            (log_date,),
        ):
            defaults = {it.get("name"): it.get("grams", 0.0) for it in json.loads(r["parsed_json"] or "[]")}
            items, k, p, f, c = _recompute_meal(r["parsed_json"], closed_q, defaults)
            row = MealRow(
                meal_slot=r["meal_slot"],
                raw_text=r["raw_text"],
                kcals=round(k, 1),
                protein_g=round(p, 1),
                fat_g=round(f, 1),
                carb_g=round(c, 1),
                items=items,
            )
            rep.meals.append(row)
            rep.kcals += k
            rep.protein_g += p
            rep.fat_g += f
            rep.carb_g += c

        rep.kcals = round(rep.kcals, 1)
        rep.protein_g = round(rep.protein_g, 1)
        rep.fat_g = round(rep.fat_g, 1)
        rep.carb_g = round(rep.carb_g, 1)

        # workout
        w = conn.execute(
            """SELECT COALESCE(SUM(duration_min), 0) AS total
               FROM workout WHERE log_date=?""",
            (log_date,),
        ).fetchone()
        rep.workout_minutes = int(w["total"] or 0)

        # v1 — workout kcal 估算 + method 计数
        wk = conn.execute(
            """SELECT
                 COALESCE(SUM(kcal_burned), 0) AS total_kcal,
                 COALESCE(SUM(CASE WHEN kcal_method='MET'    THEN 1 ELSE 0 END), 0) AS n_met,
                 COALESCE(SUM(CASE WHEN kcal_method='manual' THEN 1 ELSE 0 END), 0) AS n_manual,
                 COALESCE(SUM(CASE WHEN kcal_method='pending' THEN 1 ELSE 0 END), 0) AS n_pending
               FROM workout WHERE log_date=?""",
            (log_date,),
        ).fetchone()
        rep.workout_kcal = round(float(wk["total_kcal"] or 0), 1)
        rep.workout_kcal_estimated_count = int(wk["n_met"] or 0) + int(wk["n_manual"] or 0)
        rep.workout_kcal_manual_count = int(wk["n_manual"] or 0)
        rep.workout_pending_count = int(wk["n_pending"] or 0)

        # sleep
        s = conn.execute(
            """SELECT duration_min FROM sleep WHERE log_date=? ORDER BY id DESC LIMIT 1""",
            (log_date,),
        ).fetchone()
        rep.sleep_duration_min = s["duration_min"] if s else None

        # knee
        k = conn.execute(
            """SELECT tightness FROM knee_status WHERE log_date=? ORDER BY id DESC LIMIT 1""",
            (log_date,),
        ).fetchone()
        rep.knee_tightness = k["tightness"] if k else None

        rep.open_questions = all_open
        return rep
    finally:
        conn.close()
