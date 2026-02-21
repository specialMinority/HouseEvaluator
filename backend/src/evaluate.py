from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from backend.src.benchmark_loader import load_or_build_benchmark_index
from backend.src.benchmark_matcher import BenchmarkMatch, match_benchmark_rent
from backend.src.rules.jsonlogic import apply as jsonlogic_apply
from backend.src.scoring import (
    SpecError,
    _compute_component_score,
    _feature_score,
    _grade_for_score,
    _round,
    _select_first_rule_by_priority,
)
try:
    from backend.src.live_benchmark import available_providers, search_comparable_listings
    _LIVE_BENCHMARK_AVAILABLE = True
except Exception:
    available_providers = None  # type: ignore[assignment]
    _LIVE_BENCHMARK_AVAILABLE = False


ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Initial multiple (IM) market benchmark by prefecture
# Based on 2025/2026 JP rental market research:
#   Tokyo/Osaka: shikikin ~1M + reikin ~1M + brokerage ~1M + other fees ~2M ≈ 5.0M
#   Saitama/Chiba/Kanagawa: slightly lower demand pressure ≈ 4.5M
#   Foreigner premium: +0.5–1.0M (mandatory guarantee company replaces personal guarantor)
#   → S2 foreign_im_shift_months = 1.0 used as the foreigner-adjusted shift
# ---------------------------------------------------------------------------
_IM_MARKET_AVG_BY_PREF: Final[dict[str, float]] = {
    "tokyo": 5.0,
    "osaka": 5.0,
    "saitama": 4.5,
    "chiba": 4.5,
    "kanagawa": 4.5,
}
_IM_MARKET_AVG_DEFAULT: Final[float] = 4.5


def _estimate_benchmark_mgmt_fee_yen(
    benchmark_rent_yen: int,
    *,
    input_mgmt_fee_yen: int,
    rate_of_rent: float = 0.05,
    cap_yen: int = 20000,
) -> int:
    """
    Our CSV benchmark dataset contains *rent only* (no management fee).

    When comparing `monthly_fixed_cost_yen = rent + mgmt_fee` to a rent-only benchmark,
    the result is biased toward "expensive". To reduce this bias without
    over-crediting low management fees, we add a conservative estimate to the
    benchmark, capped and never exceeding the listing's own mgmt fee.

    This keeps mgmt fees from being double-counted (we still penalize unusually
    high mgmt fees, but avoid a systematic upward bias).
    """
    if benchmark_rent_yen <= 0:
        return 0
    if input_mgmt_fee_yen <= 0:
        return 0

    est = int(round(float(benchmark_rent_yen) * float(rate_of_rent)))
    est = max(0, min(est, int(cap_yen)))
    return min(int(input_mgmt_fee_yen), est)


def _im_assessment_label(im: float, market_avg: float) -> str:
    """Classify initial_multiple relative to prefecture market average."""
    delta = im - market_avg
    if delta <= -1.5:
        return "매우 낮음(시세보다 크게 저렴)"
    if delta <= -1.0:
        return "낮음(시세 이하)"
    if delta < 1.0:
        return "평균(시세 수준)"
    if delta < 1.5:
        return "다소 높음(시세 초과)"
    return "높음(시세 크게 초과)"


# SpecError is defined in backend.src.scoring and re-exported here for backward compat.


def _im_assessment_label_ko(im: float, market_avg: float) -> str:
    """Korean label for initial_multiple vs prefecture market average (months)."""
    delta = im - market_avg
    if delta <= -1.5:
        return "매우 낮음(시세보다 크게 저렴)"
    if delta <= -1.0:
        return "낮음(시세 이하)"
    if delta < 1.0:
        return "평균(시세 수준)"
    if delta < 1.5:
        return "다소 높음(시세 초과)"
    return "높음(시세보다 크게 초과)"


class InputValidationError(ValueError):
    pass


