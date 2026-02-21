"""
live_benchmark.py — Provider-agnostic live comparable search.

This module selects a live benchmark provider (HOMES, SUUMO, ...) and returns a
ComparisonResult compatible with evaluate.py.

Environment:
  LIVE_PROVIDERS: comma-separated provider order (default: "chintai,suumo")

Notes:
- Providers are best-effort. If a provider returns confidence="none", we treat it
  as a failure and try the next provider.
- Keep requests polite: providers should implement their own throttling/backoff.
"""

from __future__ import annotations

import os
from typing import Any

from backend.src.suumo_scraper import ComparisonResult

try:
    from backend.src import chintai_scraper
    _CHINTAI_AVAILABLE = True
except Exception:
    chintai_scraper = None  # type: ignore[assignment]
    _CHINTAI_AVAILABLE = False

try:
    from backend.src import homes_scraper
    _HOMES_AVAILABLE = True
except Exception:
    homes_scraper = None  # type: ignore[assignment]
    _HOMES_AVAILABLE = False

try:
    from backend.src import suumo_scraper
    _SUUMO_AVAILABLE = True
except Exception:
    suumo_scraper = None  # type: ignore[assignment]
    _SUUMO_AVAILABLE = False


def available_providers() -> dict[str, bool]:
    return {"chintai": bool(_CHINTAI_AVAILABLE), "homes": bool(_HOMES_AVAILABLE), "suumo": bool(_SUUMO_AVAILABLE)}


def _provider_order() -> list[str]:
    # Default to CHINTAI first, then SUUMO fallback. Users can opt-in to other
    # providers via LIVE_PROVIDERS=..., but some sites may block automated
    # fetches with WAF/JS challenges.
    raw = os.getenv("LIVE_PROVIDERS", "chintai,suumo")
    parts = [p.strip().lower() for p in str(raw).split(",")]
    return [p for p in parts if p]


