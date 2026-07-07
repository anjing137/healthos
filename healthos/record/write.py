"""把一段自然语言日记通过 parser + quantify 写到 SQLite。

设计:
- record(plain_text, log_date) 是主入口
- 内部流程:parse() -> 对每段分别处理 -> 写库
- meal:把每段的一个或多个 ParsedQuantity 累加成一行(kcals/protein/fat/carb 都 SUM)
- workout:raw_text + parsed_json + duration_min(先把 "快走 60 分钟" 抽出来)
- sleep:填 bedtime / wake_time / duration_min(尽量解析,解析不到留空)
- knee:tightness / pain / swelling / notes(尽量解析)
- weight:从 raw 抽 "72.3kg" 这种(没找到就不写)
- open_question:confidence<0.7 或 unknown 食物 → 写 open_question 表(后续 CLI 用)

D5 提到的"多轮 patch 合并":这里是新一条 meal / workout 行,而当日的"今日合计"由 query 端 SUM ——
实现上不需要特殊合并逻辑,SQLite 一次 SUM 完事。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..db.conn import connect, upsert_daily_log, init
from ..parser import parse as parse_sections
from ..nutrition.quantify import parse_item, ParsedQuantity


# ── Regex ──────────────────────────────────────────────────────────────
_WEIGHT_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d)?)\s*(?:kg|公斤|千克)\b")
_KNEE_TIGHTNESS_RE = re.compile(r"发紧[^0-9]*?(\d+)\s*/\s*10")
_KNEE_PAIN_RE = re.compile(r"疼痛[^0-9]*?(\d+)\s*/\s*10")
_KNEE_SWELLING_RE = re.compile(r"(无肿胀|无肿|肿胀|肿)")
_KNEE_SIDE_RE = re.compile(r"([左右双])膝")
_DURATION_MIN_RE = re.compile(r"(\d+)\s*(?:分钟|min|分)(?!\s*/\s*10)")
_BEDTIME_RE = re.compile(r"([01]?\d|2[0-3])\s*[:点]\s*([0-5]?\d)\s*睡")
_WAKE_RE = re.compile(r"([01]?\d|2[0-3])\s*[:点]\s*([0-5]?\d)\s*起")
_FROM_TO_DURATION_RE = re.compile(r"(\d+)\s*(?:个)?小时")


@dataclass
class RecordResult:
    log_date: str
    meals: int
    workouts: int
    sleep_rows: int
    knee_rows: int
    weights: int
    warnings: list[str]
    questions: list[int] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 段落→SQLite 写 ──────────────────────────────────────────────────────


def _write_meal(
    conn: sqlite3.Connection,
    log_date: str,
    slot: str,
    raw: str,
    parsed: list[ParsedQuantity],
    question_ids: Optional[list[int]] = None,
) -> int:
    """写一行 meal,返回写入的 id。"""
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
    qid_csv = None  # 简化:不再在 meal 上挂多 question id,关系由 open_question.log_date + raw_item 回查
    cur = conn.execute(
        """
        INSERT INTO meal(log_date, meal_slot, raw_text, parsed_json,
                         kcals, protein_g, fat_g, carb_g, logged_at, question_id)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (log_date, slot, raw, parsed_json, k, p, f, c, _now_iso(), qid_csv),
    )
    return int(cur.lastrowid)


