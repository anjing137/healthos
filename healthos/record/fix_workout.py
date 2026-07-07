"""手动校准单条 workout kcal。

CLAUDE.md 第 5 条精神:逐条改,不走批量 UPDATE 关闭。

workflow:
    $ healthos fix-workout 42 --kcal 580
    ✓ workout #42 校准为 580 kcal (method=manual, conf=1.0)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..db.conn import connect


def patch_workout_kcal(
    workout_id: int,
    kcal: float,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """把一条 workout 的 kcal_burned 设为 kcal,method='manual',confidence=1.0。

    Returns:
      dict{kcal_burned, kcal_method, confidence, log_date, sport, raw_text} 或
      None 当该 id 不存在。
    """
    conn = connect(db_path) if db_path else connect()
    try:
        row = conn.execute(
            "SELECT id, log_date, sport, raw_text FROM workout WHERE id=?",
            (workout_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """UPDATE workout
               SET kcal_burned=?, kcal_method='manual', confidence=1.0
               WHERE id=?""",
            (kcal, workout_id),
        )
        conn.commit()
        return {
            "workout_id": workout_id,
            "log_date": row["log_date"],
            "sport": row["sport"],
            "raw_text": row["raw_text"],
            "kcal_burned": kcal,
            "kcal_method": "manual",
            "confidence": 1.0,
        }
    finally:
        conn.close()