def search_comparable_listings(  # noqa: PLR0913
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    benchmark_index: dict[str, Any] | None = None,
    rent_yen: int | None = None,
    area_sqm: float | None = None,
    walk_min: int | None = None,
    building_age_years: int | None = None,
    nearest_station_name: str | None = None,
    orientation: str | None = None,
    building_structure: str | None = None,
    bathroom_toilet_separate: bool | None = None,
    min_listings: int = 3,
    max_relaxation_steps: int = 3,
    fetch_timeout: int = 12,
) -> ComparisonResult:
    """
    Try live benchmark providers in order until one returns a usable result.
    """
    attempts: list[dict[str, Any]] = []
    last_filters: dict[str, Any] | None = None
    last_provider: str | None = None
    last_provider_name: str | None = None
    last_error: str | None = None
    last_url: str | None = None

    for provider in _provider_order():
        if provider == "chintai":
            if not _CHINTAI_AVAILABLE or chintai_scraper is None:
                attempts.append({"provider": "chintai", "error": "unavailable"})
                continue
            try:
                res = chintai_scraper.search_comparable_listings(
                    prefecture=prefecture,
                    municipality=municipality,
                    layout_type=layout_type,
                    benchmark_index=benchmark_index,
                    rent_yen=rent_yen,
                    area_sqm=area_sqm,
                    walk_min=walk_min,
                    building_age_years=building_age_years,
                    nearest_station_name=nearest_station_name,
                    orientation=orientation,
                    building_structure=building_structure,
                    bathroom_toilet_separate=bathroom_toilet_separate,
                    min_listings=min_listings,
                    max_relaxation_steps=max_relaxation_steps,
                    fetch_timeout=fetch_timeout,
                )
                if res is not None:
                    last_url = res.search_url or last_url
                    if res.benchmark_confidence != "none" and res.benchmark_rent_yen is not None:
                        return res
                    last_error = res.error or "CHINTAI returned confidence=none"
                    if isinstance(getattr(res, "adjustments_applied", None), dict):
                        lf = res.adjustments_applied.get("filters")
                        if isinstance(lf, dict):
                            last_filters = lf
                        la = res.adjustments_applied.get("attempts")
                        if isinstance(la, list) and la:
                            attempts.extend(la)
                        lp = res.adjustments_applied.get("provider")
                        if isinstance(lp, str) and lp:
                            last_provider = lp
                        lpn = res.adjustments_applied.get("provider_name")
                        if isinstance(lpn, str) and lpn:
                            last_provider_name = lpn
                else:
                    last_error = "CHINTAI returned null result"
                attempts.append({"provider": "chintai", "error": last_error, "url": last_url})
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                attempts.append({"provider": "chintai", "error": last_error})
            continue

        if provider == "homes":
            if not _HOMES_AVAILABLE or homes_scraper is None:
                attempts.append({"provider": "homes", "error": "unavailable"})
                continue
            try:
                res = homes_scraper.search_comparable_listings(
                    prefecture=prefecture,
                    municipality=municipality,
                    layout_type=layout_type,
                    benchmark_index=benchmark_index,
                    rent_yen=rent_yen,
                    area_sqm=area_sqm,
                    walk_min=walk_min,
                    building_age_years=building_age_years,
                    nearest_station_name=nearest_station_name,
                    orientation=orientation,
                    building_structure=building_structure,
                    bathroom_toilet_separate=bathroom_toilet_separate,
                    min_listings=min_listings,
                    max_relaxation_steps=max_relaxation_steps,
                    fetch_timeout=fetch_timeout,
                )
                if res is not None:
                    last_url = res.search_url or last_url
                    if res.benchmark_confidence != "none" and res.benchmark_rent_yen is not None:
                        return res
                    last_error = res.error or "HOMES returned confidence=none"
                    if isinstance(getattr(res, "adjustments_applied", None), dict):
                        lf = res.adjustments_applied.get("filters")
                        if isinstance(lf, dict):
                            last_filters = lf
                        la = res.adjustments_applied.get("attempts")
                        if isinstance(la, list) and la:
                            attempts.extend(la)
                        lp = res.adjustments_applied.get("provider")
                        if isinstance(lp, str) and lp:
                            last_provider = lp
                        lpn = res.adjustments_applied.get("provider_name")
                        if isinstance(lpn, str) and lpn:
                            last_provider_name = lpn
                else:
                    last_error = "HOMES returned null result"
                attempts.append({"provider": "homes", "error": last_error, "url": last_url})
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                attempts.append({"provider": "homes", "error": last_error})
            continue

        if provider == "suumo":
            if not _SUUMO_AVAILABLE or suumo_scraper is None:
                attempts.append({"provider": "suumo", "error": "unavailable"})
                continue
            try:
                res = suumo_scraper.search_comparable_listings(
                    prefecture=prefecture,
                    municipality=municipality,
                    layout_type=layout_type,
                    rent_yen=rent_yen,
                    area_sqm=area_sqm,
                    walk_min=walk_min,
                    building_age_years=building_age_years,
                    nearest_station_name=nearest_station_name,
                    orientation=orientation,
                    building_structure=building_structure,
                    bathroom_toilet_separate=bathroom_toilet_separate,
                    min_listings=min_listings,
                    max_relaxation_steps=max_relaxation_steps,
                    fetch_timeout=fetch_timeout,
                )
                if res is not None:
                    last_url = res.search_url or last_url
                    if res.benchmark_confidence != "none" and res.benchmark_rent_yen is not None:
                        # Annotate provider for UI/debug.
                        if isinstance(getattr(res, "adjustments_applied", None), dict):
                            res.adjustments_applied.setdefault("provider", "suumo")
                            res.adjustments_applied.setdefault("provider_name", "SUUMO")
                        return res
                    last_error = res.error or "SUUMO returned confidence=none"
                    if isinstance(getattr(res, "adjustments_applied", None), dict):
                        lf = res.adjustments_applied.get("filters")
                        if isinstance(lf, dict):
                            last_filters = lf
                        la = res.adjustments_applied.get("attempts")
                        if isinstance(la, list) and la:
                            attempts.extend(la)
                        lp = res.adjustments_applied.get("provider")
                        if isinstance(lp, str) and lp:
                            last_provider = lp
                        lpn = res.adjustments_applied.get("provider_name")
                        if isinstance(lpn, str) and lpn:
                            last_provider_name = lpn
                else:
                    last_error = "SUUMO returned null result"
                attempts.append({"provider": "suumo", "error": last_error, "url": last_url})
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                attempts.append({"provider": "suumo", "error": last_error})
            continue

        attempts.append({"provider": provider, "error": "unknown_provider"})

    return ComparisonResult(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        search_url=last_url,
        adjustments_applied={
            "provider": last_provider,
            "provider_name": last_provider_name,
            "filters": last_filters,
            "attempts": attempts,
        },
        error=last_error or "No live benchmark providers succeeded",
    )
