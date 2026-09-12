"""Fixture-only evidence runner tests; no test output validates market accuracy."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.v2.demo import make_demo_snapshot
from backend.v2.models import ValidationError, iso_timestamp
from backend.v2.validation import read_json, render_markdown, validate_market

NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def bundle(tmp_path, synthetic=False):
    snapshot = make_demo_snapshot("tokyo", now=NOW - timedelta(hours=2))
    if not synthetic:
        snapshot["source"]["data_kind"] = "observed"
        for row in snapshot["listings"]:
            row["data_kind"] = "observed"
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(NOW + timedelta(days=100))
    write(tmp_path / "snapshot.json", snapshot)
    holdout = {"requests": [{"request_id": "request-1", "evaluated_at": iso_timestamp(NOW),
                 "reference_snapshot_id": snapshot["snapshot_id"], "reference_observation_id": snapshot["listings"][0]["observation_id"]}]}
    write(tmp_path / "holdouts.json", holdout)
    manifest = {"schema_version": "2.0", "validation_id": "test-fixture-is-not-market-evidence",
                "generated_at": iso_timestamp(NOW), "protocol_frozen_at": iso_timestamp(NOW - timedelta(days=32)),
                "development_cutoff": iso_timestamp(NOW - timedelta(days=31)),
                "holdout_start": iso_timestamp(NOW - timedelta(days=30)), "holdout_end": iso_timestamp(NOW),
                "snapshots": [{"path": "snapshot.json", "available_at": snapshot["as_of"]}], "holdouts_path": "holdouts.json"}
    return manifest, snapshot, holdout


def test_market_rejects_synthetic_and_smoke_can_never_approve(tmp_path):
    manifest, _, _ = bundle(tmp_path, synthetic=True)
    with pytest.raises(ValidationError, match="require observed"):
        validate_market(manifest, base_dir=tmp_path)
    report = validate_market(manifest, base_dir=tmp_path, smoke=True)
    assert report["report_kind"] == "synthetic_smoke"
    assert report["eligibility"]["passed"] is False
    assert report["eligible_segments"] == []


def test_observed_fixture_is_insufficient_without_market_evidence(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    report = validate_market(manifest, base_dir=tmp_path)
    segment = report["segments"]["tokyo/1K"]
    assert segment["metrics"]["comparable_count"] == 1
    assert segment["metrics"]["human"]["audited_units"] == 0
    assert segment["gates"]["thirty_day_supply"] is False
    assert segment["gates"]["human_request_sample"] is False
    assert segment["gates"]["request_sample"] is False
    assert segment["eligible_for_approval"] is False
    assert report["requests"][0]["sample"]["unit_count"] == 22
    assert report["requests"][0]["excluded_holdout_building_observations"] == 2
    excluded = {row["unit_id"] for row in snapshot["listings"] if row["building_id"] == snapshot["listings"][0]["building_id"]}
    assert excluded.isdisjoint(report["requests"][0]["comparison_unit_ids"])
    assert report == validate_market(manifest, base_dir=tmp_path)
    assert "미충족" in render_markdown(report)
    assert len(report["policy_sha256"]) == 64


def test_all_heldout_buildings_excluded_globally(tmp_path):
    manifest, snapshot, holdout = bundle(tmp_path)
    request = copy.deepcopy(holdout["requests"][0])
    request.update(request_id="request-2", reference_observation_id=snapshot["listings"][2]["observation_id"])
    holdout["requests"].append(request)
    write(tmp_path / "holdouts.json", holdout)
    report = validate_market(manifest, base_dir=tmp_path)
    assert report["split"]["heldout_building_count"] == 2
    for row in report["requests"]:
        assert row["excluded_holdout_building_observations"] == 4
        assert row["sample"]["unit_count"] == 20


def test_future_snapshot_not_used_at_past_evaluation(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    later = copy.deepcopy(snapshot)
    later["snapshot_id"] = "future-snapshot"
    later["as_of"] = iso_timestamp(NOW + timedelta(hours=1))
    later["listings"] = []
    write(tmp_path / "future.json", later)
    manifest["generated_at"] = iso_timestamp(NOW + timedelta(hours=2))
    manifest["snapshots"].append({"path": "future.json", "available_at": later["as_of"]})
    report = validate_market(manifest, base_dir=tmp_path)
    assert report["requests"][0]["selected_snapshot_ids"] == [snapshot["snapshot_id"]]
    assert report["requests"][0]["sample"]["unit_count"] == 22


def test_available_at_prevents_late_arrival_leakage(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    manifest["generated_at"] = iso_timestamp(NOW + timedelta(hours=2))
    manifest["snapshots"][0]["available_at"] = iso_timestamp(NOW + timedelta(hours=1))
    with pytest.raises(ValidationError, match="not available"):
        validate_market(manifest, base_dir=tmp_path)


def test_superseded_active_holdout_reference_rejected(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    later = copy.deepcopy(snapshot)
    later["snapshot_id"] = "newer-closed-reference"
    later["as_of"] = iso_timestamp(NOW - timedelta(hours=1))
    later["listings"][0]["status"] = "closed"
    write(tmp_path / "later.json", later)
    manifest["snapshots"].append({"path": "later.json", "available_at": later["as_of"]})
    with pytest.raises(ValidationError, match="latest available"):
        validate_market(manifest, base_dir=tmp_path)


@pytest.mark.parametrize("field,delta", [("development_cutoff", 0), ("protocol_frozen_at", 0), ("holdout_end", -31)])
def test_invalid_chronological_split_rejected(tmp_path, field, delta):
    manifest, _, _ = bundle(tmp_path)
    manifest[field] = iso_timestamp(NOW + timedelta(days=delta))
    with pytest.raises(ValidationError, match="frozen protocol"):
        validate_market(manifest, base_dir=tmp_path)


def test_repeated_holdout_unit_does_not_inflate_sample(tmp_path):
    manifest, _, holdout = bundle(tmp_path)
    holdout["requests"].append({**holdout["requests"][0], "request_id": "new-name-same-unit"})
    write(tmp_path / "holdouts.json", holdout)
    with pytest.raises(ValidationError, match="canonical unit"):
        validate_market(manifest, base_dir=tmp_path)


def test_review_requires_every_candidate_and_missing_labels_do_not_pass(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    report = validate_market(manifest, base_dir=tmp_path)
    candidate_ids = report["requests"][0]["comparison_unit_ids"]
    review = {"request_id": "request-1", "reviewer_id": "reviewer-1", "reviewed_at": iso_timestamp(NOW),
              "expected_judgment": "unknown", "candidates": [{"unit_id": unit, "acceptable": None} for unit in candidate_ids]}
    payload = {"reviews": [review], "unit_audits": [{"snapshot_id": snapshot["snapshot_id"],
       "observation_id": snapshot["listings"][0]["observation_id"], "reviewer_id": "reviewer-1", "reviewed_at": iso_timestamp(NOW),
       "core_fields_correct": None, "status_correct": None, "price_unit_error": None, "residual_duplicate": None, "false_merge": None}]}
    write(tmp_path / "reviews.json", payload)
    manifest["human_reviews_path"] = "reviews.json"
    report = validate_market(manifest, base_dir=tmp_path)
    m = report["segments"]["tokyo/1K"]["metrics"]["human"]
    assert m["comparability_rate"] == 0
    assert m["direction_reviews"] == 0
    assert m["field_accuracy"] == 0
    assert m["price_unit_error_count"] == 1
    assert m["audit_unknown_count"] == 5
    payload["reviews"][0]["candidates"].pop()
    write(tmp_path / "reviews.json", payload)
    with pytest.raises(ValidationError, match="every returned candidate"):
        validate_market(manifest, base_dir=tmp_path)


def test_supply_events_need_real_archived_daily_snapshots(tmp_path):
    manifest, snapshot, _ = bundle(tmp_path)
    event = {"source_id": snapshot["source"]["source_id"], "snapshot_id": snapshot["snapshot_id"],
             "observed_at": snapshot["as_of"], "status": "success"}
    write(tmp_path / "supply.json", {"events": [event]})
    manifest["supply_history_path"] = "supply.json"
    report = validate_market(manifest, base_dir=tmp_path)
    assert report["supply"]["successful_days_by_source"][snapshot["source"]["source_id"]] == 1
    assert report["supply"]["consecutive_days_passed"] is False
    write(tmp_path / "supply.json", {"events": [event, event]})
    with pytest.raises(ValidationError, match="only one successful"):
        validate_market(manifest, base_dir=tmp_path)


@pytest.mark.parametrize("path", ["https://example.test/data.json", "../outside.json", "//server/share.json"])
def test_nonlocal_and_escape_paths_rejected(tmp_path, path):
    manifest, _, _ = bundle(tmp_path)
    manifest["holdouts_path"] = path
    with pytest.raises(ValidationError, match="bundle|relative local"):
        validate_market(manifest, base_dir=tmp_path)


def test_duplicate_json_and_nonfinite_numbers_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate"):
        read_json(path)
    path.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(ValidationError, match="nonfinite"):
        read_json(path)


def test_cli_writes_failed_evidence_report_without_approval(tmp_path):
    manifest, _, _ = bundle(tmp_path)
    write(tmp_path / "manifest.json", manifest)
    output = tmp_path / "reports" / "v1.json"
    script = Path(__file__).resolve().parents[2] / "scripts" / "validate_v2_market.py"
    result = subprocess.run([sys.executable, str(script), str(tmp_path / "manifest.json"), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 3, result.stderr
    assert output.exists() and output.with_suffix(".md").exists()
    assert json.loads(output.read_text(encoding="utf-8"))["eligible_segments"] == []
    repeated = subprocess.run([sys.executable, str(script), str(tmp_path / "manifest.json"), "--output", str(output)], capture_output=True, text=True)
    assert repeated.returncode == 2
    assert "already exists" in repeated.stderr


def test_complete_fabricated_test_bundle_exercises_all_fixed_gates(tmp_path):
    """Pure arithmetic fixture, deliberately fabricated; never persisted as real evidence."""
    manifest, original, _ = bundle(tmp_path)
    template = copy.deepcopy(original)
    prototype = original["listings"][0]
    for index in range(200):
        row = copy.deepcopy(prototype)
        row.update(unit_id=f"test-only-extra-unit-{index}", building_id=f"test-only-extra-building-{index}",
                   listing_id=f"test-only-extra-listing-{index}", observation_id=f"test-only-extra-observation-{index}",
                   rent_yen=90000 if index < 100 else 200000, area_sqm=25, built_year=2016, walk_min=6)
        if index >= 100:
            row["station_id"] = "test-only-other-station"
        template["listings"].append(row)
    snapshots, events = [], []
    daily = {}
    for day in range(30):
        stamp = NOW - timedelta(days=29 - day)
        snapshot = copy.deepcopy(template)
        snapshot.update(snapshot_id=f"test-only-day-{day}", as_of=iso_timestamp(stamp))
        for row in snapshot["listings"]:
            row.update(source_updated_at=iso_timestamp(stamp - timedelta(hours=2)), received_at=iso_timestamp(stamp - timedelta(hours=1)),
                       status_verified_at=iso_timestamp(stamp - timedelta(minutes=30)))
        filename = f"test-day-{day}.json"
        write(tmp_path / filename, snapshot)
        snapshots.append({"path": filename, "available_at": snapshot["as_of"]})
        events.append({"source_id": snapshot["source"]["source_id"], "snapshot_id": snapshot["snapshot_id"], "status": "success", "observed_at": snapshot["as_of"]})
        daily[day] = snapshot
    requests, reviews = [], []
    for index in range(100):
        day = 23 + index % 7
        requests.append({"request_id": f"test-only-request-{index}", "evaluated_at": daily[day]["as_of"],
                         "reference_snapshot_id": daily[day]["snapshot_id"], "reference_observation_id": f"test-only-extra-observation-{index}"})
        reviews.append({"request_id": f"test-only-request-{index}", "reviewer_id": "test-only-fabricated-reviewer", "reviewed_at": iso_timestamp(NOW),
                        "expected_judgment": "similar", "candidates": [{"unit_id": row["unit_id"], "acceptable": True} for row in original["listings"]]})
    audits = [{"snapshot_id": daily[29]["snapshot_id"], "observation_id": row["observation_id"],
               "reviewer_id": "test-only-fabricated-reviewer", "reviewed_at": iso_timestamp(NOW), "core_fields_correct": True,
               "status_correct": True, "price_unit_error": False, "residual_duplicate": False, "false_merge": False} for row in daily[29]["listings"][:200]]
    write(tmp_path / "holdouts.json", {"requests": requests})
    write(tmp_path / "reviews.json", {"reviews": reviews, "unit_audits": audits})
    write(tmp_path / "supply.json", {"events": events})
    manifest.update(snapshots=snapshots, human_reviews_path="reviews.json", supply_history_path="supply.json")
    report = validate_market(manifest, base_dir=tmp_path)
    entry = report["segments"]["tokyo/1K"]
    assert all(entry["gates"].values()), entry["gates"]
    assert report["eligible_segments"] == ["tokyo/1K"]
    assert entry["metrics"]["request_count"] == 100
    assert entry["metrics"]["target_building_count"] == 100
    assert entry["metrics"]["time_day_count"] == 7
