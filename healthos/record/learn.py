"""D4.5 B — 回答 open_question,落库。

设计极简:
- 输入: 一段话
- 走 parse_item() 拆分食物
- 对每个有食物命中的 ParsedQuantity,在 open_question 表查(log_date + food_name)
- 命中 → UPDATE resolved_grams, status='closed', answer_text=原文
- 多 question 命中同食物(理论可能)→ 都 close

调用方(CLI)只用一行:
    answer("3两白酒 / 日本豆腐 100g / 莲藕 50g", log_date="2026-07-06")
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..db.conn import connect
from ..parser import parse as parse_sections
from ..nutrition.quantify import parse_item


@dataclass
class AnswerResult:
    log_date: str
    closed: list[int]     # 被关闭的 open_question.id
    skipped: list[str]    # 找不到对应 question 的项
    raw_input: str


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def answer(text: str, log_date: str, db_path: Optional[Path] = None) -> AnswerResult:
    """用户回复一段话,把对应的 open_question 关闭。

    算法:
    1. parse(text) → 段落(忽略段落头,直接当 items 拼)
    2. 对每件物品,parse_item → food_hit + grams
    3. UPDATE open_question SET resolved_grams=?, status='closed' WHERE log_date=? AND food_name=? AND status='open'
    4. 如果某个物品没找到 open_question — 静默丢掉(写入 skipped)
    """
    if not log_date or len(log_date) != 10:
        raise ValueError(f"log_date 必须是 'YYYY-MM-DD', got {log_date!r}")

    # 答案通常没有段头 — 用 "/" "," 等切分。先 try parse(strict=False),失败就用 fallback 切。
    sections = []
    try:
        sections = parse_sections(text, strict=False)
    except ValueError:
        pass
    items: list[str] = []
    for sec in sections:
        items.extend(sec.items)
    if not items:
        for line in text.replace("、", ",").replace("/", ",").replace("。", ",").split(","):
            line = line.strip()
            if line:
                items.append(line)

    conn = connect(db_path) if db_path else connect()
    try:
        closed: list[int] = []
        skipped: list[str] = []

        # 第一遍: parse_item 命中(单个 hit)
        for it in items:
            pqs = parse_item(it)
            any_closed = False
            for pq in pqs:
                if not pq.food_hit or pq.grams <= 0:
                    continue
                cur = conn.execute(
                    """UPDATE open_question
                       SET resolved_grams=?, status='closed',
                           answer_text=?, closed_at=?
                       WHERE log_date=? AND food_name=? AND status='open'
                       RETURNING id""",
                    (pq.grams, it, _now_iso(), log_date, pq.food_hit.name),
                )
                rows = cur.fetchall()
                if rows:
                    any_closed = True
                    for r in rows:
                        closed.append(r[0])
            if not any_closed:
                skipped.append(it)

        # 第二遍: 整 text 中 "食物名 + 数字+g" 的 fallback 扫描
        # 用于回答里"小酥肉 约50g 白菜70g"这种同一句多个食物。
        # 只针对 skipped,避免重复处理
        if skipped:
            from ..nutrition.foods import all_known_names
            for name in sorted(all_known_names(), key=len, reverse=True):
                if len(name) < 2:
                    continue
                pat = re.compile(
                    rf"({re.escape(name)})\s*[约~]?\s*(\d+(?:\.\d+)?)\s*(g|克)\b"
                )
                m = pat.search(text)
                if not m:
                    continue
                grams = float(m.group(2))
                # 看这个 (name, grams) 是否已 closed
                row = conn.execute(
                    """SELECT id, status FROM open_question
                       WHERE log_date=? AND food_name=?
                         AND (status='open' OR (status='closed' AND resolved_grams=?))
                       ORDER BY id LIMIT 1""",
                    (log_date, name, grams),
                ).fetchone()
                if row is None:
                    continue
                if row["status"] == "open":
                    conn.execute(
                        """UPDATE open_question
                           SET resolved_grams=?, status='closed',
                               answer_text=?, closed_at=?
                           WHERE id=?""",
                        (grams, text, _now_iso(), row["id"]),
                    )
                    closed.append(row["id"])
                    # 把这个 item 从 skipped 移除(如果它精确含这个 food_name+grams)
                    for s in list(skipped):
                        if name in s and m.group(2) in s:
                            skipped.remove(s)
                            break

        conn.commit()
        return AnswerResult(log_date=log_date, closed=closed, skipped=skipped, raw_input=text)
    finally:
        conn.close()
