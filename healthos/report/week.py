"""D7 — 周/趋势汇总。

`build_week(window_days=7)` 返回每天的(deficit + meal totals + workout_minutes)
列表,真读 yaml(目标蛋白 / activity_factor)。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml

from .deficit import build_deficit, _load_rules, KCAL_PER_KG_FAT
from ..db.conn import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "health_rules.yaml"


@dataclass
class DayRow:
    log_date: str
    intake_kcal: float
    intake_protein_g: float
    workout_minutes: int
    deficit_kcal: Optional[float]
    estimated_kg_per_week: Optional[float]
    has_open_questions: bool
    has_verify_pending: bool


def build_week(
    end_date: Optional[str] = None,
    window_days: int = 7,
    db_path: Optional[Path] = None,
) -> list[DayRow]:
    """返回最近 N 天的每日 row,end_date 是窗口的最末日(默认今天)。"""
    rules = _load_rules()
    activity = float(rules.get("baselines", {}).get("activity_factor", 1.55))
    fallback_bmr = rules.get("inbody_recorded", {}).get("bmr_kcal")

    end = date.fromisoformat(end_date) if end_date else date.today()
    rows: list[DayRow] = []

    conn = connect(db_path) if db_path else connect()
    try:
        for delta in range(window_days - 1, -1, -1):
            log_date = (end - timedelta(days=delta)).isoformat()
            # intake(用 build_deficit 的 _intake_kcal 重算)
            from .deficit import _intake_kcal as _calc_intake
            intake_kcal, intake_protein = _calc_intake(conn, log_date)
            # workout
            w_sum = conn.execute(
                "SELECT COALESCE(SUM(duration_min), 0) FROM workout WHERE log_date=?",
                (log_date,),
            ).fetchone()[0]
            # deficit
            d = build_deficit(log_date, db_path)
            # any open questions
            nq = conn.execute(
                "SELECT COUNT(*) FROM open_question WHERE log_date=? AND status='open'",
                (log_date,),
            ).fetchone()[0]
            nv = conn.execute(
                "SELECT COUNT(*) FROM verify_pending WHERE log_date=? AND status='open'",
                (log_date,),
            ).fetchone()[0]
            rows.append(DayRow(
                log_date=log_date,
                intake_kcal=round(intake_kcal, 1),
                intake_protein_g=round(intake_protein, 1),
                workout_minutes=int(w_sum or 0),
                deficit_kcal=d.deficit_kcal,
                estimated_kg_per_week=d.estimated_kg_per_week,
                has_open_questions=nq > 0,
                has_verify_pending=nv > 0,
            ))
    finally:
        conn.close()
    return rows


def format_week(rows: list[DayRow], protein_target_g: Optional[int] = None) -> str:
    """格式化 week 输出,表格化。"""
    lines = ["HealthOS — 最近 7 天", ""]
    lines.append(f"{'日期':<12}{'kcal':>8}{'蛋白 g':>8}{'运动 min':>10}{'缺口 kcal':>11}{'估算掉秤':>11}{'flag':>20}")
    lines.append("-" * 80)
    for r in rows:
        flags = []
        if r.has_open_questions:
            flags.append("open-q")
        if r.has_verify_pending:
            flags.append("verify")
        flag_str = ",".join(flags) if flags else ""
        deficit_str = f"{r.deficit_kcal:+.0f}" if r.deficit_kcal is not None else "—"
        kg_str = f"{r.estimated_kg_per_week:.2f} kg/w" if r.estimated_kg_per_week is not None else "—"
        lines.append(
            f"{r.log_date:<12}{r.intake_kcal:>8.0f}{r.intake_protein_g:>8.1f}{r.workout_minutes:>10}{deficit_str:>11}{kg_str:>11}{flag_str:>20}"
        )

    if protein_target_g is not None:
        lines.append("")
        lines.append(f"蛋白目标(来自 yaml):{protein_target_g} g/天")

    avg_kcal = sum(r.intake_kcal for r in rows) / len(rows)
    avg_protein = sum(r.intake_protein_g for r in rows) / len(rows)
    lines.append("")
    lines.append(f"7 天平均 kcal {avg_kcal:.0f} / 蛋白 {avg_protein:.1f} g")
    return "\n".join(lines)
