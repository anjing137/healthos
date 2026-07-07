"""用户输入 → chat 闭环。

你之前说的:
- 用户 `healthos chat "<msg>"` — 跟 LLM 自由聊天(原文不入 db)
- 用户 `healthos commit "<summary>"` — 写最终总结(入库)
- 用户 `healthos verify [--date ...]` — LLM 拉数据 + 校对 + 问你

本模块负责:
- chat_session(session_state): 内存中的会话累积(每次进 chat 命令加进 state)
- commit_summary(text): 解析 summary, 像 record 一样入库 meal/workout/sleep/knee(用 parser.parse_sections)
- verify(date): 调 LLM 做"准确度核查", 把疑问写到 verify_pending 表
- resolve_verify(verify_id, answer): 用户答了,把 status=resolved
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..db.conn import connect, init, upsert_daily_log
from ..parser import parse as parse_sections
from ..nutrition.quantify import parse_item, ParsedQuantity
from ..record.write import record as record_main, today as today_iso
from ..llm.client import LLMRequest, chat, chat_json
from ..llm.tools import (
    read_today, get_recent_trend, get_open_questions,
    TOOLS_SPEC, dispatch,
)

# Use parser in strict=False mode so commit 接受纯文字"今天吃了鸡胸肉150g"
# 前面 record() 是 strict=True 的(够严);commit 路径我们重走 parse 把 strict 关了
import healthos.parser as _parser_mod
_orig_parse = _parser_mod.parse

def _parse_lenient(text: str):
    """Wraps parser.parse with strict=False for commit-input."""
    return _orig_parse(text, strict=False, split_compound=True)


# ─── session state — 内存持久(简单 dict,不写 db) ──────────────


@dataclass
class ChatSession:
    messages: list[dict] = field(default_factory=list)  # [{role, content, ts}]

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_agent(self, content: str) -> None:
        self.messages.append({"role": "agent", "content": content})

    def to_messages(self) -> list[dict]:
        """Returns messages ready for LLM."""
        return list(self.messages)


# 这 session 是 process-local(单进程);不能用 - 进程退出就没
# 真要持久 session,你得 sqlite;这版先做内存版(简单)
_SESSION = ChatSession()


def reset_session() -> None:
    global _SESSION
    _SESSION = ChatSession()


def get_session() -> ChatSession:
    return _SESSION


# ─── summary: LLM 自动总结当前 session,入 chat_log ─────────────


SUMMARY_SYSTEM = """你是 HealthOS 的对话总结助手。

任务:
1. 阅读对话历史(user 与 agent 多个来回)
2. 抽取出关键的"事实"和"情绪":
   - 客观事实:例 "user 早上没吃饭,没喝水,工作了一上午"
   - 主观感受:例 "user 提到心情差 / 加班压力"
3. 输出格式(JSON):
{
  "summary": "一句中文要点(简洁事实描述)",
  "mood": "中性|差|好|一般|焦虑|疲惫|...",
  "facts": ["事实1", "事实2", ...]
}
4. 思考模式关闭。
"""


def summarize_session(log_date: str | None = None, db_path: Path | None = None) -> dict:
    """让 LLM 总结当前 _SESSION 的对话历史,把它入 chat_log(speaker='user_summary')。

    不入 record / meal 等数字表 — 只入 chat_log。
    """
    from datetime import date as _date

    msgs = _SESSION.to_messages()
    if not msgs:
        return {"error": "session 是空的"}
    log_date = log_date or _date.today().isoformat()

    history_text = "\n".join(
        f"[{m['role']}] {m['content'][:300]}" for m in msgs
    )
    req = LLMRequest(
        system=SUMMARY_SYSTEM,
        user=f"对话历史:\n\n{history_text}\n\n请总结。",
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        max_tokens=512,
        thinking_disabled=True,
    )
    try:
        data = chat_json(req)
    except Exception as e:
        return {"error": f"LLM 总结失败: {type(e).__name__}: {e}"}

    summary = data.get("summary", "")
    mood = data.get("mood", "")
    facts = data.get("facts", [])

    facts_json = json.dumps(facts, ensure_ascii=False)
    conn = connect(db_path) if db_path else connect()
    try:
        cur = conn.execute(
            """INSERT INTO chat_log(log_date, created_at, speaker, content, source)
               VALUES(?, datetime('now'), 'user_summary', ?, 'summary')""",
            (log_date, f"{summary}  [{mood}] {facts_json}"),
        )
        conn.commit()
        sid = int(cur.lastrowid)
        reset_session()
        return {
            "log_date": log_date,
            "chat_log_id": sid,
            "summary": summary,
            "mood": mood,
            "facts": facts,
        }
    finally:
        conn.close()


# ─── chat: 用户说一句话, LLM 回 — 不入库 ───────────────────────


CHAT_SYSTEM = """你是 HealthOS 的对话助手 + 教练。

