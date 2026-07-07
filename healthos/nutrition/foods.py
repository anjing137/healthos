"""内置食物表 + 用户自定义食物(JSON 持久化)。

L1(内置)和 L2(用户后续添加)的逻辑融合:
- 静态内置一个起步集合,大部分日常直接命中
- 运行时从 data/foods.json 读用户添加的食物(覆盖同名,但不删内置)
- 任何一次调用 learn_food() 都会落到磁盘,下次立即生效

营养数据来源: USDA SR-28 + 中国食物成分表第 6 版 第 1/2 册 中位值。
不是医学标准。所有值都是典型量产 / 典型吃法的近似。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

USER_FOODS_PATH = Path(__file__).resolve().parents[2] / "data" / "foods.json"


@dataclass(frozen=True)
class Macros:
    # per 100 g 的宏量(毫升也按 100g 等价,因为大部分稀汤/液体 k=1)
    kcals: float
    protein_g: float
    fat_g: float
    carb_g: float


@dataclass(frozen=True)
class FoodHit:
    name: str            # 查询时用的 key
    macros: Macros       # per 100g
    portion_grams: float | None  # 如果查询口径自带克数,该用多少克(对于块状/水果)
    source: str          # "builtin" | "user"


def _m(k: float, p: float, f: float, c: float) -> Macros:
    return Macros(kcals=k, protein_g=p, fat_g=f, carb_g=c)


# ─── 内置常见食物 ───────────────────────────────────────────────
# 注意:用户添加的同名食物(JSON 里)会覆盖,但代码里这个表只读。
_BUILTIN: dict[str, Macros] = {
    # 主食
    "米饭": _m(130, 2.7, 0.3, 28),
    "白米饭": _m(130, 2.7, 0.3, 28),
    "馒头": _m(223, 7.0, 1.0, 47),
    "全麦面包": _m(247, 13.0, 4.0, 41),
    "面包": _m(265, 9.0, 3.2, 49),  # 通用白面包
    "燕麦片": _m(389, 17, 7, 66),  # 干冲泡前的片
    "南瓜": _m(26, 1.0, 0.1, 6.5),
    "玉米": _m(86, 3.2, 1.2, 19),
    "红薯": _m(86, 1.6, 0.1, 20),
    "土豆": _m(77, 2.0, 0.1, 17),

    # 谷物粥粉面
    "大米粥": _m(46, 1.1, 0.1, 10),    # 1:10 比例的稀白米粥
    "小米粥": _m(46, 1.9, 0.4, 9),
    "捞面条": _m(136, 4.5, 0.6, 28),   # 白面条煮熟沥水
    "面条": _m(138, 5.0, 0.7, 28),
    "挂面": _m(138, 5.0, 0.7, 28),

    # 豆 / 奶 / 蛋
    "豆浆": _m(33, 3.3, 1.8, 0.5),
    "无糖豆浆": _m(33, 3.3, 1.8, 0.5),
    "牛奶": _m(61, 3.2, 3.3, 4.8),
    "酸奶": _m(59, 3.5, 3.3, 4.0),
    "鸡蛋": _m(144, 12.6, 9.6, 0.8),  # 全蛋 per 100g(每 50g 可食部约 72 kcal)
    "鸡蛋白": _m(52, 11.0, 0.2, 0.7),
    "蛋黄": _m(322, 16, 27, 3.6),

    # 禽 / 畜 / 鱼
    "鸡胸肉": _m(165, 31, 3.6, 0),
    "鸡腿": _m(209, 26, 11, 0),
    "牛肉": _m(217, 26, 12, 0),       # 瘦肉
    "瘦牛肉": _m(217, 26, 12, 0),
    "排骨": _m(280, 18, 21, 0),       # 带骨,带油
    "五花肉": _m(440, 18, 40, 0),
    "三文鱼": _m(208, 22, 13, 0),
    "虾": _m(99, 24, 0.3, 0.2),
    "金枪鱼": _m(116, 26, 0.8, 0),
    "火腿": _m(145, 17, 9, 1),

    # 蔬
    "西瓜": _m(30, 0.6, 0.2, 8.0),
    "冬瓜": _m(12, 0.4, 0.1, 2.6),
    "黄瓜": _m(15, 0.7, 0.1, 3.6),
    "西红柿": _m(18, 0.9, 0.2, 3.9),
    "番茄": _m(18, 0.9, 0.2, 3.9),
    "菠菜": _m(23, 2.9, 0.4, 3.6),
    "生菜": _m(15, 1.4, 0.2, 2.9),
    "蘑菇": _m(22, 3.1, 0.3, 3.3),

    # 水果
    "苹果": _m(52, 0.3, 0.2, 14),
    "香蕉": _m(89, 1.1, 0.3, 23),
    "橙": _m(47, 0.9, 0.1, 12),
    "蓝莓": _m(57, 0.7, 0.3, 14),
    "牛油果": _m(160, 2.0, 15, 9),

    # 坚果
    "杏仁": _m(579, 21, 50, 22),
    "核桃": _m(654, 15, 65, 14),
    "花生": _m(567, 24, 49, 21),

    # 油 / 调味
    "橄榄油": _m(884, 0, 100, 0),
    "酱油": _m(53, 8.0, 0.6, 4.9),
    "沙拉酱": _m(680, 1, 75, 0.6),

    # 主食成品
    "肉包": _m(231, 8.0, 9.0, 29),
    "包子": _m(227, 8.0, 9.0, 28),
    "饺子": _m(250, 11, 12, 26),   # 猪肉白菜 10 个
    "披萨": _m(266, 11, 10, 33),
    "汉堡": _m(295, 17, 14, 28),
    "凯撒沙拉": _m(190, 5.0, 15, 11),
    "沙拉": _m(60, 2.0, 3.0, 7.0),  # 蔬菜为主

    # ── D4.5 聚餐常见菜/酒(用户 L2 学习)───────────────────────────
    # 值都是参考值 / TODO - 需要用户校准
    "日本豆腐": _m(80, 7.0, 4.5, 2.0),     # per 100g,主要是蛋+豆浆凝固
    "小酥肉": _m(420, 18, 30, 25),           # 油炸裹粉五花肉片
    "白菜": _m(13, 1.5, 0.1, 2.2),           # 北方大白菜,生
    "花生米": _m(580, 26, 50, 16),           # 油炸/椒盐下酒菜,per 100g
    "莲藕": _m(73, 2.0, 0.2, 17),            # 焯/炒
    "猪肉": _m(242, 17, 19, 0),              # 瘦,熟(pork tenderloin 瘦 145 kcal;普遍猪肉 242)
    # "冬瓜炒肉" 这类复合菜条目已删除,让 parser 拆成 "冬瓜" + "肉" 两件估
    "毛血旺": _m(170, 7.0, 14, 3.5),         # 川菜,鸭血+豆芽+午餐肉+ 大量红油,per 100g,1 份约 600-800g
    "毛血旺_一份": _m(1360, 50, 105, 25),    # 1 份 ~ 800g 的毛血旺(参考值,TODO)
    "白酒": _m(355, 0, 0, 0),                # 一般白酒 50 度,per 100ml → 355 kcal(简化:全 kcal 来自酒精)
    "汾酒": _m(355, 0, 0, 0),
    "老白汾": _m(355, 0, 0, 0),              # 45 度汾酒,per 100g 大约 350-360 kcal;蛋白/脂 0
    "包子": _m(227, 8.0, 9.0, 28),
}


# ─── 持久化层 ───────────────────────────────────────────────


def _load_user() -> dict[str, Macros]:
    if not USER_FOODS_PATH.exists():
        return {}
    try:
        raw = json.loads(USER_FOODS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, Macros] = {}
    for k, v in raw.items():
        out[k] = Macros(
            kcals=float(v["k"]),
            protein_g=float(v["p"]),
            fat_g=float(v["f"]),
            carb_g=float(v["c"]),
        )
    return out


def _save_user(table: dict[str, Macros]) -> None:
    USER_FOODS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        k: {"k": v.kcals, "p": v.protein_g, "f": v.fat_g, "c": v.carb_g}
        for k, v in table.items()
    }
    USER_FOODS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def lookup(name: str) -> FoodHit | None:
    """精确查表(用户表覆盖内置)。未命中返回 None。"""
    user = _load_user()
    if name in user:
        return FoodHit(name=name, macros=user[name], portion_grams=None, source="user")
    if name in _BUILTIN:
        return FoodHit(name=name, macros=_BUILTIN[name], portion_grams=None, source="builtin")
    return None


def learn_food(
    name: str,
    kcals_per_100g: float,
    protein_g: float,
    fat_g: float,
    carb_g: float,
) -> None:
    """用户教 Agent 一个新食物。后续 lookup() 立刻命中。"""
    table = _load_user()
    table[name] = Macros(kcals=kcals_per_100g, protein_g=protein_g, fat_g=fat_g, carb_g=carb_g)
    _save_user(table)


# 用户描述"炒肉"、"炖肉"、"卤肉" 等时,默认指瘦肉;
# 单字"肉" / "牛" / "鸡" 自动 alias 到对应具体条目(都标 TODO)。
# 这只能用在 _match_food 的"找不到直接条目"时 fallback,
# 显式写"猪肉" 则仍命中猪肉(不破坏已有数据)。
_MEAT_ALIAS: dict[str, str] = {
    "肉": "猪肉",   # 用户的"肉"按 猪肉 中位数据 (瘦猪肉 cooked,24 kcal/P/F 比例普遍)
    "牛": "牛肉",
    "鸡": "鸡胸肉",  # 用户说"鸡" 默认指鸡胸肉(最常见减脂场景)
}


def all_known_names() -> list[str]:
    user = _load_user()
    return sorted(set(_BUILTIN.keys()) | set(user.keys()))


def meat_alias(name: str) -> str | None:
    """单字肉名 alias 映射。

    "肉" → "猪肉"、"牛" → "牛肉"、"鸡" → "鸡胸肉"。
    多字("猪肉"/"牛肉"/"鸡腿"/"鸡胸肉")不 alias。
    """
    if len(name) == 1:
        return _MEAT_ALIAS.get(name)
    return None
