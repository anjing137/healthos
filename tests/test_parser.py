"""parser 阶段 1 的测试 — 段落切分正确性。"""

from healthos.parser import parse


def test_empty():
    assert parse("") == []
    assert parse("   \n  ") == []


def test_single_section():
    sections = parse("早餐：一个鸡蛋，一杯豆浆")
    assert len(sections) == 1
    s = sections[0]
    assert s.name == "breakfast"
    # items 拆分由"、" "," ; 等分隔;这里逗号是全角","
    assert any("鸡蛋" in x for x in s.items)
    assert any("豆浆" in x for x in s.items)


def test_full_sample_input():
    """项目 design 里那段完整示例,6 个段落。"""
    text = """早餐：无糖豆浆600ml、肉包1个、南瓜两片
午餐：鸡胸肉150g、排骨4块、冬瓜、米饭一小碗
晚餐：西瓜300g、鸡蛋2个

运动：
快走60分钟
臀桥4×15
死虫3组
平板支撑1组

睡眠：
昨天23:30睡
今天7:00起

膝盖：
右膝发紧2/10
无疼痛
无肿胀
"""
    sections = parse(text)
    names = [s.name for s in sections]
    assert names == ["breakfast", "lunch", "dinner", "workout", "sleep", "knee"]

    breakfast = sections[0]
    assert "豆浆" in breakfast.raw
    assert any("鸡胸" in x or "鸡蛋" in x for x in sections[1].items + breakfast.items)

    workout = sections[3]
    # 段内多行,行内不分 items
    assert any("快走" in it for it in workout.items)
    assert any("臀桥" in it for it in workout.items)
    assert any("死虫" in it for it in workout.items)


def test_aliases():
    """早餐==早饭,晚餐==晚饭,运动==训练。"""
    assert parse("早饭：豆浆")[0].name == "breakfast"
    assert parse("午饭：豆浆")[0].name == "lunch"
    assert parse("晚饭：豆浆")[0].name == "dinner"
    assert parse("训练：硬拉")[0].name == "workout"
    assert parse("睡眠：")[0].name == "sleep"
    assert parse("膝盖：")[0].name == "knee"


def test_strict_orphan_text():
    """段头之前的孤儿文本严格模式抛错。"""
    import pytest
    with pytest.raises(ValueError):
        parse("今天去公园散步了一小时\n早餐：豆浆")


def test_real_user_input_with_chinese_comma():
    """用户真实输入会用中文逗号作段头分隔符。"""
    sections = parse("早餐，一个鸡蛋，一杯豆浆")
    assert len(sections) == 1
    s = sections[0]
    assert s.name == "breakfast"
    # 切完后,items 里应有鸡蛋 / 豆浆
    joined = " ".join(s.items)
    assert "鸡蛋" in joined
    assert "豆浆" in joined
