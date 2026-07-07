# HealthOS — Agent 开发约定

> 给 Claude Code / Codex / 未来协作者。不是 README，是开发规范。

---

## 工作流铁律

1. **先规划，再执行。**不要直接上手改代码。说清楚根因、修复方案、改哪些文件，等确认后再动。

2. **每步都要有明确结果。**跑测试、跑命令前先说预期，跑完了对齐预期 vs 实际。如果 3 次尝试还没修好，停下来重新评估。

3. **先跑 `uv run pytest`**。任何改动后必须 36 个测试全绿。

4. **改 db 先看 migration。**不要手动改表结构，永远加新的 `v00N_xxx.sql` 文件。init() 要幂等。

---

## 项目架构原则

| 层 | 职责 | 例子 |
|---|---|---|
| `parser.py` | 自然语言→结构化段落 | 逗号分隔、空格切分、量词识别 |
| `nutrition/` | 食物知识库 | foods.py(内置+用户自定义), portions.py(单位换算), quantify.py(数量→克→kcal) |
| `record/` | **写**库 | write.py(record入口), learn.py(回答open_q), fix_meal.py(纠错+audit) |
| `query/` | **只读** | build_today() → 用 closed_question 真值重算 |
| `report/` | 渲染 | deficit.py(减脂报告), export.py(Markdown→Obsidian), week.py(趋势) |
| `llm/` | AI 层 | client.py(抽象), deepseek.py(DeepSeek V4), mock.py(无key兜底), agent.py(chat/verify/summary), tools.py(LLM可读的SQLite视图) |
| `db/` | 存储 | conn.py(连接+migration), migrations/v00N_xxx.sql |
| `repl.py` | 用户界面 | 关键字路由(/r /c /fix-meal /summary /export /week /deficit /today) |

---

## 数据完整性

- **`meal.kcals` 是占位值**，只作 record 时的默认估算。真值在 `build_today()` 里用 `open_question.resolved_grams` 重算。
- **open_question 是"用户校准"的唯一通道**。learn 回答后，`resolved_grams` 非零真值被 query 层引用。
- **永远不要手动 `UPDATE ... resolved_grams=0`** 来"清空"已关闭的问题——这会覆盖用户真实回答。用 learn() 逐条更新。
- **audit_log 记录所有 /fix-meal 操作**，before/after JSON 完整保留。

---

## LLM 边界

| LLM 可以 | LLM 不可以 |
|---|---|
| ✅ `read_today()` / `get_recent_trend()` / `get_open_questions()` 只读 | ❌ 直接写 SQLite |
| ✅ chat 自由对话（不入 db） | ❌ 给出"你应该怎样"的建议(record_only=true) |
| ✅ verify 找出可疑数据点，写入 verify_pending | ❌ 自己编造营养数据 |
| ✅ 中文回答 | ❌ thinking 模式默认关闭 |

---

## 当前数据状态

- **InBody**: 2026-07-02 / 100.6kg / BMR=1890 / 体脂30% / 骨骼肌40.3kg / 蛋白目标130g
- **体重**: 2026-07-07 / 100.9kg
- **完整数据日**: 只有 2026-07-06(2458 kcal / 104.5g 蛋白 / 缺口+471)
- **关键事实**: 14 天滑动平均体重不可用，还要 ≥14 天真实数据积累

---

## 阅读顺序（新协作者）

1. `notes/design.md` — 为什么这样做
2. `README.md` — 用户操作手册
3. 本文件 — 开发约定
4. `healthos/db/migrations/v001_init.sql` — 表结构起点
5. `config/health_rules.yaml` — 健康规则（BMR/TDEE/蛋白目标）
6. `pyproject.toml` — 依赖清单

---

*最后更新: 2026-07-07*
