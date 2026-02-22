"""
live_quality.py — data quality defenses for live comparable benchmarks.

Goals (PR-2):
- Dedupe comparable listings to reduce bias from repeated blocks/copies.
- Optionally filter stale listings when the provider exposes an update date.
- Remove extreme outliers (robust IQR fence) to reduce impact of bad parses / bait listings.

All utilities are stdlib-only and operate on "listing-like" objects (duck-typed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Iterable, Protocol, Sequence, TypeVar
import urllib.parse


class _ListingLike(Protocol):
    rent_yen: int | None
    admin_fee_yen: int | None
    monthly_total_yen: int | None
    area_sqm: float | None
    layout: str | None
    station_names: list[str]
    detail_url: str | None
    info_updated_at: str | None  # ISO date "YYYY-MM-DD" if available


T = TypeVar("T", bound=_ListingLike)


def _canonicalize_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url))
        # Drop query/fragment to reduce false non-matches.
        path = parts.path.rstrip("/")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    except Exception:
        return str(url).strip()


def _area_bucket(area_sqm: float | None) -> float | None:
    if area_sqm is None:
        return None
    try:
        # 0.1㎡ bucket reduces float noise without over-collapsing.
        return round(float(area_sqm), 1)
    except Exception:
        return None


def _fingerprint_key(lst: _ListingLike) -> tuple[Any, ...]:
    # Fingerprint is a last-resort dedupe key. It should be stable across pages but not
    # merge truly distinct units too aggressively.
    layout = (getattr(lst, "layout", None) or "").strip().upper() or None
    rent = getattr(lst, "rent_yen", None)
    fee = getattr(lst, "admin_fee_yen", None)
    area = _area_bucket(getattr(lst, "area_sqm", None))
    # Use only the first station name if present (avoid huge keys).
    stations = getattr(lst, "station_names", None) or []
    st0 = stations[0] if stations else None
    return ("fp", rent, fee, area, layout, st0)


def dedupe_listings(listings: Sequence[T], *, provider: str | None = None) -> tuple[list[T], dict[str, Any]]:
    """
    Dedupe comparable listings while preserving order.

    Key priority:
    1) detail_url (provider listing id surrogate)
    2) fallback fingerprint of (rent, admin, area, layout, station0)
    """
    seen: set[tuple[Any, ...]] = set()
    out: list[T] = []
    removed = 0
    key_strategy_counts: dict[str, int] = {"detail_url": 0, "fingerprint": 0}

    for lst in listings:
        detail_url = getattr(lst, "detail_url", None)
        key: tuple[Any, ...]
        if detail_url:
            key = ("url", _canonicalize_url(str(detail_url)))
            key_strategy_counts["detail_url"] += 1
        else:
            key = _fingerprint_key(lst)
            key_strategy_counts["fingerprint"] += 1

        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(lst)

    return out, {
        "provider": provider,
        "n_in": len(listings),
        "n_out": len(out),
        "removed": removed,
        "key_strategy_counts": key_strategy_counts,
    }


def _parse_iso_date(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s).strip())
    except Exception:
        return None


def filter_stale_listings(
    listings: Sequence[T],
    *,
    max_age_days: int = 21,
    today: date | None = None,
    min_keep: int = 2,
) -> tuple[list[T], dict[str, Any]]:
    """
    Filter listings older than max_age_days based on listing.info_updated_at.

    Policy: keep listings without a date; if filtering would drop below min_keep,
    the filter is skipped (to avoid turning "some data" into "no data").
    """
    today_d = today or date.today()
    max_age_days_i = max(0, int(max_age_days))
    min_keep_i = max(0, int(min_keep))

    with_date = 0
    missing_date = 0
    stale_candidates = 0
    out: list[T] = []

    for lst in listings:
        iso = getattr(lst, "info_updated_at", None)
        if not iso:
            missing_date += 1
            out.append(lst)
            continue
        d = _parse_iso_date(str(iso))
        if d is None:
            missing_date += 1
            out.append(lst)
            continue
        with_date += 1
        age_days = (today_d - d).days
        if age_days > max_age_days_i:
            stale_candidates += 1
            continue
        out.append(lst)

    skipped = False
    if len(listings) >= min_keep_i and len(out) < min_keep_i:
        skipped = True
        out = list(listings)

    return out, {
        "n_in": len(listings),
        "n_out": len(out),
        "removed": len(listings) - len(out),
        "max_age_days": max_age_days_i,
        "today": today_d.isoformat(),
        "with_date": with_date,
        "missing_date": missing_date,
        "stale_candidates": stale_candidates,
        "min_keep": min_keep_i,
        "skipped_due_to_min_keep": skipped,
    }


def _quantile(sorted_vals: Sequence[int], q: float) -> float:
    if not sorted_vals:
        raise ValueError("quantile requires non-empty sequence")
    qf = float(q)
    if qf <= 0.0:
        return float(sorted_vals[0])
    if qf >= 1.0:
        return float(sorted_vals[-1])
    pos = (len(sorted_vals) - 1) * qf
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    w = pos - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * float(w)


def filter_outlier_listings_iqr(
    listings: Sequence[T],
    *,
    value_attr: str = "monthly_total_yen",
    k: float = 1.5,
    min_n: int = 8,
    min_keep: int = 2,
    max_removed_fraction: float = 0.4,
) -> tuple[list[T], dict[str, Any]]:
    """
    Remove outliers using Tukey IQR fences on a numeric attribute (default monthly_total_yen).

    Policy:
    - Only attempts filtering when n>=min_n.
    - Keeps listings with missing/invalid values.
    - Skips if it would remove too many (max_removed_fraction) or drop below min_keep.
    """
    min_n_i = max(0, int(min_n))
    min_keep_i = max(0, int(min_keep))
    kf = float(k)
    max_removed_f = float(max_removed_fraction)

    pairs: list[tuple[int, T]] = []
    missing_val = 0
    for lst in listings:
        v = getattr(lst, value_attr, None)
        try:
            iv = int(v)  # type: ignore[arg-type]
        except Exception:
            missing_val += 1
            continue
        if iv <= 0:
            missing_val += 1
            continue
        pairs.append((iv, lst))

    stats: dict[str, Any] = {
        "value_attr": value_attr,
        "k": kf,
        "min_n": min_n_i,
        "min_keep": min_keep_i,
        "max_removed_fraction": max_removed_f,
        "n_in": len(listings),
        "n_with_value": len(pairs),
        "missing_value": missing_val,
        "skipped": False,
        "skip_reason": None,
        "q1": None,
        "q3": None,
        "iqr": None,
        "lower": None,
        "upper": None,
    }

    if len(pairs) < min_n_i:
        stats["skipped"] = True
        stats["skip_reason"] = "insufficient_n"
        return list(listings), {**stats, "n_out": len(listings), "removed": 0}

    vals = sorted(v for (v, _lst) in pairs)
    q1 = _quantile(vals, 0.25)
    q3 = _quantile(vals, 0.75)
    iqr = q3 - q1
    stats.update({"q1": q1, "q3": q3, "iqr": iqr})

    if not math.isfinite(iqr) or iqr <= 0:
        stats["skipped"] = True
        stats["skip_reason"] = "iqr_zero"
        return list(listings), {**stats, "n_out": len(listings), "removed": 0}

    lower = q1 - kf * iqr
    upper = q3 + kf * iqr
    stats.update({"lower": lower, "upper": upper})

    inlier_set: set[int] = set()
    removed_cnt = 0
    # Determine inlier indices based on value fences.
    for (v, _lst) in pairs:
        if float(v) < float(lower) or float(v) > float(upper):
            removed_cnt += 1
            continue
        inlier_set.add(id(_lst))

    removed_fraction = float(removed_cnt) / float(len(pairs)) if pairs else 0.0
    if removed_fraction > max_removed_f:
        stats["skipped"] = True
        stats["skip_reason"] = "too_many_removed"
        return list(listings), {**stats, "n_out": len(listings), "removed": 0, "removed_fraction": removed_fraction}

    out: list[T] = []
    for lst in listings:
        v = getattr(lst, value_attr, None)
        try:
            iv = int(v)  # type: ignore[arg-type]
        except Exception:
            out.append(lst)
            continue
        if iv <= 0:
            out.append(lst)
            continue
        if id(lst) in inlier_set:
            out.append(lst)

    if len(listings) >= min_keep_i and len(out) < min_keep_i:
        stats["skipped"] = True
        stats["skip_reason"] = "min_keep"
        return list(listings), {**stats, "n_out": len(listings), "removed": 0, "removed_fraction": removed_fraction}

    return out, {**stats, "n_out": len(out), "removed": len(listings) - len(out), "removed_fraction": removed_fraction}


def downsample_listings_evenly(
    listings: Sequence[T],
    *,
    value_attr: str = "monthly_total_yen",
    max_keep: int = 30,
    min_keep: int = 2,
) -> tuple[list[T], dict[str, Any]]:
    """
    Downsample a listing set while roughly preserving distribution.

    Strategy: sort by `value_attr` and take evenly spaced samples including extremes.
    This is useful when providers return hundreds of results and we want a stable
    benchmark without carrying too many samples through the pipeline.
    """
    max_keep_i = max(int(min_keep), int(max_keep))
    out_stats: dict[str, Any] = {
        "value_attr": value_attr,
        "max_keep": max_keep_i,
        "min_keep": int(min_keep),
        "n_in": int(len(listings)),
        "n_out": int(len(listings)),
        "removed": 0,
        "method": "evenly_spaced_by_value",
    }
    if len(listings) <= max_keep_i:
        return list(listings), out_stats

    def _value(lst: T) -> int:
        v = getattr(lst, value_attr, None)
        try:
            iv = int(v)  # type: ignore[arg-type]
        except Exception:
            iv = 0
        return iv

    sorted_list = sorted(list(listings), key=_value)
    n = len(sorted_list)

    # Evenly spaced indices across [0, n-1]
    if max_keep_i <= 1:
        picked = [sorted_list[n // 2]]
    else:
        raw_idxs = [int(round(i * (n - 1) / float(max_keep_i - 1))) for i in range(max_keep_i)]
        # Deduplicate indices while keeping order, then fill gaps if needed.
        seen_idx: set[int] = set()
        idxs: list[int] = []
        for ix in raw_idxs:
            ix = max(0, min(n - 1, int(ix)))
            if ix in seen_idx:
                continue
            seen_idx.add(ix)
            idxs.append(ix)
        if len(idxs) < max_keep_i:
            for ix in range(n):
                if ix in seen_idx:
                    continue
                seen_idx.add(ix)
                idxs.append(ix)
                if len(idxs) >= max_keep_i:
                    break
        picked = [sorted_list[ix] for ix in idxs[:max_keep_i]]

    out_stats.update({"n_out": int(len(picked)), "removed": int(len(listings) - len(picked))})
    return picked, out_stats
