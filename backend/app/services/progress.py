from __future__ import annotations

import math
import re
from typing import Any


def parse_progress_percent(value: Any, default: int = 0) -> int:
    """Convert common report progress formats to an integer percentage."""
    def normalized_fallback(raw: Any) -> int:
        if isinstance(raw, bool) or raw is None:
            return 0
        if isinstance(raw, (int, float)):
            if not math.isfinite(float(raw)):
                return 0
            return max(0, min(100, int(round(float(raw)))))
        text = str(raw).strip()
        ratio = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*\+?", text)
        if ratio and float(ratio.group(2)) > 0:
            return max(0, min(100, int(round(float(ratio.group(1)) / float(ratio.group(2)) * 100))))
        number = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        return max(0, min(100, int(round(float(number.group(0)))))) if number else 0

    fallback = normalized_fallback(default)
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