def _write_workout(conn: sqlite3.Connection, log_date: str, raw_text: str) -> int:
    duration = 0
    m = _DURATION_MIN_RE.search(raw_text)
    if m:
        duration = int(m.group(1))
    parsed_json = json.dumps({"raw": raw_text, "duration_min_inferred": duration}, ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO workout(log_date, raw_text, parsed_json, duration_min, logged_at)
           VALUES(?, ?, ?, ?, ?)""",
        (log_date, raw_text, parsed_json, duration or None, _now_iso()),
    )
    return int(cur.lastrowid)


def _write_sleep(conn: sqlite3.Connection, log_date: str, raw_text: str) -> int:
    bedtime = wake_time = duration_min = None
    m = _BEDTIME_RE.search(raw_text)
    if m:
        bedtime = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = _WAKE_RE.search(raw_text)
    if m:
        wake_time = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    if bedtime and wake_time:
        try:
            bh, bm = map(int, bedtime.split(":"))
            wh, wm = map(int, wake_time.split(":"))
            duration_min = (wh * 60 + wm) - (bh * 60 + bm)
            if duration_min < 0:
                duration_min += 24 * 60
        except Exception:
            pass
    if duration_min is None:
        m = _FROM_TO_DURATION_RE.search(raw_text)
        if m:
            duration_min = int(m.group(1)) * 60
    cur = conn.execute(
        """INSERT INTO sleep(log_date, bedtime, wake_time, duration_min) VALUES(?, ?, ?, ?)""",
        (log_date, bedtime, wake_time, duration_min),
    )
    return int(cur.lastrowid)


def _write_knee(conn: sqlite3.Connection, log_date: str, raw_text: str) -> int:
    tightness = pain = swelling = None
    notes = raw_text
    m = _KNEE_TIGHTNESS_RE.search(raw_text)
    if m:
        tightness = int(m.group(1))
    m = _KNEE_PAIN_RE.search(raw_text)
    if m:
        pain = int(m.group(1))
    m = _KNEE_SWELLING_RE.search(raw_text)
    if m:
        swelling = 0 if m.group(1).startswith("无") else 1
    m = _KNEE_SIDE_RE.search(raw_text)
    if m:
        notes = f"{m.group(1)}侧。{raw_text}"
    cur = conn.execute(
        """INSERT INTO knee_status(log_date, tightness, pain, swelling, notes, logged_at)
           VALUES(?, ?, ?, ?, ?, ?)""",
        (log_date, tightness, pain, swelling, notes, _now_iso()),
    )
    return int(cur.lastrowid)


def _write_weight(conn: sqlite3.Connection, log_date: str, raw_text: str) -> Optional[int]:
    m = _WEIGHT_RE.search(raw_text)
    if not m:
        return None
    kg = float(m.group(1))
    cur = conn.execute(
        """INSERT INTO weight(measured_at, weight_kg, measured_at_hhmm) VALUES(?, ?, ?)""",
        (log_date, kg, None),
    )
    return int(cur.lastrowid)


def _record_open_question(
    conn: sqlite3.Connection,
    log_date: str,
    slot: Optional[str],
    raw_item: str,
    pq: ParsedQuantity,
) -> int:
    """写一行 open_question 并返回 id。"""
    food_name = pq.food_hit.name if pq.food_hit else None
    default_grams = pq.grams
    default_kcals = pq.kcals() or 0.0
    default_protein = pq.protein_g() or 0.0

    if food_name:
        question = (
            f"{food_name} 一份大约多少克?(我先用 {default_grams:.0f} g / "
            f"{default_kcals:.0f} kcal / {default_protein:.1f} g P 占位)"
        )
    else:
        question = f"这一项是什么食物?{raw_item!r}(我猜不出)"

    cur = conn.execute(
        """INSERT INTO open_question(log_date, meal_slot, raw_item, food_name,
                                     default_grams, default_kcals, default_protein_g,
                                     question, created_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            log_date,
            slot,
            raw_item,
            food_name,
            default_grams or None,
            default_kcals,
            default_protein,
            question,
            _now_iso(),
        ),
    )
    return int(cur.lastrowid)


# ── 主入口 ──────────────────────────────────────────────────────────────


def record(
    plain_text: str,
    log_date: str,
    db_path: Optional[Path] = None,
    *,
    lenient: bool = False,
) -> RecordResult:
    """把一段日记写到 SQLite。

    Args:
        plain_text:整段日记(可多段、可含多个早午晚多项)
        log_date:逻辑日期 'YYYY-MM-DD'
        db_path:仅测试用
        lenient:如果 True,parser 用 strict=False 接受无段头输入(commit 路径需要)

    Returns:
        RecordResult 各段计数 + warnings + questions(本次写入触发的 open_question id)
    """
    if not log_date or len(log_date) != 10:
        raise ValueError(f"log_date 必须是 'YYYY-MM-DD', got {log_date!r}")

    conn = connect(db_path) if db_path else connect()
    try:
        init(conn)
        upsert_daily_log(conn, log_date)

        sections = parse_sections(plain_text, strict=not lenient, split_compound=True)
        warnings: list[str] = []
        meals = workouts = sleeps = knees = weights = 0
        all_question_ids: list[int] = []

        slot_map = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack"}

        for sec in sections:
            if sec.name in slot_map:
                slot = slot_map[sec.name]
                parsed: list[ParsedQuantity] = []
                question_ids: list[int] = []
                for it in sec.items:
                    pqs = parse_item(it)
                    for pq in pqs:
                        parsed.append(pq)
                        # 触发问号:confidence < 0.7 OR 食物 unknown
                        should_ask = (
                            pq.confidence < 0.7 or pq.food_hit is None
                        )
                        if should_ask:
                            qid = _record_open_question(
                                conn, log_date, slot, it, pq
                            )
                            question_ids.append(qid)
                            all_question_ids.append(qid)
                            if pq.food_hit is None:
                                warnings.append(f"unknown food: {it!r}")
                _write_meal(conn, log_date, slot, sec.raw, parsed, question_ids)
                meals += 1

            elif sec.name == "workout":
                _write_workout(conn, log_date, sec.raw)
                workouts += 1
            elif sec.name == "sleep":
                _write_sleep(conn, log_date, sec.raw)
                sleeps += 1
            elif sec.name == "knee":
                _write_knee(conn, log_date, sec.raw)
                knees += 1

            wid = _write_weight(conn, log_date, plain_text)
            if wid:
                weights += 1

        conn.commit()
        return RecordResult(
            log_date=log_date,
            meals=meals,
            workouts=workouts,
            sleep_rows=sleeps,
            knee_rows=knees,
            weights=weights,
            warnings=warnings,
            questions=all_question_ids,
        )
    finally:
        conn.close()


def today() -> str:
    return date.today().isoformat()
