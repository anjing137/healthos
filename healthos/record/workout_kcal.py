"""workout KCal 估算 — MET × kg × h。

CLAUDE.md 第 4 条精神(workout 占位值 vs 真值)— workout 没有
"占位后重算"分层,估算即写入。靠 confidence + kcal_method 字段
做"估算透明度"。

示例:
    >>> from healthos.record.workout_kcal import estimate_kcal, UnknownSport
    >>> estimate_kcal("basketball", "moderate", 50, weight_kg=100.6)
    (545.3, 0.85)
    >>> estimate_kcal("frisbee", "moderate", 30, weight_kg=80.0)
    Traceback (most recent call last):
      ...
    UnknownSport: frisbee
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..nutrition.activities import MET_TABLE, is_synthetic


class UnknownSport(Exception):
    """Caller 应该 catch 然后写 open_question,不让 workout 行无声地写 0。"""


@dataclass(frozen=True)
class KcalEstimate:
    kcal: float
    confidence: float
    method: str  # 'MET' | 'manual' | 'pending'

    def __iter__(self):
        yield self.kcal
        yield self.confidence


# ── 体重兜底 ─────────────────────────────────────────────────────────
# 没有 weight 记录时 fallback,confidence 扣 0.20
_FALLBACK_WEIGHT_KG = 70.0
_FALLBACK_CONF_PENALTY = 0.20

# 基础 confidence:ACSM 项目 + 已知强度 + 已知体重
_BASE_CONF = 0.85
_INTENSITY_MISSING_PENALTY = 0.15


def estimate_kcal(
    sport: str,
    intensity: str,
    minutes: int,
    weight_kg: Optional[float] = None,
) -> tuple[float, float]:
    """估算燃烧 kcal。

    Args:
      sport: MET_TABLE 的 key(例 'basketball'),unknown 抛 UnknownSport
      intensity: 'light' | 'moderate' | 'high'
      minutes: 时长
      weight_kg: 已知体重;None 或 <=0 → fallback 70 + confidence 扣分

    Returns:
      (kcal, confidence)
    """
    if sport not in MET_TABLE:
        raise UnknownSport(sport)

    met = MET_TABLE[sport][intensity]
    hours = minutes / 60.0

    if weight_kg is None or weight_kg <= 0:
        weight_used = _FALLBACK_WEIGHT_KG
        conf_penalty = _FALLBACK_CONF_PENALTY
    else:
        weight_used = weight_kg
        conf_penalty = 0.0

    kcal = met * weight_used * hours

    confidence = _BASE_CONF - conf_penalty
    if is_synthetic(sport):
        # knee_rehab 不是 ACSM 标准;置信度上限压到 0.7
        confidence = min(confidence, 0.7)

    return round(kcal, 1), round(confidence, 2)
