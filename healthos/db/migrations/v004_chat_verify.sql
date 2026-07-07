-- v004 — chat_log / verify_pending 表
-- 设计原则:
--   - chat_log 入库的 **不是 LLM 原文对话** — 仅入 user 的最终 summary + LLM 对该 summary 的理解
--   - verify_pending 是 LLM 在 verify 阶段生成、用户未确认的问题
--   - 这两张表与 meal/workout/sleep/knee 完全分轨,记录类型不同

CREATE TABLE chat_log (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  speaker TEXT NOT NULL,               -- 'user' | 'agent_summary' | 'llm_ack'
  content TEXT NOT NULL,
  source TEXT                          -- 'commit' | 'verify' | NULL
);

CREATE INDEX idx_chat_log_date ON chat_log(log_date);

CREATE TABLE verify_pending (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  field TEXT NOT NULL,                 -- e.g. 'meal#3.kcals', 'weight_change'
  question TEXT NOT NULL,              -- LLM 提问原文(中文)
  severity TEXT NOT NULL,              -- 'low' | 'medium' | 'high'
  status TEXT NOT NULL DEFAULT 'open', -- 'open' | 'resolved' | 'dismissed'
  resolved_text TEXT,                  -- 用户答复原文
  resolved_at TEXT
);

CREATE INDEX idx_verify_pending_date ON verify_pending(log_date);
CREATE INDEX idx_verify_pending_status ON verify_pending(status);
