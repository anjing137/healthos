-- HealthOS schema v001 — 全部表 DDL。

-- 注意:这是 initial migration,以后 schema 变化都是 v002_xxx.sql / v003_xxx.sql。
-- 不要改这个文件。新文件加在 migrations/ 里。

-- ============================================================================
-- 静态基础数据(可能有零到多条,但 99% 时间不动)
-- ============================================================================

CREATE TABLE inbody (
  id INTEGER PRIMARY KEY,
  measured_at TEXT NOT NULL,                -- ISO date 'YYYY-MM-DD'
  height_cm REAL,
  weight_kg REAL,
  body_fat_pct REAL,
  lean_mass_kg REAL,
  notes TEXT
);

CREATE INDEX idx_inbody_date ON inbody(measured_at);

-- ============================================================================
-- 每日体重(可多次,记空腹最近一次最准;query 用最新那条)
-- ============================================================================

CREATE TABLE weight (
  id INTEGER PRIMARY KEY,
  measured_at TEXT NOT NULL,                -- ISO date
  weight_kg REAL NOT NULL,
  measured_at_hhmm TEXT                     -- 'HH:MM',optional
);

CREATE INDEX idx_weight_date ON weight(measured_at);

-- ============================================================================
-- 一天一条日志骨架 — 所有 patch 都聚合到这个 log_date
-- ============================================================================

CREATE TABLE daily_log (
  log_date TEXT PRIMARY KEY,                -- 'YYYY-MM-DD'
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- ============================================================================
-- 膳食 — 一天可多餐,每个 meal_slot 一行
-- ============================================================================

CREATE TABLE meal (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  meal_slot TEXT NOT NULL,                  -- breakfast|lunch|dinner|snack
  raw_text TEXT NOT NULL,                   -- 原话
  parsed_json TEXT,                         -- parser 输出 JSON
  kcals REAL,
  protein_g REAL,
  fat_g REAL,
  carb_g REAL,
  logged_at TEXT NOT NULL,
  FOREIGN KEY (log_date) REFERENCES daily_log(log_date)
);

CREATE INDEX idx_meal_date_slot ON meal(log_date, meal_slot);

-- ============================================================================
-- 训练 — 一天可多次(早训/晚训),每个 workout 一行
-- ============================================================================

CREATE TABLE workout (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  parsed_json TEXT,
  duration_min INTEGER,
  logged_at TEXT NOT NULL,
  FOREIGN KEY (log_date) REFERENCES daily_log(log_date)
);

CREATE INDEX idx_workout_date ON workout(log_date);

-- ============================================================================
-- 睡眠 — 起床那天算 log_date,bedtime 是昨天
-- ============================================================================

CREATE TABLE sleep (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,                   -- '起床那天'
  bedtime TEXT,                             -- 'YYYY-MM-DDTHH:MM'
  wake_time TEXT,                           -- 'YYYY-MM-DDTHH:MM' 或 'HH:MM'
  duration_min REAL,
  FOREIGN KEY (log_date) REFERENCES daily_log(log_date)
);

CREATE INDEX idx_sleep_date ON sleep(log_date);

-- ============================================================================
-- 膝关节状态 — 可多次,但通常早上一条
-- ============================================================================

CREATE TABLE knee_status (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  tightness INTEGER,                        -- 0-10 发紧
  pain INTEGER,                             -- 0-10 疼痛
  swelling INTEGER,                         -- 0|1
  notes TEXT,
  logged_at TEXT NOT NULL,
  FOREIGN KEY (log_date) REFERENCES daily_log(log_date)
);

CREATE INDEX idx_knee_date ON knee_status(log_date);
