"""Bind human market approval to a passed, immutable validation report."""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from .models import parse_timestamp

REQUIRED_MARKET_GATES = frozenset((
    "observed_market_data", "request_sample", "holdout_building_sample", "holdout_day_sample", "coverage",
    "advertised_price_mdape", "baseline_improvement", "human_request_sample", "comparison_suitability",
    "direction_review_sample", "false_opposite_direction", "direction_error", "direction_review_coverage",
    "audited_unit_sample", "source_field_accuracy", "source_status_accuracy", "no_price_unit_errors",
    "residual_duplicates", "false_merges", "stability_sample", "stability_flip_rate", "thirty_day_supply", "ingestion_latency",
))


def passed_segment(entry):
    gates = entry.get("gates", {})
    return entry.get("eligible_for_approval") is True and isinstance(gates, dict) and REQUIRED_MARKET_GATES <= gates.keys() and all(value is True for value in gates.values())


def policy_digest():
    return hashlib.sha256(Path(__file__).with_name("comparison.py").read_bytes()).hexdigest()


def approved_segments(registry_path, source_ids, now):
    if registry_path is None:
        return set()
    try:
        registry_path = Path(registry_path)
        if registry_path.stat().st_size > 2_000_000:
            return set()
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if registry.get("schema_version") != "2.0" or not isinstance(registry.get("reports"), list):
            return set()
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return set()
    valid = set()
    allowed = {f"{city}/{layout}" for city in ("tokyo", "osaka", "fukuoka") for layout in ("1R", "1K", "1DK", "1LDK")}
    for entry in registry["reports"]:
        try:
            if entry.get("policy_version") != "direct-v2.0" or entry.get("approved") is not True:
                continue
            if set(entry.get("source_ids", [])) != source_ids or not source_ids:
                continue
            validated_at = parse_timestamp(entry["validated_at"])
            if not validated_at <= now < parse_timestamp(entry["expires_at"]):
                continue
            if not isinstance(entry.get("approved_by"), str) or not entry["approved_by"].strip():
                continue
            path = (registry_path.parent / entry["report_path"]).resolve()
            if not path.is_relative_to(registry_path.parent.resolve()) or not 0 < path.stat().st_size <= 2_000_000:
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry.get("report_sha256"):
                continue
            report = json.loads(raw)
            generated = parse_timestamp(report["generated_at"])
            if not generated <= validated_at or now - generated > timedelta(days=30):
                continue
            if (report.get("schema_version") != "market-validation-1.0" or report.get("report_kind") != "market"
                    or report.get("policy_version") != "direct-v2.0" or report.get("policy_sha256") != policy_digest()
                    or set(report.get("source_ids", [])) != source_ids or report.get("eligibility", {}).get("passed") is not True):
                continue
            requested = set(entry.get("segments", [])) & allowed
            eligible = set(report.get("eligible_segments", []))
            valid.update(segment for segment in requested & eligible if passed_segment(report.get("segments", {}).get(segment, {})))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    return valid
