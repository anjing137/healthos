"""D6 — Markdown 日报导出。

`healthos export [--date ...] [--out ...]` 把 today / deficit / verify_pending / chat_log
按 Jinja 模板渲染,落地一份 .md,给 Obsidian 之类的工具读。
"""

from __future__ import annotations

import json
import sqlite3
import warnings
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Template

from ..query import build_today
from ..report.deficit import build_deficit
from ..db.conn import connect, DEFAULT_DB_PATH
from ..record.write import today as today_iso_default


# 默认 push 到 Obsidian vault — 用户最终用途
DEFAULT_VAULT_PATH = Path("/Volumes/video/obsidian/health")
DEFAULT_VAULT_DAILY_SUBDIR = "Daily"


def _default_target_path(log_date: str) -> Path:
    """Obsidian vault 优先;vault 不存在 → fallback 到 data/exports(但应该不会发生)。"""
    target = DEFAULT_VAULT_PATH / DEFAULT_VAULT_DAILY_SUBDIR / f"{log_date}.md"
    return target


def _fallback_target_path(log_date: str) -> Path:
    p = Path(__file__).resolve().parents[2] / "data" / "exports" / f"{log_date}.md"
    return p


TEMPLATE = Template(
"""# HealthOS Daily — {{ date }}

> 生成时间:{{ generated_at }}

## 一、今日摄入

| 段 | kcal | 蛋白 g | 脂肪 g | 碳水 g |
|---|---|---|---|---|
{% for m in meals -%}
| {{ m.meal_slot }} | {{ "%.0f"|format(m.kcals) }} | {{ "%.1f"|format(m.protein_g) }} | {{ "%.1f"|format(m.fat_g) }} | {{ "%.1f"|format(m.carb_g) }} |
{% endfor -%}
| **合计** | **{{ "%.0f"|format(totals.kcal) }}** | **{{ "%.1f"|format(totals.protein) }}** | **{{ "%.1f"|format(totals.fat) }}** | **{{ "%.1f"|format(totals.carb) }}** |

## 二、减脂估算(deficit)

- BMR: {{ "%.0f"|format(deficit.bmr_kcal or 0) }} kcal(来源: {{ deficit.bmr_source_date or 'yaml fallback' }})
- 活动系数: {{ deficit.activity_factor }}
- TDEE: **{{ "%.0f"|format(deficit.tdee_kcal) }}** kcal
- 缺口: {{ "%+.0f"|format(deficit.deficit_kcal) }} kcal(正=减脂)
- 估算掉秤: {{ "%.2f"|format(deficit.estimated_kg_per_week or 0) }} kg/wk

## 三、运动

**当日总时长: {{ workouts_total_min }} min**

{% if workouts -%}
{% for w in workouts -%}
- {{ w.duration_min }} min — {{ w.raw_text[:80] }}
{% endfor %}
{%- else -%}
- (当日没录训练)
{%- endif %}

## 四、睡眠 + 膝盖

- 睡眠: {{ sleep_summary }}
- 膝盖: {{ knee_summary }}

## 五、最新体重

{% if latest_weight -%}
- {{ latest_weight.weight_kg }} kg({{ latest_weight.measured_at }})
{%- else -%}
- (无体重记录)
{%- endif %}

## 六、待补全(open_question)

{% if open_questions -%}
| Q# | 段 | 食物 | 默认 g | 提示 |
|---|---|---|---|---|
{% for q in open_questions -%}
| {{ q.id }} | {{ q.meal_slot or '-' }} | {{ q.food_name or '?' }} | {{ q.default_grams or '?' }} | {{ q.question[:80] }} |
{% endfor %}
{%- else -%}
- (无)
{%- endif %}

## 七、LLM 核查项(verify_pending,待你确认)

{% if verifies -%}
{% for v in verifies -%}
- **[{{ v.severity }}]** V#{{ v.id }} — {{ v.field }}: {{ v.question }}
{% endfor %}
{%- else -%}
- (无)
{%- endif %}

## 八、对话摘要(chat_log,user 自己写或 LLM 自动抽)

{% if notes -%}
{% for n in notes -%}
- **{{ n.speaker }}** ({{ n.created_at }}): {{ n.content[:300] }}
{% endfor %}
{%- else -%}
- (无)
{%- endif %}

---

来源:HealthOS Daily Reporter · 真值重算(open_question closed 真值替代默认值)
"""
)


