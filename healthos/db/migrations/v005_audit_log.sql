-- v005 — audit_log:记录 /fix-meal / future edit/delete 操作
--
-- 设计:
--   - action: 'update' | 'delete'(未来扩展:'reparse'/'resolve_q')
--   - table_name + row_id: 定位 + 可反查
--   - before_json / after_json: 全字段快照,XML 不能直看但能 diff
--   - source: 'repl' | 'cli' 区分入口

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  action TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_id INTEGER NOT NULL,
  source TEXT,
  before_json TEXT,
  after_json TEXT,
  notes TEXT
);

CREATE INDEX idx_audit_table_row ON audit_log(table_name, row_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at);
