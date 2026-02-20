"""Scoring engine: pure functions for computing feature and component scores.

All functions here are stateless and depend only on spec JSON structures.
They are deliberately kept separate from evaluate.py (orchestration) and
server.py (HTTP transport) to maintain single-responsibility.
"""
from __future__ import annotations

from typing import Any

from backend.src.rules.jsonlogic import apply as jsonlogic_apply


class SpecError(ValueError):
    pass


def _round(x: float, ndigits: int = 6) -> float:
    return float(round(float(x), ndigits))


def _grade_for_score(score: float, bands: list[dict[str, Any]]) -> str:
    """Return the highest grade whose min_score <= score."""
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
    """Return feature score, or None if the input key is absent (triggers weight renorm)."""
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
    """Weighted average component score with weight renormalization for absent features."""
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
