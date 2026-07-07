"""REPL 路由测试 — 不真起 session,只验 route() 函数。"""

from healthos.repl import route


def test_exit_command():
    assert route("/exit") == "exit"
    assert route("/quit") == "exit"
    assert route("/bye") == "exit"
    assert route("/EXIT") == "exit"  # 大小写不敏感


def test_help_command():
    assert route("/help") == "help"


def test_other_command_routes():
    assert route("/today") == "cmd"
    assert route("/deficit") == "cmd"
    assert route("/today 2026-07-07") == "cmd"


def test_record_keywords():
    for kw in ("早餐", "午餐", "晚餐", "加餐", "运动", "训练", "睡眠", "膝盖"):
        assert route(f"{kw}: 一碗豆浆") == "record", f"kw={kw}"


def test_chat_question_marks():
    assert route("今天怎么样?") == "chat"
    assert route("我心情不好?") == "chat"
    assert route("该怎么办?") == "chat"


def test_chat_trigger_words():
    """含触发字('怎么' '减脂' 等)的 phrase → chat。"""
    assert route("我今天怎么减脂") == "chat"
    assert route("今天我会怎么样?") == "chat"


def test_fallback_chat():
    """没有 record 关键字,没有问号,没有触发字,默认 chat。"""
    # "今天心情不错" 现在匹配 _NOTE_VERBS("心情")→ 走 note
    assert route("今天心情不错") == "note"
    assert route("我刚到办公室") == "chat"


def test_empty_input():
    assert route("") == "noop"
    assert route("   ") == "noop"


def test_record_with_question_in_body():
    """段头在 / 没有问号 → record。"""
    assert route("早餐：豆浆一杯，包子一个") == "record"


def test_record_with_question_also_kw():
    """段头 + 问号,优先级是 record(段头更具体)。"""
    text = "今天吃得太多了?我该怎么办?早餐：豆浆"
    assert route(text) == "record"


# ── LLM prompt 契约测试(2026-07-07 放开"建议层"后)────────────────


def test_chat_system_allows_advice_and_writes_go_through_audit():
    """CHAT_SYSTEM + tools.py 必须满足升级后的契约:
    (a) 可发建议/教练话术
    (b) 写工具必须在 audit_log 留痕(source='llm-agent'),而不是无审计的裸 UPDATE

    2026-07-08 升级:LLM 现在可以调 3 个写工具(close_question / set_workout_kcal
    / reparse_meal),但每条写都必须走 _audit_write — 这才是真正的护栏。
    """
    from healthos.llm.agent import CHAT_SYSTEM
    from healthos.llm import tools
    import inspect

    # (a) prompt 层:允许建议
    assert "建议" in CHAT_SYSTEM
    assert "不要主动给" not in CHAT_SYSTEM
    assert "record_only" not in CHAT_SYSTEM

    # (b) 写工具必须在 audit_log 留痕:tools.py 写工具的路径是 dispatch → _tools
    # → _audit_write。三个写工具都必须经过 _audit_write。
    from healthos.llm import _tools
    _tools_src = inspect.getsource(_tools)
    assert "_audit_write" in _tools_src, "_tools.py 必须使用 _audit_write 包装"
    # 三工具每个都 update db + 调 _audit_write
    assert "_audit_write" in inspect.getsource(_tools.close_question)
    assert "_audit_write" in inspect.getsource(_tools.set_workout_kcal)
    assert "_audit_write" in inspect.getsource(_tools.reparse_meal)

    # 关键护栏:source 默认值是 'llm-agent' — LLM 走时不能伪装成 'repl'
    from healthos.llm._tools import _audit_write
    sig = inspect.signature(_audit_write)
    assert sig.parameters["source"].default == "llm-agent"

    # dispatch 路由了 3 个写工具
    assert "close_question" in inspect.getsource(tools.dispatch)
    assert "set_workout_kcal" in inspect.getsource(tools.dispatch)
    assert "reparse_meal" in inspect.getsource(tools.dispatch)