你可以调用以下工具 — 按用途分两组:

【只读工具】读用户数据,任何时候都可以调:
- read_today(date)
- get_recent_trend(window_days)
- get_open_questions(date)

【写工具】会写 SQLite、改用户数据,必须经过用户确认:
- close_question(qid, resolved_grams, notes)        关闭一条 open_question
- set_workout_kcal(workout_id, kcal, notes)         手动校准训练 kcal
- reparse_meal(meal_id, new_raw_text, notes)        替换 meal 的原话 + 重算

写工具的硬性使用规则(代码层 audit_log 也会记一笔 source='llm-agent'):
1. 在调用写工具之前,先在文字回复里**完整复述**你打算做什么:
   "我准备调用 close_question,参数是 qid=15, resolved_grams=150, notes=用户答复一份约 150g。是否确认?"
2. 等用户明确说"确认 / 好的 / 改吧" 才调。**严禁**在用户没回应前连续多次调。
3. notes 字段必须填,且要清晰说明依据(进 audit_log,出问题能反查)。
4. 如果用户没确认,改用文字描述建议、让用户自己用 REPL 跑 `healthos learn` / `fix-workout` /
   `record`。永远不要替用户拍板。

当前日期(REPL 启动时注入,这是事实不是推测): {today_iso}

任务:
1. 用户说事实 → 简短确认,不需要复述全部数据。
2. 用户问趋势/数据 → 调只读工具,**不要凭自己脑补数据**。
3. 用户明确说"帮我改 / 关掉 / 校准" → 走写工具流程(先复述 → 等确认 → 调)。
4. **可以给建议/教练话术**(健康饮食、训练量、蛋白缺口等),但:
   (a) 建议必须基于工具返回的数据,不能凭空;
   (b) 短 — 1~2 句话,不写长文;
   (c) 不要下达"立即做 X"的命令,语气保持建议性("可以试试" / "如果...")
5. 中文回答。
6. 思考模式:关闭(响应要短,直答)。

