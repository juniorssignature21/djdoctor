"""Risk levels shared by migration analysis and error explanations."""

from __future__ import annotations

from enum import Enum


class Risk(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {"none": 0, "low": 1, "unknown": 2, "medium": 2, "high": 3}[self.value]

    @property
    def label(self) -> str:
        return self.value.upper()

    @classmethod
    def highest(cls, risks) -> Risk:
        risks = list(risks)
        if not risks:
            return cls.NONE
        return max(risks, key=lambda r: (r.rank, r is cls.UNKNOWN))