def _load_workouts(log_date: str, db_path: Optional[Path] = None) -> list[dict]:
    conn = connect(db_path) if db_path else connect()
    try:
        rows = conn.execute(
            "SELECT raw_text, duration_min FROM workout WHERE log_date=? ORDER BY id",
            (log_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_open_questions(log_date: str, db_path: Optional[Path] = None) -> list[dict]:
    conn = connect(db_path) if db_path else connect()
    try:
        rows = conn.execute(
            """SELECT id, meal_slot, food_name, default_grams, question
               FROM open_question WHERE log_date=? AND status='open'
               ORDER BY id""",
            (log_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_verifies(log_date: str, db_path: Optional[Path] = None) -> list[dict]:
    conn = connect(db_path) if db_path else connect()
    try:
        rows = conn.execute(
            """SELECT id, field, question, severity
               FROM verify_pending WHERE log_date=? AND status='open'
               ORDER BY id""",
            (log_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_notes(log_date: str, db_path: Optional[Path] = None) -> list[dict]:
    """chat_log 的 note / user_summary 行都拉。"""
    conn = connect(db_path) if db_path else connect()
    try:
        rows = conn.execute(
            """SELECT speaker, content, created_at
               FROM chat_log WHERE log_date=? AND speaker IN ('note', 'user_summary')
               ORDER BY id""",
            (log_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_sleep_knee(log_date: str, db_path: Optional[Path] = None) -> tuple[str, str]:
    conn = connect(db_path) if db_path else connect()
    try:
        s = conn.execute(
            """SELECT bedtime, wake_time, duration_min FROM sleep
               WHERE log_date=? ORDER BY id DESC LIMIT 1""",
            (log_date,),
        ).fetchone()
        sleep = "未录"
        if s and s["duration_min"]:
            hrs = s["duration_min"] / 60.0
            sleep = f"{hrs:.1f} h ({s['bedtime']} → {s['wake_time']})"
        k = conn.execute(
            """SELECT tightness, pain, swelling FROM knee_status
               WHERE log_date=? ORDER BY id DESC LIMIT 1""",
            (log_date,),
        ).fetchone()
        knee = "未录"
        if k:
            knee = f"紧绷 {k['tightness'] or '-'}/10 疼痛 {k['pain'] or '-'}/10"
            if k["swelling"] == 1:
                knee += " ⚠肿胀"
        return sleep, knee
    finally:
        conn.close()


def _load_latest_weight(log_date: str, db_path: Optional[Path] = None) -> Optional[dict]:
    conn = connect(db_path) if db_path else connect()
    try:
        # 最新 weight(无论 log_date,先看当天;没有则取最新)
        row = conn.execute(
            "SELECT weight_kg, measured_at FROM weight WHERE measured_at<=? ORDER BY measured_at DESC LIMIT 1",
            (log_date,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _slot_label(slot: str) -> str:
    return {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}.get(slot, slot)


def export_day(
    log_date: Optional[str] = None,
    out_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    fallback_to_data_exports: bool = True,
) -> Path:
    """渲染当天日报,落盘 → 返 out_path。

    默认路径:Obsidian vault /Volumes/video/obsidian/health/Daily/YYYY-MM-DD.md。
    如果 vault 不存在(脱机 / 没 mount)→ fallback 到 data/exports/ 并 warn。
    """
    log_date = log_date or today_iso_default()
    if out_path is None:
        out_path = _default_target_path(log_date)
    out_path = Path(out_path)

    # parent_dir 检查 / 创建
    out_path.parent.mkdir(parents=True, exist_ok=True)

    today = build_today(log_date, db_path)
    deficit = build_deficit(log_date, db_path)
    workouts = _load_workouts(log_date, db_path)
    workouts_total_min = sum(w["duration_min"] or 0 for w in workouts)
    open_qs = _load_open_questions(log_date, db_path)
    verifies = _load_verifies(log_date, db_path)
    notes = _load_notes(log_date, db_path)
    sleep, knee = _load_sleep_knee(log_date, db_path)
    latest_weight = _load_latest_weight(log_date, db_path)

    meals = [
        {
            "meal_slot": _slot_label(m.meal_slot),
            "kcals": m.kcals,
            "protein_g": m.protein_g,
            "fat_g": m.fat_g,
            "carb_g": m.carb_g,
        }
        for m in today.meals
    ]
    totals = {"kcal": today.kcals, "protein": today.protein_g, "fat": today.fat_g, "carb": today.carb_g}

    rendered = TEMPLATE.render(
        date=log_date,
        generated_at=date.today().isoformat(),
        meals=meals,
        totals=totals,
        deficit=deficit,
        workouts=workouts,
        workouts_total_min=workouts_total_min,
        sleep_summary=sleep,
        knee_summary=knee,
        latest_weight=latest_weight,
        open_questions=open_qs,
        verifies=verifies,
        notes=notes,
    )
    out_path.write_text(rendered, encoding="utf-8")

    # 如果默认路径(vault)且 vault 不在且启用了 fallback → copy 到 data/exports
    if fallback_to_data_exports and out_path == _default_target_path(log_date):
        vault_root = DEFAULT_VAULT_PATH
        if not vault_root.exists():
            fb = _fallback_target_path(log_date)
            fb.parent.mkdir(parents=True, exist_ok=True)
            fb.write_text(rendered, encoding="utf-8")
            warnings.warn(
                f"Obsidian vault {vault_root} 不存在;同时写到 {fb}",
                stacklevel=2,
            )

    return out_path