日期重要原则:
- 不要猜测"今天"。当前日期已注入到 system prompt 顶端,**用 {today_iso} 当作今天**。
- 如果用户说"昨天" → 用 `{yesterday_iso}` (REPL 注入)。
- 如果用户说"X月Y日"或"YYYY-MM-DD" → 直接用该日期。
- 若用户没明确日期,默认查今天。
"""


def run_chat(user_msg: str, today_iso: Optional[str] = None, yesterday_iso: Optional[str] = None) -> str:
    """用户输入 → 工具调用 → LLM 短答。

    不入 db,返回 LLM 的响应文本。

    today_iso / yesterday_iso 由 REPL 注入,确保 LLM 不会猜错日期。
    """
    from datetime import date as _date, timedelta

    if today_iso is None:
        today_iso = _date.today().isoformat()
    if yesterday_iso is None:
        yesterday_iso = (_date.today() - timedelta(days=1)).isoformat()

    _SESSION.add_user(user_msg)
    sys_prompt = CHAT_SYSTEM.format(today_iso=today_iso, yesterday_iso=yesterday_iso)

    req = LLMRequest(
        system=sys_prompt,
        user=user_msg,
        tools=TOOLS_SPEC,
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        max_tokens=512,
        thinking_disabled=True,
    )
    resp = chat(req)

    # 如果 LLM 直接回 → 用
    if not resp.tool_calls:
        _SESSION.add_agent(resp.text)
        return resp.text

    # 如果调用工具 → 走 multi-turn
    messages: list[dict] = []
    messages.append({"role": "system", "content": sys_prompt})
    messages.append({"role": "user", "content": user_msg})
    messages.append(
        {
            "role": "assistant",
            "content": resp.text or "",
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for i, tc in enumerate(resp.tool_calls)
            ],
        }
    )
    # 工具结果
    for i, tc in enumerate(resp.tool_calls):
        result = dispatch(tc.name, tc.arguments)
        messages.append(
            {"role": "tool", "tool_call_id": f"call_{i}", "content": result}
        )
    # 第二轮:LLM 总结(OpenAI-style multi-turn messages)
    final_req = LLMRequest(
        system=sys_prompt,
        user=user_msg,
        model=req.model,
        max_tokens=512,
        thinking_disabled=True,
    )
    # DeepSeek client 接受 messages(✓ 我之前已经实现了 attr 兼容)
    final_req.messages = messages
    final_resp = chat(final_req)

    _SESSION.add_agent(final_resp.text)

    # 让 user 看到 LLM 调用了哪些 tool(以前 user 只看到 final text,中间吃了)
    tool_summary_lines = [
        f"\U0001f527 {tc.name}({', '.join(f'{k}={v}' for k, v in tc.arguments.items())})"
        for tc in resp.tool_calls
    ]
    if tool_summary_lines:
        prefix = "\n".join(tool_summary_lines)
        return f"{prefix}\n\n{final_resp.text}"
    return final_resp.text


# ─── commit: 用户写总结,系统 parse + 写正表 ─────────────────


def commit_summary(text: str, log_date: str | None = None, db_path: Path | None = None) -> dict:
    """用户写总结(中文), parse 后入库 meal/workout/sleep/knee。

    设计:
    - 用户文本可能无段头("今天吃了一杯豆浆")也可能结构化("早餐:...")
    - 用 record(lenient=True) 让 parser 接受任意形态
    - 同时入正表 + chat_log(存 user summary)
    """
    log_date = log_date or today_iso()
    result = record_main(text, log_date, db_path, lenient=True)
    conn = connect(db_path) if db_path else connect()
    try:
        conn.execute(
            """INSERT INTO chat_log(log_date, created_at, speaker, content, source)
               VALUES(?, datetime('now'), 'user_summary', ?, 'commit')""",
            (log_date, text),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "log_date": log_date,
        "meals": result.meals,
        "workouts": result.workouts,
        "sleep_rows": result.sleep_rows,
        "knee_rows": result.knee_rows,
        "warnings": result.warnings,
        "questions": result.questions,
    }


def record_note(text: str, log_date: str | None = None, db_path: Path | None = None) -> dict:
    """用户写一条 'note / mood / status' 内容,**不入数字表**,只入 chat_log。

    设计意图:
    - "今天没吃饭" / "加班一上午" / "心情糟糕" → 这是生活状态,不是食物
    - "早餐:豆浆" → 这是事实,走 record
    - "今天怎么减脂" → 这是问题,走 chat
    """
    log_date = log_date or today_iso()
    conn = connect(db_path) if db_path else connect()
    try:
        cur = conn.execute(
            """INSERT INTO chat_log(log_date, created_at, speaker, content, source)
               VALUES(?, datetime('now'), 'note', ?, 'repl')""",
            (log_date, text),
        )
        conn.commit()
        return {"log_date": log_date, "id": int(cur.lastrowid), "stored": True}
    finally:
        conn.close()


# ─── verify: LLM 拉数据 + 找疑点 + 写 verify_pending ──────────


VERIFY_SYSTEM = """你是 HealthOS 的"准确度核查"助手。

任务:
1. 用 read_today(date) 拉当日数据
2. 用 get_recent_trend(window_days=14) 看近期趋势
3. 用 get_open_questions(date) 看哪些数字还在猜
4. 找可疑点:
   - 数字异常(例如摄入 < 500 或 > 5000 kcal)
   - 与前几天变化剧烈的指标(体重 ±2kg / 一日)
   - 还没回答的 open_question
   - 不合理的食物组合(摄入与营养素不匹配)
