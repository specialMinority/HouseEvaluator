"""Local incident tests use isolated fixtures and never deliver notifications."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from backend.v2.alerts import AlertStore, check_once, checked_paths, inspect_local, write_fresh
from backend.v2.demo import make_demo_snapshot
from backend.v2.models import ValidationError, iso_timestamp
from backend.v2.release import checked_evidence
from backend.v2.storage import open_store
from backend.v2.supply import IngestionRunner
from scripts import check_v2_alerts, drill_v2_alerts

NOW = datetime(2026, 9, 13, 5, tzinfo=timezone.utc)


def fixture(tmp_path, *, rights_expiry=None):
    source = "offline-alert-test-not-a-real-license"
    snapshot = make_demo_snapshot("tokyo", now=NOW)
    snapshot["source"].update(source_id=source, independent_source_id=source, data_kind="observed")
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(rights_expiry or NOW + timedelta(days=30))
    for row in snapshot["listings"]:
        row.update(source_id=source, data_kind="observed")
    (tmp_path / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    agreement = b"Offline alert test only, not a real data license."
    (tmp_path / "agreement.txt").write_bytes(agreement)
    config = tmp_path / "suppliers.json"
    config.write_text(json.dumps({"schema_version": "2.0", "suppliers": [{
        "source_id": source, "enabled": True, "interval_seconds": 300, "max_attempts": 3, "allow_empty": False,
        "contract": {"agreement_path": "agreement.txt", "agreement_sha256": hashlib.sha256(agreement).hexdigest(),
                     "comparison": True, "display": True, "storage": True, "revoked": False,
                     "expires_at": iso_timestamp(NOW + timedelta(days=30))},
        "feed": {"type": "local", "path": "snapshot.json"}}]}), encoding="utf-8")
    database, state = tmp_path / "live.sqlite3", tmp_path / "worker.sqlite3"
    store = open_store(database)
    result = IngestionRunner(config, state, store, clock=lambda: NOW).run_once()
    assert result["attempted"] == 1
    return database, state, config


def test_durable_restart_dedup_and_recovery(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    first = AlertStore(path).reconcile(["worker_failed"], now=NOW)
    repeat = AlertStore(path).reconcile(["worker_failed"], now=NOW + timedelta(seconds=1))
    recovered = AlertStore(path).reconcile([], now=NOW + timedelta(seconds=2))
    quiet = AlertStore(path).reconcile([], now=NOW + timedelta(seconds=3))
    assert first["events"][0]["event"] == "opened"
    assert repeat["new_event_count"] == quiet["new_event_count"] == 0
    assert repeat["active_incidents"][0]["opened_at"] == iso_timestamp(NOW)
    assert recovered["events"][0]["event"] == "recovered" and recovered["active_incidents"] == []
    assert quiet["status"] == "healthy" and quiet["notification_sent"] is False
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 2


def test_recurrence_is_a_new_incident(tmp_path):
    store = AlertStore(tmp_path / "alerts.sqlite3")
    store.reconcile(["worker_failed"], now=NOW)
    store.reconcile([], now=NOW + timedelta(seconds=1))
    again = store.reconcile(["worker_failed"], now=NOW + timedelta(seconds=2))
    assert again["events"][0]["event_id"] == 3
    assert again["active_incidents"][0]["opened_at"] == iso_timestamp(NOW + timedelta(seconds=2))


@pytest.mark.parametrize("codes", [["https://secret.invalid/?api_key=secret"], "worker_failed", ["arbitrary text"]])
def test_only_allowlisted_codes_are_stored(tmp_path, codes):
    path = tmp_path / "alerts.sqlite3"
    store = AlertStore(path)
    with pytest.raises(ValidationError):
        store.reconcile(codes, now=NOW)
    assert b"api_key" not in path.read_bytes()


def test_clock_regression_cannot_clear_an_incident(tmp_path):
    store = AlertStore(tmp_path / "alerts.sqlite3")
    store.reconcile(["worker_failed"], now=NOW)
    with pytest.raises(ValidationError, match="backwards"):
        store.reconcile([], now=NOW - timedelta(seconds=1))
    assert len(store.reconcile(["worker_failed"], now=NOW)["active_incidents"]) == 1


def test_unconfigured_monitor_creates_only_alert_db(tmp_path):
    database, state, alerts = [tmp_path / name for name in ("missing.sqlite3", "state.sqlite3", "alerts.sqlite3")]
    report = check_once(database, state, alerts, now=NOW)
    codes = {item["code"] for item in report["active_incidents"]}
    assert {"snapshot_unavailable", "market_data_unavailable", "supplier_not_configured", "worker_not_configured"} <= codes
    assert not database.exists() and not state.exists() and alerts.exists()
    assert report["release_readiness_checked"] is False
    assert report["source_count"] == report["active_listing_count"] == 0
    assert check_once(database, state, alerts, now=NOW)["new_event_count"] == 0


def test_live_databases_are_read_only_and_healthy_fixture_has_no_incidents(tmp_path):
    database, state, config = fixture(tmp_path)
    before = {path: path.read_bytes() for path in (database, state, config)}
    report = check_once(database, state, tmp_path / "alerts.sqlite3", suppliers=config, now=NOW)
    assert report["active_incidents"] == [] and report["status"] == "healthy"
    assert report["source_count"] == 1 and report["fresh_listing_count"] == 24
    assert all(path.read_bytes() == content for path, content in before.items())
    raw = json.dumps(report)
    for sensitive in ("offline-alert-test", "agreement", "source_id", "rent_yen", "api_key", "localhost", str(tmp_path)):
        assert sensitive not in raw


def test_fixture_becomes_stale_and_worker_overdue_then_recovers(tmp_path):
    database, state, config = fixture(tmp_path)
    alerts = tmp_path / "alerts.sqlite3"
    report = check_once(database, state, alerts, suppliers=config, now=NOW + timedelta(hours=25))
    assert {item["code"] for item in report["active_incidents"]} == {"listings_stale", "worker_overdue"}
    # Recovery is verified separately via reconciliation; the monitor must never
    # alter source verification times to manufacture fresh listings.
    again = check_once(database, state, alerts, suppliers=config, now=NOW + timedelta(hours=26))
    assert again["new_event_count"] == 0 and again["fresh_listing_count"] == 0


@pytest.mark.parametrize("unavailable", ["deleted_snapshot", "expired_snapshot_rights"])
def test_healthy_supplier_cannot_mask_another_permitted_suppliers_unavailable_snapshot(tmp_path, unavailable):
    expiry = NOW + timedelta(seconds=1) if unavailable == "expired_snapshot_rights" else None
    database, state, config = fixture(tmp_path, rights_expiry=expiry)
    settings = json.loads(config.read_text())
    first_source = settings["suppliers"][0]["source_id"]
    second = deepcopy(settings["suppliers"][0])
    second_source = second["source_id"] = "second-offline-alert-test-not-a-real-license"
    second["feed"]["path"] = "second-snapshot.json"
    settings["suppliers"].append(second)
    config.write_text(json.dumps(settings), encoding="utf-8")
    snapshot = json.loads((tmp_path / "snapshot.json").read_text())
    snapshot["source"].update(source_id=second_source, independent_source_id=second_source)
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(NOW + timedelta(days=30))
    for row in snapshot["listings"]:
        row["source_id"] = second_source
    (tmp_path / "second-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    IngestionRunner(config, state, open_store(database), clock=lambda: NOW).run_once()
    baseline = inspect_local(database, state, suppliers=config, now=NOW)
    assert baseline["codes"] == [] and baseline["source_count"] == 2
    if unavailable == "deleted_snapshot":
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM v2_current WHERE source_id=?", (first_source,))
    before = {path: path.read_bytes() for path in (database, state, config)}
    report = check_once(database, state, tmp_path / "alerts.sqlite3", suppliers=config, now=NOW + timedelta(seconds=2))
    assert report["status"] == "needs_attention"
    assert {item["code"] for item in report["active_incidents"]} == {"snapshot_unavailable"}
    assert report["source_count"] == 1 and report["fresh_listing_count"] == 24
    assert all(path.read_bytes() == content for path, content in before.items())


def test_revoked_rights_remove_market_data(tmp_path):
    database, state, config = fixture(tmp_path)
    settings = json.loads(config.read_text())
    settings["suppliers"][0]["contract"]["revoked"] = True
    config.write_text(json.dumps(settings))
    report = inspect_local(database, state, suppliers=config, now=NOW)
    assert report["source_count"] == 0 and "market_data_unavailable" in report["codes"]


def test_invalid_configuration_error_does_not_expose_secret(tmp_path):
    config = tmp_path / "suppliers.json"
    config.write_text('{"api_key":"secret-value", "url":"https://example.invalid/private"}')
    report = check_once(tmp_path / "db.sqlite3", tmp_path / "state.sqlite3", tmp_path / "alerts.sqlite3", suppliers=config, now=NOW)
    assert "supplier_configuration_invalid" in {item["code"] for item in report["active_incidents"]}
    assert "secret-value" not in json.dumps(report) and "example.invalid" not in json.dumps(report)


def test_corrupt_snapshot_is_fail_closed(tmp_path):
    database, state, config = fixture(tmp_path)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE v2_current SET content_hash='bad'")
    before = database.read_bytes()
    report = inspect_local(database, state, suppliers=config, now=NOW)
    assert "snapshot_invalid" in report["codes"] and report["fresh_listing_count"] == 0
    assert database.read_bytes() == before


def test_invalid_state_is_a_safe_incident(tmp_path):
    database, state, config = fixture(tmp_path)
    state.write_bytes(b"invalid private raw bytes")
    report = inspect_local(database, state, suppliers=config, now=NOW)
    assert "worker_unavailable" in report["codes"]
    assert "private raw" not in json.dumps(report)


@pytest.mark.parametrize("collision", ["same", "sidecar", "reverse", "hardlink"])
def test_alert_database_cannot_alias_or_use_snapshot_sidecars(tmp_path, collision):
    database = tmp_path / "live.sqlite3"
    open_store(database)
    alerts = database
    if collision == "sidecar":
        alerts = Path(str(database) + "-wal")
    elif collision == "reverse":
        alerts, database = database, Path(str(database) + "-journal")
    elif collision == "hardlink":
        alerts = tmp_path / "hard.sqlite3"
        alerts.hardlink_to(database)
    with pytest.raises(ValidationError, match="distinct"):
        checked_paths(db=database, state_db=tmp_path / "state.sqlite3", alerts_db=alerts)


def test_unrelated_database_is_not_initialized_or_overwritten(tmp_path):
    database = tmp_path / "live.sqlite3"
    open_store(database)
    before = database.read_bytes()
    with pytest.raises(ValidationError, match="unsupported"):
        AlertStore(database)
    assert database.read_bytes() == before


def test_existing_report_is_rejected_before_incident_mutation(tmp_path, capsys):
    output, alerts = tmp_path / "report.json", tmp_path / "alerts.sqlite3"
    output.write_text("preserve")
    result = check_v2_alerts.main(["--db", str(tmp_path / "db.sqlite3"), "--state-db", str(tmp_path / "state.sqlite3"),
                                  "--alerts-db", str(alerts), "--output", str(output)])
    assert result == 2 and output.read_text() == "preserve" and not alerts.exists()
    assert "local_alert_check_rejected" in capsys.readouterr().err


def test_report_writer_never_overwrites(tmp_path):
    output = tmp_path / "report.json"
    write_fresh(output, {"safe": True})
    with pytest.raises(ValidationError):
        write_fresh(output, {"safe": False})
    assert json.loads(output.read_text()) == {"safe": True}


def test_drill_proves_durable_transitions_but_cannot_approve_release(tmp_path):
    report = drill_v2_alerts.run_drill(now=NOW)
    assert report["passed"] is True and all(report["checks"].values())
    assert report["scope"] == "synthetic_smoke" and report["persisted_event_count"] == 4
    assert report["temporary_fixture_removed"] is True
    for key in ("notification_sent", "external_delivery_verified", "human_receipt_verified", "operational_database_modified", "evidence_registered"):
        assert report[key] is False
    path = tmp_path / "report.json"
    write_fresh(path, report)
    evidence = tmp_path / "release.json"
    evidence.write_text(json.dumps({"schema_version": "release-evidence-1.0", "reports": {"alerts_drill": {
        "path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}}))
    assert checked_evidence(evidence, now=NOW, kind="alerts_drill", context=report["context_fingerprint"]) is False
