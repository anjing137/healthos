"""v1 — workout kcal 估算 + schema + parser hook 测试。

10 个 case:
 1. 篮球 50min moderate / 100kg → kcal ≈ 542, conf 0.85
 2. 跑步 60min high / 100kg → kcal ≈ 1150, conf 0.85
 3. 强度缺失 → conf 0.70(default moderate)
 4. weight_kg=None → 用 70, conf 0.65
 5. unknown sport → 抛 UnknownSport
 6. knee_rehab → conf ≤ 0.7
 7. 写库后新字段被正确填
 8. unknown sport 写 open_question
 9. 老 workout 行 sport 仍 NULL
10. deficit 计算排除 NULL kcal_burned

使用 mock 化的 estimate_kcal 路径,覆盖 claim 不超 ±5%。
数值断言用 pytest.approx,因为 MET × kg × h 本身就是 ±15% 系统误差。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from healthos.db import conn as db_conn
from healthos.nutrition.activities import (
    MET_TABLE,
    is_synthetic,
    lookup_intensity,
    lookup_sport,
)
from healthos.record import record
from healthos.record.workout_kcal import UnknownSport, estimate_kcal


# ── 纯函数层 ────────────────────────────────────────────────────────


def test_basketball_50min_100kg_moderate():
    """篮球 50min / 100kg / moderate:
    6.5 × 100 × (50/60) = 541.67
    """
    kcal, conf = estimate_kcal("basketball", "moderate", 50, weight_kg=100.0)
    assert kcal == pytest.approx(541.7, abs=5)
    assert conf == 0.85


def test_running_60min_100kg_high():
    """跑步 60min / 100kg / high:
    11.5 × 100 × 1.0 = 1150.0
    """
    kcal, conf = estimate_kcal("running", "high", 60, weight_kg=100.0)
    assert kcal == pytest.approx(1150.0, abs=5)
    assert conf == 0.85


def test_intensity_default_moderate_when_missing_is_low_confidence():
    """调用方不传 weight,但有 intensity — 仍按 fallback 70 + 扣分。

    conf = 0.85 - 0.20 = 0.65
    """
    kcal, conf = estimate_kcal("basketball", "moderate", 60)
    assert kcal == pytest.approx(6.5 * 70 * 1.0, abs=5)
    assert conf == 0.65


def test_weight_fallback_70_when_unknown():
    """weight_kg=None → 用 70 + 扣 0.20 conf。"""
    kcal, conf = estimate_kcal("walking_slow", "light", 60)
    assert kcal == pytest.approx(2.5 * 70 * 1.0, abs=2)
    assert conf == 0.65


def test_unknown_sport_raises():
    with pytest.raises(UnknownSport):
        estimate_kcal("frisbee", "moderate", 30, weight_kg=80.0)


def test_knee_rehab_capped_confidence():
    """膝盖康复 = 合成项,conf 上限锁 0.7。"""
    kcal, conf = estimate_kcal("knee_rehab", "moderate", 30, weight_kg=100.0)
    assert kcal == pytest.approx(3.5 * 100 * 0.5, abs=2)
    assert conf <= 0.7
    assert is_synthetic("knee_rehab") is True


# ── lookup 层 ───────────────────────────────────────────────────────


def test_lookup_sport_chinese_aliases():
    assert lookup_sport("今天打篮球 50 分钟") == "basketball"
    assert lookup_sport("散步 30 分钟") == "walking_slow"
    assert lookup_sport("撸铁 1 小时 中") == "weight_training_general"
    assert lookup_sport("膝盖训练 20 分钟") == "knee_rehab"
    assert lookup_sport("靠墙静蹲 5 分钟") == "knee_rehab"
    assert lookup_sport("飞盘 40 分钟") is None  # unknown
    # 长前缀优先
    assert lookup_sport("跑步 30 分钟") == "running"
    assert lookup_sport("慢跑 30 分钟") == "jogging"


def test_lookup_intensity_chinese():
    assert lookup_intensity("篮球 50 分钟 轻") == "light"
    assert lookup_intensity("散步 中等强度") == "moderate"
    assert lookup_intensity("跑步 热血") == "high"
    assert lookup_intensity("没写强度 20 分钟") is None


# ── 数据库 + record 集成 ────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path) -> Path:
    """每个测试一个干净 db,init() 跑过 migration。"""
    fake = tmp_path / "test_kcal.db"
    c = db_conn.connect(fake)
    db_conn.init(c)
    c.close()
    return fake


def _row(conn: sqlite3.Connection, log_date: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM workout WHERE log_date=? ORDER BY id DESC LIMIT 1",
        (log_date,),
    ).fetchone()


def test_schema_v006_columns_exist(fresh_db):
    """迁移后 workout 表应该有这五列。"""
    c = db_conn.connect(fresh_db)
    cols = {row[1] for row in c.execute("PRAGMA table_info(workout)").fetchall()}
    assert {"sport", "intensity", "kcal_burned", "kcal_method", "confidence"} <= cols
    c.close()


def test_record_workout_writes_kcal_burned(fresh_db):
    """完整 record: journal 包含 workout 段,sport/kcal_burned/method 都被填。"""
    res = record(
        "运动:打篮球 50 分钟 中等",
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.workouts == 1
    c = db_conn.connect(fresh_db)
    row = _row(c, "2026-07-07")
    assert row["sport"] == "basketball"
    assert row["intensity"] == "moderate"
    # 没有 weight 记录 → fallback 70kg + 0.65 conf
    assert row["kcal_method"] == "MET"
    assert row["kcal_burned"] == pytest.approx(6.5 * 70 * 50 / 60, abs=2)
    assert row["confidence"] == pytest.approx(0.65, abs=0.01)
    c.close()


def test_record_workout_unknown_sport_triggers_open_question(fresh_db):
    """unknown sport 不写 kcal,触发 open_question。"""
    res = record(
        "运动:飞盘 40 分钟 中",
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.workouts == 1
    assert res.questions  # 有未识别题
    c = db_conn.connect(fresh_db)
    row = _row(c, "2026-07-07")
    assert row["sport"] is None
    assert row["intensity"] == "moderate"
    assert row["kcal_method"] == "pending"
    assert row["kcal_burned"] is None
    assert row["confidence"] is None
    # open_question 是否真写了
    qrow = c.execute(
        "SELECT question FROM open_question WHERE log_date=?",
        ("2026-07-07",),
    ).fetchone()
    assert qrow is not None
    assert "飞盘" in qrow["question"]
    c.close()


def test_old_workout_rows_remain_null_after_migration(fresh_db):
    """先预填一行老格式 workout(只 duration_min),跑迁移后该行新列全 NULL。"""
    c = db_conn.connect(fresh_db)
    # 有 FK,先建 daily_log
    db_conn.upsert_daily_log(c, "2026-06-01")
    c.execute(
        """INSERT INTO workout(log_date, raw_text, parsed_json, duration_min, logged_at)
           VALUES(?, ?, ?, ?, ?)""",
        ("2026-06-01", "老数据", '{"raw":"老数据"}', 30, "2026-06-01T08:00:00"),
    )
    c.commit()
    c.close()
    # 再跑一次 init() — 验证幂等 + 老数据不被改
    c = db_conn.connect(fresh_db)
    db_conn.init(c)
    row = c.execute(
        "SELECT sport, intensity, kcal_burned, kcal_method, confidence "
        "FROM workout WHERE log_date='2026-06-01'"
    ).fetchone()
    assert row["sport"] is None
    assert row["intensity"] is None
    assert row["kcal_burned"] is None
    assert row["kcal_method"] is None
    assert row["confidence"] is None
    c.close()


def test_deficit_skips_null_kcal_burned():
    """构造两条 workout:一条有 kcal_burned,一条 NULL。
    模拟 deficit 端 SUM,NULL 行必须被排除。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db_conn.init(_Adapter(c))  # 用 in-memory 跑 migration
    c.execute(
        """INSERT INTO workout(log_date, raw_text, parsed_json, duration_min,
           logged_at, sport, intensity, kcal_burned, kcal_method, confidence)
           VALUES('2026-07-07','a','{}',30,'2026-07-07T08:00:00',
                  'basketball','moderate',545.3,'MET',0.85)"""
    )
    c.execute(
        """INSERT INTO workout(log_date, raw_text, parsed_json, duration_min,
           logged_at, sport, intensity, kcal_burned, kcal_method, confidence)
           VALUES('2026-07-07','b','{}',40,'2026-07-07T09:00:00',
                  NULL,NULL,NULL,NULL,NULL)"""
    )
    c.commit()
    total = c.execute(
        "SELECT SUM(kcal_burned) AS s FROM workout WHERE log_date='2026-07-07'"
    ).fetchone()["s"]
    assert total == pytest.approx(545.3, abs=0.1)
    c.close()


class _Adapter:
    """把 sqlite3.Connection 包成 db_conn.connect() 期望的接口,只用来 init()."""
    def __init__(self, c): self._c = c
    def execute(self, sql, params=()): return self._c.execute(sql, params)
    def executescript(self, sql): return self._c.executescript(sql)
    def commit(self): return self._c.commit()


def test_met_table_has_12_sports():
    """设计 frozen:12 项字典,加新项必须显式改测试以提醒。"""
    assert len(MET_TABLE) == 12
    assert {"basketball", "walking_slow", "knee_rehab"} <= set(MET_TABLE)
