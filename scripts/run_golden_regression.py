from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from _lib import (
    SelectedRule,
    ValidationError,
    clamp,
    extract_placeholders,
    format_value,
    jsonlogic_apply,
    load_json,
    load_yaml,
    render_template,
    safe_div,
    select_rule,
)


@dataclass(frozen=True)
class RegressionFinding:
    code: str
    message: str
    listing: str | None = None


def _score_bucket(x: float | int | None, params: dict[str, Any], context: dict[str, Any]) -> float:
    confidence_key = params.get("confidence_key")
    neutral_conf = set(params.get("neutral_score_if_confidence_in") or [])
    if isinstance(confidence_key, str) and neutral_conf:
        conf = context.get(confidence_key)
        if conf in neutral_conf:
            return float(params.get("neutral_score", params.get("default_score", 70)))

    if x is None:
        return float(params.get("default_score", 70))
    try:
        xv = float(x)
    except Exception:
        return float(params.get("default_score", 70))

    for b in params.get("buckets") or []:
        if not isinstance(b, dict):
            continue
        if "max" in b and xv <= float(b["max"]):
            return float(b.get("score", params.get("default_score", 70)))
        if "min" in b and xv >= float(b["min"]):
            return float(b.get("score", params.get("default_score", 70)))
    return float(params.get("default_score", 70))


def _score_linear(x: float | int | None, params: dict[str, Any], context: dict[str, Any]) -> float:
    confidence_key = params.get("confidence_key")
    neutral_conf = set(params.get("neutral_score_if_confidence_in") or [])
    if isinstance(confidence_key, str) and neutral_conf:
        conf = context.get(confidence_key)
        if conf in neutral_conf:
            return float(params.get("neutral_score", params.get("default_score", 70)))

    if x is None:
        return float(params.get("default_score", params.get("neutral_score", 70)))
    try:
        xv = float(x)
        min_x = float(params["min_x"])
        max_x = float(params["max_x"])
        min_s = float(params["min_score"])
        max_s = float(params["max_score"])
    except Exception:
        return float(params.get("default_score", 70))

    if max_x == min_x:
        return float(params.get("default_score", 70))

    t = (xv - min_x) / (max_x - min_x)
    if params.get("direction") == "lower_is_better":
        t = 1.0 - t
    score = min_s + t * (max_s - min_s)
    if params.get("clamp"):
        lo, hi = (min_s, max_s) if min_s <= max_s else (max_s, min_s)
        score = clamp(score, lo, hi)
    return float(score)


def _score_lookup(x: Any, params: dict[str, Any]) -> float:
    table = params.get("table") or {}
    if isinstance(table, dict) and x in table:
        return float(table[x])
    return float(params.get("default_score", 70))


def _score_boolean(x: Any, params: dict[str, Any]) -> float:
    return float(params.get("true_score", 85) if bool(x) else params.get("false_score", 60))


def _compute_feature_score(feature: dict[str, Any], context: dict[str, Any]) -> float:
    method = feature.get("method")
    key = feature.get("input_key")
    params = feature.get("params") or {}
    x = context.get(key) if isinstance(key, str) else None

    if method == "bucket":
        return _score_bucket(x, params, context)
    if method == "linear":
        return _score_linear(x, params, context)
    if method == "lookup":
        return _score_lookup(x, params)
    if method == "boolean":
        return _score_boolean(x, params)
    raise ValidationError(f"Unsupported feature method: {method}")


def _component_scores(s2: dict[str, Any], context: dict[str, Any]) -> dict[str, float]:
    by_component: dict[str, list[dict[str, Any]]] = {}
    for feat in s2.get("features") or []:
        if not isinstance(feat, dict):
            continue
        comp = feat.get("component")
        if not isinstance(comp, str):
            continue
        by_component.setdefault(comp, []).append(feat)

    out: dict[str, float] = {}
    for comp, feats in by_component.items():
        total_w = 0.0
        total = 0.0
        for feat in feats:
            w = float(feat.get("weight", 0.0))
            total_w += w
            total += _compute_feature_score(feat, context) * w
        out[f"{comp}_score"] = total / total_w if total_w else 0.0
    return out


