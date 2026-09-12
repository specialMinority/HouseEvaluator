"""Offline ingestion boundary regressions: rights, replay, atomicity, identity."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend.v2.demo import demo_subject, make_demo_snapshot
from backend.v2.models import ValidationError, parse_timestamp, validate_observation, validate_snapshot, validate_subject
from backend.v2.store import SnapshotStore
from scripts.import_v2_snapshot import MAX_INPUT_BYTES, main as import_main


NOW = datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc)


def observed_snapshot(city="tokyo", now=NOW, source_id="licensed-fixture"):
    """Mark fixtures observed only inside tests; this claims no actual license."""
    snapshot = make_demo_snapshot(city, now=now)
    snapshot["source"].update(source_id=source_id, independent_source_id=source_id, data_kind="observed")
    for listing in snapshot["listings"]:
        listing.update(source_id=source_id, data_kind="observed")
    return snapshot


def test_unknown_characteristics_are_preserved_without_claims():
    result = validate_subject({"city": "tokyo", "municipality": "新宿区", "station_id": " station-A ", "layout": "1K", "area_sqm": 25, "rent_yen": 70000}, now=NOW)
    for field in ("structure", "built_year", "floor", "walk_min", "orientation", "bathroom_separate", "furnished", "mgmt_fee_yen", "station_name", "unit_id", "building_id"):
        assert result[field] is None
    assert result["station_id"] == "station-A"
    assert result["property_type"] == "apartment"
    assert result["contract_type"] == "standard"


@pytest.mark.parametrize("field,value", [
    ("rent_yen", True), ("rent_yen", 100.0), ("rent_yen", 0),
    ("mgmt_fee_yen", -1), ("mgmt_fee_yen", "0"), ("area_sqm", False),
    ("area_sqm", float("nan")), ("area_sqm", float("inf")),
    ("area_sqm", 10 ** 1000), ("walk_min", -0.1), ("walk_min", "5"),
    ("built_year", 2027), ("built_year", 2000.0), ("built_year", True),
    ("furnished", "false"), ("furnished", 0), ("bathroom_separate", 1),
    ("floor", 1.5), ("structure", "RC"), ("orientation", "south"),
    ("layout", "2LDK"), ("property_type", "share_house"), ("city", "kyoto"),
    ("station_id", ""), ("municipality", "\n"), ("unit_id", "a\x00b"),
    ("unexpected", "anything"),
])
def test_invalid_subjects_fail_loudly(field, value):
    subject = demo_subject("tokyo")
    subject[field] = value
    with pytest.raises(ValidationError):
        validate_subject(subject, now=NOW)


@pytest.mark.parametrize("value", [None, "2026-09-13", "2026-09-13T12:00:00", "2026-02-30T00:00:00Z", "2026-09-13 00:00:00Z", "2026-09-13T00:00:00+00:90", "2026-09-13T00:00:00+24:00"])
def test_timestamp_requires_real_date_and_explicit_timezone(value):
    with pytest.raises(ValidationError):
        parse_timestamp(value)


def test_timezone_normalization_and_naive_clock_rejection():
    assert parse_timestamp("2026-09-13T12:00:00+09:00") == NOW
    with pytest.raises(ValidationError):
        validate_subject(demo_subject("tokyo"), now=NOW.replace(tzinfo=None))


def test_identifiers_remain_exact_and_input_is_not_mutated():
    subject = demo_subject("tokyo")
    subject["station_id"] = "JP:小岩"
    original = deepcopy(subject)
    first = validate_subject(subject, now=NOW)
    second = validate_subject({**subject, "station_id": "JP:新小岩"}, now=NOW)
    assert first["station_id"] != second["station_id"]
    assert subject == original


@pytest.mark.parametrize("field,value", [
    ("received_at", "2026-09-13T03:00:01Z"),
    ("source_updated_at", "2026-09-13T03:00:01Z"),
    ("status_verified_at", "2026-09-13T03:00:01Z"),
    ("unit_id", None), ("building_id", ""), ("status", "available"),
    ("data_kind", "estimated"), ("public_url", "http://example.com/1"),
    ("public_url", "https://user:pass@example.com/1"),
    ("public_url", "https://example.com:bad/1"),
    ("public_url", "https://example.com\\@evil.test/1"),
])
def test_invalid_observation_fields(field, value):
    observation = observed_snapshot()["listings"][0]
    observation[field] = value
    with pytest.raises(ValidationError):
        validate_observation(observation, now=NOW)


def test_https_reference_and_nullable_observation_times():
    observation = observed_snapshot()["listings"][0]
    observation.update(public_url="https://example.test/listing/123", source_updated_at=None, status_verified_at=None)
    result = validate_observation(observation, now=NOW)
    assert result["public_url"] == observation["public_url"]
    assert result["source_updated_at"] is None and result["status_verified_at"] is None


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(complete=False),
    lambda s: s.update(complete=1),
    lambda s: s.update(schema_version=2.0),
    lambda s: s.update(validated_segments=["tokyo/1K"]),
    lambda s: s["source"].update(validated_segments=["tokyo/1K"]),
    lambda s: s["source"]["rights"].update(comparison=False),
    lambda s: s["source"]["rights"].update(display=1),
    lambda s: s["source"]["rights"].update(storage="true"),
    lambda s: s["source"]["rights"].update(expires_at="2026-09-13T03:00:00Z"),
    lambda s: s["listings"][0].update(source_id="different"),
    lambda s: s["listings"][0].update(data_kind="synthetic"),
    lambda s: s["listings"].append(deepcopy(s["listings"][0])),
    lambda s: s["listings"][0].update(received_at="2026-09-13T02:59:59Z"),
])
def test_snapshot_contract_and_rights_are_strict(mutation):
    snapshot = observed_snapshot(now=NOW - timedelta(minutes=30))
    mutation(snapshot)
    with pytest.raises(ValidationError):
        validate_snapshot(snapshot, now=NOW)


@pytest.mark.parametrize("city", ["tokyo", "osaka", "fukuoka"])
def test_demo_is_reproducible_explicit_and_never_self_compares(city):
    snapshot = make_demo_snapshot(city, now=NOW)
    assert snapshot == make_demo_snapshot(city, now=NOW)
    assert validate_snapshot(snapshot, now=NOW)["source"]["data_kind"] == "synthetic"
    units = {r["unit_id"] for r in snapshot["listings"]}
    buildings = {r["building_id"] for r in snapshot["listings"]}
    assert len(units) >= 24 and len(buildings) >= 12
    subject = demo_subject(city)
    assert subject["unit_id"] not in units and subject["building_id"] not in buildings
    assert all(row["public_url"] is None for row in snapshot["listings"])
    assert "합성" in snapshot["source"]["display_name"]


def test_synthetic_public_links_and_unknown_demo_city_rejected():
    observation = make_demo_snapshot("tokyo", now=NOW)["listings"][0]
    observation["public_url"] = "https://example.test/123"
    with pytest.raises(ValidationError):
        validate_observation(observation, now=NOW)
    with pytest.raises(ValidationError):
        demo_subject("kyoto")


def test_empty_store_is_not_market_data_and_mode_versions_differ(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite3")
    read = store.read_current(now=NOW)
    assert read["sources"] == [] and read["listings"] == [] and not read["is_demo"]
    assert read["snapshot_version"] != store.read_current(now=NOW, include_synthetic=True)["snapshot_version"]
    assert store.health(now=NOW)["market_data_available"] is False


def test_import_is_idempotent_content_based_and_preserves_metadata(tmp_path):
    snapshot = observed_snapshot()
    original = deepcopy(snapshot)
    store = SnapshotStore(tmp_path / "one.sqlite")
    result = store.import_snapshot(snapshot, now=NOW)
    before = store.read_current(now=NOW)
    snapshot["listings"].reverse()
    replay = store.import_snapshot(snapshot, now=NOW)
    assert result["status"] == "imported" and replay["status"] == "unchanged"
    assert store.read_current(now=NOW) == before
    assert before["sources"][0]["snapshot_id"] == snapshot["snapshot_id"]
    assert before["sources"][0]["independent_source_id"] == "licensed-fixture"
    assert before["sources"][0]["rights"]["storage"] is True
    other = SnapshotStore(tmp_path / "two.sqlite")
    other.import_snapshot(original, now=NOW)
    assert before["snapshot_version"] == other.read_current(now=NOW)["snapshot_version"]


def test_rights_expire_at_read_even_for_previously_accepted_snapshots(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    snapshot = observed_snapshot()
    snapshot["source"]["rights"]["expires_at"] = "2026-09-13T04:00:00Z"
    store.import_snapshot(snapshot, now=NOW)
    assert store.read_current(now=NOW)["listings"]
    expired = store.read_current(now=NOW + timedelta(hours=1))
    assert expired["sources"] == [] and expired["listings"] == []
    assert store.health(now=NOW + timedelta(hours=1))["unavailable_source_count"] == 1
    assert store.read_current(now=NOW - timedelta(seconds=1))["listings"] == []


def test_synthetic_is_denied_by_default_and_never_mixes_with_observed(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    demo = make_demo_snapshot("fukuoka", now=NOW)
    with pytest.raises(ValidationError):
        store.import_snapshot(demo, now=NOW)
    store.import_snapshot(demo, now=NOW, allow_synthetic=True)
    assert store.read_current(now=NOW)["listings"] == []
    store.import_snapshot(observed_snapshot(), now=NOW)
    market = store.read_current(now=NOW)
    synthetic = store.read_current(now=NOW, include_synthetic=True)
    assert len(market["listings"]) == 24 and len(synthetic["listings"]) == 24
    assert all(row["data_kind"] == "observed" for row in market["listings"])
    assert all(row["data_kind"] == "synthetic" for row in synthetic["listings"])
    assert synthetic["is_demo"] and not market["is_demo"]


def test_bad_partial_conflicting_or_old_snapshots_never_replace_current(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    first = observed_snapshot(now=NOW - timedelta(hours=2))
    second = observed_snapshot(now=NOW - timedelta(hours=1))
    store.import_snapshot(first, now=NOW)
    store.import_snapshot(second, now=NOW)
    expected = store.read_current(now=NOW)
    invalid = deepcopy(second)
    invalid["listings"][0]["rent_yen"] += 1000
    with pytest.raises(ValidationError):
        store.import_snapshot(invalid, now=NOW)
    equal = deepcopy(second)
    equal["snapshot_id"] = "different-id-equal-time"
    with pytest.raises(ValidationError):
        store.import_snapshot(equal, now=NOW)
    old = observed_snapshot(now=NOW - timedelta(hours=3))
    with pytest.raises(ValidationError):
        store.import_snapshot(old, now=NOW)
    partial = observed_snapshot()
    partial["complete"] = False
    with pytest.raises(ValidationError):
        store.import_snapshot(partial, now=NOW)
    # Historical replay acknowledges the old receipt, without rolling back.
    assert store.import_snapshot(first, now=NOW)["current_snapshot_id"] == second["snapshot_id"]
    assert store.read_current(now=NOW) == expected


def test_authoritative_replacement_removes_closed_and_deleted_inventory(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    first = observed_snapshot(now=NOW - timedelta(hours=1))
    store.import_snapshot(first, now=NOW)
    next_snapshot = observed_snapshot()
    next_snapshot["listings"] = [next_snapshot["listings"][0]]
    next_snapshot["listings"][0]["status"] = "closed"
    store.import_snapshot(next_snapshot, now=NOW)
    assert len(store.read_current(now=NOW)["listings"]) == 1
    assert store.read_current(now=NOW)["listings"][0]["status"] == "closed"
    empty = observed_snapshot(now=NOW + timedelta(minutes=1))
    empty["listings"] = []
    with pytest.raises(ValidationError):
        store.import_snapshot(empty, now=NOW + timedelta(minutes=1))
    assert len(store.read_current(now=NOW)["listings"]) == 1
    store.import_snapshot(empty, now=NOW + timedelta(minutes=1), allow_empty=True)
    assert store.read_current(now=NOW + timedelta(minutes=1))["listings"] == []


def test_source_kind_cannot_be_changed_and_options_are_strict(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    store.import_snapshot(observed_snapshot(now=NOW - timedelta(hours=1)), now=NOW)
    changed = make_demo_snapshot("tokyo", now=NOW)
    changed["source"]["source_id"] = "licensed-fixture"
    for row in changed["listings"]:
        row["source_id"] = "licensed-fixture"
    with pytest.raises(ValidationError):
        store.import_snapshot(changed, now=NOW, allow_synthetic=True)
    with pytest.raises(ValidationError):
        store.read_current(now=NOW, include_synthetic="false")
    with pytest.raises(ValidationError):
        store.import_snapshot(observed_snapshot(), now=NOW, allow_empty=1)


def test_import_is_atomic_when_sqlite_write_fails(tmp_path):
    path = tmp_path / "data.sqlite"
    store = SnapshotStore(path)
    store.import_snapshot(observed_snapshot(now=NOW - timedelta(hours=1)), now=NOW)
    before = store.read_current(now=NOW)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_current BEFORE UPDATE ON v2_current BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.import_snapshot(observed_snapshot(), now=NOW)
    assert store.read_current(now=NOW) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM v2_receipts").fetchone()[0] == 1


def test_tampered_snapshot_fails_closed_and_health_has_no_paths(tmp_path):
    path = tmp_path / "data.sqlite"
    store = SnapshotStore(path)
    store.import_snapshot(observed_snapshot(), now=NOW)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT payload FROM v2_current").fetchone()[0]
        tampered = json.loads(row)
        tampered["listings"][0]["rent_yen"] = 1
        db.execute("UPDATE v2_current SET payload = ?", (json.dumps(tampered),))
    assert store.read_current(now=NOW)["listings"] == []
    health = store.health(now=NOW)
    assert health["unavailable_source_count"] == 1
    assert str(tmp_path) not in json.dumps(health)
    assert "licensed-fixture" not in json.dumps(health)


def test_concurrent_replays_use_per_call_connections(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    snapshot = observed_snapshot()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.import_snapshot(snapshot, now=NOW), range(12)))
    assert sum(result["status"] == "imported" for result in results) == 1
    assert len(store.read_current(now=NOW)["listings"]) == 24


@pytest.mark.parametrize("path", ["https://example.test/data.sqlite", "file:secret?mode=ro", "//server/share/data.sqlite", "\\\\server\\share\\data.sqlite", "", None, 7, b"data.sqlite"])
def test_store_rejects_nonlocal_paths(path):
    with pytest.raises(ValidationError):
        SnapshotStore(path)


def test_cli_runs_from_any_cwd_with_explicit_synthetic_opt_in(tmp_path):
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps(make_demo_snapshot("osaka")), encoding="utf-8")
    database = tmp_path / "out.sqlite"
    script = Path(__file__).resolve().parents[2] / "scripts" / "import_v2_snapshot.py"
    base = [sys.executable, str(script), str(source), "--db", str(database)]
    denied = subprocess.run(base, cwd=tmp_path, capture_output=True, text=True)
    assert denied.returncode == 2 and "synthetic" in denied.stderr
    accepted = subprocess.run(base + ["--allow-synthetic"], cwd=tmp_path, capture_output=True, text=True)
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["listing_count"] == 24
    store = SnapshotStore(database)
    assert store.read_current()["listings"] == []
    assert len(store.read_current(include_synthetic=True)["listings"]) == 24


@pytest.mark.parametrize("contents", ['{"schema_version":"2.0","schema_version":"2.0"}', '{"rent_yen":NaN}', '{bad JSON', '"not an envelope"'])
def test_cli_rejects_duplicate_keys_nonfinite_and_malformed_input(tmp_path, contents, capsys):
    source = tmp_path / "snapshot.json"
    source.write_text(contents, encoding="utf-8")
    assert import_main([str(source), "--db", str(tmp_path / "out.sqlite")]) == 2
    assert "Import rejected" in capsys.readouterr().err


def test_cli_size_limit_and_no_http(tmp_path, capsys):
    source = tmp_path / "oversize.json"
    with source.open("wb") as stream:
        stream.truncate(MAX_INPUT_BYTES + 1)
    assert import_main([str(source), "--db", str(tmp_path / "out.sqlite")]) == 2
    assert "20 MiB" in capsys.readouterr().err
    assert import_main(["https://example.test/snapshot.json", "--db", str(tmp_path / "out.sqlite")]) == 2
    assert "local filesystem" in capsys.readouterr().err
