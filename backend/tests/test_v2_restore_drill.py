"""Restore drills use temporary fixture data; no actual supplier license exists."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from backend.v2.demo import make_demo_snapshot
from backend.v2.models import ValidationError, iso_timestamp
from backend.v2.release import checked_evidence, context_fingerprint
from backend.v2.service import Runtime
from backend.v2.storage import open_store
from scripts import drill_v2_restore as drill


NOW = datetime(2026, 9, 13, 3, tzinfo=timezone.utc)


def fixture(tmp_path, *, observed=False, now=NOW):
    path = tmp_path / "live.sqlite3"
    store = open_store(path)
    snapshot = make_demo_snapshot("tokyo", now=now)
    config = None
    if observed:
        source_id = "offline-fixture-not-a-real-license"
        snapshot["source"].update(source_id=source_id, independent_source_id=source_id, data_kind="observed")
        for row in snapshot["listings"]:
            row.update(source_id=source_id, data_kind="observed")
        evidence = b"Offline test evidence only; not a data license."
        (tmp_path / "agreement.txt").write_bytes(evidence)
        config = tmp_path / "suppliers.json"
        config.write_text(json.dumps({"schema_version": "2.0", "suppliers": [{
            "source_id": source_id, "enabled": True, "interval_seconds": 300, "max_attempts": 3, "allow_empty": False,
            "contract": {"agreement_path": "agreement.txt", "agreement_sha256": hashlib.sha256(evidence).hexdigest(), "comparison": True, "display": True, "storage": True, "revoked": False, "expires_at": iso_timestamp(now + timedelta(days=30))},
            "feed": {"type": "local", "path": "snapshot.json"},
        }]}), encoding="utf-8")
    store.import_snapshot(snapshot, now=now, allow_synthetic=not observed)
    return path, store, snapshot, config


def test_synthetic_drill_preserves_original_cleans_temporary_and_cannot_pass_release(tmp_path, monkeypatch):
    path, _, snapshot, _ = fixture(tmp_path)
    original = path.read_bytes()
    created = []
    original_temporary = drill.TemporaryDirectory

    def temporary(*args, **kwargs):
        result = original_temporary(*args, **kwargs)
        created.append(Path(result.name))
        return result

    monkeypatch.setattr(drill, "TemporaryDirectory", temporary)
    report = drill.run_drill(path, tmp_path / "backups", now=NOW)
    assert report["passed"] is True and all(report["checks"].values())
    assert report["scope"] == "synthetic_smoke"
    assert report["restored_listing_count"] == len(snapshot["listings"])
    assert report["evidence_registered"] is False
    assert path.read_bytes() == original
    assert all(not item.exists() for item in created)
    report_path = tmp_path / "drill.json"
    report_path.write_text(json.dumps(report))
    evidence = tmp_path / "release.json"
    evidence.write_text(json.dumps({"schema_version": "release-evidence-1.0", "reports": {"restore_drill": {"path": report_path.name, "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}}}))
    assert checked_evidence(evidence, now=NOW, kind="restore_drill", context=report["context_fingerprint"]) is False


def test_observed_drill_requires_explicit_current_permission_for_market_scope(tmp_path):
    path, _, snapshot, config = fixture(tmp_path, observed=True)
    unconfigured = drill.run_drill(path, tmp_path / "backups", now=NOW)
    assert unconfigured["scope"] == "synthetic_smoke"
    report = drill.run_drill(path, tmp_path / "backups", suppliers=config, now=NOW)
    assert report["passed"] is True
    assert report["scope"] == "market_operations"
    assert report["source_ids"] == [snapshot["source"]["source_id"]]
    runtime = Runtime(path, suppliers_path=config)
    assert report["context_fingerprint"] == context_fingerprint(runtime, {snapshot["source"]["source_id"]})


def test_explicit_revocation_rejects_drill_without_creating_backup(tmp_path):
    path, _, _, config = fixture(tmp_path, observed=True)
    payload = json.loads(config.read_text())
    payload["suppliers"][0]["contract"]["revoked"] = True
    config.write_text(json.dumps(payload))
    with pytest.raises(ValidationError, match="revoked"):
        drill.run_drill(path, tmp_path / "backups", suppliers=config, now=NOW)
    assert list((tmp_path / "backups").iterdir()) == []


def test_current_live_update_after_backup_does_not_create_false_mismatch(tmp_path, monkeypatch):
    path, store, snapshot, _ = fixture(tmp_path)
    original_backup = drill.backup_store

    def backup_then_update(*args, **kwargs):
        result = original_backup(*args, **kwargs)
        later = NOW + timedelta(minutes=1)
        changed = make_demo_snapshot("tokyo", now=later)
        changed["listings"] = changed["listings"][:10]
        store.import_snapshot(changed, now=later, allow_synthetic=True)
        return result

    monkeypatch.setattr(drill, "backup_store", backup_then_update)
    report = drill.run_drill(path, tmp_path / "backups", now=NOW)
    assert report["passed"] is True
    assert report["restored_listing_count"] == len(snapshot["listings"])
    assert len(store.read_current(now=NOW + timedelta(minutes=1), include_synthetic=True)["listings"]) == 10


def test_failed_restore_removes_temporary_and_does_not_modify_live(tmp_path, monkeypatch):
    path, _, _, _ = fixture(tmp_path)
    before = path.read_bytes()
    destinations = []

    def fail(_manifest, destination, **kwargs):
        destinations.append(destination)
        destination.write_bytes(b"partial fixture")
        raise ValidationError("simulated restore failure")

    monkeypatch.setattr(drill, "restore_backup", fail)
    with pytest.raises(ValidationError):
        drill.run_drill(path, tmp_path / "backups", now=NOW)
    assert path.read_bytes() == before
    assert all(not destination.exists() for destination in destinations)


def test_cli_output_is_fresh_only_and_reports_smoke_scope(tmp_path, capsys):
    path, _, _, _ = fixture(tmp_path, now=datetime.now(timezone.utc))
    output = tmp_path / "report.json"
    args = ["--db", str(path), "--backup-dir", str(tmp_path / "backups"), "--output", str(output)]
    assert drill.main(args) == 0
    report = json.loads(output.read_text())
    assert report["passed"] is True and report["scope"] == "synthetic_smoke"
    assert json.loads(capsys.readouterr().out) == report
    original = output.read_bytes()
    backup_count = len(list((tmp_path / "backups").iterdir()))
    assert drill.main(args) == 2
    assert output.read_bytes() == original
    assert len(list((tmp_path / "backups").iterdir())) == backup_count
    assert "fresh file" in capsys.readouterr().err
