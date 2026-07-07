# CLAUDE.md — Agent Guidelines for HealthOS

> 当工作目录在此项目时，Claude Code 会自动加载本文件。面向 AI Agent，给出指令而非建议。

## 核心原则

1. **先规划，再执行**
  - 说清楚：根因 → 方案 → 改哪些文件
  - 等到用户确认后再动手
  - 如果 3 次尝试还修不好，停下来重新评估

2. **改完立刻 `uv run pytest`** — 必须 36/36 全绿

3. **改 db 只加 migration** — 永远不手动改表结构，加新的 `v00N_xxx.sql`；init() 要幂等

4. **meal.kcals 是占位值** — 真值在 `build_today()` 里用 `open_question.resolved_grams` 重算

5. **永远不要 `UPDATE resolved_grams=0`** 来批量关闭问题 — 用 `learn()` 逐条更新，保留 answer_text

6. **LLM 不可直接写 SQLite，不可发建议** — `record_only=true`

7. **Obsidian vault**: `/Volumes/video/obsidian/health/Daily/` — `healthos export` 默认写这里

## 项目位置

- 根目录: `/Volumes/program/healthos/`
- venv: `.venv/`
- DB: `data/healthos.db` (gitignored)
- .env: 项目根，含 `DEEPSEEK_API_KEY` (gitignored)

## 架构速查

| 路径 | 职责 |
|---|---|
| `healthos/parser.py` | 自然语言 → 段落 |
| `healthos/nutrition/` | foods / portions / quantify |
| `healthos/record/` | write / learn / fix_meal |
| `healthos/query/` | 只读，真值重算 |
| `healthos/report/` | deficit / export / week |
| `healthos/llm/` | client / deepseek / mock / agent / tools |
| `healthos/db/` | conn.py + migrations |
| `healthos/repl.py` | REPL 界面 |
| `healthos/cli.py` | argparse 入口 |

## 常用命令

```bash
uv run pytest
uv run healthos                    # 进 REPL
uv run healthos today [YYYY-MM-DD] # 当日统计
uv run healthos deficit [date]     # 减脂报告
uv run healthos export [date]      # Markdown → Obsidian vault
uv run healthos week [--days N]    # N 天趋势
```

## 数据状态（2026-07-07）

- InBody: 2026-07-02 / 100.6kg / BMR=1890 / 体脂30% / 骨骼肌40.3kg
- 蛋白目标: 130g (来自 InBody 推荐)
- 体重: 2026-07-07 / 100.9kg
- 完整数据天数: 1 天 (2026-07-06: 2458 kcal/104.5g 蛋白/+471 缺口)
- 14 天滑动平均: 不可用 — 需要 ≥14 天真实数据