def _find_spec_bundle_dir() -> Path:
    env_dir = os.getenv("SPEC_BUNDLE_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if not p.exists():
            raise FileNotFoundError(f"SPEC_BUNDLE_DIR not found: {p}")
        return p

    candidates = [
        ROOT_DIR / "spec_bundle_v0.1.2",
        ROOT_DIR / "spec_bundle_v0.1.1",
        ROOT_DIR / "spec_bundle_v0.1.0",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError("spec_bundle directory not found (set SPEC_BUNDLE_DIR)")


def _load_spec_bundle(spec_dir: Path) -> dict[str, Any]:
    bundle_path = spec_dir / "spec_bundle.json"
    if bundle_path.exists():
        with bundle_path.open("r", encoding="utf-8") as f:
            bundle = json.load(f)
        # Expected shape: {"S1": {...}, "S2": {...}, "C1": {...}, "D1": {...}}
        for k in ("S1", "S2", "C1", "D1"):
            if k not in bundle:
                raise SpecError(f"spec_bundle.json missing key: {k}")
        return bundle

    # Fallback: load individual spec JSON files.
    def load_json(name: str) -> dict[str, Any]:
        p = spec_dir / name
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "version": "unknown",
        "generated_at": "unknown",
        "S1": load_json("S1_InputSchema.json"),
        "S2": load_json("S2_ScoringSpec.json"),
        "C1": load_json("C1_ReportTemplates.json"),
        "D1": load_json("D1_BenchmarkSpec.json"),
    }


@dataclass(frozen=True)
class Runtime:
    spec_dir: Path
    spec: dict[str, Any]
    benchmark_index: dict[str, Any]


_RUNTIME: Runtime | None = None


def _default_benchmark_raw_paths() -> list[Path]:
    return [
        ROOT_DIR / "benchmark_rent_raw.json",
        ROOT_DIR / "benchmark_rent_raw.csv",
        ROOT_DIR / "agents" / "agent_D_benchmark_data" / "out" / "benchmark_rent_raw.json",
        ROOT_DIR / "agents" / "agent_D_benchmark_data" / "out" / "benchmark_rent_raw.csv",
        BACKEND_DIR / "data" / "benchmark_rent_raw.json",
        BACKEND_DIR / "data" / "benchmark_rent_raw.csv",
    ]


def get_runtime() -> Runtime:
    global _RUNTIME  # noqa: PLW0603 (simple process-wide cache)
    if _RUNTIME is not None:
        return _RUNTIME

    spec_dir = _find_spec_bundle_dir()
    spec = _load_spec_bundle(spec_dir)

    index_path = Path(os.getenv("BENCHMARK_INDEX_PATH", str(BACKEND_DIR / "data" / "benchmark_index.json"))).resolve()
    raw_paths = [str(p) for p in _default_benchmark_raw_paths() if p.exists()]
    benchmark_index = load_or_build_benchmark_index(index_path=str(index_path), raw_paths=raw_paths, write_if_missing=True)

    _RUNTIME = Runtime(spec_dir=spec_dir, spec=spec, benchmark_index=benchmark_index)
    return _RUNTIME


def _round(x: float, ndigits: int = 6) -> float:
    return float(round(float(x), ndigits))


def _format_value_for_template(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Heuristic formatting: ratios often look better with 4dp, scores with 2dp.
        if abs(value) < 2:
            s = f"{value:.4f}"
        else:
            s = f"{value:.2f}"
        s = s.rstrip("0").rstrip(".")
        return s
    return str(value)


def _render_placeholders(text: str, ctx: dict[str, Any]) -> str:
    out = text
    for k, v in ctx.items():
        token = "{" + k + "}"
        if token in out:
            out = out.replace(token, _format_value_for_template(v))
    return out


def _validate_input(payload: dict[str, Any], s1: dict[str, Any]) -> None:
    fields = s1.get("fields", [])
    allowed_keys = {f.get("key") for f in fields if isinstance(f, dict) and f.get("key")}
    required = list(s1.get("mvp_required_fields", []))

    unknown = sorted(set(payload.keys()) - allowed_keys)
    if unknown:
        raise InputValidationError(f"Unknown input keys: {unknown}")

    missing = [k for k in required if k not in payload]
    if missing:
        raise InputValidationError(f"Missing required keys: {missing}")

    # Conditional requirement: hub_station_other_name when hub_station == "other"
    if payload.get("hub_station") == "other" and "hub_station_other_name" not in payload:
        raise InputValidationError("Missing required key: hub_station_other_name (hub_station=='other')")

    # Type + constraints
    field_by_key = {f["key"]: f for f in fields if isinstance(f, dict) and "key" in f}
    for k, v in payload.items():
        spec = field_by_key.get(k)
        if not spec:
            continue
        ftype = spec.get("type")
        constraints = spec.get("constraints") or {}

        if ftype == "integer":
            if not isinstance(v, int):
                raise InputValidationError(f"Expected integer for {k}")
        elif ftype == "number":
            if not isinstance(v, (int, float)):
                raise InputValidationError(f"Expected number for {k}")
        elif ftype == "boolean":
            if not isinstance(v, bool):
                raise InputValidationError(f"Expected boolean for {k}")
        elif ftype in ("string", "enum"):
            if not isinstance(v, str):
                raise InputValidationError(f"Expected string for {k}")
            if ftype == "enum":
                ev = constraints.get("enum_values")
                if isinstance(ev, list) and v not in ev:
                    raise InputValidationError(f"Invalid enum value for {k}: {v}")
        else:
            raise InputValidationError(f"Unsupported field type in S1 for {k}: {ftype}")

        min_v = constraints.get("min")
        max_v = constraints.get("max")
        unit = spec.get("unit")
        if isinstance(min_v, (int, float)) and isinstance(v, (int, float)) and v < min_v:
            raise InputValidationError(f"{k} below min {min_v}")
        enforce_max = str(unit or "none") != "yen"
        if enforce_max and isinstance(max_v, (int, float)) and isinstance(v, (int, float)) and v > max_v:
            raise InputValidationError(f"{k} above max {max_v}")





def evaluate(payload: dict[str, Any], *, runtime: Runtime | None = None, benchmark_index_override: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = runtime or get_runtime()
    s1 = rt.spec["S1"]
    s2 = rt.spec["S2"]
    c1 = rt.spec["C1"]

    _validate_input(payload, s1)

    current_year = datetime.now().year
    monthly_fixed_cost_yen = int(payload["rent_yen"]) + int(payload["mgmt_fee_yen"])
    building_age_years = max(0, current_year - int(payload["building_built_year"]))
    initial_multiple = float(payload["initial_cost_total_yen"]) / monthly_fixed_cost_yen if monthly_fixed_cost_yen > 0 else 0.0

    # ── Live benchmark: real-time comparable search (HOMES/SUUMO) ───────────
    _live_result = None
    _live_used = False
    # Default OFF for offline determinism (unit tests, local scripting).
    # The HTTP server entrypoint (`python -m backend.src.server`) enables it by default via env setdefault.
    live_enabled = os.getenv("SUUMO_LIVE", "0") not in ("0", "false", "no")
    use_live = live_enabled and _LIVE_BENCHMARK_AVAILABLE
    provider_availability = available_providers() if callable(available_providers) else None
    providers_env_raw = os.getenv("LIVE_PROVIDERS")
    provider_order = [p.strip().lower() for p in str(os.getenv("LIVE_PROVIDERS", "chintai,suumo")).split(",") if p.strip()]
    live_benchmark: dict[str, Any] = {
        "enabled": bool(live_enabled),
        "available": bool(_LIVE_BENCHMARK_AVAILABLE),
        "providers": provider_availability,
        "providers_env": providers_env_raw,
        "provider_order": provider_order,
        "provider": None,
        "provider_name": None,
        "attempted": False,
        "used": False,
        "confidence": "none",
        "n_sources": 0,
        "matched_level": "none",
        "relaxation_applied": None,
        "search_url": None,
        "filters": None,
        "attempts": None,
        "error": None,
    }
    if not live_enabled:
        live_benchmark["error"] = "disabled (set SUUMO_LIVE=1 to enable)"
    elif not _LIVE_BENCHMARK_AVAILABLE:
        live_benchmark["error"] = "unavailable (live benchmark module import failed)"
    if use_live:
        try:
            live_benchmark["attempted"] = True
            _live_result = search_comparable_listings(
                prefecture=str(payload["prefecture"]),
                municipality=str(payload.get("municipality")) if payload.get("municipality") else None,
                layout_type=str(payload["layout_type"]),
                benchmark_index=benchmark_index_override if benchmark_index_override is not None else rt.benchmark_index,
                rent_yen=int(payload["rent_yen"]),
                area_sqm=float(payload["area_sqm"]) if payload.get("area_sqm") is not None else None,
                walk_min=int(payload["station_walk_min"]) if payload.get("station_walk_min") is not None else None,
                building_age_years=building_age_years,
                nearest_station_name=str(payload.get("nearest_station_name")) if payload.get("nearest_station_name") else None,
                orientation=str(payload.get("orientation")) if payload.get("orientation") else None,
                building_structure=str(payload.get("building_structure")) if payload.get("building_structure") else None,
                bathroom_toilet_separate=bool(payload.get("bathroom_toilet_separate")) if payload.get("bathroom_toilet_separate") is not None else None,
            )
            if _live_result is not None:
                live_filters = None
                live_attempts = None
                live_provider = None
                live_provider_name = None
                if isinstance(getattr(_live_result, "adjustments_applied", None), dict):
                    lf = _live_result.adjustments_applied.get("filters")
                    if isinstance(lf, dict):
                        live_filters = lf
                    la = _live_result.adjustments_applied.get("attempts")
                    if isinstance(la, list):
                        live_attempts = la
                    lp = _live_result.adjustments_applied.get("provider")
                    if isinstance(lp, str) and lp:
                        live_provider = lp
                    lpn = _live_result.adjustments_applied.get("provider_name")
                    if isinstance(lpn, str) and lpn:
                        live_provider_name = lpn
                live_benchmark.update(
                    {
                        "confidence": _live_result.benchmark_confidence,
                        "n_sources": _live_result.benchmark_n_sources,
                        "matched_level": _live_result.matched_level,
                        "relaxation_applied": _live_result.relaxation_applied,
                        "search_url": _live_result.search_url,
                        "provider": live_provider,
                        "provider_name": live_provider_name,
                        "filters": live_filters,
                        "attempts": live_attempts,
                        "error": _live_result.error,
                    }
                )
            if _live_result and _live_result.benchmark_confidence != "none":
                _live_used = True
                live_benchmark["used"] = True
        except Exception as e:  # noqa: BLE001
            live_benchmark["error"] = f"exception during live benchmark fetch: {e}"
            _live_result = None

    # ── CSV fallback benchmark ────────────────────────────────────────────────
    benchmark_index = benchmark_index_override if benchmark_index_override is not None else rt.benchmark_index
    if _live_used and _live_result is not None:
        # Convert ComparisonResult → BenchmarkMatch-compatible object
        bm = BenchmarkMatch(
            benchmark_rent_yen=_live_result.benchmark_rent_yen,
            benchmark_rent_yen_raw=_live_result.benchmark_rent_yen_raw,
            benchmark_n_sources=_live_result.benchmark_n_sources,
            benchmark_confidence=_live_result.benchmark_confidence,
            matched_level=_live_result.matched_level,
            adjustments_applied=None,
        )
    else:
        bm = match_benchmark_rent(
            prefecture=str(payload["prefecture"]),
            municipality=str(payload.get("municipality")) if payload.get("municipality") is not None else None,
            layout_type=str(payload["layout_type"]),
            building_structure=str(payload.get("building_structure", "other")),
            index=benchmark_index,
            area_sqm=float(payload.get("area_sqm")) if payload.get("area_sqm") is not None else None,
            building_age_years=building_age_years,
            station_walk_min=int(payload.get("station_walk_min")) if payload.get("station_walk_min") is not None else None,
            orientation=str(payload.get("orientation", "UNKNOWN")),
            bathroom_toilet_separate=bool(payload["bathroom_toilet_separate"]) if "bathroom_toilet_separate" in payload else None,
            benchmark_spec=rt.spec.get("D1"),
        )

    benchmark_monthly_fixed_cost_yen = bm.benchmark_rent_yen
    benchmark_monthly_fixed_cost_yen_raw = bm.benchmark_rent_yen_raw
    benchmark_confidence = bm.benchmark_confidence
    benchmark_n_sources = bm.benchmark_n_sources
    benchmark_matched_level = bm.matched_level
    benchmark_adjustments = bm.adjustments_applied

    # CSV benchmark index is rent-only. If the listing has a management fee,
    # compare against a conservatively mgmt-adjusted benchmark to avoid
    # systematic "expensive" bias.
    benchmark_mgmt_fee_estimate_yen = 0
    is_live_total_benchmark = benchmark_matched_level in (
        "suumo_live",
        "suumo_relaxed",
        "homes_live",
        "homes_relaxed",
        "chintai_live",
        "chintai_relaxed",
    )
    if (not is_live_total_benchmark) and benchmark_monthly_fixed_cost_yen is not None:
        mgmt_fee_yen = int(payload.get("mgmt_fee_yen") or 0)
        benchmark_mgmt_fee_estimate_yen = _estimate_benchmark_mgmt_fee_yen(
            int(benchmark_monthly_fixed_cost_yen),
            input_mgmt_fee_yen=mgmt_fee_yen,
        )
        if benchmark_mgmt_fee_estimate_yen > 0:
            benchmark_monthly_fixed_cost_yen = int(benchmark_monthly_fixed_cost_yen) + int(benchmark_mgmt_fee_estimate_yen)
            if benchmark_monthly_fixed_cost_yen_raw is not None:
                benchmark_monthly_fixed_cost_yen_raw = int(benchmark_monthly_fixed_cost_yen_raw) + int(benchmark_mgmt_fee_estimate_yen)

            # Merge into adjustments for transparency (frontend already displays adjustments when present).
            merged_adj: dict[str, Any] = {}
            if isinstance(benchmark_adjustments, dict):
                merged_adj.update(benchmark_adjustments)
            merged_adj.update(
                {
                    "management_fee_estimate_yen": int(benchmark_mgmt_fee_estimate_yen),
                    "management_fee_estimate_rate_of_rent": 0.05,
                    "management_fee_estimate_cap_yen": 20000,
                    "management_fee_estimate_note": "Benchmark dataset is rent-only; added conservative mgmt estimate (<= listing mgmt).",
                }
            )
            benchmark_adjustments = merged_adj

    # rent_delta_ratio: monthly_fixed_cost (rent+mgmt) vs benchmark monthly fixed cost.
    rent_delta_ratio = 0.0
    if benchmark_monthly_fixed_cost_yen and benchmark_monthly_fixed_cost_yen > 0:
        rent_delta_ratio = (float(monthly_fixed_cost_yen) - float(benchmark_monthly_fixed_cost_yen)) / float(
            benchmark_monthly_fixed_cost_yen
        )

    # IM assessment: compare initial_multiple to prefecture market average.
    pref_str = str(payload.get("prefecture", ""))
    im_market_avg = _IM_MARKET_AVG_BY_PREF.get(pref_str, _IM_MARKET_AVG_DEFAULT)
    foreigner_shift = float(s2.get("foreigner_adjustment", {}).get("foreign_im_shift_months", 1.0))
    effective_im_foreigner = max(0.0, initial_multiple - foreigner_shift)
    im_market_delta = initial_multiple - im_market_avg
    im_market_delta_foreigner = effective_im_foreigner - im_market_avg
    im_assessment = _im_assessment_label_ko(initial_multiple, im_market_avg)
    im_assessment_foreigner = _im_assessment_label_ko(effective_im_foreigner, im_market_avg)

    ctx: dict[str, Any] = {}
    ctx.update(payload)
    ctx.update(
        {
            "monthly_fixed_cost_yen": monthly_fixed_cost_yen,
            "building_age_years": building_age_years,
            "initial_multiple": initial_multiple,
            "benchmark_monthly_fixed_cost_yen": benchmark_monthly_fixed_cost_yen,
            "benchmark_monthly_fixed_cost_yen_raw": benchmark_monthly_fixed_cost_yen_raw,
            "benchmark_mgmt_fee_estimate_yen": benchmark_mgmt_fee_estimate_yen,
            "benchmark_confidence": benchmark_confidence,
            "benchmark_n_sources": benchmark_n_sources,
            "benchmark_matched_level": benchmark_matched_level,
            "benchmark_adjustments": benchmark_adjustments,
            "rent_delta_ratio": rent_delta_ratio,
            "im_assessment": im_assessment,
            "im_assessment_foreigner": im_assessment_foreigner,
            "initial_multiple_market_avg": im_market_avg,
            "initial_multiple_market_delta": im_market_delta,
            "initial_multiple_market_delta_foreigner": im_market_delta_foreigner,
        }
    )

    # Component scores from S2 features.
    component_features: dict[str, list[dict[str, Any]]] = {"location": [], "condition": [], "cost": []}
    for f in s2.get("features", []):
        if not isinstance(f, dict):
            continue
        comp = f.get("component")
        if comp in component_features:
            component_features[comp].append(f)

    def component_score(component: str) -> float:
        return _compute_component_score(component_features[component], ctx, s2)

    location_score = component_score("location")
    condition_score = component_score("condition")
    cost_score = component_score("cost")

    overall_score = _round(
        float(s2["weights"]["location"]) * location_score
        + float(s2["weights"]["condition"]) * condition_score
        + float(s2["weights"]["cost"]) * cost_score,
        6,
    )

    grades = {
        "location_grade": _grade_for_score(location_score, s2["grade_thresholds"]["location"]),
        "condition_grade": _grade_for_score(condition_score, s2["grade_thresholds"]["condition"]),
        "cost_grade": _grade_for_score(cost_score, s2["grade_thresholds"]["cost"]),
        "overall_grade": _grade_for_score(overall_score, s2["grade_thresholds"]["overall"]),
    }

    ctx.update(
        {
            "location_score": location_score,
            "condition_score": condition_score,
            "cost_score": cost_score,
            "overall_score": overall_score,
            **grades,
        }
    )

    # Risk flags (all matching rules; de-duplicate by risk_flag_id).
    risk_flags: list[dict[str, Any]] = []
    seen_flags: set[str] = set()
    for r in sorted(s2.get("risk_flag_rules", []), key=lambda x: int(x.get("priority", 0))):
        cond = r.get("when")
        if cond is None:
            continue
        if bool(jsonlogic_apply(cond, ctx)):
            out = r.get("outputs") or {}
            rid = out.get("risk_flag_id")
            sev = out.get("severity")
            if isinstance(rid, str) and rid not in seen_flags:
                seen_flags.add(rid)
                risk_flags.append({"risk_flag_id": rid, "severity": sev})

    # Trade-off (S2 tradeoff_rules) - compute once for internal context (C1 may choose templates independently).
    tradeoff_tag: str | None = None
    tradeoff_message_key: str | None = None
    for tr in sorted(s2.get("tradeoff_rules", []), key=lambda x: int(x.get("priority", 0))):
        cond = tr.get("when")
        if cond is None:
            continue
        if bool(jsonlogic_apply(cond, ctx)):
            out = tr.get("outputs") or {}
            tradeoff_tag = out.get("tradeoff_tag") if isinstance(out.get("tradeoff_tag"), str) else None
            tradeoff_message_key = out.get("message_key") if isinstance(out.get("message_key"), str) else None
            break
    ctx["tradeoff_tag"] = tradeoff_tag
    ctx["tradeoff_message_key"] = tradeoff_message_key

    # What-if results (S2 what_if_rules)
    what_if_results: list[dict[str, Any]] = []
    for wi in s2.get("what_if_rules", []):
        enabled_if = wi.get("enabled_if")
        if enabled_if is None:
            continue
        if not bool(jsonlogic_apply(enabled_if, ctx)):
            continue

        for action in wi.get("actions", []):
            if not isinstance(action, dict):
                continue
            target_key = action.get("target_key")
            action_type = action.get("type")
            if not isinstance(target_key, str) or not isinstance(action_type, str):
                continue

            new_payload = dict(payload)

            def _as_int(x: Any) -> int:
                if x is None:
                    return 0
                return int(x)

            # Apply action on the target key, and adjust initial_cost_total_yen if target is a breakdown fee.
            old_target = new_payload.get(target_key)
            old_target_int = _as_int(old_target)

            if action_type == "delta_yen":
                delta = int(action.get("value", 0))
                new_value = max(0, _as_int(new_payload.get(target_key)) + delta)
                new_payload[target_key] = new_value
            elif action_type == "set_zero":
                new_value = 0
                new_payload[target_key] = 0
            elif action_type == "scale":
                factor = float(action.get("value", 1.0))
                new_value = max(0, int(round(old_target_int * factor)))
                new_payload[target_key] = new_value
            else:
                continue

            # If this action touches a breakdown key, reflect delta into initial_cost_total_yen.
            if target_key != "initial_cost_total_yen":
                if target_key.endswith("_yen") and "initial_cost_total_yen" in new_payload:
                    delta_total = _as_int(new_payload.get(target_key)) - old_target_int
                    new_payload["initial_cost_total_yen"] = max(0, _as_int(new_payload["initial_cost_total_yen"]) + delta_total)

            # Re-evaluate only the values UX expects to change.
            new_monthly_fixed = int(new_payload["rent_yen"]) + int(new_payload["mgmt_fee_yen"])
            new_im = float(new_payload["initial_cost_total_yen"]) / new_monthly_fixed if new_monthly_fixed > 0 else 0.0
            new_rent_delta_ratio = (
                (float(new_monthly_fixed) - float(benchmark_monthly_fixed_cost_yen)) / float(benchmark_monthly_fixed_cost_yen)
                if benchmark_monthly_fixed_cost_yen and benchmark_monthly_fixed_cost_yen > 0
                else 0.0
            )

            new_ctx = dict(ctx)
            new_effective_im_foreigner = max(0.0, new_im - foreigner_shift)
            new_ctx.update(
                {
                    **new_payload,
                    "monthly_fixed_cost_yen": new_monthly_fixed,
                    "rent_delta_ratio": new_rent_delta_ratio,
                    "initial_multiple": new_im,
                    "initial_multiple_market_delta": new_im - im_market_avg,
                    "initial_multiple_market_delta_foreigner": new_effective_im_foreigner - im_market_avg,
                }
            )

            new_cost_score = _compute_component_score(component_features["cost"], new_ctx, s2)
            new_overall_score = _round(
                float(s2["weights"]["location"]) * location_score
                + float(s2["weights"]["condition"]) * condition_score
                + float(s2["weights"]["cost"]) * new_cost_score,
                6,
            )
            new_cost_grade = _grade_for_score(new_cost_score, s2["grade_thresholds"]["cost"])
            new_overall_grade = _grade_for_score(new_overall_score, s2["grade_thresholds"]["overall"])

            what_if_results.append(
                {
                    "id": wi.get("id"),
                    "label_ko": action.get("label_ko"),
                    "label_ja": action.get("label_ja"),
                    "initial_cost_total_yen": int(new_payload["initial_cost_total_yen"]),
                    "initial_multiple": _round(new_im, 6),
                    "cost_score": new_cost_score,
                    "overall_score": new_overall_score,
                    "cost_grade": new_cost_grade,
                    "overall_grade": new_overall_grade,
                }
            )

    # Select C1 template rule (higher priority wins).
    c1_rules = sorted(c1.get("rules", []), key=lambda x: int(x.get("priority", 0)), reverse=True)
    tpl = _select_first_rule_by_priority(c1_rules, ctx) or {}

    rendered_summary_ko = _render_placeholders(str(tpl.get("summary_ko", "")), ctx)
    evidence_bullets_ko = [_render_placeholders(str(b), ctx) for b in (tpl.get("evidence_bullets_ko") or [])]
    negotiation_ko = [_render_placeholders(str(s), ctx) for s in (tpl.get("negotiation_suggestions_ko") or [])]
    negotiation_ja = [_render_placeholders(str(s), ctx) for s in (tpl.get("negotiation_suggestions_ja") or [])]
    alt_queries_ja = [_render_placeholders(str(s), ctx) for s in (tpl.get("alternative_search_queries_ja") or [])]

    return {
        "derived": {
            "monthly_fixed_cost_yen": monthly_fixed_cost_yen,
            "building_age_years": building_age_years,
            "initial_multiple": _round(initial_multiple, 6),
            "benchmark_monthly_fixed_cost_yen": benchmark_monthly_fixed_cost_yen,
            "benchmark_monthly_fixed_cost_yen_raw": benchmark_monthly_fixed_cost_yen_raw,
            "benchmark_confidence": benchmark_confidence,
            "benchmark_n_sources": benchmark_n_sources,
            "benchmark_matched_level": benchmark_matched_level,
            "benchmark_adjustments": benchmark_adjustments,
            "benchmark_mgmt_fee_estimate_yen": int(benchmark_mgmt_fee_estimate_yen or 0),
            "live_benchmark": live_benchmark,
            "rent_delta_ratio": _round(rent_delta_ratio, 6),
            "im_assessment": im_assessment,
            "im_assessment_foreigner": im_assessment_foreigner,
            "initial_multiple_market_avg": im_market_avg,
            "initial_multiple_market_delta": _round(im_market_delta, 6),
            "initial_multiple_market_delta_foreigner": _round(im_market_delta_foreigner, 6),
        },
        "scoring": {
            "location_score": location_score,
            "condition_score": condition_score,
            "cost_score": cost_score,
            "overall_score": overall_score,
        },
        "grades": grades,
        "report": {
            "summary_ko": rendered_summary_ko,
            "evidence_bullets_ko": evidence_bullets_ko,
            "risk_flags": risk_flags,
            "negotiation_suggestions": {"ko": negotiation_ko, "ja": negotiation_ja},
            "alternative_search_queries_ja": alt_queries_ja,
            "what_if_results": what_if_results,
        },
    }

# HTTP server and entrypoint are in backend.src.server.
# This module is kept focused on evaluation logic only.
