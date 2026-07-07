"""把自然语言日记切成段落(section)。

D2:段落切分
D4.5 B:复合句拆分 — "X 配 Y" / "X 炒 Y" / "X 炖 Y" / "X Y 的菜" 这种
       一项 item 含多食材的,拆成多件,送 quantify 走两次。

D32:无标点空格句的切分 — 例如 "豆浆一杯 包子一个 鸡蛋一个"
       按空格 + 量词/数字边界切分。

约定:
- 段落头 = 行首的可识别名 + 冒号或逗号(全/半角)
- 段内 item 切完后,再做"复合句切分"
- 复合句模式:
  - "X 配 Y"            — "米饭配冬瓜炒肉"
  - "X 炒/炖/煮/烧/烤 Y" — "冬瓜炒肉"、"小酥肉炖白菜"
  - "X Y 的菜"           — "冬瓜炒肉的菜"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SectionName = Literal["breakfast", "lunch", "dinner", "snack", "workout", "sleep", "knee"]

_HEADERS: dict[str, SectionName] = {
    "早餐": "breakfast",
    "早饭": "breakfast",
    "午餐": "lunch",
    "午饭": "lunch",
    "晚餐": "dinner",
    "晚饭": "dinner",
    "加餐": "snack",
    "零食": "snack",
    "宵夜": "snack",
    "运动": "workout",
    "训练": "workout",
    "睡眠": "sleep",
    "睡觉": "sleep",
    "膝盖": "knee",
}

_HEADER_RE = re.compile(
    r"^([一-龥]{2,4})\s*[:：,，]\s*",
    flags=re.MULTILINE,
)

# "豆浆一杯, 包子一个" — "豆浆一杯, " 匹配 _HEADER_RE(逗号) → 误触发为段头
# 真实段头如 "早餐，" 后面跟的内容不是量词。
# 用这个检查来排除逗号后的误触:
def _is_likely_item_not_header(body_start, text) -> bool:
    """如果段头匹配后跟的 body 不以量词开头,说明这不是一个段头。

    "豆浆一杯, 包子一个" — 段头是 "豆浆一杯,", body 是 "包子一个" — body 不以量词开头
    "早餐，一杯豆浆" — 段头是 "早餐，", body 是 "一杯豆浆" — body 以量词开头
    只有后者才算真正的段头。前者是 food item 误触。
    """
    tail = text[body_start:body_start+4]
    # body 不以量词开头 → 不是真段头
    return not bool(re.search(r"^\s*[\d一二两三四五六七八九十半]", tail))

_BARE_HEADER_RE = re.compile(
    r"^([一-龥]{2,4})\s*(?=[:：,，\n]|$)",
    flags=re.MULTILINE,
)


@dataclass
class Section:
    name: SectionName
    raw: str
    items: list[str]

    def to_dict(self) -> dict:
        return {"name": self.name, "raw": self.raw, "items": list(self.items)}


def _resolve(name_cn: str) -> SectionName | None:
    return _HEADERS.get(name_cn)


# 烹饪动词:配/炒/炖/煮/烧/烤/煎/炸/拌 — 后面接另一项食材
# 复合句包括:
#   - "X 配 Y"
#   - "X 炒 Y" / "X 炖 Y" / "X 烤 Y" / ...
#   - "X Y 的菜"  — "X Y" 后面带"的菜",视为 X + Y
_COOK_VERBS = ("配", "炒", "炖", "煮", "烧", "烤", "煎", "炸", "拌", "蒸", "烩")
_COOK_VERB_RE = re.compile(r"(?P<verb>" + "|".join(_COOK_VERBS) + r")")
_TRAILING_DE_CAI_RE = re.compile(r"(.+?)\s*的菜$")  # "冬瓜炒肉的菜" → "冬瓜炒肉"


def _split_compound(item: str) -> list[str]:
    """把"米饭配冬瓜炒肉的菜" 这种含多食材的复合句拆成多个 item。

    拆法优先级:
    1. 末尾"的菜" 截掉(无意义修饰),再走 2
    2. "X [动词] Y" 拆 → ["X", "Y"]
    3. "X 配 Y" 拆 → ["X", "Y"]
    4. 拆不动 → 原句

    示例:
    - "米饭配冬瓜炒肉的菜" → ["米饭", "冬瓜", "猪肉"]
    - "小酥肉炖白菜"       → ["小酥肉", "白菜"]
    - "鸡胸肉150g"          → ["鸡胸肉150g"]
    - "一个鸡蛋"            → ["一个鸡蛋"]
    """
    s = item.strip()
    if not s:
        return []

    # 0. 末尾"的菜"
    m = _TRAILING_DE_CAI_RE.search(s)
    if m:
        s = m.group(1).strip()

    # 1. "X 配 Y" — 配 的优先级最高(后续动词拆分留给 3)
    if "配" in s:
        parts = s.split("配", 1)
        a, b = parts[0].strip(), parts[1].strip()
        out = [a] if a else []
        if b:
            out.extend(_split_compound(b))
        return out

    # 2. 找第一个烹饪动词
    m = _COOK_VERB_RE.search(s)
    if m:
        verb = m.group("verb")
        a, b = s.split(verb, 1)
        a, b = a.strip(), b.strip()
        if a and b:
            out = [a]
            out.extend(_split_compound(b))
            return out

    # 3. 单项
    return [s]


def _split_stacked_items(line: str) -> list[str]:
    """把无标点但有空格的多项句子切开。

    例如 "豆浆一杯 包子一个 鸡蛋一个" → ["豆浆一杯", "包子一个", "鸡蛋一个"]

    算法:按"量词/数字"后的空格作切点。
    """
    tokens = re.split(r"\s+", line.strip())
    if len(tokens) < 2:
        return [line]
    out: list[str] = []
    buf: list[str] = []
    for t in tokens:
        # 量词:数字 / 汉字 "一杯"、"三两"、"两个"、"半斤"、"300g" ...
        if re.search(r"[\d一二两三四五六七八九十半]|\d+\s*(g|克|两|ml|毫升|kg|个|只|片|块|把|碗|杯|勺|拳|份)", t):
            buf.append(t)
            out.append(" ".join(buf))
            buf = []
        else:
            buf.append(t)
    if buf:
        # 尾部留在 buf 里的,要么全是食物名,要么是单数人"还喝了白酒"
        if out:
            out[-1] = out[-1] + " " + " ".join(buf)
        else:
            out = [line]
    return out if out else [line]


def parse(text: str, *, strict: bool = True, split_compound: bool = True) -> list[Section]:
    """切分自然语言日记为段落列表。

    split_compound=True(默认)对每段 items 做复合句拆分。
    """
    if not text or not text.strip():
        return []

    raw_hits: list[tuple[int, int, int, str]] = []
    for m in _HEADER_RE.finditer(text):
        header_cn = m.group(1)
        seg_start = m.start()
        body_start = m.end()
        # 排除"豆浆一杯, 包子一个" 这类误触:逗号段头后面是数字/量词
        if "," in m.group(0) or "，" in m.group(0):
            if _is_likely_item_not_header(body_start, text):
                continue
        raw_hits.append((seg_start, body_start, -1, header_cn))

    if not raw_hits:
        if strict and text.strip():
            # 如果严格模式找不到段头,不做 hard error,做 lenient fallback
            # 很多用户输入如 "今天的午餐,鸡胸肉150g" 没段头但有事实
            pass
        # Fallback:复合句 + 空格切
        items = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            # 1. 按标点(逗号/顿号/分号)切
            parts = re.split(r"[、，,;；]\s*", ln)
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                # 2. 无标点单段含空格 — 按量词+空格切("豆浆一杯 包子一个")
                if " " in p and not re.search(r"[、，,;；]", p):
                    items.extend(_split_stacked_items(p))
                elif split_compound:
                    items.extend(_split_compound(p))
                else:
                    items.append(p)
        return [Section(name="snack", raw=text.strip(), items=items)] if items else []

    for i, hit in enumerate(raw_hits):
        seg_s, body_s, _body_e, header = hit
        next_seg_s = raw_hits[i + 1][0] if i + 1 < len(raw_hits) else len(text)
        raw_hits[i] = (seg_s, body_s, next_seg_s, header)

    if raw_hits[0][0] > 0:
        orphan = text[0 : raw_hits[0][0]].strip()
        if orphan and strict:
            raise ValueError(f"段落头之前有无法识别的文本: {orphan!r}")

    sections: list[Section] = []
    for _seg_s, body_start, body_end, header_cn in raw_hits:
        sec_name = _resolve(header_cn)
        if sec_name is None:
            if strict:
                raise ValueError(f"未识别的段落头: {header_cn!r}")
            sec_name = "snack"
        body = text[body_start:body_end].strip()
        if not body:
            sections.append(Section(name=sec_name, raw="", items=[]))
            continue

        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        raw_items: list[str] = []
        for ln in lines:
            parts = re.split(r"[、，,;；]\s*", ln)
            raw_items.extend(p.strip() for p in parts if p.strip())

        # 如果分隔后依然只有一段且含空格 — 按量词+空格切
        # 注意:有逗号分隔时 raw_items 已有多个元素,不需要走这个分支。
        # 如果只有一个元素且含空格,才有必要。
        if len(raw_items) == 1 and " " in raw_items[0]:
            raw_items = _split_stacked_items(raw_items[0])

        # D4.5 B: 复合句拆分
        items: list[str] = []
        for raw_item in raw_items:
            if split_compound:
                items.extend(_split_compound(raw_item))
            else:
                items.append(raw_item)

        sections.append(Section(name=sec_name, raw=body, items=items))

    return sections


def parse_to_dict(text: str, *, strict: bool = True, split_compound: bool = True) -> list[dict]:
    return [s.to_dict() for s in parse(text, strict=strict, split_compound=split_compound)]
