"""Contract-aware maintenance CLI checks; all data are offline test fixtures."""

from datetime import timedelta
import hashlib
import json
import sqlite3

import pytest

from backend.v2.demo import make_demo_snapshot
from backend.v2.maintenance import backup_store
from backend.v2.models import iso_timestamp, utc_now
from backend.v2.storage import open_store
from scripts import maintain_v2_store as cli


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("maintenance must not contact suppliers or resolve network names")
    monkeypatch.setattr("socket.getaddrinfo", denied)
    monkeypatch.setattr("socket.create_connection", denied)


def fixture(tmp_path, *, synthetic=False):
    now = utc_now()
    evidence = b"OFFLINE TEST FIXTURE; NOT AN ACTUAL DATA LICENSE."
    (tmp_path / "agreement.txt").write_bytes(evidence)
    item = {
        "source_id": "offline-maintenance", "enabled": True, "interval_seconds": 300,
        "max_attempts": 3, "allow_empty": False,
        "contract": {"agreement_path": "agreement.txt", "agreement_sha256": hashlib.sha256(evidence).hexdigest(),
                     "comparison": True, "display": True, "storage": True, "revoked": False,
                     "expires_at": iso_timestamp(now + timedelta(days=30))},
        "feed": {"type": "https", "url": "https://offline.invalid/feed", "allowed_origin": "https://offline.invalid"},
    }
    config = tmp_path / "suppliers.json"
    rewrite(config, item)
    path, directory = tmp_path / "live.sqlite3", tmp_path / "backups"
    snapshot = make_demo_snapshot("tokyo", now=now)
    source_id = snapshot["source"]["source_id"] if synthetic else item["source_id"]
    kind = "synthetic" if synthetic else "observed"
    snapshot["source"].update(source_id=source_id, independent_source_id=source_id, data_kind=kind)
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(now + timedelta(days=14))
    for row in snapshot["listings"]:
        row.update(source_id=source_id, data_kind=kind)
    open_store(path).import_snapshot(snapshot, now=now, allow_synthetic=synthetic)
    result = backup_store(path, directory, now=now)
    return config, item, path, directory, directory / result["manifest_file"]


def rewrite(config, item):
    config.write_text(json.dumps({"schema_version": "2.0", "suppliers": [item] if item else []}), encoding="utf-8")


def args(command, config, path, directory, manifest, *, apply=False):
    result = [command, "--suppliers", str(config)]
    if command == "restore":
        return result + ["--manifest", str(manifest), "--destination", str(path.parent / "restored.sqlite3")]
    result += ["--db", str(path), "--backup-dir", str(directory)]
    return result + (["--apply"] if apply else [])


def file_state(path, directory):
    return path.read_bytes(), {item.name: item.read_bytes() for item in directory.iterdir()}


