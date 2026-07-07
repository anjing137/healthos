-- v006 — workout kcal:为 workout 表加 sport / intensity / kcal_burned / kcal_method / confidence
--
-- 设计:
--   - 老 workout 行的这五列全为 NULL:保留历史数据,deficit 把它们当 0
--   - kcal_method 标记数值来源:'MET' / 'manual' / 'pending'(unknown sport)
--   - confidence 是 [0,1] 的估算置信度,反映 MET 系统误差 ±15~20% 的诚实下限
--   - 1.0 表示用户手动校准过(用 healthos fix-workout <id> --kcal=X)
--
-- 不动旧列,只 ADD COLUMN。init() 通过 PRAGMA table_info 检查幂等。

ALTER TABLE workout ADD COLUMN sport TEXT;
ALTER TABLE workout ADD COLUMN intensity TEXT;
ALTER TABLE workout ADD COLUMN kcal_burned REAL;
ALTER TABLE workout ADD COLUMN kcal_method TEXT;
ALTER TABLE workout ADD COLUMN confidence REAL;
