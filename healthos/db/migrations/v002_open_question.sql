-- v002 — open_question 表 + meal 表挂 question_id
-- 用途:confidence 低于 0.7 的 ParsedQuantity,record 时写入 open_question,
--       user 在 CLI 里回答后自动补默认克数,关掉 question。

CREATE TABLE open_question (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  meal_slot TEXT,
  raw_item TEXT NOT NULL,           -- 当时写的那段 item 原文
  food_name TEXT,                   -- 我们猜的
  default_grams REAL,
  default_kcals REAL,
  default_protein_g REAL,
  question TEXT NOT NULL,           -- "日本豆腐 一份约多少克?"
  status TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'closed' | 'skipped'
  answer_text TEXT,                 -- user 给的原文回复
  resolved_grams REAL,
  created_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE INDEX idx_oq_log_date ON open_question(log_date);
CREATE INDEX idx_oq_status ON open_question(status);

-- 让 meal 表挂上 question,以后 user 答完之后还能追溯是哪条 question 引出的这条记录
ALTER TABLE meal ADD COLUMN question_id INTEGER REFERENCES open_question(id);
