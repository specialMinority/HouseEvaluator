"""
live_aggregate.py — shared aggregation utilities for live comparable benchmarks.

Policy (as requested by the project plan):
- If n == 1: use the single value.
- If n == 2: use mean(values).
- If n >= 3: use midrange = (min + max) / 2.
- Safety belt: if max/min ratio is too large, fall back to median to reduce
  sensitivity to a single bad parse/outlier.
"""

from __future__ import annotations

from statistics import median
from typing import Iterable


def aggregate_benchmark(values: Iterable[int], *, outlier_ratio_threshold: float = 2.0) -> tuple[int, str, dict]:
    vals = [int(v) for v in values if v is not None]
    vals = [v for v in vals if v >= 0]
    vals.sort()
    if not vals:
        raise ValueError("aggregate_benchmark requires at least 1 value")

    n = len(vals)
    min_v = int(vals[0])
    max_v = int(vals[-1])
    med_v = float(median(vals))

    ratio = None
    if min_v > 0:
        ratio = float(max_v) / float(min_v)

    method = "single"
    out = float(vals[0])
    if n == 2:
        method = "mean_2"
        out = (float(vals[0]) + float(vals[1])) / 2.0
    elif n >= 3:
        method = "midrange"
        out = (float(min_v) + float(max_v)) / 2.0
        if ratio is not None and ratio > float(outlier_ratio_threshold):
            method = "median_fallback"
            out = med_v

    return int(round(out)), method, {"n": n, "min": min_v, "max": max_v, "median": med_v, "max_min_ratio": ratio}

