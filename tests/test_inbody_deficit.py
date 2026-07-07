"""D21 — InBody 接入 + deficit reporter + 体重追踪"""

import json
from pathlib import Path

import pytest

from healthos.db.conn import connect
from healthos.report.deficit import build_deficit


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr("healthos.report.deficit.CONFIG_PATH", tmp_path / "rules.yaml")
    db = tmp_path / "test.db"
    yield db


def _write_sample_yaml(path: Path) -> None:
    path.write_text(
        """baselines:
  activity_factor: 1.55
inbody_recorded:
  bmr_kcal: 1890
  daily_protein_target_g: 130
""",
        encoding="utf-8",
    )


def test_deficit_known_intake(fresh_db, monkeypatch):
    _write_sample_yaml(fresh_db.parent / "rules.yaml")
    conn = connect(fresh_db)
    from healthos.db.conn import init, upsert_daily_log
    init(conn)
    upsert_daily_log(conn, "2026-07-06")
    conn.execute("""
        INSERT INTO inbody(measured_at, test_date, weight_kg, body_fat_pct, basal_metabolic_rate_kcal)
        VALUES(?, ?, ?, ?, ?)
    """, ("2026-07-02", "2026-07-02", 100.6, 30.0, 1890))
    conn.execute("INSERT INTO meal(log_date, meal_slot, raw_text, parsed_json, kcals, protein_g, fat_g, carb_g, logged_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-07-06", "breakfast", "早饭", "[]", 2500, 100, 50, 250, "now"))
    conn.commit()
    conn.close()

    rep = build_deficit("2026-07-06", db_path=fresh_db)
    assert rep.bmr_kcal == 1890
    assert rep.tdee_kcal == pytest.approx(1890 * 1.55)
    assert rep.intake_kcal == 2500
    # 2500 < tdee 2929.5 → 429.5 kcal deficit (消耗比吃多) → 减脂
    assert rep.deficit_kcal == pytest.approx(429.5, abs=0.5)
    assert rep.estimated_kg_per_week == pytest.approx(0.39, abs=0.05)


def test_deficit_low_intake_skipped(fresh_db, monkeypatch):
    """if intake_kcal < 100,kg_week 应该 None."""
    _write_sample_yaml(fresh_db.parent / "rules.yaml")
    conn = connect(fresh_db)
    from healthos.db.conn import init, upsert_daily_log
    init(conn)
    upsert_daily_log(conn, "2026-07-07")
    conn.execute("""
        INSERT INTO inbody(measured_at, test_date, weight_kg, basal_metabolic_rate_kcal)
        VALUES(?, ?, ?, ?)
    """, ("2026-07-02", "2026-07-02", 100.6, 1890))
    conn.execute("INSERT INTO meal(log_date, meal_slot, raw_text, parsed_json, kcals, protein_g, fat_g, carb_g, logged_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-07-07", "breakfast", "x", "[]", 0, 0, 0, 0, "now"))
    conn.commit()
    conn.close()

    rep = build_deficit("2026-07-07", db_path=fresh_db)
    assert rep.intake_kcal == 0
    assert rep.estimated_kg_per_week is None


def test_inbody_round_trip(fresh_db):
    """InBody 写了能读回。"""
    conn = connect(fresh_db)
    from healthos.db.conn import init
    init(conn)
    conn.execute("""
        INSERT INTO inbody(
            measured_at, test_date, height_cm, weight_kg, gender,
            overall_score, bmi, basal_metabolic_rate_kcal,
            visceral_fat_level, skeletal_muscle_mass_kg,
            body_fat_mass_kg, body_fat_pct,
            segmental_lean_mass_json, health_assessment_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        "2026-07-02", "2026-07-02", 176.4, 100.6, "male",
        74, 32.3, 1890,
        11, 40.3, 30.2, 30.0,
        json.dumps({"right_arm_kg": 4.01}),
        json.dumps({"primary_issue": "Excess body fat"}),
    ))
    conn.commit()
    row = conn.execute(
        "SELECT height_cm, weight_kg, basal_metabolic_rate_kcal, visceral_fat_level FROM inbody"
    ).fetchone()
    assert row["height_cm"] == 176.4
    assert row["weight_kg"] == 100.6
    assert row["basal_metabolic_rate_kcal"] == 1890
    assert row["visceral_fat_level"] == 11
