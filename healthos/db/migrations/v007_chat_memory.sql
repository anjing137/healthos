-- v007 — chat memory:扩 chat_log + session_id
--
-- 设计:
--   - 原 v004 schema 有 (log_date, created_at, speaker, content, source),只用于 commit/summary
--   - v007 加 session_id + role + metadata — 让 chat 也能入 db
--   - session_id 持久化在 data/.chat_session_id 文件(REPL 启动时读一次)
--   - Markdown 落盘在 data/chat_history/<session_id>.md(真源,可读)
--   - SQLite 是索引(可查询、可统计)
--
-- 老 chat_log 行(session_id NULL)保留 — 那是 v004 commit/summary 写的
--   不动它们,也不把它们当"chat history"用。

ALTER TABLE chat_log ADD COLUMN session_id TEXT;
ALTER TABLE chat_log ADD COLUMN role TEXT;
ALTER TABLE chat_log ADD COLUMN metadata TEXT;

CREATE INDEX idx_chat_log_session ON chat_log(session_id);
CREATE INDEX idx_chat_log_role ON chat_log(role);