from __future__ import annotations

import math
import re
from typing import Any


def parse_progress_percent(value: Any, default: int = 0) -> int:
    """Convert common report progress formats to an integer percentage."""
    fallback = max(0, min(100, int(default or 0)))
    if value is None or isinstance(value, bool):
        return fallback

    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return fallback
        return max(0, min(100, int(round(float(value)))))

    text = str(value).strip()
    if not text:
        return fallback

    ratio = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*\+?", text)
    if ratio:
        completed = float(ratio.group(1))
        total = float(ratio.group(2))
        if total > 0:
            return max(0, min(100, int(round(completed / total * 100))))
        return fallback

    number = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not number:
        return fallback
    return max(0, min(100, int(round(float(number.group(0))))))
