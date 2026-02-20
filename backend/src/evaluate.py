from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Final

from backend.src.benchmark_loader import load_or_build_benchmark_index
from backend.src.benchmark_matcher import match_benchmark_rent
from backend.src.rules.jsonlogic import apply as jsonlogic_apply


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


def _im_assessment_label(im: float, market_avg: float) -> str:
    """Classify initial_multiple relative to prefecture market average."""
    delta = im - market_avg
    if delta < -1.5:
        return "매우 낮음(시세보다 크게 저렴)"
    if delta < -0.5:
        return "낮음(시세 이하)"
    if delta < 0.5:
        return "평균(시세 수준)"
    if delta < 1.5:
        return "다소 높음(시세 초과)"
    return "높음(시세 크게 초과)"


class SpecError(ValueError):
    pass


def _im_assessment_label_ko(im: float, market_avg: float) -> str:
    """Korean label for initial_multiple vs prefecture market average (months)."""
    delta = im - market_avg
    if delta < -1.5:
        return "매우 낮음(시세보다 크게 저렴)"
    if delta < -0.5:
        return "낮음(시세 이하)"
    if delta < 0.5:
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


def _grade_for_score(score: float, bands: list[dict[str, Any]]) -> str:
    # bands are expected to be 4 entries with min_score, grade.
    # Choose the highest grade whose min_score <= score.
    best_grade = "D"
    best_min = -1.0
    for b in bands:
        g = b.get("grade")
        m = b.get("min_score")
        if isinstance(g, str) and isinstance(m, (int, float)) and score >= float(m) and float(m) >= best_min:
            best_grade = g
            best_min = float(m)
    return best_grade


def _score_bucket(value: float, params: dict[str, Any]) -> float:
    buckets = params.get("buckets") or []
    for b in buckets:
        if not isinstance(b, dict):
            continue
        if "max" in b and isinstance(b["max"], (int, float)) and value <= float(b["max"]):
            return float(b["score"])
        if "min" in b and isinstance(b["min"], (int, float)) and value >= float(b["min"]):
            return float(b["score"])
    return float(params.get("default_score", 70))


def _score_linear(value: float, params: dict[str, Any]) -> float:
    min_x = float(params["min_x"])
    max_x = float(params["max_x"])
    min_score = float(params["min_score"])
    max_score = float(params["max_score"])
    clamp = bool(params.get("clamp", False))
    direction = params.get("direction", "higher_is_better")

    if max_x == min_x:
        return float(params.get("default_score", 70))

    if direction == "higher_is_better":
        t = (value - min_x) / (max_x - min_x)
    else:
        t = (max_x - value) / (max_x - min_x)

    score = min_score + t * (max_score - min_score)
    if clamp:
        score = max(min_score, min(max_score, score))
    return float(score)


def _feature_score(feature: dict[str, Any], ctx: dict[str, Any], s2: dict[str, Any]) -> float | None:
    input_key = feature["input_key"]
    method = feature["method"]
    params = feature.get("params") or {}

    confidence_key = params.get("confidence_key")
    if isinstance(confidence_key, str):
        conf = ctx.get(confidence_key)
        neutral_list = params.get("neutral_score_if_confidence_in")
        if isinstance(neutral_list, list) and conf in neutral_list:
            return float(params.get("neutral_score", 70))

    raw_value = ctx.get(input_key)
    if raw_value is None:
        # Input not in ctx → return None so component_score can renormalize weights.
        # This prevents ghost features (e.g. area_access_score_0_100 which users never input)
        # from artificially capping the score via a fixed default.
        return None

    if method == "boolean":
        return float(params["true_score"] if bool(raw_value) else params["false_score"])

    if method == "lookup":
        table = params.get("table") or {}
        if isinstance(table, dict):
            hit = table.get(raw_value)
            if isinstance(hit, (int, float)):
                return float(hit)
        return float(params.get("default_score", 70))

    if method == "bucket":
        value = float(raw_value)
        if params.get("apply_foreigner_adjustment") == "im_shift_months":
            shift = float(s2.get("foreigner_adjustment", {}).get("foreign_im_shift_months", 0.0))
            value = max(0.0, value - shift)
        return _score_bucket(value, params)

    if method == "linear":
        return _score_linear(float(raw_value), params)

    raise SpecError(f"Unsupported feature method: {method}")


def _select_first_rule_by_priority(rules: list[dict[str, Any]], ctx: dict[str, Any]) -> dict[str, Any] | None:
    # Priority: higher number wins in C1; lower number wins in S2 tradeoff/risk? We'll sort outside when needed.
    for r in rules:
        cond = r.get("when")
        if cond is None:
            continue
        try:
            if bool(jsonlogic_apply(cond, ctx)):
                return r
        except Exception:
            continue
    return None


def _compute_component_score(component_feats: list[dict[str, Any]], ctx: dict[str, Any], s2: dict[str, Any]) -> float:
    """Compute a weighted average component score with renormalization for absent features."""
    all_weight = sum(float(f.get("weight", 0.0)) for f in component_feats)
    avail_weight = 0.0
    total = 0.0
    for f in component_feats:
        w = float(f.get("weight", 0.0))
        fs = _feature_score(f, ctx, s2)
        if fs is None:
            continue
        total += w * fs
        avail_weight += w
    if avail_weight <= 0.0:
        return _round(70.0, 6)
    scale = all_weight / avail_weight if all_weight > 0.0 else 1.0
    return _round(total * scale, 6)


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

    benchmark_index = benchmark_index_override if benchmark_index_override is not None else rt.benchmark_index
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

    benchmark_monthly_fixed_cost_yen = bm.benchmark_rent_yen  # Spec prompt allows rent-only benchmark.
    benchmark_monthly_fixed_cost_yen_raw = bm.benchmark_rent_yen_raw
    benchmark_confidence = bm.benchmark_confidence
    benchmark_n_sources = bm.benchmark_n_sources
    benchmark_matched_level = bm.matched_level
    benchmark_adjustments = bm.adjustments_applied

    # rent_delta_ratio: monthly_fixed_cost (rent+mgmt) vs rent-only benchmark.
    # Using monthly_fixed_cost_yen so that mgmt_fee is included in the cost comparison.
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


class _ApiHandler(SimpleHTTPRequestHandler):
    server_version = "wh-eval/0.1"

    def __init__(self, *args, **kwargs):  # noqa: ANN002,ANN003 (SimpleHTTPRequestHandler signature)
        # Serve static files from the repo root so the frontend can fetch specs/benchmark data
        # via same-origin paths (e.g. /spec_bundle_v0.1.1/...).
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
        # Default UX: open the frontend app at /frontend/ when hitting the service root.
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/frontend/")
            self.end_headers()
            return
        return super().do_GET()

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
        if self.path != "/api/evaluate":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise InputValidationError("Request body must be a JSON object")
            result = evaluate(payload)
            self._send_json(200, result)
        except InputValidationError as e:
            self._send_json(400, {"error": "bad_request", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": "internal_error", "message": str(e)})


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    httpd = HTTPServer((host, port), _ApiHandler)
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    serve(host=host, port=port)
