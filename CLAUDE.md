# CLAUDE.md — Agent Guidelines for HealthOS

> 当工作目录在此项目时，Claude Code 会自动加载本文件。内容与 `notes/agents.md` 保持同步，但面向 AI Agent 指令格式。

## ⚠️ 核心原则

1. **先规划，再执行** — 说清楚：根因 → 方案 → 改哪些文件。等确认后再动手。
2. **每步要有明确结果** — 不要陷入无意义 debug 循环。3 次没修好就停下来重新评估。
3. **改完立刻跑 `uv run pytest`** — 必须 36/36 全绿。
4. **改 db 只加 migration** — 永远不手动改表结构，加新的 `v00N_xxx.sql`。
5. **meal.kcals 是占位值** — 真值在 build_today() 里用 open_question.resolved_grams 重算。deficit 报告也要走重算。
6. **永远不要 `UPDATE resolved_grams=0`** — 用 learn() 逐条更新。
7. **LLM 不可直接写 SQLite**，不可发建议 — record_only=true。
8. **Obsidian vault**: `/Volumes/video/obsidian/health/Daily/` — export 命令默认写这里。

## 项目位置

- 根目录: `/Volumes/program/healthos/`
- venv: `.venv/`
- DB: `data/healthos.db` (gitignored)
- .env: 项目根，含 DEEPSEEK_API_KEY (gitignored)

## 架构速查

| 目录 | 职责 |
|---|---|
| `healthos/parser.py` | 自然语言→段落 |
| `healthos/nutrition/` | foods / portions / quantify |
| `healthos/record/` | write / learn / fix_meal |
| `healthos/query/` | 只读，真值重算 |
| `healthos/report/` | deficit / export(Markdown→Obsidian) / week |
| `healthos/llm/` | client / deepseek / mock / agent / tools |
| `healthos/db/` | conn.py + migrations |
| `healthos/repl.py` | REPL 界面 |
| `healthos/cli.py` | argparse 入口 |

## 常用命令

```bash
uv run pytest                    # 跑测试，36 必须全绿
uv run healthos                  # 进 REPL
uv run healthos today [date]     # 当日统计
uv run healthos deficit [date]   # 减脂报告
uv run healthos export [date]    # Markdown → Obsidian vault
uv run healthos week [--days N]  # N 天趋势
```

## 数据状态

- InBody: 2026-07-02 / 100.6kg / BMR 1890 / 体脂 30% / 肌 40.3kg / 蛋白目标 130g
- 体重: 2026-07-07 / 100.9kg
- 有完整数据的天数: 1 天 (2026-07-06)
- 14 天滑动平均体重: 不可用，需要数据积累