5. 输出严格 JSON:
{
  "checks": [
    {"field": "meal#1.kcals", "question": "今日摄入 700 kcal 偏低?", "severity": "high"},
    ...
  ]
}
- 不超过 5 个 check。
- 只问 user 仍能决定的疑点。不要凭空质疑明显正确的数据。
- thinking 模式关闭。
- 中文问题。
"""


def verify(log_date: str | None = None, db_path: Path | None = None) -> dict:
    """调 LLM 做准确度核查,把它找到的可疑点写进 verify_pending 表。"""
    log_date = log_date or today_iso()
    today_data = read_today(log_date, db_path)
    trend = get_recent_trend(14, db_path)
    open_q = get_open_questions(log_date, db_path)

    user_prompt = f"""待核查数据:
log_date: {log_date}

今日 meal/workout/sleep/knee/weight: {json.dumps(today_data, ensure_ascii=False, default=str)[:3000]}

14d 趋势: {json.dumps(trend, ensure_ascii=False, default=str)[:1500]}

待答 open_question: {json.dumps(open_q, ensure_ascii=False, default=str)[:1500]}
"""

    req = LLMRequest(
        system=VERIFY_SYSTEM,
        user=user_prompt,
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        max_tokens=800,
        thinking_disabled=True,
    )
    try:
        result = chat_json(req)
        checks = result.get("checks", [])
    except Exception as e:
        return {"log_date": log_date, "error": f"LLM failed: {e}", "stored": []}

    if not isinstance(checks, list):
        return {"log_date": log_date, "error": "LLM returned non-list", "stored": []}

    conn = connect(db_path) if db_path else connect()
    try:
        stored_ids: list[int] = []
        for c in checks:
            if not isinstance(c, dict):
                continue
            field = str(c.get("field", "?")).strip()
            question = str(c.get("question", "?")).strip()
            sev = str(c.get("severity", "low")).strip()
            if not field or not question:
                continue
            cur = conn.execute(
                """INSERT INTO verify_pending(log_date, created_at, field, question, severity, status)
                   VALUES(?, datetime('now'), ?, ?, ?, 'open')""",
                (log_date, field, question, sev),
            )
            stored_ids.append(int(cur.lastrowid))
        conn.commit()
        return {"log_date": log_date, "stored": stored_ids, "checks": checks}
    finally:
        conn.close()


def list_verify_pending(log_date: str | None = None, db_path: Path | None = None) -> list[dict]:
    """返回 status='open' 的核查项。"""
    conn = connect(db_path) if db_path else connect()
    try:
        if log_date:
            rows = conn.execute(
                """SELECT id, log_date, field, question, severity, status, created_at
                   FROM verify_pending WHERE log_date=? AND status='open'
                   ORDER BY id""",
                (log_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, log_date, field, question, severity, status, created_at
                   FROM verify_pending WHERE status='open'
                   ORDER BY id"""
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_verify(verify_id: int, answer: str, db_path: Path | None = None) -> dict:
    """用户给出答案;verify_pending 状态变 resolved,记 answer_text。"""
    conn = connect(db_path) if db_path else connect()
    try:
        conn.execute(
            """UPDATE verify_pending
               SET status='resolved', resolved_text=?, resolved_at=datetime('now')
               WHERE id=?""",
            (answer, verify_id),
        )
        conn.commit()
        # 同时记录到 chat_log(用户答的内容也算对话存档)
        date_row = conn.execute(
            "SELECT log_date FROM verify_pending WHERE id=?", (verify_id,)
        ).fetchone()
        if date_row:
            conn.execute(
                """INSERT INTO chat_log(log_date, created_at, speaker, content, source)
                   VALUES(?, datetime('now'), 'verify_answer', ?, 'verify')""",
                (date_row["log_date"], f"verify#{verify_id}: {answer}"),
            )
            conn.commit()
        return {"id": verify_id, "resolved": True}
    finally:
        conn.close()
