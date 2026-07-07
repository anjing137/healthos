"""nutrition 层 tests — foods + portions + quantify 联手。"""

import json
import pytest

from healthos.nutrition.foods import lookup, learn_food, USER_FOODS_PATH
from healthos.nutrition.portions import convert
from healthos.nutrition.quantify import parse_item


def test_builtin_lookup():
    assert lookup("鸡胸肉") is not None
    assert lookup("unknown-thing-xyz") is None


def test_learn_user_food_persists(tmp_path, monkeypatch):
    # 把 USER_FOODS_PATH 指到临时,确保不污染真实 data/
    fake = tmp_path / "foods.json"
    monkeypatch.setattr("healthos.nutrition.foods.USER_FOODS_PATH", fake)
    learn_food("测试食物", kcals_per_100g=100, protein_g=20, fat_g=5, carb_g=10)
    assert fake.exists()
    raw = json.loads(fake.read_text(encoding="utf-8"))
    assert "测试食物" in raw
    assert raw["测试食物"]["k"] == 100


def test_user_food_overrides_builtin(tmp_path, monkeypatch):
    fake = tmp_path / "foods.json"
    monkeypatch.setattr("healthos.nutrition.foods.USER_FOODS_PATH", fake)
    learn_food("鸡蛋", kcals_per_100g=200, protein_g=15, fat_g=10, carb_g=0)
    h = lookup("鸡蛋")
    assert h.source == "user"
    assert h.macros.kcals == 200


def test_portion_food_specific():
    # 1 个鸡蛋 = 50g
    g = convert("鸡蛋", 2.0, "个")
    assert g.grams == 100.0
    assert g.food_specific


def test_portion_generic():
    g = convert("鸡胸肉", 150.0, "g")
    assert g.grams == 150.0
    assert g.unit_kind == "mass"


def test_quantify_inline_grams():
    items = parse_item("鸡胸肉(150g)")
    assert len(items) == 1
    pq = items[0]
    assert pq.inline is True
    assert pq.grams == 150.0
    assert pq.food_hit.name == "鸡胸肉"
    assert pq.food_hit.macros.kcals == 165
    # 150g chicken = 247.5 kcal
    assert pq.kcals() == pytest.approx(247.5)


def test_quantify_chinese_count_unit():
    items = parse_item("鸡蛋两个")
    pq = items[0]
    assert pq.qty == 2.0
    assert pq.unit == "个"
    assert pq.grams == 100.0


def test_quantify_no_unit_food_default_100g():
    items = parse_item("鸡蛋")
    pq = items[0]
    # 无 qty 无 unit → qty=1,unit='份' → 鸡蛋 1 份 = 100g
    assert pq.grams == 100.0


def test_quantify_unknown_food_returns_empty_hit():
    items = parse_item("鲸鱼三文鱼味道的白酱")
    assert items[-1].food_hit is None  # 至少最后一条为 None


def test_real_user_input_chinese_comma(tmp_path, monkeypatch):
    fake = tmp_path / "foods.json"
    monkeypatch.setattr("healthos.nutrition.foods.USER_FOODS_PATH", fake)
    items = parse_item("一个鸡蛋")
    assert items[0].qty == 1.0
    assert items[0].grams == 50.0  # 鸡蛋 1 个 = 50g
    items2 = parse_item("一杯豆浆")
    pq = items2[0]
    assert pq.unit == "杯"
    assert pq.food_hit.name == "豆浆"
    # 豆浆 1 杯 = 240ml ≈ 240g;每 100g 33 kcal → 79.2
    assert pq.kcals() == pytest.approx(79.2, rel=0.01)
