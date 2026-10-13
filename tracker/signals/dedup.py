"""Groups the same story from different outlets and detects rehashed news."""

from __future__ import annotations

import re

STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "its", "into", "after",
        "over", "amid", "says", "said", "will", "has", "have", "new", "stock", "stocks", "shares",
        "inc", "corp", "ltd", "plc", "today", "report", "reports", "update", "news"}


def tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9$]+", title.lower())
    return frozenset(w for w in words if len(w) > 2 and w not in STOP)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


SAME_STORY = 0.5
