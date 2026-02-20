from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BenchmarkMatch:
    benchmark_rent_yen: int | None  # adjusted (used downstream)
    benchmark_rent_yen_raw: int | None  # raw from index (pre-adjustment)
    benchmark_n_sources: int  # proxy: n_rows in index (sample count)
    benchmark_confidence: str  # V0 enum: high|mid|low|none
    matched_level: str  # internal: muni_structure_level|muni_level|pref_level|none
    adjustments_applied: dict[str, Any] | None = None  # transparency for hedonic adjustments


def match_benchmark_rent(
    *,
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    building_structure: str | None = None,
    index: dict[str, Any],
    area_sqm: float | None = None,
    building_age_years: int | None = None,
    station_walk_min: int | None = None,
    benchmark_spec: dict[str, Any] | None = None,
) -> BenchmarkMatch:
    """
    Matching priority:
    1) (prefecture + municipality + layout_type + building_structure) exact match
    2) (prefecture + municipality + layout_type) exact match
    3) fallback (prefecture + layout_type)
    4) none

    Confidence mapping:
    - exact muni/structure match -> high (downgrade to mid if n_rows < 2)
    - exact muni match -> high (downgrade to mid if n_rows < 2)
    - prefecture fallback -> mid
    - none -> none
    """

    def _as_float(x: Any) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except Exception:
            return None

    def _as_int(x: Any) -> int | None:
        if x is None:
            return None
        if isinstance(x, int):
            return x
        try:
            return int(float(str(x).strip()))
        except Exception:
            return None

    def _get_hedonic_config(spec: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(spec, dict):
            return {}
        seg = spec.get("segmentation")
        if not isinstance(seg, dict):
            return {}
        rules = seg.get("bucket_rules")
        if not isinstance(rules, dict):
            return {}
        ha = rules.get("hedonic_adjustments")
        return ha if isinstance(ha, dict) else {}

    def _apply_adjustment(raw_yen: int, n_sources: int, confidence: str, matched_level: str) -> BenchmarkMatch:
        # Defaults (safe even if spec does not include hedonic config)
        hedonic = _get_hedonic_config(benchmark_spec)

        age_factors = {
            "0_5": 1.05,
            "6_10": 1.0,
            "11_20": 0.92,
            "ge21": 0.82,
        }
        walk_factors = {
            "le5": 1.03,
            "6_10": 1.0,
            "11_15": 0.93,
            "ge16": 0.87,
        }
        layout_avg_area = {
            "1R": 20.0,
            "1K": 22.0,
            "1DK": 28.0,
            "1LDK": 38.0,
        }
        area_elasticity = 0.6

        # Spec-driven overrides (best-effort)
        if isinstance(hedonic.get("building_age_bucket_multipliers"), dict):
            for k, v in hedonic["building_age_bucket_multipliers"].items():
                if k in age_factors and isinstance(v, (int, float)):
                    age_factors[k] = float(v)
        if isinstance(hedonic.get("station_walk_bucket_multipliers"), dict):
            for k, v in hedonic["station_walk_bucket_multipliers"].items():
                if k in walk_factors and isinstance(v, (int, float)):
                    walk_factors[k] = float(v)
        if isinstance(hedonic.get("layout_avg_area_sqm"), dict):
            for k, v in hedonic["layout_avg_area_sqm"].items():
                if k in layout_avg_area and isinstance(v, (int, float)):
                    layout_avg_area[k] = float(v)
        if isinstance(hedonic.get("area_elasticity"), (int, float)):
            area_elasticity = float(hedonic["area_elasticity"])

        adj: dict[str, Any] = {}
        multiplier = 1.0

        # Building age factor
        age = building_age_years if isinstance(building_age_years, int) else None
        if age is not None:
            if age <= 5:
                age_bucket = "0_5"
            elif age <= 10:
                age_bucket = "6_10"
            elif age <= 20:
                age_bucket = "11_20"
            else:
                age_bucket = "ge21"
            age_factor = float(age_factors.get(age_bucket, 1.0))
            multiplier *= age_factor
            adj.update(
                {
                    "building_age_years": age,
                    "building_age_bucket": age_bucket,
                    "building_age_factor": age_factor,
                }
            )

        # Station walk factor
        walk = station_walk_min if isinstance(station_walk_min, int) else _as_int(station_walk_min)
        if walk is not None:
            if walk <= 5:
                walk_bucket = "le5"
            elif walk <= 10:
                walk_bucket = "6_10"
            elif walk <= 15:
                walk_bucket = "11_15"
            else:
                walk_bucket = "ge16"
            walk_factor = float(walk_factors.get(walk_bucket, 1.0))
            multiplier *= walk_factor
            adj.update(
                {
                    "station_walk_min": walk,
                    "station_walk_bucket": walk_bucket,
                    "station_walk_factor": walk_factor,
                }
            )

        # Area factor (continuous, relative to layout average)
        sqm = _as_float(area_sqm)
        avg_sqm = float(layout_avg_area.get(layout_type, 0.0))
        if sqm is not None and sqm > 0 and avg_sqm > 0 and area_elasticity:
            area_factor = 1.0 + float(area_elasticity) * (float(sqm) - avg_sqm) / avg_sqm
            multiplier *= area_factor
            adj.update(
                {
                    "area_sqm": float(sqm),
                    "area_avg_sqm": avg_sqm,
                    "area_elasticity": float(area_elasticity),
                    "area_factor": area_factor,
                }
            )

        adjusted = int(round(raw_yen * multiplier))
        adjustments_applied = adj if adj else None
        if adjustments_applied is not None:
            adjustments_applied["multiplier_total"] = multiplier
            adjustments_applied["benchmark_rent_yen_raw"] = raw_yen
            adjustments_applied["benchmark_rent_yen_adjusted"] = adjusted

        return BenchmarkMatch(
            benchmark_rent_yen=adjusted,
            benchmark_rent_yen_raw=raw_yen,
            benchmark_n_sources=n_sources,
            benchmark_confidence=confidence,
            matched_level=matched_level,
            adjustments_applied=adjustments_applied,
        )

    muni = (municipality or "").strip()
    structure = (building_structure or "").strip()

    # 1) Municipality + structure (skip if structure is unknown)
    if muni and structure and structure not in ("other", "all"):
        key_exact_struct = f"{prefecture}|{muni}|{layout_type}|{structure}"
        exact_struct = index.get("by_pref_muni_layout_structure", {}).get(key_exact_struct)
        if exact_struct and isinstance(exact_struct, dict):
            yen = exact_struct.get("benchmark_rent_yen_median")
            if isinstance(yen, int):
                n_rows = _as_int(exact_struct.get("n_rows")) or 0
                if n_rows >= 2:
                    # Sufficient multi-source data at structure level → use it directly.
                    return _apply_adjustment(yen, n_rows, "high", "muni_structure_level")
                # n_rows == 1: single source only — fall through to muni_level aggregate
                # which typically has more rows and is more reliable as a baseline.

    # 2) Municipality (structure-agnostic / legacy)
    key_exact = f"{prefecture}|{muni}|{layout_type}"
    exact = index.get("by_pref_muni_layout", {}).get(key_exact)
    if muni and exact and isinstance(exact, dict):
        yen = exact.get("benchmark_rent_yen_median")
        if isinstance(yen, int):
            n_rows = _as_int(exact.get("n_rows")) or 0
            confidence = "high" if n_rows >= 2 else "mid"
            return _apply_adjustment(yen, n_rows, confidence, "muni_level")

    # 3) Prefecture fallback
    key_pref = f"{prefecture}|{layout_type}"
    pref = index.get("by_pref_layout", {}).get(key_pref)
    if pref and isinstance(pref, dict):
        yen = pref.get("benchmark_rent_yen_median")
        if isinstance(yen, int):
            n_rows = _as_int(pref.get("n_rows")) or 0
            return _apply_adjustment(yen, n_rows, "mid", "pref_level")

    return BenchmarkMatch(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        adjustments_applied=None,
    )
