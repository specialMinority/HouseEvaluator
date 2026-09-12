"""Explainable, bounded comparison of current individual rental observations.

This module never fetches a website or substitutes historical aggregate data.
The numerical policy is an implementation hypothesis, not market validation.
See docs/COMPARISON_POLICY_V2.md for the exact sampling and quantile rules.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json
from typing import Any

from .models import ValidationError, parse_timestamp, validate_observation, validate_subject


POLICY = {
    "version": "direct-v2.0",
    "freshness_hours": 24,
    "area_strict_fraction": 0.05,
    "area_strict_min_sqm": 1,
    "area_relaxed_fraction": 0.10,
    "age_strict_years": 2,
    "age_relaxed_years": 5,
    "walk_strict_min": 2,
    "walk_relaxed_min": 5,
    "new_build_max_age_years": 1,
    "floor_bands": ["basement:<=0", "ground:1", "low:2..5", "mid:6..10", "high:>=11"],
    "comparable_min_units": 20,
    "comparable_min_buildings": 10,
    "comparable_min_effective_n": 12,
    "limited_min_units": 10,
    "limited_min_buildings": 5,
    "limited_min_effective_n": 8,
    "meaningful_delta_fraction": 0.05,
    "relaxation_order": ["orientation", "area_sqm", "built_year", "walk_min"],
    "weighting_method": "equal_building_mass;type7_if_equal_else_midpoint_cdf",
    "yen_rounding": "nearest_integer_half_up",
}

_CRITICAL = (
    "mgmt_fee_yen", "structure", "built_year", "walk_min", "floor",
    "bathroom_separate", "furnished",
)
_EXACT = (
    "city", "municipality", "station_id", "layout", "structure",
    "property_type", "contract_type", "furnished", "bathroom_separate",
)
_OFFER_FIELDS = (
    "building_id", *_EXACT, "area_sqm", "built_year", "walk_min", "floor",
    "orientation", "rent_yen", "mgmt_fee_yen",
)
_PUBLIC_FIELDS = (
    "unit_id", "building_id", "source_id", "public_url", "rent_yen",
    "mgmt_fee_yen", "area_sqm", "built_year", "walk_min", "structure",
    "layout", "station_id", "station_name", "floor", "orientation",
    "bathroom_separate", "status_verified_at",
)


def _json(value: Any) -> str:
    # Invalid records are also part of provenance. Non-JSON values are rejected
    # by validation, but their stable textual representation can still be hashed.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _utc_now(now: datetime | None) -> datetime:
    now = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _floor_band(floor: int) -> str:
    if floor <= 0:
        return "basement"
    if floor == 1:
        return "ground"
    if floor <= 5:
        return "low"
    if floor <= 10:
        return "mid"
    return "high"


def _offer_signature(row: dict) -> tuple:
    return tuple(row.get(field) for field in _OFFER_FIELDS)


def _latest_key(row: dict) -> tuple:
    return (
        parse_timestamp(row["received_at"]),
        parse_timestamp(row["status_verified_at"]) if row.get("status_verified_at") else datetime.min.replace(tzinfo=timezone.utc),
        row["observation_id"],
        _json(row),
    )


def _rights_sources(sources: list[dict] | None, now: datetime, mode: str) -> dict | None:
    if sources is None:
        return None
    grouped = defaultdict(list)
    for source in sources:
        if isinstance(source, dict) and isinstance(source.get("source_id"), str):
            grouped[source["source_id"].strip()].append(source)
    result = {}
    for source_id, entries in grouped.items():
        # Conflicting source metadata cannot be resolved by array order.
        if not source_id or len({_json(entry) for entry in entries}) != 1:
            continue
        source = entries[0]
        rights = source.get("rights", {})
        if not isinstance(rights, dict) or any(rights.get(field) is not True for field in ("comparison", "display", "storage")):
            continue
        try:
            if parse_timestamp(rights.get("expires_at")) <= now:
                continue
            if source.get("as_of") is not None and parse_timestamp(source["as_of"]) > now:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        if source.get("data_kind") != mode:
            continue
        if source.get("complete", True) is not True:
            continue
        result[source_id] = source
    return result


def _weights(rows: list[dict]) -> tuple[list[Fraction], int, Fraction]:
    buildings = Counter(row["building_id"] for row in rows)
    count = len(buildings)
    if not count:
        return [], 0, Fraction(0)
    weights = [Fraction(1, count * buildings[row["building_id"]]) for row in rows]
    effective_n = Fraction(1) / sum((weight * weight for weight in weights), Fraction(0))
    return weights, count, effective_n


def _enough(rows: list[dict], level: str) -> bool:
    _, buildings, effective_n = _weights(rows)
    return (
        len(rows) >= POLICY[f"{level}_min_units"]
        and buildings >= POLICY[f"{level}_min_buildings"]
        and effective_n >= POLICY[f"{level}_min_effective_n"]
    )


def _quantile(rows: list[dict], weights: list[Fraction], q: Fraction) -> Fraction:
    """One shared weighted distribution; do not calculate an unweighted median."""
    values = sorted(
        (Fraction(row["rent_yen"] + row["mgmt_fee_yen"]), weight, row["unit_id"])
        for row, weight in zip(rows, weights)
    )
    if len({weight for _, weight, _ in values}) == 1:
        # Conventional linearly interpolated percentiles, including the ordinary
        # even-sample median (Hyndman-Fan type 7).
        position = q * (len(values) - 1)
        index = position.numerator // position.denominator
        fraction = position - index
        right = min(index + 1, len(values) - 1)
        return values[index][0] + fraction * (values[right][0] - values[index][0])
    positions = []
    cumulative = Fraction(0)
    for price, weight, _ in values:
        positions.append((cumulative + weight / 2, price))
        cumulative += weight
    if q <= positions[0][0]:
        return positions[0][1]
    for (left_p, left_y), (right_p, right_y) in zip(positions, positions[1:]):
        if q <= right_p:
            return left_y + (q - left_p) / (right_p - left_p) * (right_y - left_y)
    return positions[-1][1]


def _yen(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _criteria(subject: dict) -> dict:
    area = Fraction(str(subject["area_sqm"]))
    strict_area = max(Fraction(POLICY["area_strict_min_sqm"]), area * Fraction(str(POLICY["area_strict_fraction"])))
    return {
        "orientation": subject.get("orientation"),
        "area_sqm": strict_area,
        "built_year": POLICY["age_strict_years"],
        "walk_min": POLICY["walk_strict_min"],
    }


def _relaxation_limits(subject: dict) -> dict:
    strict = _criteria(subject)
    return {
        "orientation": "any",
        "area_sqm": max(strict["area_sqm"], Fraction(str(subject["area_sqm"])) * Fraction(str(POLICY["area_relaxed_fraction"]))),
        "built_year": POLICY["age_relaxed_years"],
        "walk_min": POLICY["walk_relaxed_min"],
    }


def _fixed_exclusion(row: dict, subject: dict, now: datetime) -> str | None:
    if any(row.get(field) is None for field in _CRITICAL):
        return "missing_critical_conditions"
    mismatch = next((field for field in _EXACT if row.get(field) != subject.get(field)), None)
    if mismatch is not None:
        return f"mismatch_{mismatch}"
    row_new = now.year - row["built_year"] <= POLICY["new_build_max_age_years"]
    subject_new = now.year - subject["built_year"] <= POLICY["new_build_max_age_years"]
    if row_new != subject_new:
        return "new_build_boundary"
    if _floor_band(row["floor"]) != _floor_band(subject["floor"]):
        return "floor_band_boundary"
    return None


def _display_criterion(field: str, value: Any) -> Any:
    if isinstance(value, Fraction):
        return float(value)
    return value


def _matches(row: dict, subject: dict, criteria: dict) -> bool:
    # Fixed categorical values, critical completeness, new-build boundary and
    # floor bands have already been checked before any relaxation.
    if criteria["orientation"] != "any":
        if criteria["orientation"] is None or row.get("orientation") != criteria["orientation"]:
            return False
    return (
        abs(Fraction(str(row["area_sqm"])) - Fraction(str(subject["area_sqm"]))) <= criteria["area_sqm"]
        and abs(row["built_year"] - subject["built_year"]) <= criteria["built_year"]
        and abs(Fraction(str(row["walk_min"])) - Fraction(str(subject["walk_min"]))) <= criteria["walk_min"]
    )


def _differences(row: dict, subject: dict) -> list[str]:
    differences = []
    for field in ("orientation", "area_sqm", "built_year", "walk_min", "floor"):
        actual, requested = row.get(field), subject.get(field)
        if actual is None or requested is None:
            differences.append(f"{field}:unknown")
        elif actual != requested:
            differences.append(f"{field}:{requested}->{actual}")
    return differences


def compare(
    subject: dict,
    observations: list[dict],
    *,
    now: datetime | None = None,
    snapshot_version: str = "",
    sources: list[dict] | None = None,
    is_demo: bool = False,
    validated_segments: set[str] | None = None,
) -> dict:
    """Compare a subject against a complete supplied observation collection.

    Segment approval is trusted operator configuration. HTTP payloads/imports
    must never supply it. Unsupported subject enums raise ValidationError.
    """
    now = _utc_now(now)
    subject = validate_subject(subject, now=now)
    if not isinstance(observations, list):
        raise ValidationError("observations must be a list")
    if sources is not None and not isinstance(sources, list):
        raise ValidationError("sources must be a list or null")
    if not isinstance(is_demo, bool):
        raise ValidationError("is_demo must be boolean")
    approved = sorted(set(validated_segments or ()))
    segment_approved = f"{subject['city']}/{subject['layout']}" in approved
    mode = "synthetic" if is_demo else "observed"
    source_map = _rights_sources(sources, now, mode)
    reasons = []
    if not segment_approved:
        reasons.append("policy_unvalidated")
    if is_demo:
        reasons.append("synthetic_data")
    if source_map is None:
        reasons.append("source_rights_unverified")
    identity_verified = bool(subject.get("unit_id") or subject.get("building_id"))
    if not identity_verified:
        reasons.append("identity_unverified")
    elif not subject.get("unit_id"):
        reasons.append("self_building_excluded")
    missing_fields = [field for field in _CRITICAL if subject.get(field) is None]
    if subject.get("orientation") is None:
        missing_fields.append("orientation")
    critical_missing = any(subject.get(field) is None for field in _CRITICAL)
    provenance = {
        "subject": subject,
        "observations": sorted((_json(row) for row in observations)),
        "sources": sorted((_json(source) for source in sources)) if sources is not None else None,
        "evaluated_at": _iso(now),
        "snapshot_version": snapshot_version,
        "is_demo": is_demo,
        "validated_segments": approved,
        "policy": POLICY,
    }
    result = {
        "assessment_id": hashlib.sha256(_json(provenance).encode("utf-8")).hexdigest(),
        "evaluated_at": _iso(now),
        "status": "insufficient",
        "reason_codes": reasons,
        "judgment": None,
        "is_demo": is_demo,
        "price_basis": "rent_plus_management",
        "subject_price_yen": subject["rent_yen"] + subject["mgmt_fee_yen"] if subject.get("mgmt_fee_yen") is not None else None,
        "benchmark_yen": None,
        "delta_yen": None,
        "delta_ratio": None,
        "distribution": {"q25_yen": None, "q50_yen": None, "q75_yen": None, "weighting_method": POLICY["weighting_method"]},
        "sample": {"unit_count": 0, "building_count": 0, "effective_n": 0.0},
        "freshness": {"status_verified_at_min": None, "status_verified_at_max": None},
        "matching": {"steps": [], "relaxed_fields": [], "missing_fields": missing_fields},
        "comparables": [],
        "excluded_counts": {},
        "sources": [],
        "versions": {"schema": "2.0", "policy": POLICY["version"], "snapshot": snapshot_version, "model": None},
    }
    if critical_missing:
        reasons.append("missing_critical_fields")
        return result

    excluded = Counter()
    offers = defaultdict(list)
    for payload in observations:
        try:
            row = validate_observation(payload, now=now)
        except (ValidationError, ValueError, TypeError, KeyError):
            excluded["invalid_observation"] += 1
            continue
        if row["data_kind"] != mode:
            excluded["data_kind_mismatch"] += 1
            continue
        if source_map is not None and row["source_id"] not in source_map:
            excluded["source_not_authorized"] += 1
            continue
        if subject.get("unit_id") and row["unit_id"] == subject["unit_id"]:
            excluded["self_unit"] += 1
            continue
        if not subject.get("unit_id") and subject.get("building_id") == row["building_id"]:
            excluded["self_building"] += 1
            continue
        offers[(row["unit_id"], row["source_id"], row["listing_id"])].append(row)

    # Resolve observation history *before* freshness/status filtering. A newer
    # stale/closed record must never resurrect a superseded active observation.
    by_unit = defaultdict(list)
    ambiguous_units = set()
    for key in sorted(offers):
        history = offers[key]
        latest_received = max(parse_timestamp(row["received_at"]) for row in history)
        latest = [row for row in history if parse_timestamp(row["received_at"]) == latest_received]
        signatures = {(row["status"], _offer_signature(row)) for row in latest}
        excluded["superseded_observation"] += len(history) - len(latest)
        if len(signatures) > 1:
            ambiguous_units.add(key[0])
            excluded["ambiguous_latest_observation"] += len(latest)
            continue
        selected = max(latest, key=_latest_key)
        excluded["duplicate_observation"] += len(latest) - 1
        by_unit[key[0]].append(selected)

    boundary = now - timedelta(hours=POLICY["freshness_hours"])
    relaxed = _relaxation_limits(subject)
    eligible = []
    stale_matching = 0
    for unit_id in sorted(by_unit):
        rows = by_unit[unit_id]
        if unit_id in ambiguous_units:
            excluded["ambiguous_canonical_unit"] += len(rows)
            continue
        current = []
        for row in rows:
            received = parse_timestamp(row["received_at"])
            verified = parse_timestamp(row["status_verified_at"]) if row.get("status_verified_at") else None
            updated = parse_timestamp(row["source_updated_at"]) if row.get("source_updated_at") else None
            if received > now or (updated is not None and updated > now) or (verified is not None and verified > now):
                excluded["future_timestamp"] += 1
            elif verified is None:
                excluded["status_unverified"] += 1
            elif verified < boundary:
                excluded["stale_observation"] += 1
                if row["status"] == "active" and _fixed_exclusion(row, subject, now) is None and _matches(row, subject, relaxed):
                    stale_matching += 1
            else:
                current.append(row)
        active = [row for row in current if row["status"] == "active"]
        inactive = [row for row in current if row["status"] != "active"]
        if active and inactive:
            excluded["conflicting_status"] += len(current)
            continue
        if not active:
            excluded["inactive_observation"] += len(inactive)
            continue
        if len({_offer_signature(row) for row in active}) != 1:
            excluded["conflicting_active_offers"] += len(active)
            continue
        row = max(active, key=_latest_key)
        excluded["duplicate_unit"] += len(active) - 1
        fixed_exclusion = _fixed_exclusion(row, subject, now)
        if fixed_exclusion is not None:
            excluded[fixed_exclusion] += 1
            continue
        eligible.append(row)

    criteria = _criteria(subject)
    candidates = [row for row in eligible if _matches(row, subject, criteria)]
    result["matching"]["steps"].append({"step": 0, "changed_field": None, "before": None, "after": None, "candidate_count": len(candidates)})
    for field in POLICY["relaxation_order"]:
        if _enough(candidates, "comparable"):
            break
        before = criteria[field]
        if before == relaxed[field]:
            # An extremely small area's 1 sqm initial tolerance may already
            # exceed 10%; do not report a no-op as a changed condition.
            continue
        criteria[field] = relaxed[field]
        candidates = [row for row in eligible if _matches(row, subject, criteria)]
        result["matching"]["steps"].append({
            "step": len(result["matching"]["steps"]), "changed_field": field,
            "before": _display_criterion(field, before),
            "after": _display_criterion(field, criteria[field]),
            "candidate_count": len(candidates),
        })
        result["matching"]["relaxed_fields"].append(field)
    excluded["outside_bounded_conditions"] += len(eligible) - len(candidates)
    result["excluded_counts"] = {key: value for key, value in sorted(excluded.items()) if value}
    weights, buildings, effective_n = _weights(candidates)
    result["sample"] = {"unit_count": len(candidates), "building_count": buildings, "effective_n": round(float(effective_n), 6)}
    for row, weight in zip(candidates, weights):
        comparable = {field: row.get(field) for field in _PUBLIC_FIELDS}
        comparable["total_yen"] = row["rent_yen"] + row["mgmt_fee_yen"]
        comparable["status_verified_at"] = _iso(parse_timestamp(row["status_verified_at"]))
        comparable["differences"] = _differences(row, subject)
        comparable["weight"] = float(weight)
        result["comparables"].append(comparable)
    if candidates:
        verified_times = [parse_timestamp(row["status_verified_at"]) for row in candidates]
        result["freshness"] = {"status_verified_at_min": _iso(min(verified_times)), "status_verified_at_max": _iso(max(verified_times))}
    source_ids = sorted({row["source_id"] for row in candidates})
    for source_id in source_ids:
        metadata = source_map.get(source_id, {}) if source_map is not None else {}
        # No made-up source names, links, source lineage or coverage claims.
        result["sources"].append({
            "source_id": source_id,
            "display_name": metadata.get("display_name"),
            "independent_source_id": metadata.get("independent_source_id"),
            "data_kind": mode,
        })
    independent_ids = {entry["independent_source_id"] for entry in result["sources"] if entry["independent_source_id"]}
    if source_ids and (not independent_ids or len(independent_ids) < 2):
        reasons.append("source_scope_limited" if independent_ids else "source_independence_unverified")
    if any(row.get("orientation") is None for row in candidates) or subject.get("orientation") is None:
        reasons.append("orientation_unverified")
    if result["matching"]["relaxed_fields"]:
        reasons.append("conditions_relaxed")

    if not _enough(candidates, "limited"):
        result["status"] = "stale" if not candidates and stale_matching else "insufficient"
        reasons.append("freshness_expired" if result["status"] == "stale" else "insufficient_sample")
        return result
    sufficient = _enough(candidates, "comparable")
    result["status"] = "comparable" if sufficient and segment_approved and identity_verified and not is_demo else "limited"
    if not sufficient:
        reasons.append("limited_sample")
    quantiles = [_quantile(candidates, weights, q) for q in (Fraction(1, 4), Fraction(1, 2), Fraction(3, 4))]
    q25, benchmark, q75 = map(_yen, quantiles)
    result["distribution"].update({"q25_yen": q25, "q50_yen": benchmark, "q75_yen": q75})
    result["benchmark_yen"] = benchmark
    result["delta_yen"] = result["subject_price_yen"] - benchmark
    result["delta_ratio"] = round(result["delta_yen"] / benchmark, 8)
    if result["status"] == "comparable":
        price = result["subject_price_yen"]
        delta = Fraction(result["delta_yen"], benchmark)
        margin = Fraction(str(POLICY["meaningful_delta_fraction"]))
        if price > q75 and delta >= margin:
            result["judgment"] = "higher"
        elif price < q25 and delta <= -margin:
            result["judgment"] = "lower"
        else:
            result["judgment"] = "similar"
    return result