@pytest.mark.parametrize("change", ["revoked", "disabled", "expired", "storage", "missing_evidence", "changed_evidence", "removed"])
def test_current_contract_denial_blocks_backup_restore_and_cleans_originals(tmp_path, capsys, change):
    config, item, path, directory, manifest = fixture(tmp_path)
    if change == "revoked":
        item["contract"]["revoked"] = True
    elif change == "disabled":
        item["enabled"] = False
    elif change == "expired":
        item["contract"]["expires_at"] = iso_timestamp(utc_now() - timedelta(seconds=1))
    elif change == "storage":
        item["contract"]["storage"] = False
    elif change == "missing_evidence":
        (tmp_path / "agreement.txt").unlink()
    elif change == "changed_evidence":
        (tmp_path / "agreement.txt").write_text("changed test evidence")
    rewrite(config, None if change == "removed" else item)
    before = file_state(path, directory)
    assert cli.main(args("backup", config, path, directory, manifest)) == 2
    assert cli.main(args("restore", config, path, directory, manifest)) == 2
    assert not (tmp_path / "restored.sqlite3").exists()
    assert file_state(path, directory) == before
    assert cli.main(args("cleanup", config, path, directory, manifest)) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["snapshot_count"] == planned["backup_count"] == 1
    assert file_state(path, directory) == before
    assert cli.main(args("cleanup", config, path, directory, manifest, apply=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["deleted_snapshot_count"] == result["deleted_backup_count"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM v2_current").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM v2_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("command", ["backup", "restore", "cleanup"])
@pytest.mark.parametrize("invalid", ['{"schema_version":', '{"schema_version":"2.0","suppliers":false}', '{"schema_version":"2.0","suppliers":[],"suppliers":[]}'])
def test_invalid_configuration_rejects_before_any_storage_change(tmp_path, command, invalid):
    config, _, path, directory, manifest = fixture(tmp_path)
    config.write_text(invalid)
    before = file_state(path, directory)
    assert cli.main(args(command, config, path, directory, manifest, apply=command == "cleanup")) == 2
    assert file_state(path, directory) == before
    assert not (tmp_path / "restored.sqlite3").exists()


def test_removed_source_found_in_backup_even_after_live_snapshot_is_absent(tmp_path, capsys):
    config, _, path, directory, manifest = fixture(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM v2_current")
    rewrite(config, None)
    assert cli.main(args("cleanup", config, path, directory, manifest, apply=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["deleted_snapshot_count"] == 0
    assert result["deleted_backup_count"] == 1


def test_synthetic_backups_restore_and_cleanup_do_not_require_supplier_contract(tmp_path, capsys):
    config, _, path, directory, manifest = fixture(tmp_path, synthetic=True)
    rewrite(config, None)
    assert cli.main(args("backup", config, path, directory, manifest)) == 0
    capsys.readouterr()
    assert cli.main(args("restore", config, path, directory, manifest)) == 0
    assert json.loads(capsys.readouterr().out)["listing_count"] == 24
    assert cli.main(args("cleanup", config, path, directory, manifest, apply=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["deleted_snapshot_count"] == result["deleted_backup_count"] == 0


def test_explicit_revocation_unioned_with_current_permitted_contract(tmp_path):
    config, item, path, directory, manifest = fixture(tmp_path)
    command = args("restore", config, path, directory, manifest) + ["--revoke-source", item["source_id"]]
    assert cli.main(command) == 2
    assert not (tmp_path / "restored.sqlite3").exists()


def test_permitted_observed_source_can_be_backed_up_and_restored(tmp_path):
    config, _, path, directory, manifest = fixture(tmp_path)
    assert cli.main(args("backup", config, path, directory, manifest)) == 0
    assert cli.main(args("restore", config, path, directory, manifest)) == 0


def test_cleanup_invalid_configuration_after_inventory_keeps_live_and_backups(tmp_path, monkeypatch):
    config, _, path, directory, manifest = fixture(tmp_path)
    original_inventory = cli._source_inventory
    def changed_inventory(database):
        result = original_inventory(database)
        config.write_text("broken during inventory")
        return result
    monkeypatch.setattr(cli, "_source_inventory", changed_inventory)
    before = file_state(path, directory)
    assert cli.main(args("cleanup", config, path, directory, manifest, apply=True)) == 2
    assert file_state(path, directory) == before


@pytest.mark.parametrize("invalid", [False, True])
def test_configuration_rechecked_after_inventory_before_action(tmp_path, monkeypatch, invalid):
    config, item, path, directory, manifest = fixture(tmp_path)
    original_inventory = cli._source_inventory
    def changed_inventory(database):
        result = original_inventory(database)
        if invalid:
            config.write_text("broken")
        else:
            item["contract"]["revoked"] = True
            rewrite(config, item)
        return result
    monkeypatch.setattr(cli, "_source_inventory", changed_inventory)
    before = file_state(path, directory)
    assert cli.main(args("backup", config, path, directory, manifest)) == 2
    assert file_state(path, directory) == before


def test_unvalidated_manifest_path_is_never_opened_as_database(tmp_path, monkeypatch):
    config, _, path, directory, manifest = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["database_file"] = "../live.sqlite3"
    manifest.write_text(json.dumps(data))
    monkeypatch.setattr(cli, "_source_inventory", lambda _: pytest.fail("unvalidated manifest must not open SQLite"))
    assert cli.main(args("restore", config, path, directory, manifest)) == 2
    assert not (tmp_path / "restored.sqlite3").exists()


def test_invalid_backup_blocks_contract_cleanup_without_partial_deletion(tmp_path):
    config, _, path, directory, manifest = fixture(tmp_path)
    rewrite(config, None)
    data = json.loads(manifest.read_text())
    data["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data))
    before = file_state(path, directory)
    assert cli.main(args("cleanup", config, path, directory, manifest, apply=True)) == 2
    assert file_state(path, directory) == before


def test_legacy_without_suppliers_retains_original_behavior(tmp_path):
    config, _, path, directory, manifest = fixture(tmp_path)
    rewrite(config, None)
    assert cli.main(["restore", "--manifest", str(manifest), "--destination", str(tmp_path / "restored.sqlite3")]) == 0
