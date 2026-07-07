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
