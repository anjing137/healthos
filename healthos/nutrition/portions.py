"""单位换算 + 食物专属单位。

设计:
- "1 碗米饭" / "150g 鸡胸" / "一碗豆浆" 都最终落到 克数,再用 foods.py 的 macros 算
- 食物专属单位优先于通用单位(米饭:一碗=150g;鸡胸:1块=120g)
- 未知单位 + 未知份量 → 留空/标 unknown,不要瞎猜
"""

from __future__ import annotations

from dataclasses import dataclass

Unit = str  # 'g' | 'ml' | 'cup' | 'bowl' | 'piece' | 'slice' | 'handful' | ...


@dataclass(frozen=True)
class Grams:
    grams: float
    unit_kind: str          # 'mass' | 'volume' | 'count' | 'piece'
    food_specific: bool     # True: 用了 food-specific 单位


# 通用单位 → 假设 100g 起点 / 1 杯 = 240ml
_GENERIC_UNIT_TO_GRAMS = {
    "g": 1.0,
    "克": 1.0,
    "kg": 1000.0,
    "千克": 1000.0,
    "公斤": 1000.0,
    "ml": 1.0,
    "毫升": 1.0,
    "l": 1000.0,
    "升": 1000.0,
}


# 食物专属单位(克数):key = 食物名
_FOOD_SPECIFIC_UNIT_GRAMS: dict[str, dict[str, float]] = {
    "米饭":   {"碗": 150.0, "小碗": 100.0, "大碗": 200.0, "中碗": 150.0},
    "白米饭": {"碗": 150.0, "小碗": 100.0, "大碗": 200.0, "中碗": 150.0},
    "大米粥": {"碗": 250.0, "小碗": 200.0, "大碗": 300.0},
    "小米粥": {"碗": 250.0},
    "面条":   {"碗": 150.0, "把": 80.0},
    "捞面条": {"碗": 150.0, "把": 80.0},
    "馒头":   {"个": 100.0},
    "肉包":   {"个": 100.0},
    "包子":   {"个": 100.0},
    "饺子":   {"个": 25.0, "个饺子": 25.0},
    "鸡蛋":   {"个": 50.0},
    "鸡蛋白": {"个": 33.0},
    "面包":   {"片": 35.0},
    "全麦面包": {"片": 35.0},
    "苹果":   {"个": 150.0},
    "橙":     {"个": 150.0},
    "香蕉":   {"个": 120.0},
    "西瓜":   {"块": 200.0, "片": 200.0},
    "南瓜":   {"片": 50.0, "块": 50.0},
    "排骨":   {"块": 30.0},
    "三文鱼": {"片": 100.0},
    "虾":     {"只": 15.0},
    "鸡腿":   {"只": 100.0},
    "牛肉":   {"拳": 100.0},
    "瘦牛肉": {"拳": 100.0},
    "坚果":   {"把": 28.0},
    "杏仁":   {"把": 28.0},
    "核桃":   {"把": 28.0},
    "花生":   {"把": 28.0},
    "燕麦片": {"勺": 15.0, "把": 30.0},
}

# 通用计数/容器单位(用在 cup/bowl/piece 上,克数取决于上下文)
_BOWL_GRAMS = 250.0    # 普通碗,内容物不同(粥、面、米饭)已经被 _FOOD_SPECIFIC 覆盖
_CUP_ML = 240.0


def convert(
    food_name: str,
    qty: float,
    unit: str,
) -> Grams | None:
    """把 (qty, unit) 转成克。None 表示无法识别单位(由 caller 决定)。"""
    u = unit.strip().lower()

    # 食物专属单位
    food_table = _FOOD_SPECIFIC_UNIT_GRAMS.get(food_name)
    if food_table and unit in food_table:
        return Grams(grams=qty * food_table[unit], unit_kind="piece", food_specific=True)

    # 通用 g/ml
    if unit in _GENERIC_UNIT_TO_GRAMS:
        return Grams(grams=qty * _GENERIC_UNIT_TO_GRAMS[unit], unit_kind="mass" if u in {"g", "克", "kg", "千克", "公斤"} else "volume", food_specific=False)

    # 通用"碗/cup"
    if unit in {"杯", "cup"}:
        return Grams(grams=qty * _CUP_ML, unit_kind="volume", food_specific=False)
    if unit in {"碗"}:
        return Grams(grams=qty * _BOWL_GRAMS, unit_kind="volume", food_specific=False)

    return None


def convert_food_only(food_name: str, qty: float, unit: str) -> Grams | None:
    """只走食物专属单位,通用单位也返 None,用于 noisy text 的探针。"""
    food_table = _FOOD_SPECIFIC_UNIT_GRAMS.get(food_name)
    if food_table and unit in food_table:
        return Grams(grams=qty * food_table[unit], unit_kind="piece", food_specific=True)
    return None

# ── D4.5 聚餐常见(D4.5 阶段 A 估的,TODO)──────────────
_FOOD_SPECIFIC_UNIT_GRAMS["日本豆腐"] = {"块": 30.0}       # 你估 ~10 块 / 一盘 → 估 1 块 = 30g
_FOOD_SPECIFIC_UNIT_GRAMS["花生米"] = {"份": 100.0}        # 1 份下酒小碟约 100g
_FOOD_SPECIFIC_UNIT_GRAMS["白菜"] = {"份": 500.0}          # 一盘炒白菜约 500g
_FOOD_SPECIFIC_UNIT_GRAMS["莲藕"] = {"份": 300.0}          # 1 份炒莲藕约 300g
_FOOD_SPECIFIC_UNIT_GRAMS["小酥肉"] = {"份": 250.0}        # 1 份约 250g(中份)
_FOOD_SPECIFIC_UNIT_GRAMS["猪肉"] = {"块": 25.0, "片": 30.0}
_FOOD_SPECIFIC_UNIT_GRAMS["毛血旺"] = {"份": 700.0}        # 1 份毛血旺约 700g(中份)
_FOOD_SPECIFIC_UNIT_GRAMS["白酒"] = {"两": 50.0, "杯": 30.0}
_FOOD_SPECIFIC_UNIT_GRAMS["汾酒"] = {"两": 50.0, "杯": 30.0}
_FOOD_SPECIFIC_UNIT_GRAMS["老白汾"] = {"两": 50.0, "杯": 30.0}

# 午餐肉: 标准罐装午餐肉切片,1片约 22g
_FOOD_SPECIFIC_UNIT_GRAMS["午餐肉"] = {"片": 22.0, "块": 22.0}
