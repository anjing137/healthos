"""写库入口 — 把自然语言日记 → SQLite。"""
from .write import record, RecordResult, today

__all__ = ["record", "RecordResult", "today"]
