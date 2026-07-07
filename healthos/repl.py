"""REPL — `uv run healthos` 直接进入的会话循环。

设计:
- 提示符 >>>,用户每次输一行
- 路由优先级:
  1. /exit  /quit / bye  → 退出
  2. /help              → 显示命令帮助
  3. /cmd args          → 调对应子命令(显式命令)
  4. 关键字 "早餐|午餐|晚餐|加餐|运动|训练|睡眠|膝盖" + 段头 → record
  5. 问号 / 怎么|什么|你吗|今天如何|减脂|建议 / 行尾"?" → chat(LLM)
  6. 兜底 → chat(LLM)
- session 状态:DEEPSEEK / cmd 调用一切像 CLI,但 history 持续
- 日志:不写 session 数据 db 入表,跟 CLI 走完全一样规则
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .banner import BANNER, WELCOME_BANNER

WELCOME = WELCOME_BANNER


HELP_TEXT = """\
HealthOS REPL 命令
====================

显式子命令(以 / 起头):
  /today             — 当日统计
  /today YYYY-MM-DD  — 指定日统计
  /deficit [date]    — 减脂报告
  /record <text>     — 直接走 record(不带 / 也行,带段头自动 record)
  /r <text>          — record 快捷
  /learn <text>      — 回答 open question
  /commit <text>     — 写当日总结(入 chat_log + record)
  /c <text>          — chat 快捷(LLM 真接)
  /summary           — LLM 总结当前 session → chat_log
  /verify            — LLM 准确度核查
  /verify-show       — 列待答核查
  /verify-answer <id> <text>
  /export [--date]   — Markdown 日报 → Obsidian vault
  /chat <text>       — 直接进 chat(不带 / 也可以,被 routing)
  /week [--days N]   — 最近 N 天趋势
  /fix-meal <id>     — 改 meal.raw_text;跑一次 input("新的 raw_text:")
  /exit / /quit / /bye

自动路由:
  你的输入被检查:
    段头关键字  → record(走 record 管线)
    事实动词    → record(今天吃 / 没吃 / 跑了 ...)
    mood/状态  → note(入 chat_log,不进数字表)
    句末 ? / 怎么 / 减脂 / 建议  → chat
    其它        → chat

