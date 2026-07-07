-- v003 — InBody 字段扩展 + 双轨 weight
-- 一次性加列,old 表里有 0 行(7-6 inbody 那条已写完 7/2 之前还没建表)
-- inbody 表存放「静态 / 测量日档案」:测的日子 + 当时体重 + 体脂 + BMR + 节段
-- weight 表存放「日常 weight 测」的 daily measure

-- 注意:SQLite ALTER TABLE ADD COLUMN 是 idempotent,
-- 我们用 IF NOT EXISTS 一类技巧:SQlite 不支持 ADD COLUMN IF NOT EXISTS,
-- 但 v001/v002 migration 已注册,这里再跑时 schema_migrations 表会跳过。

ALTER TABLE inbody ADD COLUMN test_date TEXT;
ALTER TABLE inbody ADD COLUMN gender TEXT;
ALTER TABLE inbody ADD COLUMN age INTEGER;
ALTER TABLE inbody ADD COLUMN overall_score INTEGER;
ALTER TABLE inbody ADD COLUMN target_weight_kg REAL;
ALTER TABLE inbody ADD COLUMN weight_control_kg REAL;
ALTER TABLE inbody ADD COLUMN fat_control_kg REAL;
ALTER TABLE inbody ADD COLUMN muscle_control_kg REAL;
ALTER TABLE inbody ADD COLUMN skeletal_muscle_mass_kg REAL;
ALTER TABLE inbody ADD COLUMN body_fat_pct REAL;
ALTER TABLE inbody ADD COLUMN body_fat_mass_kg REAL;
ALTER TABLE inbody ADD COLUMN bmi REAL;
ALTER TABLE inbody ADD COLUMN basal_metabolic_rate_kcal REAL;
ALTER TABLE inbody ADD COLUMN visceral_fat_level INTEGER;
ALTER TABLE inbody ADD COLUMN bmi_status TEXT;
ALTER TABLE inbody ADD COLUMN body_fat_status TEXT;
ALTER TABLE inbody ADD COLUMN ecw_ratio REAL;
ALTER TABLE inbody ADD COLUMN segmental_lean_mass_json TEXT;
ALTER TABLE inbody ADD COLUMN segmental_fat_mass_json TEXT;
ALTER TABLE inbody ADD COLUMN health_assessment_json TEXT;
ALTER TABLE inbody ADD COLUMN long_term_goal_json TEXT;
ALTER TABLE inbody ADD COLUMN notes_json TEXT;
