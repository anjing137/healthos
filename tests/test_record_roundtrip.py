"""D4 — record 一段自然语言写库的端到端测试。"""

from pathlib import Path

import pytest

from healthos.record import record


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """每个测试一个干净 db,不污染真实 data/。"""
    # ensure data/ exists so connect() can mkdir; won't conflict
    fake = tmp_path / "test.db"
    yield fake


def test_breakfast_only(fresh_db):
    res = record(
        "早餐，一个鸡蛋，一杯豆浆",
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.meals == 1
    assert res.workouts == 0
    # 一个鸡蛋 ~72 kcal,一杯豆浆 ~79.2 kcal → 151 kcal
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute("SELECT kcals, protein_g FROM meal WHERE log_date=?", ("2026-07-07",)).fetchone()
    assert row is not None
    assert abs(row[0] - 151.2) < 1.5
    conn.close()


def test_full_lunch(fresh_db):
    res = record(
        "午餐：鸡胸肉150g、排骨4块、冬瓜、米饭一小碗",
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.meals == 1
    # 鸡胸 247.5 + 排骨 336 + 冬瓜 12 + 米饭一小碗 130 = ~725 kcal
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute("SELECT kcals FROM meal WHERE log_date=?", ("2026-07-07",)).fetchone()
    assert row is not None
    assert abs(row[0] - 725.5) < 50  # 上限 因为多行
    conn.close()


def test_workout_section(fresh_db):
    res = record(
        """运动：
    快走60分钟
    臀桥4×15
    """,
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.workouts == 1
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute("SELECT duration_min FROM workout").fetchone()
    assert row[0] == 60
    conn.close()


def test_sleep_knee(fresh_db):
    res = record(
        "睡眠：23:30 睡，7:00 起\n膝盖：右膝发紧 2/10，无疼痛，无肿胀",
        log_date="2026-07-07",
        db_path=fresh_db,
    )
    assert res.sleep_rows == 1
    assert res.knee_rows == 1
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    s = conn.execute("SELECT bedtime, wake_time, duration_min FROM sleep").fetchone()
    assert s[0] == "23:30"
    assert s[1] == "07:00"
    # 23:30 -> 07:00 = 7.5 h = 450 min
    assert s[2] == 450
    k = conn.execute("SELECT tightness, pain, swelling FROM knee_status").fetchone()
    assert k[0] == 2
    assert k[1] is None
    assert k[2] == 0
    conn.close()


def test_unknown_food_warns(fresh_db):
    res = record("早餐，澳洲牛肉 200g，一个鸡蛋", log_date="2026-07-07", db_path=fresh_db)
    assert any("澳洲牛肉" in w for w in res.warnings)
    # meal 仍写入(未知项 kcals=0)
    assert res.meals == 1


def test_multi_section_one_log_date(fresh_db):
    text = """早餐：无糖豆浆600ml、肉包1个、南瓜两片
午餐：鸡胸肉150g、排骨4块、冬瓜、米饭一小碗
晚餐：西瓜300g、鸡蛋2个

运动：
快走60分钟
臀桥4×15
死虫3组
平板支撑1组

睡眠：
昨天23:30睡
今天7:00起

膝盖：
右膝发紧2/10
无疼痛
无肿胀
"""
    res = record(text, "2026-07-07", db_path=fresh_db)
    assert res.meals == 3
    assert res.workouts == 1
    assert res.sleep_rows == 1
    assert res.knee_rows == 1


def test_multiple_patches_same_day(fresh_db):
    """D5 行为验证:多次调用 record() 同一日应该都能写,后续 query 端 SUM。"""
    record("早餐，一个鸡蛋", "2026-07-07", db_path=fresh_db)
    record("午餐，鸡胸肉 150g", "2026-07-07", db_path=fresh_db)
    record("晚餐，一个鸡蛋", "2026-07-07", db_path=fresh_db)

    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    rows = conn.execute("SELECT meal_slot, kcals FROM meal WHERE log_date=?", ("2026-07-07",)).fetchall()
    assert len(rows) == 3
    conn.close()
