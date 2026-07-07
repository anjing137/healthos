"""D31 — /fix-meal <id>:

工作流:
1. 取 meal 行 #<id> 的 raw_text → 给 user 看
2. user 输入新 raw_text(一行)— `/` 取消
3. parser 重跑 → updated kcal/protein/parsed_json
4. UPDATE meal.raw_text + 营养字段
5. 写 audit_log(before=旧 row, after=新 row)

约束:
- 不动关联 open_question(已经 closed-state 完成了)
- 不动 verify_pending
- audit_log 真实进 disk,user 之后可查
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..db.conn import connect
from ..parser import parse as parse_sections
from ..nutrition.quantify import parse_item, ParsedQuantity


@dataclass
class FixResult:
    meal_id: int
    raw_text_before: str
    raw_text_after: str
    kcals_before: float
    protein_before: float
    fat_before: float
    carb_before: float
    parsed_json_before: str
    kcals_after: float
    protein_after: float
    fat_after: float
    carb_after: float
    parsed_json_after: str
    audit_log_id: Optional[int]
    warnings: list[str]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _post_meal(
    log_date: str,
    meal_slot: str,
    raw: str,
    parsed: list[ParsedQuantity],
) -> int:
    """跟 record._write_meal 一样 — 上 reparse 后的数据。

    重写 _write_meal 一次,避免 import record 内部 private 函数。
    """
    k = sum(p.kcals() or 0.0 for p in parsed)
    p = sum(p.protein_g() or 0.0 for p in parsed)
    f = sum(p.fat_g() or 0.0 for p in parsed)
    c = sum(p.carb_g() or 0.0 for p in parsed)
    parsed_json = json.dumps(
        [
            {
                "raw": raw,
                "name": p.food_hit.name if p.food_hit else None,
                "qty": p.qty,
                "unit": p.unit,
                "grams": round(p.grams, 1),
                "kcals": round(p.kcals() or 0, 1),
                "protein_g": round(p.protein_g() or 0, 1),
                "inline": p.inline,
                "confidence": p.confidence,
            }
            for p in parsed
        ],
        ensure_ascii=False,
    )
    return (k, p, f, c, parsed_json)


def reparse_raw_text(
    raw: str, log_date: str, db_path: Optional[Path] = None
) -> dict[str, Any]:
    """对一段 raw_text 跑 lenient parser,返回 kcal/protein/fat/carb + parsed_json。"""
    sections = parse_sections(raw, strict=False, split_compound=True)
    parsed: list[ParsedQuantity] = []
    warnings: list[str] = []
    for sec in sections:
        for it in sec.items:
            pqs = parse_item(it)
            parsed.extend(pqs)
            for pq in pqs:
                if not pq.food_hit:
                    warnings.append(f"unknown food: {it!r}")
    k, p, f, c, parsed_json = _post_meal(log_date, "(reparse)", raw, parsed)
    return {
        "kcals": round(k, 1),
        "protein_g": round(p, 1),
        "fat_g": round(f, 1),
        "carb_g": round(c, 1),
        "parsed_json": parsed_json,
        "warnings": warnings,
    }


def fix_meal(meal_id: int, new_raw: str, db_path: Optional[Path] = None) -> FixResult:
    """改 meal 行的 raw_text,重 parse,写 audit_log。

    Caller 通常是 REPL(/fix-meal)或 CLI。
    """
    if not new_raw.strip():
        raise ValueError("new_raw 不能为空")

    conn = connect(db_path) if db_path else connect()
    try:
        # 1. 取老 row
        row = conn.execute(
            "SELECT id, log_date, meal_slot, raw_text, kcals, protein_g, fat_g, carb_g, parsed_json FROM meal WHERE id=?",
            (meal_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"meal#{meal_id} 不存在")

        log_date = row["log_date"]
        before = {
            "raw_text": row["raw_text"],
            "kcals": row["kcals"],
            "protein_g": row["protein_g"],
            "fat_g": row["fat_g"],
            "carb_g": row["carb_g"],
            "parsed_json": row["parsed_json"],
        }

        # 2. 重 parse
        rep = reparse_raw_text(new_raw, log_date, db_path=db_path)

        # 3. UPDATE
        conn.execute(
            """UPDATE meal
               SET raw_text=?, kcals=?, protein_g=?, fat_g=?, carb_g=?,
                   parsed_json=?, logged_at=?
               WHERE id=?""",
            (
                new_raw, rep["kcals"], rep["protein_g"],
                rep["fat_g"], rep["carb_g"],
                rep["parsed_json"], _now_iso(),
                meal_id,
            ),
        )

        # 4. 写 audit_log
        cur = conn.execute(
            """INSERT INTO audit_log(created_at, action, table_name, row_id,
                                    source, before_json, after_json, notes)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(),
                "update",
                "meal",
                meal_id,
                "repl",  # 当前调用方
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps(
                    {
                        "raw_text": new_raw,
                        "kcals": rep["kcals"],
                        "protein_g": rep["protein_g"],
                        "fat_g": rep["fat_g"],
                        "carb_g": rep["carb_g"],
                        "parsed_json": rep["parsed_json"],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                "fix-meal reparse raw_text",
            ),
        )
        audit_id = int(cur.lastrowid)
        conn.commit()

        return FixResult(
            meal_id=meal_id,
            raw_text_before=row["raw_text"],
            raw_text_after=new_raw,
            kcals_before=row["kcals"] or 0.0,
            protein_before=row["protein_g"] or 0.0,
            fat_before=row["fat_g"] or 0.0,
            carb_before=row["carb_g"] or 0.0,
            parsed_json_before=row["parsed_json"] or "",
            kcals_after=rep["kcals"],
            protein_after=rep["protein_g"],
            fat_after=rep["fat_g"],
            carb_after=rep["carb_g"],
            parsed_json_after=rep["parsed_json"],
            audit_log_id=audit_id,
            warnings=rep["warnings"],
        )
    finally:
        conn.close()


def get_meal(meal_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """REPL 第一次看 meal 信息。"""
    conn = connect(db_path) if db_path else connect()
    try:
        row = conn.execute(
            """SELECT id, log_date, meal_slot, raw_text, kcals, protein_g, fat_g, carb_g
               FROM meal WHERE id=?""",
            (meal_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