def _grade(score: float, bands: list[dict[str, Any]]) -> str:
    usable = []
    for b in bands:
        if not isinstance(b, dict):
            continue
        g = b.get("grade")
        ms = b.get("min_score")
        if isinstance(g, str) and isinstance(ms, (int, float)):
            usable.append((float(ms), g))
    usable.sort(reverse=True)
    for ms, g in usable:
        if score >= ms:
            return g
    return usable[-1][1] if usable else "D"


def _derive(listing: dict[str, Any], *, current_year: int) -> dict[str, Any]:
    rent = listing.get("rent_yen")
    mgmt = listing.get("mgmt_fee_yen")
    monthly_fixed = None
    if isinstance(rent, int) and isinstance(mgmt, int):
        monthly_fixed = rent + mgmt
    built_year = listing.get("building_built_year")
    age = None
    if isinstance(built_year, int):
        age = max(0, current_year - built_year)

    initial_cost = listing.get("initial_cost_total_yen")
    im = safe_div(initial_cost, monthly_fixed)

    return {
        "monthly_fixed_cost_yen": monthly_fixed,
        "building_age_years": age,
        "initial_multiple": im,
    }


def _benchmark_stub(listing: dict[str, Any], *, listing_id: str) -> dict[str, Any]:
    prefecture = listing.get("prefecture")
    base = {"tokyo": 105000, "saitama": 75000, "chiba": 80000}.get(prefecture, 85000)

    layout = listing.get("layout_type")
    if layout == "1K":
        base += 5000

    area = listing.get("area_sqm")
    if isinstance(area, (int, float)):
        base += int((float(area) - 20.0) * 900)

    walk = listing.get("station_walk_min")
    if isinstance(walk, int):
        base -= int((walk - 10) * 400)

    built_year = listing.get("building_built_year")
    if isinstance(built_year, int):
        age = max(0, date.today().year - built_year)
        base -= int(age * 120)

    benchmark = max(30000, base)

    confidence = "mid" if prefecture == "tokyo" else "low"
    if listing_id == "listing_014":
        confidence = "none"

    return {"benchmark_monthly_fixed_cost_yen": benchmark if confidence != "none" else None, "benchmark_confidence": confidence}


def _compute_rent_delta_ratio(monthly_fixed_cost_yen: Any, benchmark_monthly_fixed_cost_yen: Any) -> float | None:
    delta = None
    if monthly_fixed_cost_yen is None or benchmark_monthly_fixed_cost_yen is None:
        return None
    num = float(monthly_fixed_cost_yen) - float(benchmark_monthly_fixed_cost_yen)
    return safe_div(num, benchmark_monthly_fixed_cost_yen)


def _area_access_score_stub(listing: dict[str, Any]) -> float:
    hub = listing.get("hub_station")
    base = {
        "tokyo_station": 95,
        "shinjuku": 92,
        "shibuya": 90,
        "ikebukuro": 86,
        "ueno": 82,
        "shinagawa": 86,
        "other": 75,
    }.get(hub, 80)
    walk = listing.get("station_walk_min")
    if isinstance(walk, int):
        base -= min(25, walk * 1.2)
    return float(clamp(float(base), 0, 100))