键 / reset  → 重置对话 session(只是 in-memory chat,不影响 db)。
"""


# ─── 路由决定 ──────────────────────────────────────────────────────


_RECORD_KEYWORDS = (
    "早餐", "早饭", "午餐", "午饭", "晚餐", "晚饭",
    "加餐", "零食", "宵夜",
    "运动", "训练",
    "睡眠", "睡觉", "膝盖",
)

# 中文里有这些动词出现,**且** 看起来是事实陈述(非问号非"怎么")→ record
_FACT_VERBS = (
    "没吃", "没喝", "没练", "吃了", "喝了", "啃了", "炒了",
    "跑了", "走了", "练了", "游了", "睡了", "睡了八小时",
    "做了一组", "做了两组", "做了三组",
    "今天吃", "今早吃", "今晚吃",
    "摄入", "补充",
)

# 「生活事件 / mood / status」动词 — 不进数字表,入 chat_log.note
_NOTE_VERBS = (
    "心情", "压力", "焦虑", "沮丧", "崩溃", "开心", "累", "疲惫",
    "熬夜", "加班", "出差", "会", "开会",
    "今天没吃饭", "今天什么都没吃", "今天什么也没吃",
    "跳过了", "没办", "没赶上",
    "睡得", "醒来",
    "工作", "上班",
)

_CHAT_TRIGGERS = (
    "怎么", "为什么", "什么", "哪", "减脂", "建议", "真的",
    "你吗", "你觉", "聊", "帮我看看",
)


def route(text: str) -> str:
    """返回 'cmd' | 'record' | 'chat' | 'exit' | 'help' | 'noop' | 'note' 中的一个。"""
    t = text.strip()
    if not t:
        return "noop"

    if t.startswith("/"):
        parts = t[1:].split(None, 1)
        verb = (parts[0].lower() if parts else "").strip("/")
        if verb in ("exit", "quit", "bye"):
            return "exit"
        if verb == "help":
            return "help"
        return "cmd"

    if any(kw in t for kw in _RECORD_KEYWORDS):
        return "record"

    # 事实陈述动词 / mood 触发
    if not (t.rstrip().endswith("?") or t.rstrip().endswith("?") or t.rstrip().endswith("?")):
        if any(verb in t for verb in _NOTE_VERBS):
            return "note"
        if any(verb in t for verb in _FACT_VERBS):
            return "record"

    if t.rstrip().endswith("?") or t.rstrip().endswith("?") or t.rstrip().endswith("?"):
        return "chat"

    if any(trig in t for trig in _CHAT_TRIGGERS):
        return "chat"

    return "chat"


# ─── REPL 主体 ────────────────────────────────────────────────────


def _cmd_fix_meal(db_path: Optional[Path]) -> None:
    """交互式 /fix-meal <id>:
    - 取 meal
    - 展示 raw + 当前 kcal
    - 等 user 输入新 raw_text(回车),`/` 取消
    - 调 fix_meal.reparse_raw_text,写 audit_log,更新 meal
    """
    from .record.fix_meal import get_meal as _get_meal, fix_meal as _fix

    arg = input(">>> meal id (number, / cancel): ").strip()
    if arg.startswith("/") or not arg:
        print("cancelled")
        return
    try:
        meal_id = int(arg)
    except ValueError:
        print("usage: /fix-meal <id>")
        return

    meal_info = _get_meal(meal_id, db_path)
    if meal_info is None:
        print(f"meal#{meal_id} 不存在")
        return

    print(f"\n> meal#{meal_id} ({meal_info['log_date']} {meal_info['meal_slot']}):")
    print(f"  raw        : {meal_info['raw_text']!r}")
    print(f"  kcal       : {meal_info['kcals']:.0f}")
    print(f"  protein_g  : {meal_info['protein_g']:.1f}")

    new_raw = input("\n>>> new raw_text (or / to cancel): ").strip()
    if new_raw.startswith("/") or not new_raw:
        print("cancelled")
        return

    if new_raw == meal_info["raw_text"]:
        print("same as before, no change")
        return

    try:
        res = _fix(meal_id, new_raw, db_path)
    except Exception as e:
        print(f"fix failed: {e}")
        return

    print(f"\n> meal#{res.meal_id} updated, audit#{res.audit_log_id}")
    print(f"  raw        : {res.raw_text_before!r}")
    print(f"             → {res.raw_text_after!r}")
    print(f"  kcal       : {res.kcals_before:.0f} → {res.kcals_after:.0f}")
    print(f"  protein_g  : {res.protein_before:.1f} → {res.protein_after:.1f}")
    print(f"  fat_g      : {res.fat_before:.1f} → {res.fat_after:.1f}")
    print(f"  carb_g     : {res.carb_before:.1f} → {res.carb_after:.1f}")
    if res.warnings:
        print(f"  warnings   : {res.warnings}")


def run_repl(db_path: Optional[Path] = None) -> int:
    """REPL 主体。Ctrl+D / /exit 退出。

    db_path 仅测试用。
    """
    from .record.write import record as record_main, today as today_iso
    from .record.learn import answer
    from .query import build_today
    from .report.deficit import build_deficit, format_deficit
    from .llm.agent import run_chat as _rc, commit_summary, verify

    print(BANNER)
    print()
    print(WELCOME)

    while True:
        try:
            raw = input(">>> ").strip()
        except EOFError:
            print("(Ctrl-D received, exit)")
            return 0
        except KeyboardInterrupt:
            print("(Ctrl-C received, exit)")
            return 0

        if not raw:
            continue

        decision = route(raw)

        if decision == "exit":
            print("bye")
            return 0
        elif decision == "help":
            print(HELP_TEXT)
            continue
        elif decision == "noop":
            continue
        elif decision == "record":
            log_date = today_iso()
            try:
                res = record_main(raw, log_date, db_path=db_path, lenient=True)
            except Exception as e:
                print(f"record failed: {e}")
                continue
            print(f"ok — {res.meals} meals / {res.workouts} workouts / {res.sleep_rows} sleeps / {res.knee_rows} knees")
            if res.warnings:
                sample = res.warnings[:3]
                extra = len(res.warnings) - len(sample)
                tail = f" (+{extra} more)" if extra > 0 else ""
                print(f"warnings {len(res.warnings)}: {sample}{tail}")
            if res.questions:
                print(f"? {len(res.questions)} open question(s) — type /learn to answer")
            continue
        elif decision == "note":
            from .llm.agent import record_note
            log_date = today_iso()
            try:
                res = record_note(raw, log_date, db_path)
            except Exception as e:
                print(f"note failed: {e}")
                continue
            print(f"note stored (log_date={log_date}, id={res.get('id')})")
            continue
        elif decision == "chat":
            from datetime import date as _date, timedelta
            today_iso = _date.today().isoformat()
            yesterday_iso = (_date.today() - timedelta(days=1)).isoformat()
            try:
                resp = _rc(raw, today_iso=today_iso, yesterday_iso=yesterday_iso)
            except Exception as e:
                print(f"chat failed: {type(e).__name__}: {e}")
                continue
            print(f"\nagent: {resp}\n")
            continue
        elif decision == "cmd":
            # 交互式:专门为 /fix-meal <id> 提供交互式 raw_text 编辑
            parts = raw.lstrip("/").split(None, 1)
            verb = parts[0] if parts else ""
            if verb == "fix-meal":
                _cmd_fix_meal(db_path)
                continue
            # 其它 /cmd 透明转发给 cli.main
            try:
                from .cli import main as cli_main
                argv = raw.lstrip("/").split()
                rc = cli_main(argv)
                if rc:
                    print(f"(exit code {rc})")
            except SystemExit as e:
                if e.code and e.code != 0:
                    print(f"(exit code {e.code})")
            except Exception as e:
                print(f"cmd failed: {type(e).__name__}: {e}")
            continue

    return 0
