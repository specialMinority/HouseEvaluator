"""Structural fixtures exercise the gate; these are not actual user or delivery evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from backend.v2.acceptance import ALERT_CHECKS, TASKS, acceptance_records_valid
from backend.v2.release import checked_evidence
from scripts.drill_v2_alerts import run_drill


NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def stamp(seconds):
    return (NOW + timedelta(seconds=seconds)).isoformat()


def attachment(tmp_path):
    raw = b"Automated-test fixture only; no real recipient, participants or execution."
    (tmp_path / "fixture.txt").write_bytes(raw)
    return {"path": "fixture.txt", "sha256": hashlib.sha256(raw).hexdigest()}


def base_report(tmp_path, kind):
    return {"schema_version": "operations-report-1.0", "kind": kind, "scope": "market_operations", "passed": True,
            "context_fingerprint": "test-context", "checked_at": stamp(0), "protocol_frozen_at": stamp(-1000),
            "source_ids": ["test-permitted-source"], "protocol_evidence": attachment(tmp_path)}


def alert_report(tmp_path):
    report = base_report(tmp_path, "alerts_drill")
    report.update({"delivery_scope": "external", "notification_sent": True, "external_delivery_verified": True,
                   "human_receipt_verified": True, "operator_id": "operator-fixture", "max_delivery_seconds": 300,
                   "checks": {key: True for key in ALERT_CHECKS}, "deliveries": []})
    for index, event in enumerate(("opened", "recovered")):
        seconds = -500 + index * 100
        report["deliveries"].append({"event_id": f"event-{index}", "incident_id": "incident-fixture", "code": "worker_failed",
                                     "event": event, "provider_receipt_id": f"receipt-{index}", "recipient_id": "recipient-fixture",
                                     "occurred_at": stamp(seconds), "delivered_at": stamp(seconds + 10),
                                     "acknowledged_at": stamp(seconds + 20), "delivery_evidence": attachment(tmp_path),
                                     "receipt_evidence": attachment(tmp_path)})
    return report


def usability_report(tmp_path):
    report = base_report(tmp_path, "usability_review")
    report.update({"facilitator_id": "facilitator-fixture", "reviewer_id": "reviewer-fixture", "open_blocker_count": 0,
                   "human_participants_verified": True, "planned_tasks_completed": True, "approved_segments": ["tokyo/1K"],
                   "protocol": {"minimum_participants": 1, "minimum_mobile_participants": 1, "minimum_novice_participants": 1,
                                "required_cities": ["tokyo", "osaka", "fukuoka"], "required_task_ids": sorted(TASKS)},
                   "participants": [{"participant_id": "participant-fixture", "target_user": True,
                                     "japan_housing_novice": True, "devices": ["mobile"]}], "observations": [], "issues": []})
    for index, task in enumerate(sorted(TASKS)):
        report["observations"].append({"observation_id": f"observation-{index}", "participant_id": "participant-fixture",
                                       "task_id": task, "city": task.removeprefix("city_input_") if task.startswith("city_input_") else "tokyo",
                                       "device": "mobile", "result": "completed", "segment": "tokyo/1K",
                                       "data_kind": "synthetic" if task == "synthetic_distinction" else "observed",
                                       "started_at": stamp(-500 + index * 10), "ended_at": stamp(-495 + index * 10),
                                       "notes": "Test fixture observation, not an actual participant statement.", "evidence": attachment(tmp_path)})
    return report


def registered(tmp_path, report):
    raw = json.dumps(report).encode()
    (tmp_path / "report.json").write_bytes(raw)
    manifest = {"schema_version": "release-evidence-1.0", "reports": {report["kind"]: {
        "path": "report.json", "sha256": hashlib.sha256(raw).hexdigest()}}}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return checked_evidence(path, now=NOW, kind=report["kind"], context="test-context")


@pytest.mark.parametrize("factory", [alert_report, usability_report])
def test_complete_structural_fixture_is_accepted(tmp_path, factory):
    assert registered(tmp_path, factory(tmp_path))


@pytest.mark.parametrize("kind", ["alerts_drill", "usability_review"])
def test_generic_passed_flag_without_execution_records_is_rejected(tmp_path, kind):
    assert not registered(tmp_path, base_report(tmp_path, kind))


def test_relabelled_local_alert_drill_is_rejected_even_with_matching_hash(tmp_path):
    report = run_drill(now=NOW)
    report.update(scope="market_operations", context_fingerprint="test-context")
    assert not registered(tmp_path, report)


@pytest.mark.parametrize("factory", [alert_report, usability_report])
@pytest.mark.parametrize("change", ["schema", "sources", "protocol_missing", "protocol_after", "attachment_missing", "attachment_tampered", "attachment_escape"])
def test_common_missing_or_invalid_execution_evidence_fails_closed(tmp_path, factory, change):
    report = factory(tmp_path)
    if change == "schema":
        report["schema_version"] = "other"
    elif change == "sources":
        report["source_ids"] = []
    elif change == "protocol_missing":
        del report["protocol_frozen_at"]
    elif change == "protocol_after":
        report["protocol_frozen_at"] = stamp(-1)
    elif change == "attachment_missing":
        (tmp_path / "fixture.txt").unlink()
    elif change == "attachment_tampered":
        (tmp_path / "fixture.txt").write_text("changed", encoding="utf-8")
    else:
        outside = tmp_path.parent / "outside-fixture.txt"
        outside.write_bytes(b"external")
        report["protocol_evidence"] = {"path": "../outside-fixture.txt", "sha256": hashlib.sha256(b"external").hexdigest()}
    assert not registered(tmp_path, report)


@pytest.mark.parametrize("field", ["notification_sent", "external_delivery_verified", "human_receipt_verified"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_alert_required_flags_are_strict_booleans(tmp_path, field, value):
    report = alert_report(tmp_path)
    report[field] = value
    assert not registered(tmp_path, report)


@pytest.mark.parametrize("change", ["local", "no_delivery", "no_recovery", "duplicate_event", "duplicate_receipt", "wrong_incident",
                                    "wrong_recipient", "future_ack", "backwards", "too_slow", "negative_budget", "bool_budget",
                                    "check_missing", "check_failed", "check_numeric", "no_receipt_evidence"])
def test_external_delivery_needs_complete_consistent_event_pair(tmp_path, change):
    report = alert_report(tmp_path)
    first, second = report["deliveries"]
    if change == "local":
        report["delivery_scope"] = "local_only"
    elif change == "no_delivery":
        report["deliveries"] = []
    elif change == "no_recovery":
        report["deliveries"] = [first]
    elif change == "duplicate_event":
        second["event_id"] = first["event_id"]
    elif change == "duplicate_receipt":
        second["provider_receipt_id"] = first["provider_receipt_id"]
    elif change == "wrong_incident":
        second["incident_id"] = "other"
    elif change == "wrong_recipient":
        second["recipient_id"] = "other"
    elif change == "future_ack":
        second["acknowledged_at"] = stamp(1)
    elif change == "backwards":
        second["delivered_at"] = stamp(-1000)
    elif change == "too_slow":
        first["acknowledged_at"] = stamp(-1)
    elif change == "negative_budget":
        report["max_delivery_seconds"] = -1
    elif change == "bool_budget":
        report["max_delivery_seconds"] = True
    elif change == "check_missing":
        del report["checks"]["incident_delivered"]
    elif change == "check_failed":
        report["checks"]["incident_delivered"] = False
    elif change == "check_numeric":
        report["checks"]["incident_delivered"] = 1
    else:
        del second["receipt_evidence"]
    assert not registered(tmp_path, report)


@pytest.mark.parametrize("change", ["no_people", "no_observations", "missing_task", "unknown_person", "duplicate_person", "no_novice",
                                    "no_mobile", "wrong_city", "synthetic_real_task", "unapproved_segment", "no_notes", "no_evidence",
                                    "late_protocol", "not_completed", "unaccounted_help", "negative_blockers", "bool_blockers",
                                    "false_human", "numeric_human", "false_completed", "missing_city", "zero_minimum", "bool_minimum", "minimum_unmet"])
def test_usability_needs_actual_observation_structure_and_frozen_plan(tmp_path, change):
    report = usability_report(tmp_path)
    row = next(item for item in report["observations"] if item["task_id"] == "higher_price")
    if change == "no_people":
        report["participants"] = []
    elif change == "no_observations":
        report["observations"] = []
    elif change == "missing_task":
        report["observations"].remove(row)
    elif change == "unknown_person":
        row["participant_id"] = "unknown"
    elif change == "duplicate_person":
        report["participants"].append(deepcopy(report["participants"][0]))
    elif change == "no_novice":
        report["participants"][0]["japan_housing_novice"] = False
    elif change == "no_mobile":
        report["participants"][0]["devices"] = ["desktop"]
    elif change == "wrong_city":
        next(item for item in report["observations"] if item["task_id"] == "city_input_osaka")["city"] = "tokyo"
    elif change == "synthetic_real_task":
        row["data_kind"] = "synthetic"
    elif change == "unapproved_segment":
        row["segment"] = "osaka/1K"
    elif change == "no_notes":
        row["notes"] = " "
    elif change == "no_evidence":
        del row["evidence"]
    elif change == "late_protocol":
        report["protocol_frozen_at"] = stamp(-10)
    elif change == "not_completed":
        row["result"] = "not_run"
    elif change == "unaccounted_help":
        row["result"] = "help_needed"
        retest = deepcopy(row)
        retest.update(observation_id="retest", result="completed", started_at=stamp(-10), ended_at=stamp(-5))
        report["observations"].append(retest)
    elif change == "negative_blockers":
        report["open_blocker_count"] = -1
    elif change == "bool_blockers":
        report["open_blocker_count"] = False
    elif change == "false_human":
        report["human_participants_verified"] = False
    elif change == "numeric_human":
        report["human_participants_verified"] = 1
    elif change == "false_completed":
        report["planned_tasks_completed"] = False
    elif change == "missing_city":
        report["protocol"]["required_cities"].remove("osaka")
    elif change == "zero_minimum":
        report["protocol"]["minimum_participants"] = 0
    elif change == "bool_minimum":
        report["protocol"]["minimum_participants"] = True
    else:
        report["protocol"]["minimum_participants"] = 2
    assert not registered(tmp_path, report)


def with_resolved_issue(tmp_path):
    report = usability_report(tmp_path)
    row = report["observations"][0]
    row["result"] = "help_needed"
    retest = deepcopy(row)
    retest.update(observation_id="retest", result="completed", started_at=stamp(-10), ended_at=stamp(-5))
    report["observations"].append(retest)
    report["issues"] = [{"issue_id": "issue-fixture", "original_observation_id": row["observation_id"], "severity": "blocker",
                         "resolved": True, "resolution": "Fixture change for the observed issue.", "resolution_evidence": attachment(tmp_path),
                         "retest_observation_ids": ["retest"]}]
    return report


def test_resolved_blocker_requires_later_completed_human_retest(tmp_path):
    assert registered(tmp_path, with_resolved_issue(tmp_path))


@pytest.mark.parametrize("change", ["unresolved", "missing_resolution", "missing_evidence", "no_retest", "wrong_task", "early_retest", "failed_retest"])
def test_blocker_summary_cannot_override_unresolved_issue(tmp_path, change):
    report = with_resolved_issue(tmp_path)
    issue, retest = report["issues"][0], report["observations"][-1]
    if change == "unresolved":
        issue["resolved"] = False
    elif change == "missing_resolution":
        issue["resolution"] = ""
    elif change == "missing_evidence":
        del issue["resolution_evidence"]
    elif change == "no_retest":
        issue["retest_observation_ids"] = []
    elif change == "wrong_task":
        retest["task_id"] = "higher_price"
    elif change == "early_retest":
        retest.update(started_at=stamp(-900), ended_at=stamp(-899))
    else:
        retest["result"] = "help_needed"
    assert not registered(tmp_path, report)


@pytest.mark.parametrize("bad", [None, [], True, 1, "passed"])
def test_malformed_reports_fail_closed(tmp_path, bad):
    assert not acceptance_records_valid(bad, kind="alerts_drill", directory=tmp_path)


def test_existing_machine_drill_kinds_are_unaffected(tmp_path):
    assert acceptance_records_valid({}, kind="restore_drill", directory=tmp_path)
