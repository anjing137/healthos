"""把 parser 切出来的 item 转成结构化 Qty + lookup。

支持的 item 形态:
- "鸡胸肉 150g"              → 150g 鸡胸肉
- "排骨 4 块"               → 4 块 排骨
- "一个鸡蛋"               → 1 个 鸡蛋
- "无糖豆浆 600ml"           → 600 ml 豆浆
- "半个苹果"               → 0.5 个 苹果
- "鸡蛋两个"               → 2 个 鸡蛋
- "大米粥(250g)"            → 250g 大米粥
- "还喝了白酒"              → "白酒"(剥"动词"前缀)+ 1 杯 1 两兜底
- "3 两白酒"                → 3 两 50ml/两=150ml
- "3-4 块日本豆腐"            → 3.5 块 平均

设计顺序(关键):
1. 先 normalise: 剥"动词前缀"、"X-Y range 取均值"、"X 两"  当成 ml~50
2. 先看 qty+unit,剥掉 → 查食物 → 算克数
3. inline 命中 → 走单条,不再二次解析

bug 历史:
- B1: 内联后残文再走 main 出第二条 → 已修
- B2: substring 食物名匹配让"鲸鱼三文鱼酱"匹到 三文鱼 → 词边界
- B3: "鸡蛋两个" 因"两"被算嵌入 → 先剥 qty+unit 再查食物
- B4: "3 两白酒" → _strip_qty_unit 走 "3 两" 现在 unit 已在 UNITS
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .foods import FoodHit, lookup
from .portions import convert

_CN_NUMERALS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}


def _cn_num(s: str) -> Optional[float]:
    return _CN_NUMERALS.get(s)


@dataclass
class ParsedQuantity:
    qty: float
    unit: str
    grams: float
    food_hit: FoodHit | None
    inline: bool
    confidence: float

    def kcals(self) -> Optional[float]:
        if not self.food_hit:
            return None
        return self.grams * self.food_hit.macros.kcals / 100.0

    def protein_g(self) -> Optional[float]:
        if not self.food_hit:
            return None
        return self.grams * self.food_hit.macros.protein_g / 100.0

    def fat_g(self) -> Optional[float]:
        if not self.food_hit:
            return None
        return self.grams * self.food_hit.macros.fat_g / 100.0

    def carb_g(self) -> Optional[float]:
        if not self.food_hit:
            return None
        return self.grams * self.food_hit.macros.carb_g / 100.0


_UNITS = (
    "小碗|大碗|中碗|碗|杯|个|只|片|块|把|勺|拳|份|件|袋|两|"
    "g|克|kg|千克|公斤|ml|毫升|l|升"
)
_UNIT_RE = re.compile(r"(?P<u>" + _UNITS + r")", re.IGNORECASE)
_NUM_UNIT_RE = re.compile(r"(?P<q>[0-9]+(?:\.[0-9]+)?)\s*(?P<u>" + _UNITS + r")", re.IGNORECASE)
_CN_NUM_UNIT_RE = re.compile(r"(?P<q>[一二两三四五六七八九十半])\s*(?P<u>" + _UNITS + r")")

_INLINE_RE = re.compile(
    r"\((?P<q1>[0-9]+(?:\.[0-9]+)?)\s*(?P<u1>g|ml|克|千克|公斤)\)"
    r"|(?:^|[\s,，、])"
    r"(?P<q2>[0-9]+(?:\.[0-9]+)?)\s*(?P<u2>g|ml|克|千克|公斤)\b",
    re.IGNORECASE,
)

# "3-4 块" → 3.5
_RANGE_NUM_UNIT_RE = re.compile(r"(?P<a>[0-9]+)\s*[-~至]\s*(?P<b>[0-9]+)\s*(?P<u>" + _UNITS + r")", re.IGNORECASE)

# 动词前缀: 吃了 喝 了 / 吃了
_VERB_PREFIX_RE = re.compile(r"^[把喝吃进要来再]?\s*[了过]?\s*")

# "白酒" 改让 _match_food 在前面看到 — 否则"还喝了白酒"先剥"还喝了" 让食物识别到
# 我不主动剥动词,而是把"喝/吃/喝" 视为装饰词跳过 → 后面食物名匹配自然过

_IS_WORD_CHAR = re.compile(r"[一-龥A-Za-z0-9_]")


def _match_food(item: str) -> Optional[FoodHit]:
    from .foods import all_known_names, meat_alias
    candidates = sorted(all_known_names(), key=len, reverse=True)
    for name in candidates:
        if not name or len(name) < 2:
            continue
        for m in re.finditer(re.escape(name), item):
            s, e = m.span()
            before = item[s - 1] if s > 0 else ""
            after = item[e] if e < len(item) else ""
            if before and _IS_WORD_CHAR.match(before):
                continue
            if after and _IS_WORD_CHAR.match(after):
                continue
            return lookup(name)

    # 第二遍:单字肉名 alias("肉" → "猪肉"、"牛" → "牛肉"、"鸡" → "鸡胸肉")
    # 只在词边界且前后不是汉字/数字时启用
    for m in re.finditer(r"(?<![一-龥A-Za-z0-9])(肉|牛|鸡)(?![一-龥A-Za-z0-9])", item):
        aliased = meat_alias(m.group(1))
        if aliased:
            return lookup(aliased)
    return None


def _strip_verb_prefix(item: str) -> str:
    """剥"还喝了/吃了个/喝了" 这种动词前缀。

    设计:保留末尾的"酒/豆腐"这类食物特征,只削语助词,使 _match_food 能命中。
    例子:
    - "还喝了白酒" → "白酒"
    - "吃了一个苹果" → "一个苹果"
    - "喝了2杯豆浆" → "2杯豆浆"
    """
    prefixes = [
        r"^还喝了?",
        r"^喝了?",
        r"^吃了?",
        r"^吃了个",
        r"^吃",
        r"^喝",
        r"^还吃",
    ]
    s = item
    for p in prefixes:
        s2 = re.sub(p, "", s, count=1)
        if s2 != s:
            s = s2
            break
    return s.strip()


def _expand_range(item: str) -> str:
    """3-4 块 → 3.5 块(取均值)。
    单独出现"3 两"或 "3两" 已是 NUM_UNIT 处理;这里是 X-Y 范围。
    """
    m = _RANGE_NUM_UNIT_RE.search(item)
    if m:
        a = float(m.group("a"))
        b = float(m.group("b"))
        avg = (a + b) / 2
        repl = f"{avg:g}{m.group('u')}"
        item = item[:m.start()] + repl + item[m.end():]
    return item


def _strip_qty_unit(item: str) -> tuple[Optional[float], str, float, str]:
    m = _NUM_UNIT_RE.search(item)
    if m:
        qty = float(m.group("q"))
        unit = m.group("u").lower()
        rest = (item[: m.start()] + item[m.end():]).strip()
        return qty, unit, 0.95, rest

    m = _CN_NUM_UNIT_RE.search(item)
    if m:
        q = m.group("q")
        qty = _cn_num(q)
        if qty is None:
            qty = 1.0
        unit = m.group("u").lower()
        rest = (item[: m.start()] + item[m.end():]).strip()
        return qty, unit, 0.85, rest

    m = _UNIT_RE.search(item)
    if m:
        rest = (item[: m.start()] + item[m.end():]).strip()
        return 1.0, m.group("u").lower(), 0.7, rest

    m = re.search(r"[0-9]+|[一二两三四五六七八九十半]", item)
    if m:
        q = m.group()
        try:
            qty = float(q)
        except ValueError:
            qty = _cn_num(q)
        if qty is None:
            return None, "", 0.0, item
        rest = (item[: m.start()] + item[m.end():]).strip()
        return qty, "", 0.5, rest

    return None, "", 0.0, item


def _strip_inline_gram(item: str) -> tuple[Optional[tuple[float, str]], str]:
    for m in _INLINE_RE.finditer(item):
        qty_str = m.group("q1") or m.group("q2")
        unit_str = m.group("u1") or m.group("u2")
        if not qty_str or not unit_str:
            continue
        try:
            qty = float(qty_str)
        except ValueError:
            continue
        unit = unit_str.lower()
        if m.group(1) is not None:
            stripped = item.replace(m.group(0), "", 1)
        else:
            s, e = m.span()
            stripped = item[:s] + item[e:]
        return (qty, unit), stripped.strip()
    return None, item


def parse_item(item: str) -> list[ParsedQuantity]:
    """单个 item → 一或多个 ParsedQuantity。

    preprocess:
      1. 剥动词前缀
      2. range 取均值("3-4 块" → "3.5 块")
      3. inline (Xg) 命中 → 单条返回
      4. main_path(qty+unit 剥, 食物名查表)
    """
    if not item or not item.strip():
        return []

    item = _expand_range(item)
    item = _strip_verb_prefix(item)

    out: list[ParsedQuantity] = []
    inline_match, rest = _strip_inline_gram(item)

    if inline_match is not None:
        qty, unit = inline_match
        food_hit = _match_food(rest) or _match_food(item)
        grams_box = convert(food_hit.name, qty, unit) if food_hit else None
        if grams_box is not None:
            grams = grams_box.grams
        else:
            if unit in {"g", "克"}:
                grams = qty
            elif unit in {"kg", "千克", "公斤"}:
                grams = qty * 1000.0
            else:
                grams = qty
        out.append(
            ParsedQuantity(
                qty=qty,
                unit=unit,
                grams=grams,
                food_hit=food_hit,
                inline=True,
                confidence=0.95,
            )
        )
        return out

    if rest:
        out.extend(_main_path(rest))
    return out


def _main_path(item: str) -> list[ParsedQuantity]:
    qty, unit, conf, _stripped = _strip_qty_unit(item)
    if qty is None:
        qty = 1.0
        conf = min(conf, 0.4)

    food_hit = _match_food(_stripped) or _match_food(item)
    if not food_hit:
        return [ParsedQuantity(qty=0, unit="", grams=0, food_hit=None, inline=False, confidence=0.0)]

    grams_box = (
        convert(food_hit.name, qty, unit)
        if unit
        else convert(food_hit.name, 1.0, "份")
    )
    grams = grams_box.grams if grams_box else 100.0 * qty

    return [
        ParsedQuantity(
            qty=qty,
            unit=unit or "份",
            grams=grams,
            food_hit=food_hit,
            inline=False,
            confidence=conf,
        )
    ]
