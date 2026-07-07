"""reporter — deficit 计算器 + 体重趋势

deficit --date YYYY-MM-DD:
    摄入 kcal vs TDEE(BMR × activity_factor)
    估算周掉秤 weight_loss_kg/week = deficit_kcal_per_day × 7 / 7700

trend:
    7d / 14d / 30d weight 滑动平均

规则:
- BMR 优先读 inbody(最新一条;用 test_date 最新一条)
- BMR fallback 用 config/health_rules.yaml.inbody_recorded.bmr_kcal(1890)
- activity_factor 从 yaml 读
- 蛋白缺口 = protein_target(130) - 实际摄入
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from ..db.conn import connect

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "health_rules.yaml"

# 7700 kcal ≈ 1 kg 脂肪
KCAL_PER_KG_FAT = 7700


def _load_rules() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _latest_bmr(conn: sqlite3.Connection) -> tuple[Optional[float], Optional[str]]:
    """返回 (BMR kcal, 测的日期),从 inbody 表取最新一条。"""
    row = conn.execute(
        """SELECT basal_metabolic_rate_kcal, test_date
           FROM inbody
           WHERE basal_metabolic_rate_kcal IS NOT NULL
           ORDER BY test_date DESC LIMIT 1"""
    ).fetchone()
    if row:
        return float(row[0] or 0) or None, row[1]
    return None, None


def _latest_weight(conn: sqlite3.Connection) -> tuple[Optional[float], Optional[str]]:
    row = conn.execute(
        """SELECT weight_kg, measured_at FROM weight ORDER BY measured_at DESC LIMIT 1"""
    ).fetchone()
    if row:
        return float(row[0]) if row[0] is not None else None, row[1]
    return None, None


def _intake_kcal(conn: sqlite3.Connection, log_date: str) -> tuple[float, float]:
    """重算当日摄入,用 closed_question 真值替换 默认 grams。

    不用 `SUM(meal.kcals)` 因为那是 record 时按占位 default 写的;真实摄入
    必须按 closed_question.resolved_grams 重算。
    """
    from ..query import _recompute_meal

    closed_q: dict[str, float] = {}
    for r in conn.execute(
        """SELECT food_name, resolved_grams, status
           FROM open_question WHERE log_date=?""",
        (log_date,),
    ):
        if r["status"] == "closed" and r["food_name"]:
            rg = r["resolved_grams"]
            closed_q[r["food_name"]] = rg if (rg is not None and rg != 0.0) else None

    total_kcal = total_protein = 0.0
    for r in conn.execute(
        """SELECT parsed_json FROM meal WHERE log_date=?""",
        (log_date,),
    ):
        if not r[0]:
            continue
        defaults = {
            it.get("name"): it.get("grams", 0.0)
            for it in __import__("json").loads(r[0] or "[]")
        }
        # 简化:没有 parsed_json 的退回 raw(极端兼容路径)
        if not defaults:
            raw = conn.execute(
                """SELECT COALESCE(SUM(kcals),0), COALESCE(SUM(protein_g),0)
                   FROM meal WHERE log_date=?""",
                (log_date,),
            ).fetchone()
            return float(raw[0] or 0), float(raw[1] or 0)
        _items, k, p, _f, _c = _recompute_meal(r[0], closed_q, defaults)
        total_kcal += k
        total_protein += p

    return total_kcal, total_protein


def _trend_weight(conn: sqlite3.Connection, log_date: str, window_days: int) -> list[tuple[str, float]]:
    """取 log_date 之前 [1..window_days] 天的体重数据点。"""
    rows = conn.execute(
        """SELECT measured_at, weight_kg FROM weight
           WHERE measured_at <= ?
           ORDER BY measured_at DESC LIMIT ?""",
        (log_date, window_days * 2),  # 每两天最多一条
    ).fetchall()
    return [(r["measured_at"], r["weight_kg"]) for r in rows if r["weight_kg"] is not None]


@dataclass
class DeficitReport:
    log_date: str
    intake_kcal: float
    intake_protein_g: float
    bmr_kcal: Optional[float]
    bmr_source_date: Optional[str]
    activity_factor: float
    tdee_kcal: float
    deficit_kcal: float
    estimated_kg_per_week: Optional[float]
    protein_target_g: Optional[float]
    protein_gap: Optional[float]
    weight_kg: Optional[float]
    weight_date: Optional[str]
    weight_trend_7d: Optional[float]
    weight_trend_30d: Optional[float]
    notes: list[str]


def build_deficit(log_date: str, db_path: Optional[Path] = None) -> DeficitReport:
    rules = _load_rules()
    activity = float(rules.get("baselines", {}).get("activity_factor", 1.55))
    fallback_bmr = (
        rules.get("inbody_recorded", {}).get("bmr_kcal")
    )
    protein_target = rules.get("inbody_recorded", {}).get("daily_protein_target_g")

    conn = connect(db_path) if db_path else connect()
    try:
        bmr, bmr_date = _latest_bmr(conn) or (fallback_bmr, "yaml-fallback")
        intake_kcal, intake_protein = _intake_kcal(conn, log_date)
        weight_kg, weight_date = _latest_weight(conn)

        notes = []
        bmr_known = bmr is not None
        if not bmr_known:
            notes.append("⚠ BMR 缺失:未录 InBody")
            tdee = 0
            deficit = 0
            kg_week = None
        else:
            tdee = bmr * activity
            deficit = tdee - intake_kcal  # 正 = 减脂(吃得比 TDEE 少)
            kg_week = (deficit * 7) / KCAL_PER_KG_FAT

        if intake_kcal < 100:
            kg_week = None
            notes.append(
                f"⚠ 当日摄入 {intake_kcal:.0f} kcal 低于 100,跳过估算(可能未录)"
            )

        protein_gap = (protein_target - intake_protein) if protein_target else None

        # 体重滑动平均
        trend_7d, trend_30d = None, None
        ws = _trend_weight(conn, log_date, 30)
        if len(ws) >= 2:
            avg = lambda n: sum(w for _, w in ws[:n]) / min(n, len(ws))
            trend_7d = avg(7)
            trend_30d = avg(30)

        return DeficitReport(
            log_date=log_date,
            intake_kcal=round(intake_kcal, 0),
            intake_protein_g=round(intake_protein, 1),
            bmr_kcal=bmr,
            bmr_source_date=bmr_date,
            activity_factor=activity,
            tdee_kcal=tdee,
            deficit_kcal=round(deficit, 0),
            estimated_kg_per_week=round(kg_week, 2) if kg_week is not None else None,
            protein_target_g=protein_target,
            protein_gap=round(protein_gap, 1) if protein_gap is not None else None,
            weight_kg=weight_kg,
            weight_date=weight_date,
            weight_trend_7d=round(trend_7d, 1) if trend_7d is not None else None,
            weight_trend_30d=round(trend_30d, 1) if trend_30d is not None else None,
            notes=notes,
        )
    finally:
        conn.close()


def format_deficit(r: DeficitReport) -> str:
    lines: list[str] = []
    lines.append(f"HealthOS — {r.log_date} 减脂报告")
    lines.append("")

    lines.append("── 摄入 ──")
    lines.append(f"  kcal {r.intake_kcal:.0f}   蛋白 {r.intake_protein_g:.1f} g")
    if r.protein_target_g is not None:
        gap_sign = "差" if r.protein_gap > 0 else "超出"
        lines.append(f"  蛋白目标 {r.protein_target_g}g — {gap_sign} {abs(r.protein_gap):.1f}g")
    lines.append("")

    lines.append("── 减脂 ──")
    if r.bmr_kcal:
        lines.append(f"  BMR {r.bmr_kcal:.0f} kcal (来源: {'InBody ' + r.bmr_source_date if r.bmr_source_date and r.bmr_source_date != 'yaml-fallback' else 'yaml fallback'})")
        lines.append(f"  活动系数 {r.activity_factor:.2f}")
        lines.append(f"  TDEE 估算 {r.tdee_kcal:.0f} kcal")
        lines.append(f"  缺口 {r.deficit_kcal:+.0f} kcal (摄入 - TDEE;正=减脂)")
        if r.estimated_kg_per_week is not None:
            lines.append(f"  估算掉秤 {r.estimated_kg_per_week:.2f} kg/wk (按 7700 kcal/kg)")
        else:
            lines.append(f"  缺口为负或多摄入 → 不能简单估算掉秤")
    else:
        lines.append("  BMR 缺失:跑一次 InBody")
    lines.append("")

    lines.append("── 体重 ──")
    if r.weight_kg is not None:
        lines.append(f"  最新 {r.weight_kg:.1f} kg ({r.weight_date})")
        if r.weight_trend_7d is not None:
            lines.append(f"  7 天平均 {r.weight_trend_7d:.1f} kg")
        if r.weight_trend_30d is not None:
            lines.append(f"  30 天平均 {r.weight_trend_30d:.1f} kg")
    else:
        lines.append("  (无体重记录)")
    lines.append("")

    if r.notes:
        for n in r.notes:
            lines.append(n)
    return "\n".join(lines)
