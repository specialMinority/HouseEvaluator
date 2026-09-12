"""Offline lifecycle checks; destructive cases operate only in pytest tmp_path."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from backend.v2 import maintenance
from backend.v2.demo import make_demo_snapshot
from backend.v2.models import ValidationError
from backend.v2.storage import open_store
from scripts.maintain_v2_store import main


NOW = datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc)


def fixture_store(tmp_path, *, now=NOW, expiry_days=30):
    path = tmp_path / "live.sqlite3"
    store = open_store(path)
    snapshot = make_demo_snapshot("tokyo", now=now)
    snapshot["source"]["rights"]["expires_at"] = (now + timedelta(days=expiry_days)).isoformat()
    store.import_snapshot(snapshot, now=now, allow_synthetic=True)
    return path, store, snapshot


def archive(tmp_path, **kwargs):
    path, store, snapshot = fixture_store(tmp_path, **kwargs)
    directory = tmp_path / "managed"
    result = maintenance.backup_store(path, directory, now=kwargs.get("now", NOW))
    return path, store, snapshot, directory, directory / result["manifest_file"], result


@pytest.mark.parametrize("target", [":memory:", "postgres://user:secret@host/db", "postgresql://host/db", "https://host/db", "file:live.db?mode=ro", "//server/share/live.db", None])
def test_factory_rejects_unsupported_targets_without_downgrade(target):
    with pytest.raises(ValidationError):
        open_store(target)


def test_backup_restore_roundtrip_and_expiry_rechecked_on_read(tmp_path):
    path, store, snapshot, directory, manifest, result = archive(tmp_path, expiry_days=2)
    destination = tmp_path / "restored.sqlite3"
    restored = maintenance.restore_backup(manifest, destination, now=NOW)
    assert restored["source_count"] == 1
    assert restored["listing_count"] == len(snapshot["listings"])
    assert open_store(destination).read_current(now=NOW, include_synthetic=True) == store.read_current(now=NOW, include_synthetic=True)
    assert open_store(destination).read_current(now=NOW + timedelta(days=3), include_synthetic=True)["listings"] == []
    assert result["expires_at"] == "2026-09-15T03:00:00Z"
    assert sorted(p.suffix for p in directory.iterdir()) == [".json", ".sqlite3"]


def test_dry_run_changes_no_files_and_apply_preserves_receipts_without_raw_rows(tmp_path):
    path, store, snapshot, directory, manifest, result = archive(tmp_path, expiry_days=2)
    other = directory / "do-not-delete.txt"
    other.write_text("preserve")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    original_db = path.read_bytes()
    now = NOW + timedelta(days=3)
    plan = maintenance.maintenance_plan(path, directory, now=now)
    assert plan["status"] == "plan_only"
    assert plan["snapshot_count"] == plan["backup_count"] == 1
    assert path.read_bytes() == original_db
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    applied = maintenance.apply_maintenance(path, directory, now=now)
    assert applied["status"] == "applied"
    assert applied["deleted_snapshot_count"] == applied["deleted_backup_count"] == 1
    assert applied["secure_erasure_guaranteed"] is False
    assert other.read_text() == "preserve"
    assert not manifest.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM v2_current").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM v2_receipts").fetchone()[0] == 1
        assert db.execute("SELECT deleted_snapshots FROM v2_maintenance_audit").fetchone()[0] == 1
        audit_columns = [row[1] for row in db.execute("PRAGMA table_info(v2_maintenance_audit)")]
        assert "payload" not in audit_columns and "source_id" not in audit_columns
    # A replay receipt cannot restore a payload purged for expiry or revocation.
    assert store.import_snapshot(snapshot, now=NOW, allow_synthetic=True)["status"] == "unchanged"
    assert store.read_current(now=NOW, include_synthetic=True)["listings"] == []


def test_revocation_removes_live_and_managed_backup_and_restore_refuses(tmp_path):
    path, _, snapshot, directory, manifest, _ = archive(tmp_path)
    revoked = {snapshot["source"]["source_id"]}
    with pytest.raises(ValidationError, match="revoked"):
        maintenance.restore_backup(manifest, tmp_path / "restored.db", now=NOW, revoked_source_ids=revoked)
    assert not (tmp_path / "restored.db").exists()
    result = maintenance.apply_maintenance(path, directory, now=NOW, revoked_source_ids=revoked)
    assert result["deleted_snapshot_count"] == result["deleted_backup_count"] == 1


def test_newer_import_between_plan_and_apply_is_retained(tmp_path):
    path, store, snapshot, directory, _, _ = archive(tmp_path)
    later = NOW + timedelta(days=8)
    assert maintenance.maintenance_plan(path, directory, now=later)["snapshot_count"] == 1
    newer = make_demo_snapshot("tokyo", now=later)
    store.import_snapshot(newer, now=later, allow_synthetic=True)
    result = maintenance.apply_maintenance(path, directory, now=later)
    assert result["deleted_snapshot_count"] == 0
    assert store.read_current(now=later, include_synthetic=True)["listings"]


def test_shortened_retention_uses_snapshot_age_not_just_backup_creation(tmp_path):
    path, _, _ = fixture_store(tmp_path, now=NOW - timedelta(days=2))
    directory = tmp_path / "managed"
    maintenance.backup_store(path, directory, now=NOW)
    plan = maintenance.maintenance_plan(path, directory, now=NOW, retention_days=1)
    assert plan["snapshot_count"] == plan["backup_count"] == 1


@pytest.mark.parametrize("advance", [timedelta(days=7), timedelta(days=8), -timedelta(seconds=1)])
def test_expired_or_future_backup_is_not_restorable(tmp_path, advance):
    *_, manifest, _ = archive(tmp_path)
    destination = tmp_path / "restore.db"
    with pytest.raises(ValidationError, match="expired or future"):
        maintenance.restore_backup(manifest, destination, now=NOW + advance)
    assert not destination.exists()


def test_tampering_checksum_refused_and_live_database_preserved(tmp_path):
    path, _, _, directory, manifest, result = archive(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValidationError, match="fresh destination"):
        maintenance.restore_backup(manifest, path, now=NOW)
    assert path.read_bytes() == original
    backup = directory / result["database_file"]
    with backup.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValidationError, match="checksum"):
        maintenance.restore_backup(manifest, tmp_path / "new.db", now=NOW)
    assert not (tmp_path / "new.db").exists()


def test_manifest_cannot_redirect_restore_or_cleanup_outside_managed_directory(tmp_path):
    path, _, snapshot, directory, manifest, _ = archive(tmp_path)
    outside = tmp_path / "important.sqlite3"
    outside.write_bytes(b"do not touch")
    payload = json.loads(manifest.read_text())
    payload["database_file"] = "../important.sqlite3"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValidationError):
        maintenance.restore_backup(manifest, tmp_path / "restored.db", now=NOW)
    plan = maintenance.maintenance_plan(path, directory, now=NOW)
    assert len(plan["blocked_backups"]) == 1
    result = maintenance.apply_maintenance(path, directory, now=NOW, revoked_source_ids={snapshot["source"]["source_id"]})
    assert result["status"] == "partially_applied"
    assert outside.read_bytes() == b"do not touch"


def test_symlinked_paths_are_rejected_when_supported(tmp_path):
    path, _, _, directory, manifest, _ = archive(tmp_path)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("host does not grant symlink creation")
    with pytest.raises(ValidationError, match="symlink"):
        maintenance.backup_store(path, alias, now=NOW)
    with pytest.raises(ValidationError, match="symlink"):
        maintenance.restore_backup(alias / manifest.name, tmp_path / "restored.db", now=NOW)


def test_invalid_schema_or_expired_sources_never_publish_backup(tmp_path):
    path, _, _ = fixture_store(tmp_path, expiry_days=1)
    directory = tmp_path / "managed"
    with pytest.raises(ValidationError, match="expired"):
        maintenance.backup_store(path, directory, now=NOW + timedelta(days=2))
    assert list(directory.iterdir()) == []
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unexpected (secret TEXT)")
    with pytest.raises(ValidationError, match="schema"):
        maintenance.backup_store(path, directory, now=NOW)
    assert list(directory.iterdir()) == []


def test_partial_backup_publication_failure_leaves_no_orphan_or_changes_to_live(tmp_path, monkeypatch):
    path, _, _ = fixture_store(tmp_path)
    before = path.read_bytes()
    original = maintenance._publish

    def fail_manifest(temporary, destination):
        if destination.name.endswith(".manifest.json"):
            raise OSError("simulated full disk")
        return original(temporary, destination)

    monkeypatch.setattr(maintenance, "_publish", fail_manifest)
    with pytest.raises(OSError):
        maintenance.backup_store(path, tmp_path / "managed", now=NOW)
    assert path.read_bytes() == before
    assert list((tmp_path / "managed").iterdir()) == []


def test_failed_restore_publication_leaves_existing_target_unchanged(tmp_path, monkeypatch):
    *_, manifest, _ = archive(tmp_path)
    target = tmp_path / "restored.db"
    original = maintenance._publish

    def concurrent_target(temporary, destination):
        destination.write_bytes(b"created by someone else")
        return original(temporary, destination)

    monkeypatch.setattr(maintenance, "_publish", concurrent_target)
    with pytest.raises(FileExistsError):
        maintenance.restore_backup(manifest, target, now=NOW)
    assert target.read_bytes() == b"created by someone else"
    assert list(tmp_path.glob(".restore-*.partial")) == []


def test_online_backup_with_concurrent_writer_and_reader_is_a_valid_snapshot(tmp_path):
    path, store, snapshot = fixture_store(tmp_path)
    expected_count = len(snapshot["listings"])
    barrier = threading.Barrier(3)

    def write():
        barrier.wait()
        for second in range(1, 15):
            snapshot = make_demo_snapshot("tokyo", now=NOW + timedelta(seconds=second))
            store.import_snapshot(snapshot, now=NOW + timedelta(seconds=second), allow_synthetic=True)

    def read():
        barrier.wait()
        for _ in range(20):
            assert len(store.read_current(now=NOW + timedelta(minutes=1), include_synthetic=True)["listings"]) == expected_count

    def backup():
        barrier.wait()
        return maintenance.backup_store(path, tmp_path / "managed", now=NOW + timedelta(minutes=1))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(write), pool.submit(read), pool.submit(backup)]
        results = [future.result() for future in futures]
    result = results[2]
    restored = tmp_path / "restore.db"
    maintenance.restore_backup(tmp_path / "managed" / result["manifest_file"], restored, now=NOW + timedelta(minutes=1))
    assert len(open_store(restored).read_current(now=NOW + timedelta(minutes=1), include_synthetic=True)["listings"]) == expected_count


def test_cli_defaults_to_plan_and_sanitizes_io_errors(tmp_path, capsys):
    path, _, snapshot = fixture_store(tmp_path, now=datetime.now(timezone.utc))
    arguments = ["cleanup", "--db", str(path), "--backup-dir", str(tmp_path / "managed"), "--revoke-source", snapshot["source"]["source_id"]]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "plan_only"
    assert open_store(path).read_current(include_synthetic=True)["listings"]
    assert main(arguments + ["--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted_snapshot_count"] == 1
    assert main(["backup", "--db", str(tmp_path / "private-secret-does-not-exist"), "--backup-dir", str(tmp_path)]) == 2
    assert "private-secret" not in capsys.readouterr().err


def test_path_checker_rejects_symlink_components_even_when_host_cannot_create_them(tmp_path, monkeypatch):
    directory = tmp_path / "alias"
    original = Path.is_symlink

    def simulated_symlink(path):
        return path == directory or original(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_symlink)
    with pytest.raises(ValidationError, match="symlink"):
        maintenance._safe_path(directory / "nested" / "backup.db")


def test_live_database_inside_managed_backup_directory_is_refused_before_deletion(tmp_path):
    path, _, snapshot = fixture_store(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValidationError, match="outside"):
        maintenance.apply_maintenance(path, tmp_path, now=NOW, revoked_source_ids={snapshot["source"]["source_id"]})
    assert path.read_bytes() == original


@pytest.mark.parametrize("days", [0, -1, 366, 1.5, True, "7"])
def test_invalid_retention_never_mutates_files(tmp_path, days):
    path, _, _ = fixture_store(tmp_path)
    with pytest.raises(ValidationError, match="retention_days"):
        maintenance.backup_store(path, tmp_path / "managed", now=NOW, retention_days=days)
    assert not (tmp_path / "managed").exists()
