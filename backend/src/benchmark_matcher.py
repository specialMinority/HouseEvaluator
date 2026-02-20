from __future__ import annotations

from dataclasses import dataclass
import math
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
    orientation: str | None = None,
    bathroom_toilet_separate: bool | None = None,
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
        """Apply benchmark adjustments.

        V2 policy: Only apply **building structure** adjustment when the
        benchmark is structure-agnostic (muni_level / pref_level fallback).
        All other hedonic factors (age, walk, area, bath, orientation) are
        intentionally disabled because they duplicate penalties already
        captured by the Condition score component.
        """
        hedonic = _get_hedonic_config(benchmark_spec)

        # Allow spec to force-disable all adjustments
        if hedonic.get("enabled") is False:
            return BenchmarkMatch(
                benchmark_rent_yen=raw_yen,
                benchmark_rent_yen_raw=raw_yen,
                benchmark_n_sources=n_sources,
                benchmark_confidence=confidence,
                matched_level=matched_level,
                adjustments_applied=None,
            )

        # If we already matched at structure level, no adjustment needed
        if matched_level == "muni_structure_level":
            return BenchmarkMatch(
                benchmark_rent_yen=raw_yen,
                benchmark_rent_yen_raw=raw_yen,
                benchmark_n_sources=n_sources,
                benchmark_confidence=confidence,
                matched_level=matched_level,
                adjustments_applied=None,
            )

        # --- Structure-only adjustment for non-structure-matched benchmarks ---
        struct_key = (building_structure or "").strip().lower() or "other"
        struct_defaults: dict[str, float] = {
            "wood": 0.90,
            "light_steel": 0.94,
            "steel": 0.98,
            "rc": 1.08,
            "src": 1.12,
            "other": 1.00,
        }
        if isinstance(hedonic.get("building_structure_multipliers"), dict):
            for k, v in hedonic["building_structure_multipliers"].items():
                if k in struct_defaults and isinstance(v, (int, float)):
                    struct_defaults[k] = float(v)

        struct_factor = float(struct_defaults.get(struct_key, struct_defaults["other"]))

        # Clamp to [0.85, 1.15] for safety
        struct_factor = min(max(struct_factor, 0.85), 1.15)
        adjusted = int(round(raw_yen * struct_factor))

        adj: dict[str, Any] = {
            "building_structure_key": struct_key,
            "building_structure_factor": struct_factor,
            "multiplier_total": struct_factor,
            "benchmark_rent_yen_raw": raw_yen,
            "benchmark_rent_yen_adjusted": adjusted,
            "note": "V2: structure-only adjustment (other hedonic factors disabled)",
        }

        return BenchmarkMatch(
            benchmark_rent_yen=adjusted,
            benchmark_rent_yen_raw=raw_yen,
            benchmark_n_sources=n_sources,
            benchmark_confidence=confidence,
            matched_level=matched_level,
            adjustments_applied=adj,
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
