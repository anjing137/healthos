"""运动 MET 表 + 中文→key 映射。

营养来源并行:
- healthos/nutrition/foods.py 装吃东西的宏量;
- 本文件装"动"的能量消耗。

数据来源: ACSM *Compendium of Physical Activities* 2011
(Ainsworth 等),运动医学界公认。
`knee_rehab` 不是 ACSM 标准条目,是靠墙静蹲(3.5 MET)+
拉伸(2.5 MET)综合估计,confidence 上限锁 0.7 区别对待。

用法:
    >>> from healthos.nutrition.activities import MET_TABLE, lookup_sport
    >>> MET_TABLE["basketball"]["moderate"]
    6.5
    >>> lookup_sport("打篮球")
    'basketball'
    >>> lookup_sport("飞盘")
    None  # unknown sport,留给 open_question
"""

from __future__ import annotations

from typing import Optional

# MET 表。每个 key 三档强度:intensity ∈ {light, moderate, high}
MET_TABLE: dict[str, dict[str, float]] = {
    "walking_slow":            {"light": 2.5, "moderate": 3.0, "high": 3.5},
    "walking_brisk":           {"light": 4.0, "moderate": 4.5, "high": 5.5},
    "jogging":                 {"light": 6.0, "moderate": 7.0, "high": 8.5},
    "running":                 {"light": 8.0, "moderate": 9.8, "high": 11.5},
    "basketball":              {"light": 5.0, "moderate": 6.5, "high": 8.0},
    "jump_rope":               {"light": 8.0, "moderate": 12.0, "high": 13.0},
    "elliptical":              {"light": 4.5, "moderate": 5.0, "high": 6.5},
    "swimming_freestyle":      {"light": 5.0, "moderate": 6.0, "high": 9.0},
    "rowing":                  {"light": 4.5, "moderate": 7.0, "high": 8.5},
    "weight_training_general": {"light": 3.5, "moderate": 5.0, "high": 6.0},
    "yoga_stretching":         {"light": 2.0, "moderate": 3.0, "high": 4.0},
    "knee_rehab":              {"light": 2.5, "moderate": 3.5, "high": 4.5},
}

# 哪些运动是 ACSM 标准条目(可以上 0.85 confidence);哪些是估算(上限 0.7)
# 只有 knee_rehab 不在 ACSM 里。
_SYNTHETIC_KEYS = frozenset({"knee_rehab"})


# 中文→key 的别名映射。同一行多个别名用 | 分隔。
# 顺序敏感:长前缀先行(避免「散步」抢「慢跑」)
_SPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "knee_rehab": (
        "膝盖康复", "膝盖训练", "康复训练",
        "靠墙静蹲", "静蹲",
        "臀桥", "死虫", "直腿抬高", "侧卧抬腿",
    ),
    "swimming_freestyle": ("游泳", "自由泳", "游了"),
    "elliptical":          ("椭圆机",),
    "rowing":              ("划船机", "划船"),
    "basketball":          ("打篮球", "篮球"),
    "jump_rope":           ("跳绳",),
    "weight_training_general": ("撸铁", "举铁", "力量训练", "器械训练", "器械"),
    "yoga_stretching":     ("瑜伽", "拉伸"),
    "running":             ("跑步",),
    "jogging":             ("慢跑", "轻松跑"),
    "walking_brisk":       ("快走",),
    "walking_slow":        ("散步", "走走", "遛弯"),
}


# 强度词:中文 → key
_INTENSITY_ALIASES: dict[str, str] = {
    "轻": "light",  "轻松": "light",  "轻度": "light",
    "中": "moderate", "中等": "moderate", "普通": "moderate",
    "高": "high", "高强": "high", "热血": "high", "拼命": "high",
}


def lookup_sport(text: str) -> Optional[str]:
    """从任意中文片段里识别出运动 key。找不到 → None(代表 unknown)。"""
    # 按 _SPORT_ALIASES 顺序走;匹配到最长前缀优先
    best_key: Optional[str] = None
    best_len = 0
    for key, aliases in _SPORT_ALIASES.items():
        for a in aliases:
            if a in text and len(a) > best_len:
                best_key = key
                best_len = len(a)
    return best_key


def lookup_intensity(text: str) -> Optional[str]:
    """从中文片段识别强度。找不到 → None(默认 moderate)。"""
    for alias, intensity in _INTENSITY_ALIASES.items():
        if alias in text:
            return intensity
    return None


def is_known(sport_key: str) -> bool:
    """这个 key 在字典里?(预留给 unknown 校验)"""
    return sport_key in MET_TABLE


def is_synthetic(sport_key: str) -> bool:
    """是否合成(非 ACSM 标准)项目?用于 confidence 上限。"""
    return sport_key in _SYNTHETIC_KEYS
