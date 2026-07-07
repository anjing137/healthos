"""D4.5 C — CLI 三命令:
    healthos today [日期]
    healthos record "<日记>"
    healthos learn "<回答>"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from pathlib import Path

from .record.write import record, today as today_iso
from .record.learn import answer
from .query import build_today
from .report.deficit import build_deficit, format_deficit
from .report.export import export_day
from .report.week import build_week as build_week_window, format_week
from .llm.agent import (
    run_chat, commit_summary, verify,
    list_verify_pending, resolve_verify,
    reset_session, get_session, summarize_session,
)


DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "healthos.db"


def _format_report(r) -> str:
    lines: list[str] = []
    lines.append(f"HealthOS — {r.log_date} 日报")
    lines.append("")

    if not r.meals:
        lines.append("(当日还没录)")
        return "\n".join(lines)

    slot_label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
    for m in r.meals:
        lines.append(f"## {slot_label.get(m.meal_slot, m.meal_slot)}")
        lines.append(f"  {m.raw_text}")
        for it in m.items:
            name = it.get("name") or "?"
            g = it.get("resolved_grams", 0)
            k = it.get("resolved_kcals", 0)
            p = it.get("resolved_protein_g", 0)
            lines.append(f"    - {name:<12} {g:>6.0f} g  {k:>5.0f} kcal  {p:>5.1f} g P")
        lines.append(f"  小计 {m.kcals:.0f} kcal / {m.protein_g:.1f} g 蛋白")
        lines.append("")

    lines.append("── 今日合计 ──")
    lines.append(f"  kcal {r.kcals:.0f}   蛋白 {r.protein_g:.1f}   脂肪 {r.fat_g:.1f}   碳水 {r.carb_g:.1f}")
    if r.workout_minutes:
        lines.append(f"  训练 {r.workout_minutes} min")
    if r.sleep_duration_min:
        hrs = r.sleep_duration_min / 60
        lines.append(f"  睡眠 {hrs:.1f} h")
    if r.knee_tightness is not None:
        lines.append(f"  膝盖紧绷 {r.knee_tightness}/10")

    if r.open_questions:
        lines.append("")
        lines.append("── 待你补全 ──")
        for q in r.open_questions:
            lines.append(f"  Q#{q['id']} {q['food_name'] or '(未识别)'}: {q['question']}")
        lines.append("  → 用 `healthos learn \"<你的回复>\"` 回答")

    return "\n".join(lines)


def cmd_today(args: argparse.Namespace) -> int:
    # 优先使用位置参数,其次 --date,缺省今天的 ISO date
    log_date = args.date_str or getattr(args, "date", None) or today_iso()
    rep = build_today(log_date, args.db)
    print(_format_report(rep))
    return 0


def cmd_deficit(args: argparse.Namespace) -> int:
    log_date = args.date_str or getattr(args, "date", None) or today_iso()
    rep = build_deficit(log_date, args.db)
    print(format_deficit(rep))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    resp = run_chat(text)
    print(f"agent: {resp}")
    print(f"\n(session 已累积 {len(get_session().messages)//2} 轮。要入库请用 `healthos commit \"<summary>\"`)")
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    log_date = args.date
    res = commit_summary(text, log_date, args.db)
    print(f"✓ committed {res['log_date']}: {res['meals']} meals / {res['workouts']} workouts / {res['sleep_rows']} sleeps / {res['knee_rows']} knees")
    if res["warnings"]:
        print(f"⚠ warnings: {res['warnings']}")
    if res["questions"]:
        print(f"? {len(res['questions'])} open question(s) created")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    log_date = args.date or today_iso()
    res = verify(log_date, args.db)
    if "error" in res and not res.get("stored"):
        print(f"✗ {res['error']}")
        return 1
    n = len(res.get("stored", []))
    print(f"✓ {n} check(s) stored in verify_pending")
    for c in res.get("checks", []):
        sev = c.get("severity", "?")
        field = c.get("field", "?")
        q = c.get("question", "?")
        print(f"  [{sev}] {field}: {q}")
    print("\n→ 用 `healthos verify show` 看全部待答 / `healthos verify answer <id> \"<answer>\"` 答")
    return 0


def cmd_verify_show(args: argparse.Namespace) -> int:
    pending = list_verify_pending(args.date, args.db)
    if not pending:
        print("(无待答核查)")
        return 0
    print(f"== 待答核查 ({len(pending)} 条) ==")
    for p in pending:
        print(f"  V#{p['id']:<3} [{p['severity']:<6}] {p['log_date']} {p['field']}: {p['question']}")
    return 0


def cmd_verify_answer(args: argparse.Namespace) -> int:
    verify_id = args.id
    text = " ".join(args.text)
    res = resolve_verify(verify_id, text, args.db)
    print(f"✓ V#{verify_id} closed")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    log_date = args.date or today_iso()
    res = record(text, log_date, args.db)
    print(f"✓ recorded {res.meals} meals / {res.workouts} workouts / {res.sleep_rows} sleeps / {res.knee_rows} knees")
    if res.warnings:
        print(f"⚠ warnings: {res.warnings}")
    if res.questions:
        print(f"? {len(res.questions)} open question(s) created (id: {res.questions})")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    log_date = args.date or today_iso()
    res = answer(text, log_date, args.db)
    print(f"✓ closed {len(res.closed)} question(s): ids {res.closed}")
    if res.skipped:
        print(f"⚠ skipped (没找到对应 question): {res.skipped}")
    return 0


def cmd_repl(args: argparse.Namespace) -> int:
    """REPL 入口: `uv run healthos repl` 或 `uv run healthos`(无 subcommand 时)."""
    from .repl import run_repl
    return run_repl(args.db)


def cmd_r(args: argparse.Namespace) -> int:
    """REPL 内部 /r 入口 — 走 record(lenient=True)。"""
    from .record.write import record as record_main
    text = " ".join(args.text)
    log_date = args.date or today_iso()
    res = record_main(text, log_date, args.db, lenient=True)
    print(f"ok — {res.meals} meals / {res.workouts} workouts / {res.sleep_rows} sleeps / {res.knee_rows} knees")
    if res.warnings:
        sample = res.warnings[:3]
        extra = len(res.warnings) - len(sample)
        tail = f" (+{extra} more)" if extra > 0 else ""
        print(f"warnings {len(res.warnings)}: {sample}{tail}")
    if res.questions:
        print(f"? {len(res.questions)} open question(s) — type /learn to answer")
    return 0


def cmd_c(args: argparse.Namespace) -> int:
    """REPL 内部 /c 入口 — 走 chat(LLM 真接)。"""
    text = " ".join(args.text)
    from datetime import date as _date, timedelta
    today_iso = _date.today().isoformat()
    yesterday_iso = (_date.today() - timedelta(days=1)).isoformat()
    resp = run_chat(text, today_iso=today_iso, yesterday_iso=yesterday_iso)
    print(f"\nagent: {resp}\n")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """REPL 内部 /summary 入口 — LLM 自动总结当前 session。"""
    log_date = args.date or today_iso()
    res = summarize_session(log_date, args.db)
    if "error" in res:
        print(f"✗ {res['error']}")
        return 1
    print(f"✓ chat_log #{res['chat_log_id']} (date={res['log_date']})")
    print(f"  summary: {res.get('summary', '')}")
    print(f"  mood:    {res.get('mood', '')}")
    facts = res.get("facts", []) or []
    if facts:
        print(f"  facts:")
        for f in facts:
            print(f"    - {f}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """D6: Markdown 日报落盘(默认 → Obsidian vault)。"""
    log_date = args.date or today_iso()
    out = None
    if args.out:
        out = Path(args.out)
    path = export_day(log_date, out, args.db)
    print(f"✓ 写到 {path}")
    return 0


def cmd_week(args: argparse.Namespace) -> int:
    """D7: 周汇总 / 趋势。"""
    end = args.date or today_iso()
    rules_path = Path(__file__).resolve().parents[1] / "config" / "health_rules.yaml"
    import yaml
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
    protein_target = rules.get("inbody_recorded", {}).get("daily_protein_target_g")

    rows = build_week_window(end_date=end, window_days=args.days, db_path=args.db)
    print(format_week(rows, protein_target_g=protein_target))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="healthos", description="Personal Health Agent (record-only)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径(默认 data/healthos.db)")
    sub = parser.add_subparsers(dest="cmd")

    p_today = sub.add_parser("today", help="显示当日统计")
    p_today.add_argument("date_str", nargs="?", default=None, help="YYYY-MM-DD(位置参数);缺省今天")
    p_today.add_argument("--date", help="YYYY-MM-DD(兼容旧版)")
    p_today.set_defaults(func=cmd_today)

    p_rec = sub.add_parser("record", help="录入一日日记")
    p_rec.add_argument("text", nargs="+", help="日记正文(可多段)")
    p_rec.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_rec.set_defaults(func=cmd_record)

    p_learn = sub.add_parser("learn", help="回答 open question")
    p_learn.add_argument("text", nargs="+", help="回答正文,例如 '3两白酒 / 日本豆腐 100g'")
    p_learn.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_learn.set_defaults(func=cmd_learn)

    p_def = sub.add_parser("deficit", help="今日减脂报告(摄入 vs TDEE)")
    p_def.add_argument("date_str", nargs="?", default=None, help="YYYY-MM-DD(位置参数);缺省今天")
    p_def.add_argument("--date", help="YYYY-MM-DD(兼容旧版)")
    p_def.set_defaults(func=cmd_deficit)

    p_chat = sub.add_parser("chat", help="跟 LLM 自由聊天(原文不入 db;commit 才入)")
    p_chat.add_argument("text", nargs="+", help="你的问/说")
    p_chat.set_defaults(func=cmd_chat)

    p_commit = sub.add_parser("commit", help="写当日总结(走 record 的解析管线)")
    p_commit.add_argument("text", nargs="+", help="你今天的总结 text")
    p_commit.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_commit.set_defaults(func=cmd_commit)

    p_verify = sub.add_parser("verify", help="LLM 准确度核查,找出可疑点")
    p_verify.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_verify.set_defaults(func=cmd_verify)

    p_vshow = sub.add_parser("verify-show", help="列出 verify 待答项")
    p_vshow.add_argument("--date", help="YYYY-MM-DD;缺省全部")
    p_vshow.set_defaults(func=cmd_verify_show)

    p_vans = sub.add_parser("verify-answer", help="回答 verify 中的某个问题")
    p_vans.add_argument("id", type=int, help="verify_pending.id")
    p_vans.add_argument("text", nargs="+", help="你的回答 text")
    p_vans.set_defaults(func=cmd_verify_answer)

    p_r = sub.add_parser("r", help="REPL 内部: record")
    p_r.add_argument("text", nargs="+", help="日记正文")
    p_r.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_r.set_defaults(func=cmd_r)

    p_c = sub.add_parser("c", help="REPL 内部: chat")
    p_c.add_argument("text", nargs="+", help="问句")
    p_c.set_defaults(func=cmd_c)

    p_summary = sub.add_parser("summary", help="REPL 内部: LLM 总结会话")
    p_summary.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_summary.set_defaults(func=cmd_summary)

    p_repl = sub.add_parser("repl", help="进入 REPL 会话(等效不传 subcommand)")
    p_repl.set_defaults(func=cmd_repl)

    p_export = sub.add_parser("export", help="Markdown 日报落 Obsidian vault(Daily/YYYY-MM-DD.md)")
    p_export.add_argument("--date", help="YYYY-MM-DD;缺省今天")
    p_export.add_argument("--out", help="输出路径;缺省 vault Daily/")
    p_export.set_defaults(func=cmd_export)

    p_week = sub.add_parser("week", help="最近 N 天趋势(默认 7 天)")
    p_week.add_argument("--days", type=int, default=7, help="窗口天数;default 7")
    p_week.add_argument("--date", help="end_date (窗口末日);缺省今天")
    p_week.set_defaults(func=cmd_week)

    args = parser.parse_args(argv)
    if args.cmd is None:
        # 无 subcommand 时 = REPL
        return cmd_repl(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
