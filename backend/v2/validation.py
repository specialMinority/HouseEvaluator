"""Offline, reproducible market evidence. Never grants runtime approval.

Held-out advertised rents are surrogate outcomes, not fair-rent ground truth.
All inputs are explicit local files; no network, sampling, or registry writes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from statistics import median

from .comparison import POLICY, compare
from .models import ValidationError, iso_timestamp, parse_timestamp, validate_snapshot
from .store import local_path

REPORT_SCHEMA = "market-validation-1.0"
MAX_BYTES = 20 * 1024 * 1024
THRESHOLDS = {
    "version": "release-proposal-1.0", "min_requests_per_segment": 100,
    "min_audited_units_per_segment": 200, "min_reviewed_requests_per_segment": 100,
    "min_direction_reviews": 30, "min_stability_comparisons": 30,
    "min_target_buildings": 30, "min_holdout_days": 7,
    "min_supply_days": 30, "coverage_min": 0.70, "mdape_max": 0.10,
    "baseline_relative_improvement_min": 0.15, "comparability_min": 0.90,
    "field_accuracy_min": 0.98, "status_accuracy_min": 0.95,
    "residual_duplicate_max": 0.02, "false_merge_max": 0.02,
    "opposite_direction_max": 0.05, "direction_error_max": 0.05,
    "direction_review_coverage_min": 0.90, "stability_flip_max": 0.05,
    "ingestion_p95_seconds_max": 900,
}
_SUBJECT = ("city", "municipality", "station_id", "station_name", "unit_id", "building_id", "layout", "structure", "property_type", "contract_type", "furnished", "area_sqm", "built_year", "walk_min", "floor", "orientation", "bathroom_separate", "rent_yen", "mgmt_fee_yen")
_MANIFEST_FIELDS = {"schema_version", "validation_id", "generated_at", "protocol_frozen_at", "development_cutoff", "holdout_start", "holdout_end", "snapshots", "holdouts_path", "human_reviews_path", "supply_history_path"}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def policy_sha256():
    return _digest(Path(__file__).with_name("comparison.py").read_bytes())


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_):
    raise ValidationError("nonfinite JSON number")


def read_json(path):
    """Read a bounded local JSON file, rejecting duplicate/non-finite values."""
    path = local_path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError("validation input exceeds 20 MiB")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique, parse_constant=_constant), _digest(raw)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be nonempty text")
    return value.strip()


def _array(value, name):
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be an array")
    return value


def _load(path, base, hashes):
    if not isinstance(path, str) or not path:
        raise ValidationError("input path is required")
    # Reject URLs/UNC before joining. Relative files must stay inside the bundle.
    if "://" in path or path.startswith(("\\\\", "//")) or Path(path).is_absolute() or path.lower().startswith("file:"):
        raise ValidationError("validation files must be relative local paths inside the evidence bundle")
    target = local_path(base / path)
    if not target.is_relative_to(base):
        raise ValidationError("validation input escapes evidence bundle")
    value, digest = read_json(target)
    key = target.relative_to(base).as_posix()
    if key in hashes and hashes[key] != digest:
        raise ValidationError("validation input changed during the run")
    hashes[key] = digest
    return value


def _quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _baseline(rows, subject, now):
    by_unit = defaultdict(list)
    for row in rows:
        if any(row[field] != subject[field] for field in ("city", "municipality", "layout", "property_type", "contract_type")):
            continue
        if row["status"] != "active" or row["mgmt_fee_yen"] is None or not row["status_verified_at"]:
            continue
        if not timedelta(0) <= now - parse_timestamp(row["status_verified_at"]) <= timedelta(hours=24):
            continue
        by_unit[row["unit_id"]].append(row["rent_yen"] + row["mgmt_fee_yen"])
    prices = [values[0] for values in by_unit.values() if len(set(values)) == 1]
    return median(prices) if prices else None


def _opposite(left, right):
    return {left, right} == {"higher", "lower"}


def _supply_metrics(history, snapshots, source_ids, end, generated):
    by_day = defaultdict(set)
    latency, errors, seen = [], 0, set()
    if history is not None:
        for item in _array(history.get("events"), "supply history events"):
            if not isinstance(item, dict):
                raise ValidationError("supply event must be an object")
            source_id = _text(item.get("source_id"), "source_id")
            observed = parse_timestamp(item.get("observed_at"))
            if source_id not in source_ids or observed > generated:
                raise ValidationError("supply event source or timestamp is invalid")
            if item.get("status") == "error":
                errors += 1
                continue
            if item.get("status") != "success":
                raise ValidationError("supply event status must be success or error")
            snapshot = snapshots.get(item.get("snapshot_id"))
            if snapshot is None or snapshot["payload"]["source"]["source_id"] != source_id:
                raise ValidationError("supply success requires a matching archived snapshot")
            if item["snapshot_id"] in seen:
                raise ValidationError("one snapshot may contribute only one successful supply event")
            seen.add(item["snapshot_id"])
            if observed < snapshot["available_at"]:
                raise ValidationError("snapshot cannot be applied before availability")
            by_day[source_id].add(observed.date())
            latency.append((observed - snapshot["available_at"]).total_seconds())
    # Thirty distinct consecutive calendar days ending at the final holdout day.
    expected = {end.date() - timedelta(days=i) for i in range(THRESHOLDS["min_supply_days"])}
    coverage = {source: len(expected & by_day[source]) for source in sorted(source_ids)}
    return {"required_days": sorted(day.isoformat() for day in expected), "successful_days_by_source": coverage,
            "consecutive_days_passed": bool(source_ids) and all(count == 30 for count in coverage.values()),
            "ingestion_p95_seconds": _quantile(latency, 0.95), "successful_events": len(latency), "reported_error_events": errors}


def validate_market(manifest, *, base_dir, smoke=False):
    """Return evidence report without approving segments or changing any input.

    ``generated_at`` is explicit, making identical bundles deterministic. Smoke
    requires synthetic snapshots and its eligibility is unconditionally false.
    """
    if type(smoke) is not bool or not isinstance(manifest, dict) or set(manifest) - _MANIFEST_FIELDS:
        raise ValidationError("invalid validation manifest")
    if manifest.get("schema_version") != "2.0":
        raise ValidationError("manifest schema_version must be 2.0")
    validation_id = _text(manifest.get("validation_id"), "validation_id")
    clocks = {key: parse_timestamp(manifest.get(key)) for key in ("generated_at", "protocol_frozen_at", "development_cutoff", "holdout_start", "holdout_end")}
    if not clocks["protocol_frozen_at"] <= clocks["development_cutoff"] < clocks["holdout_start"] <= clocks["holdout_end"] <= clocks["generated_at"]:
        raise ValidationError("require frozen protocol <= development cutoff < holdout start <= holdout end <= generated_at")
    base = local_path(base_dir)
    hashes, snapshots, source_ids = {}, {}, set()
    expected_kind = "synthetic" if smoke else "observed"
    for entry in _array(manifest.get("snapshots"), "snapshots"):
        if not isinstance(entry, dict) or set(entry) != {"path", "available_at"}:
            raise ValidationError("snapshot entries require only path and available_at")
        available = parse_timestamp(entry["available_at"])
        if available > clocks["generated_at"]:
            raise ValidationError("snapshot availability is in the future")
        raw = _load(entry["path"], base, hashes)
        payload = validate_snapshot(raw, now=clocks["generated_at"])
        if parse_timestamp(payload["as_of"]) > available:
            raise ValidationError("snapshot as_of is after its claimed availability")
        if payload["source"]["data_kind"] != expected_kind:
            raise ValidationError("market reports require observed data; smoke reports require synthetic data")
        identity = payload["snapshot_id"]
        if identity in snapshots:
            raise ValidationError("duplicate snapshot_id in validation bundle")
        snapshots[identity] = {"payload": payload, "available_at": available}
        source_ids.add(payload["source"]["source_id"])
    by_source_time = {}
    for entry in snapshots.values():
        payload = entry["payload"]
        key = (payload["source"]["source_id"], payload["as_of"])
        if key in by_source_time:
            raise ValidationError("conflicting source snapshots at the same as_of")
        by_source_time[key] = payload["snapshot_id"]

    holdouts = _load(manifest.get("holdouts_path"), base, hashes)
    requests, ids, buildings, units = [], set(), set(), set()
    for item in _array(holdouts.get("requests"), "holdout requests"):
        if not isinstance(item, dict) or set(item) != {"request_id", "evaluated_at", "reference_snapshot_id", "reference_observation_id"}:
            raise ValidationError("holdout must reference one archived advertised observation")
        request_id = _text(item["request_id"], "request_id")
        if request_id in ids:
            raise ValidationError("duplicate holdout request_id")
        ids.add(request_id)
        evaluated = parse_timestamp(item["evaluated_at"])
        if not clocks["holdout_start"] <= evaluated <= clocks["holdout_end"]:
            raise ValidationError("holdout evaluation is outside the frozen time split")
        reference = snapshots.get(item["reference_snapshot_id"])
        if reference is None or reference["available_at"] > evaluated:
            raise ValidationError("holdout reference was not available at evaluation time")
        reference_source = reference["payload"]["source"]["source_id"]
        available_references = [entry for entry in snapshots.values() if entry["available_at"] <= evaluated and entry["payload"]["source"]["source_id"] == reference_source]
        latest_reference = max(available_references, key=lambda entry: parse_timestamp(entry["payload"]["as_of"]))
        if latest_reference["payload"]["snapshot_id"] != item["reference_snapshot_id"]:
            raise ValidationError("holdout reference must use the latest available source snapshot")
        matches = [row for row in reference["payload"]["listings"] if row["observation_id"] == item["reference_observation_id"]]
        if len(matches) != 1:
            raise ValidationError("holdout reference observation must exist exactly once")
        row = matches[0]
        if row["unit_id"] in units:
            raise ValidationError("repeated held-out canonical unit cannot inflate the request sample")
        units.add(row["unit_id"])
        if row["mgmt_fee_yen"] is None or row["status"] != "active" or row["status_verified_at"] is None or evaluated - parse_timestamp(row["status_verified_at"]) > timedelta(hours=24):
            raise ValidationError("holdout target needs a fresh active advertised price including management")
        buildings.add(row["building_id"])
        requests.append({"request_id": request_id, "evaluated_at": evaluated, "subject": {field: row[field] for field in _SUBJECT}})

    rows = []
    for item in sorted(requests, key=lambda value: value["request_id"]):
        evaluated, subject = item["evaluated_at"], item["subject"]
        current = {}
        for entry in snapshots.values():
            payload = entry["payload"]
            if entry["available_at"] > evaluated:
                continue
            source_id = payload["source"]["source_id"]
            if source_id not in current or parse_timestamp(payload["as_of"]) > parse_timestamp(current[source_id]["as_of"]):
                current[source_id] = payload
        sources = [{**payload["source"], "as_of": payload["as_of"], "snapshot_id": payload["snapshot_id"], "complete": True} for payload in current.values()]
        observations = [row for payload in current.values() for row in payload["listings"] if row["building_id"] not in buildings]
        selected_snapshot_ids = sorted(payload["snapshot_id"] for payload in current.values())
        version = _digest(_canonical(selected_snapshot_ids).encode())
        segment = f"{subject['city']}/{subject['layout']}"
        # Offline counterfactual only: exercise proposed judgments, never enable them at runtime.
        result = compare(subject, observations, now=evaluated, sources=sources, snapshot_version=version, is_demo=smoke, validated_segments={segment})
        truth = subject["rent_yen"] + subject["mgmt_fee_yen"]
        baseline = _baseline(observations, subject, evaluated)
        benchmark = result["benchmark_yen"]
        comparable = result["status"] == "comparable"
        perturbations = Counter()
        if comparable:
            # Deterministic source removal and building-block jackknife. No random row split.
            removals = [("source_id", source) for source in sorted({row["source_id"] for row in observations})]
            removals += [("building_id", building) for building in sorted({row["building_id"] for row in result["comparables"]})]
            for field, remove in removals:
                reduced = [row for row in observations if row[field] != remove]
                check = compare(subject, reduced, now=evaluated, sources=sources, snapshot_version=version, validated_segments={segment})
                perturbations["total"] += 1
                if check["status"] == "comparable":
                    perturbations["eligible"] += 1
                    perturbations["opposite_flips"] += int(_opposite(result["judgment"], check["judgment"]))
                else:
                    perturbations["abstained"] += 1
        rows.append({"request_id": item["request_id"], "segment": segment, "evaluated_at": iso_timestamp(evaluated),
                     "target_building_id": subject["building_id"], "target_unit_id": subject["unit_id"], "station_id": subject["station_id"], "structure": subject["structure"],
                     "contract_type": subject["contract_type"], "area_sqm": subject["area_sqm"], "built_year": subject["built_year"],
                     "selected_snapshot_ids": selected_snapshot_ids, "status": result["status"], "reason_codes": result["reason_codes"],
                     "sample": result["sample"], "matching": result["matching"], "hypothetical_judgment": result["judgment"],
                     "reference_advertised_yen": truth, "benchmark_yen": benchmark, "baseline_yen": baseline,
                     "absolute_percentage_error": abs(benchmark - truth) / truth if comparable else None,
                     "baseline_absolute_percentage_error": abs(baseline - truth) / truth if comparable and baseline is not None else None,
                     "excluded_holdout_building_observations": sum(row["building_id"] in buildings for payload in current.values() for row in payload["listings"]),
                     "comparison_unit_ids": [row["unit_id"] for row in result["comparables"]], "stability": dict(perturbations)})

    reviews = _load(manifest["human_reviews_path"], base, hashes) if manifest.get("human_reviews_path") else {"reviews": [], "unit_audits": []}
    human = _review_metrics(reviews, rows, snapshots, clocks["generated_at"])
    history = _load(manifest["supply_history_path"], base, hashes) if manifest.get("supply_history_path") else None
    supply = _supply_metrics(history, snapshots, source_ids, clocks["holdout_end"], clocks["generated_at"])
    segments = {}
    for segment in sorted({row["segment"] for row in rows}):
        subset = [row for row in rows if row["segment"] == segment]
        errors = [row["absolute_percentage_error"] for row in subset if row["absolute_percentage_error"] is not None]
        paired = [row for row in subset if row["baseline_absolute_percentage_error"] is not None]
        baseline_error = median([row["baseline_absolute_percentage_error"] for row in paired]) if paired else None
        paired_error = median([row["absolute_percentage_error"] for row in paired]) if paired else None
        improvement = (baseline_error - paired_error) / baseline_error if baseline_error else None
        stability = Counter()
        for row in subset:
            stability.update(row["stability"])
        metrics = {"request_count": len(subset), "target_building_count": len({row["target_building_id"] for row in subset}),
                   "time_day_count": len({row["evaluated_at"][:10] for row in subset}), "station_counts": dict(Counter(row["station_id"] for row in subset)),
                   "structure_counts": dict(Counter(str(row["structure"]) for row in subset)), "status_counts": dict(Counter(row["status"] for row in subset)),
                   "contract_type_counts": dict(Counter(row["contract_type"] for row in subset)),
                   "comparable_count": len(errors), "coverage": len(errors) / len(subset), "abstention_rate": 1 - len(errors) / len(subset),
                   "mdape": median(errors) if errors else None, "p90_ape": _quantile(errors, 0.9),
                   "baseline_paired_count": len(paired), "baseline_mdape": baseline_error, "baseline_relative_improvement": improvement,
                   "stability": {**dict(stability), "opposite_flip_rate": _rate(stability["opposite_flips"], stability["eligible"])},
                   "human": human.get(segment, _empty_human())}
        gates = _gates(metrics, supply, smoke)
        segments[segment] = {"metrics": metrics, "gates": gates, "eligible_for_approval": all(gates.values())}
    eligible = [segment for segment, value in segments.items() if value["eligible_for_approval"]]
    reasons = []
    if smoke:
        reasons.append("synthetic_smoke_never_eligible")
    if not rows:
        reasons.append("no_holdout_requests")
    if not eligible:
        reasons.append("no_segment_meets_evidence_gates")
    return {"schema_version": REPORT_SCHEMA, "validation_id": validation_id, "report_kind": "synthetic_smoke" if smoke else "market",
            "generated_at": iso_timestamp(clocks["generated_at"]), "policy_version": POLICY["version"], "policy_sha256": policy_sha256(),
            "source_ids": sorted(source_ids), "input_hashes": {**dict(sorted(hashes.items())), "manifest_canonical_sha256": _digest(_canonical(manifest).encode())},
            "thresholds": THRESHOLDS.copy(), "split": {**{key: iso_timestamp(value) for key, value in clocks.items() if key != "generated_at"},
                "mechanism": "frozen chronological holdout; global heldout-building exclusion; latest available snapshot per source",
                "heldout_building_count": len(buildings), "heldout_building_ids": sorted(buildings), "random_row_split": False},
            "supply": supply, "segments": segments, "eligible_segments": eligible,
            "eligibility": {"passed": bool(eligible) and not smoke, "reasons": reasons}, "requests": rows,
            "limitations": ["Advertised-price prediction error is a surrogate, not fair-rent truth or contract-price accuracy.",
                "Thresholds are prerelease proposals; minimum per-segment counts conservatively extend the product plan's city-level counts.",
                "Protocol freeze dates and source permissions are operator attestations; this runner cannot independently authenticate them.",
                "Eligibility does not approve a registry, prove supply contracts, or complete production readiness.",
                "Stability uses deterministic source removal and building jackknife, not a statistical confidence interval.",
                "Human unknowns remain in denominators; no missing evidence is automatically marked correct."]}


def _empty_human():
    return {"reviewed_requests": 0, "candidate_reviews": 0, "comparability_rate": None, "direction_reviews": 0,
            "opposite_direction_rate": None, "direction_error_rate": None, "direction_review_coverage": None,
            "reviewer_disagreement_requests": 0, "audited_units": 0,
            "field_accuracy": None, "status_accuracy": None, "price_unit_error_count": None,
            "residual_duplicate_rate": None, "false_merge_rate": None, "audit_unknown_count": 0}


def _review_metrics(payload, rows, snapshots, generated):
    if not isinstance(payload, dict) or set(payload) - {"reviews", "unit_audits"}:
        raise ValidationError("human reviews must contain reviews and unit_audits")
    by_request = {row["request_id"]: row for row in rows}
    groups = defaultdict(lambda: {"reviews": [], "audits": [], "audit_ids": set()})
    seen = set()
    for review in _array(payload.get("reviews", []), "reviews"):
        if not isinstance(review, dict):
            raise ValidationError("review must be an object")
        request_id = review.get("request_id")
        reviewer = _text(review.get("reviewer_id"), "reviewer_id")
        reviewed = parse_timestamp(review.get("reviewed_at"))
        row = by_request.get(request_id)
        if row is None or not parse_timestamp(row["evaluated_at"]) <= reviewed <= generated:
            raise ValidationError("human review target or timestamp invalid")
        key = (request_id, reviewer)
        if key in seen:
            raise ValidationError("duplicate reviewer/request cannot increase sample size")
        seen.add(key)
        candidate_ids = set(row["comparison_unit_ids"])
        labels = _array(review.get("candidates"), "review candidates")
        label_ids = []
        for label in labels:
            if not isinstance(label, dict) or set(label) != {"unit_id", "acceptable"} or label["acceptable"] is not None and type(label["acceptable"]) is not bool:
                raise ValidationError("candidate review requires unit_id and boolean/null acceptable")
            label_ids.append(_text(label["unit_id"], "unit_id"))
        if len(label_ids) != len(set(label_ids)) or set(label_ids) != candidate_ids:
            raise ValidationError("review must label every returned candidate exactly once")
        expected = review.get("expected_judgment")
        if expected not in {"higher", "similar", "lower", "unknown"}:
            raise ValidationError("unsupported expected_judgment")
        groups[row["segment"]]["reviews"].append({**review, "actual_judgment": row["hypothetical_judgment"]})
    audited = set()
    for audit in _array(payload.get("unit_audits", []), "unit audits"):
        if not isinstance(audit, dict):
            raise ValidationError("unit audit must be an object")
        _text(audit.get("reviewer_id"), "reviewer_id")
        reviewed = parse_timestamp(audit.get("reviewed_at"))
        snapshot = snapshots.get(audit.get("snapshot_id"))
        if snapshot is None or not snapshot["available_at"] <= reviewed <= generated:
            raise ValidationError("unit audit snapshot or timestamp invalid")
        matches = [row for row in snapshot["payload"]["listings"] if row["observation_id"] == audit.get("observation_id")]
        if len(matches) != 1:
            raise ValidationError("audited observation missing")
        row = matches[0]
        if row["unit_id"] in audited:
            raise ValidationError("one audited canonical unit may contribute only once")
        audited.add(row["unit_id"])
        for field in ("core_fields_correct", "status_correct", "price_unit_error", "residual_duplicate", "false_merge"):
            if field not in audit or audit[field] is not None and type(audit[field]) is not bool:
                raise ValidationError("audit outcomes must be boolean or null")
        groups[f"{row['city']}/{row['layout']}"]["audits"].append(audit)
    metrics = {}
    for segment, group in groups.items():
        reviews, audits = group["reviews"], group["audits"]
        labels = [label for review in reviews for label in review["candidates"]]
        directions = [review for review in reviews if review["actual_judgment"] is not None and review["expected_judgment"] != "unknown"]
        comparable_reviews = [review for review in reviews if review["actual_judgment"] is not None]
        directions_by_request = defaultdict(set)
        for review in reviews:
            directions_by_request[review["request_id"]].add(review["expected_judgment"])
        count = len(audits)
        metrics[segment] = {"reviewed_requests": len({review["request_id"] for review in reviews}),
            "candidate_reviews": len(labels), "comparability_rate": _rate(sum(label["acceptable"] is True for label in labels), len(labels)),
            "direction_reviews": len({review["request_id"] for review in directions}),
            "opposite_direction_rate": _rate(sum(_opposite(review["actual_judgment"], review["expected_judgment"]) for review in directions), len(directions)),
            "direction_error_rate": _rate(sum(review["actual_judgment"] != review["expected_judgment"] for review in directions), len(directions)),
            "direction_review_coverage": _rate(len(directions), len(comparable_reviews)),
            "reviewer_disagreement_requests": sum(len(values) > 1 for values in directions_by_request.values()),
            "audited_units": count, "field_accuracy": _rate(sum(audit["core_fields_correct"] is True for audit in audits), count),
            "status_accuracy": _rate(sum(audit["status_correct"] is True for audit in audits), count),
            "price_unit_error_count": sum(audit["price_unit_error"] is not False for audit in audits) if count else None,
            "residual_duplicate_rate": _rate(sum(audit["residual_duplicate"] is not False for audit in audits), count),
            "false_merge_rate": _rate(sum(audit["false_merge"] is not False for audit in audits), count),
            "audit_unknown_count": sum(value is None for audit in audits for key, value in audit.items() if key in {"core_fields_correct", "status_correct", "price_unit_error", "residual_duplicate", "false_merge"})}
    return metrics


def _gates(metrics, supply, smoke):
    human, stability = metrics["human"], metrics["stability"]
    def at_least(value, threshold):
        return value is not None and value >= threshold
    def at_most(value, threshold):
        return value is not None and value <= threshold
    return {"observed_market_data": not smoke,
        "request_sample": metrics["request_count"] >= THRESHOLDS["min_requests_per_segment"],
        "holdout_building_sample": metrics["target_building_count"] >= THRESHOLDS["min_target_buildings"],
        "holdout_day_sample": metrics["time_day_count"] >= THRESHOLDS["min_holdout_days"],
        "coverage": at_least(metrics["coverage"], THRESHOLDS["coverage_min"]),
        "advertised_price_mdape": at_most(metrics["mdape"], THRESHOLDS["mdape_max"]),
        "baseline_improvement": at_least(metrics["baseline_relative_improvement"], THRESHOLDS["baseline_relative_improvement_min"]),
        "human_request_sample": human["reviewed_requests"] >= THRESHOLDS["min_reviewed_requests_per_segment"],
        "comparison_suitability": at_least(human["comparability_rate"], THRESHOLDS["comparability_min"]),
        "direction_review_sample": human["direction_reviews"] >= THRESHOLDS["min_direction_reviews"],
        "false_opposite_direction": at_most(human["opposite_direction_rate"], THRESHOLDS["opposite_direction_max"]),
        "direction_error": at_most(human["direction_error_rate"], THRESHOLDS["direction_error_max"]),
        "direction_review_coverage": at_least(human["direction_review_coverage"], THRESHOLDS["direction_review_coverage_min"]),
        "audited_unit_sample": human["audited_units"] >= THRESHOLDS["min_audited_units_per_segment"],
        "source_field_accuracy": at_least(human["field_accuracy"], THRESHOLDS["field_accuracy_min"]),
        "source_status_accuracy": at_least(human["status_accuracy"], THRESHOLDS["status_accuracy_min"]),
        "no_price_unit_errors": human["price_unit_error_count"] == 0,
        "residual_duplicates": at_most(human["residual_duplicate_rate"], THRESHOLDS["residual_duplicate_max"]),
        "false_merges": at_most(human["false_merge_rate"], THRESHOLDS["false_merge_max"]),
        "stability_sample": stability.get("eligible", 0) >= THRESHOLDS["min_stability_comparisons"],
        "stability_flip_rate": at_most(stability.get("opposite_flip_rate"), THRESHOLDS["stability_flip_max"]),
        "thirty_day_supply": supply["consecutive_days_passed"],
        "ingestion_latency": at_most(supply["ingestion_p95_seconds"], THRESHOLDS["ingestion_p95_seconds_max"])}


def render_markdown(report):
    """Human-readable companion to the complete JSON evidence report."""
    def shown(value):
        return "미측정" if value is None else f"{value:.2%}"
    lines = ["# HouseEvaluator 시장 검증 보고서", "", f"- 검증 ID: `{report['validation_id']}`",
             f"- 종류: `{report['report_kind']}`", f"- 기준 시각: `{report['generated_at']}`",
             f"- 정책: `{report['policy_version']}` / SHA256 `{report['policy_sha256']}`",
             "- 모집가격 예측 오차를 측정합니다. 적정 월세나 실제 계약가격의 정답 정확도가 아닙니다.",
             "- 이 보고서는 운영 승인 파일을 변경하지 않습니다.", "",
             "| 구간 | 요청 | 비교 가능률 | 보류율 | MdAPE | P90 APE | 기준선 개선 | 승인 검토 자격 |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for segment, entry in report["segments"].items():
        m = entry["metrics"]
        lines.append(f"| {segment} | {m['request_count']} | {shown(m['coverage'])} | {shown(m['abstention_rate'])} | {shown(m['mdape'])} | {shown(m['p90_ape'])} | {shown(m['baseline_relative_improvement'])} | {'충족' if entry['eligible_for_approval'] else '미충족'} |")
    if not report["segments"]:
        lines.extend(["", "검증 요청이 없어 모든 시장 승인 판단을 보류합니다."])
    for segment, entry in report["segments"].items():
        lines.extend(["", f"## {segment} 미충족 기준", ""])
        lines += [f"- {name}" for name, passed in entry["gates"].items() if not passed] or ["- 없음. 별도 운영자 승인과 공급 권리 확인이 필요합니다."]
    lines.extend(["", "## 재현과 한계", "", f"- 분할: {report['split']['mechanism']}",
                  f"- 보류 건물 수: {report['split']['heldout_building_count']}",
                  f"- 공급 관찰: `{json.dumps(report['supply']['successful_days_by_source'], ensure_ascii=False)}` / 각 공급원 연속 30일 필요",
                  "- 입력 파일별 SHA256, 요청별 사용 스냅숏·완화 이력·비교 수·기준선·오차·보류 사유는 동반 JSON에 있습니다."])
    lines += [f"- {limitation}" for limitation in report["limitations"]]
    return "\n".join(lines) + "\n"