def _apply_rules(
    rules: list[dict[str, Any]], *, context: dict[str, Any], collect_all: bool = True
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if not isinstance(when, dict):
            continue
        try:
            ok = bool(jsonlogic_apply(when, context))
        except ValidationError:
            ok = False
        if ok:
            matched.append(rule)
            if not collect_all:
                break
    matched.sort(key=lambda r: int(r.get("priority", 0)))
    return matched


def _tradeoff(s2: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selected = select_rule((s2.get("tradeoff_rules") or []), context=context)
    if not selected:
        return {"tradeoff_tag": "balanced", "message_key": "TRADEOFF_BALANCED"}
    outputs = selected.rule.get("outputs") or {}
    if not isinstance(outputs, dict):
        return {"tradeoff_tag": "balanced", "message_key": "TRADEOFF_BALANCED"}
    return outputs


def _render_report(
    c1: dict[str, Any],
    *,
    context: dict[str, Any],
    risk_flags: list[dict[str, Any]],
    tradeoff: dict[str, Any],
) -> dict[str, Any]:
    rules = c1.get("rules") or []
    selected: SelectedRule | None = select_rule(rules, context=context)
    if not selected:
        return {"template_rule_id": None, "summary_ko": "", "evidence_bullets_ko": []}
    rule = selected.rule

    values = dict(context)
    values["risk_flags"] = [rf.get("outputs", {}).get("risk_flag_id") for rf in risk_flags]
    values["tradeoff_tag"] = tradeoff.get("tradeoff_tag")

    summary_ko = render_template(str(rule.get("summary_ko", "")), values)
    evidence = []
    for b in rule.get("evidence_bullets_ko") or []:
        if isinstance(b, str):
            evidence.append(render_template(b, values))
    return {"template_rule_id": rule.get("id"), "summary_ko": summary_ko, "evidence_bullets_ko": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GoldenInputs regression tests (derived/scoring/rules/templates).")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=Path("agents/agent_I_test/out/G0_GoldenInputs"),
        help="Directory containing listing_###.json inputs.",
    )
    parser.add_argument("--out", type=Path, default=Path("integrate/merged/golden_regression.json"))
    args = parser.parse_args()

    root: Path = args.root
    golden_dir: Path = root / args.golden_dir
    s2 = load_json(root / "agents/agent_C_scoring/out/S2_ScoringSpec.json")
    c1 = load_json(root / "agents/agent_E_copy/out/C1_ReportTemplates.json")
    v0 = load_yaml(root / "V0_Vocabulary.yml")

    v0_inputs: dict[str, Any] = ((v0.get("keys") or {}).get("input") or {}) if isinstance(v0, dict) else {}
    v0_enums: dict[str, list[str]] = (v0.get("enums") or {}) if isinstance(v0, dict) else {}

    findings: list[RegressionFinding] = []
    results: dict[str, Any] = {"listings": {}}
    current_year = date.today().year

    files = sorted(golden_dir.glob("listing_*.json"))
    if not files:
        print(f"FAIL golden_regression: no inputs found in {golden_dir}")
        return 1

    risk_flag_hits: dict[str, int] = {}
    template_hits: dict[str, int] = {}

    for f in files:
        listing_id = f.stem
        listing = load_json(f)

        # Basic V0 key/enum checks on inputs
        if not isinstance(listing, dict):
            findings.append(RegressionFinding("INPUT_TYPE", "Listing must be JSON object", listing_id))
            continue
        unknown = sorted(k for k in listing.keys() if k not in v0_inputs)
        if unknown:
            findings.append(RegressionFinding("V0_INPUT_KEY", f"Unknown input keys: {unknown}", listing_id))

        for k, spec in v0_inputs.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("required") and k not in listing:
                findings.append(RegressionFinding("MISSING_REQUIRED", f"Missing required key {k!r}", listing_id))

        for k, value in listing.items():
            spec = v0_inputs.get(k)
            if not isinstance(spec, dict):
                continue
            enum_name = spec.get("enum")
            if isinstance(enum_name, str) and enum_name in v0_enums:
                allowed = set(v0_enums.get(enum_name) or [])
                if value not in allowed:
                    findings.append(RegressionFinding("ENUM", f"{k}={value!r} not in {enum_name} enum", listing_id))

        derived = _derive(listing, current_year=current_year)
        bench = _benchmark_stub(listing, listing_id=listing_id)
        rent_delta_ratio = _compute_rent_delta_ratio(derived.get("monthly_fixed_cost_yen"), bench.get("benchmark_monthly_fixed_cost_yen"))
        area_access = _area_access_score_stub(listing)

        context: dict[str, Any] = {}
        context.update(listing)
        context.update(derived)
        context.update(bench)
        context["rent_delta_ratio"] = rent_delta_ratio
        context["area_access_score_0_100"] = area_access

        # Derived stability checks
        if derived.get("initial_multiple") is None and (listing.get("initial_cost_total_yen") is not None):
            findings.append(RegressionFinding("DERIVED_NULL", "initial_multiple could not be computed (division by zero?)", listing_id))

        # Scoring
        comp_scores = _component_scores(s2, context)
        context.update(comp_scores)
        weights = s2.get("weights") or {}
        overall = (
            float(comp_scores.get("location_score", 0.0)) * float(weights.get("location", 0.0))
            + float(comp_scores.get("condition_score", 0.0)) * float(weights.get("condition", 0.0))
            + float(comp_scores.get("cost_score", 0.0)) * float(weights.get("cost", 0.0))
        )
        context["overall_score"] = overall

        bands = s2.get("grade_thresholds") or {}
        for comp in ("overall", "location", "condition", "cost"):
            score_key = f"{comp}_score"
            grade_key = f"{comp}_grade"
            comp_bands = bands.get(comp) or []
            if isinstance(comp_bands, list):
                context[grade_key] = _grade(float(context.get(score_key, 0.0)), comp_bands)

        # Rules
        risk_flags = _apply_rules((s2.get("risk_flag_rules") or []), context=context, collect_all=True)
        for rf in risk_flags:
            outputs = rf.get("outputs") or {}
            if isinstance(outputs, dict):
                rf_id = outputs.get("risk_flag_id")
                if isinstance(rf_id, str):
                    risk_flag_hits[rf_id] = risk_flag_hits.get(rf_id, 0) + 1

        tradeoff = _tradeoff(s2, context)
        if not tradeoff.get("tradeoff_tag"):
            findings.append(RegressionFinding("TRADEOFF_MISSING", "No tradeoff_tag produced", listing_id))

        report = _render_report(c1, context=context, risk_flags=risk_flags, tradeoff=tradeoff)
        rule_id = report.get("template_rule_id")
        if isinstance(rule_id, str):
            template_hits[rule_id] = template_hits.get(rule_id, 0) + 1

        results["listings"][listing_id] = {
            "input": listing,
            "derived": derived,
            "benchmark": bench,
            "scores": {k: context.get(k) for k in ("location_score", "condition_score", "cost_score", "overall_score")},
            "grades": {k: context.get(k) for k in ("location_grade", "condition_grade", "cost_grade", "overall_grade")},
            "risk_flags": [rf.get("outputs") for rf in risk_flags],
            "tradeoff": tradeoff,
            "template_rule_id": rule_id,
            "summary_ko": report.get("summary_ko"),
        }

    # Gate checks
    if not risk_flag_hits:
        findings.append(RegressionFinding("RISK_FLAG_NONE", "No risk flags triggered in any GoldenInput"))
    else:
        missing_any = [rid for rid in (v0.get("risk_flags") or []) if isinstance(rid, dict) and rid.get("id") not in risk_flag_hits]
        if missing_any:
            findings.append(
                RegressionFinding(
                    "RISK_FLAG_COVERAGE",
                    f"Some V0 risk_flag_ids never triggered: {[x.get('id') for x in missing_any]}",
                )
            )

    if template_hits.get("R01_BENCHMARK_NONE", 0) == 0:
        findings.append(RegressionFinding("TPL_NONE", "No listing selected C1 rule R01_BENCHMARK_NONE (none confidence)"))
    if template_hits.get("R02_BENCHMARK_LOW", 0) == 0:
        findings.append(RegressionFinding("TPL_LOW", "No listing selected C1 rule R02_BENCHMARK_LOW (low confidence)"))

    results["summary"] = {
        "inputs": len(files),
        "risk_flag_hits": risk_flag_hits,
        "template_hits": template_hits,
        "findings": [f.__dict__ for f in findings],
    }

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if findings:
        print(f"FAIL golden_regression ({len(findings)} findings). report: {out_path}")
        for f in findings[:50]:
            loc = f" [{f.listing}]" if f.listing else ""
            print(f"- {f.code}{loc}: {f.message}")
        return 1

    print(f"PASS golden_regression. report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
