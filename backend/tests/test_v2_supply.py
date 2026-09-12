"""Offline ingestion/lease/security tests; fixtures are not licensed market data."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import socket
import sqlite3
import time
from threading import Event, Thread

import pytest

from backend.v2.demo import demo_subject, make_demo_snapshot
from backend.v2.models import iso_timestamp
from backend.v2.store import SnapshotStore
from backend.v2.supply import (
    FetchResult, IngestionRunner, SupplyError, _PinnedHTTPSConnection,
    _public_addresses, fetch_feed, load_suppliers, monitor_status,
    permitted_source_ids,
)


NOW = datetime(2026, 9, 13, 3, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def setup(tmp_path, *, enabled=True):
    evidence = b"OFFLINE TEST EVIDENCE. Not an actual data license."
    (tmp_path / "agreement.txt").write_bytes(evidence)
    item = {
        "source_id": "offline-fixture", "enabled": enabled, "interval_seconds": 300,
        "max_attempts": 3, "allow_empty": False,
        "contract": {"agreement_path": "agreement.txt", "agreement_sha256": hashlib.sha256(evidence).hexdigest(),
                     "comparison": True, "display": True, "storage": True, "revoked": False,
                     "expires_at": iso_timestamp(NOW + timedelta(days=30))},
        "feed": {"type": "local", "path": "snapshot.json"},
    }
    path = tmp_path / "suppliers.json"
    path.write_text(json.dumps({"schema_version": "2.0", "suppliers": [item]}), encoding="utf-8")
    snapshot = make_demo_snapshot("tokyo", now=NOW)
    snapshot["source"].update(source_id=item["source_id"], independent_source_id=item["source_id"], data_kind="observed")
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(NOW + timedelta(days=14))
    for row in snapshot["listings"]:
        row.update(source_id=item["source_id"], data_kind="observed")
    (tmp_path / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return path, item, snapshot


def rewrite(path, item):
    path.write_text(json.dumps({"schema_version": "2.0", "suppliers": [item]}), encoding="utf-8")


def runner_for(tmp_path, path, clock, fetcher=None):
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    return IngestionRunner(path, tmp_path / "queue.sqlite3", store, fetcher=fetcher, clock=clock)


def event(result):
    return result["outcomes"][0]["event_code"]


def test_local_ingestion_is_durable_and_due_time_survives_restart(tmp_path):
    path, _, _ = setup(tmp_path)
    clock = Clock()
    runner = runner_for(tmp_path, path, clock)
    assert event(runner.run_once()) == "snapshot_imported"
    assert len(runner.store.read_current(now=NOW)["listings"]) == 24
    again = runner_for(tmp_path, path, clock)
    assert again.run_once()["attempted"] == 0
    clock.advance(300)
    assert event(again.run_once()) == "snapshot_unchanged"
    source = monitor_status(again.state_db, now=clock())["sources"][0]
    assert source["last_success"] == iso_timestamp(clock())
    assert source["snapshot_as_of"] == iso_timestamp(NOW)
    assert source["latest_status_verified_at"] == iso_timestamp(NOW - timedelta(minutes=30))


def test_304_does_not_refresh_snapshot_or_comparable_verification(tmp_path):
    path, _, _ = setup(tmp_path)
    clock = Clock()
    runner = runner_for(tmp_path, path, clock)
    runner.run_once()
    before = runner.store.read_current(now=NOW)
    runner.fetcher = lambda _: FetchResult(304)
    clock.advance(25 * 3600)
    assert event(runner.run_once()) == "not_modified"
    after = runner.store.read_current(now=clock())
    assert before["snapshot_version"] == after["snapshot_version"]
    assert before["listings"] == after["listings"]
    source = monitor_status(runner.state_db, now=clock())["sources"][0]
    assert source["freshness"] == "stale"
    assert source["latest_status_verified_at"] == iso_timestamp(NOW - timedelta(minutes=30))


def test_304_without_previously_accepted_snapshot_fails(tmp_path):
    path, _, _ = setup(tmp_path)
    runner = runner_for(tmp_path, path, Clock(), lambda _: FetchResult(304))
    assert event(runner.run_once()) == "not_modified_without_snapshot"
    assert not runner.store.read_current(now=NOW)["sources"]


@pytest.mark.parametrize("change,code", [
    (lambda x: x.update(schema_version="3.0"), "snapshot_invalid"),
    (lambda x: x.update(complete=False), "snapshot_invalid"),
    (lambda x: x["source"].update(source_id="different"), "source_mismatch"),
    (lambda x: x["source"]["rights"].update(display=False), "snapshot_invalid"),
    (lambda x: x["source"]["rights"].update(expires_at=iso_timestamp(NOW + timedelta(days=90))), "rights_exceed_contract"),
    (lambda x: x["source"].update(data_kind="synthetic"), "snapshot_invalid"),
    (lambda x: x.update(listings=[]), "snapshot_invalid"),
])
def test_failed_new_snapshot_keeps_previous_current(tmp_path, change, code):
    path, _, snapshot = setup(tmp_path)
    clock = Clock()
    runner = runner_for(tmp_path, path, clock)
    runner.run_once()
    before = runner.store.read_current(now=NOW)
    changed = deepcopy(snapshot)
    changed.update(snapshot_id="new", as_of=iso_timestamp(NOW + timedelta(minutes=5)))
    change(changed)
    runner.fetcher = lambda _: FetchResult(200, json.dumps(changed).encode())
    clock.advance(300)
    assert event(runner.run_once()) == code
    assert runner.store.read_current(now=clock()) == before


def test_empty_snapshot_needs_operator_allow_empty(tmp_path):
    path, item, snapshot = setup(tmp_path)
    snapshot["listings"] = []
    item["allow_empty"] = True
    rewrite(path, item)
    runner = runner_for(tmp_path, path, Clock(), lambda _: FetchResult(200, json.dumps(snapshot).encode()))
    assert event(runner.run_once()) == "snapshot_imported"
    assert runner.store.read_current(now=NOW)["sources"]
    assert not runner.store.read_current(now=NOW)["listings"]


@pytest.mark.parametrize("mutate,status", [
    (lambda i: i.update(enabled=False), "disabled"),
    (lambda i: i["contract"].update(revoked=True), "revoked"),
    (lambda i: i["contract"].update(storage=False), "permissions_missing"),
    (lambda i: i["contract"].update(expires_at=iso_timestamp(NOW)), "expired"),
    (lambda i: i["contract"].update(agreement_sha256="0" * 64), "evidence_mismatch"),
    (lambda i: i["contract"].update(agreement_path="absent.txt"), "evidence_missing"),
])
def test_contract_denial_prevents_fetch_and_runtime_permission(tmp_path, mutate, status):
    path, item, _ = setup(tmp_path)
    mutate(item)
    rewrite(path, item)
    def never(_):
        pytest.fail("Denied contracts must never be fetched")
    runner = runner_for(tmp_path, path, Clock(), never)
    assert runner.run_once()["attempted"] == 0
    assert permitted_source_ids(path, now=NOW) == set()
    assert monitor_status(runner.state_db, now=NOW)["sources"][0]["contract_status"] == status


def test_contract_is_rechecked_after_fetch_and_current_is_unchanged(tmp_path):
    path, item, snapshot = setup(tmp_path)
    def revoke(_):
        item["contract"]["revoked"] = True
        rewrite(path, item)
        return FetchResult(200, json.dumps(snapshot).encode())
    runner = runner_for(tmp_path, path, Clock(), revoke)
    assert event(runner.run_once()) == "contract_changed"
    assert not runner.store.read_current(now=NOW)["sources"]
    assert permitted_source_ids(path, now=NOW) == set()


def test_live_contract_check_detects_evidence_replacement_and_missing_config(tmp_path):
    path, _, _ = setup(tmp_path)
    assert permitted_source_ids(path, now=NOW) == {"offline-fixture"}
    (tmp_path / "agreement.txt").write_bytes(b"Changed after review")
    assert permitted_source_ids(path, now=NOW) == set()
    assert permitted_source_ids(None, now=NOW) == set()
    path.write_text("{}", encoding="utf-8")
    assert permitted_source_ids(path, now=NOW) == set()


def test_retry_backoff_exhaustion_redaction_and_explicit_resume(tmp_path):
    path, _, _ = setup(tmp_path)
    clock = Clock()
    secret = "Bearer SECRET token https://private.example/feed C:\\private\\agreement"
    def fail(_):
        raise RuntimeError(secret)
    runner = runner_for(tmp_path, path, clock, fail)
    assert event(runner.run_once()) == "ingestion_failed"
    row = monitor_status(runner.state_db, now=clock())["sources"][0]
    assert row["next_due"] == iso_timestamp(NOW + timedelta(seconds=30))
    assert runner.run_once()["attempted"] == 0
    clock.advance(30)
    runner.run_once()
    row = monitor_status(runner.state_db, now=clock())["sources"][0]
    assert row["next_due"] == iso_timestamp(NOW + timedelta(seconds=90))
    clock.advance(60)
    runner.run_once()
    state = monitor_status(runner.state_db, now=clock())
    assert state["sources"][0]["retry_exhausted"] is True
    clock.advance(9999)
    assert runner.run_once()["attempted"] == 0
    assert secret not in json.dumps(state)
    with sqlite3.connect(runner.state_db) as db:
        dump = "\n".join(db.iterdump())
    assert "SECRET" not in dump and "private.example" not in dump and "agreement.txt" not in dump
    runner.resume("offline-fixture")
    assert runner.run_once()["attempted"] == 1
    assert SupplyError(secret).code == "ingestion_failed"


def test_same_source_cannot_be_leased_by_two_workers(tmp_path):
    path, _, snapshot = setup(tmp_path)
    clock = Clock()
    started, finish = Event(), Event()
    def slow(_):
        started.set()
        assert finish.wait(timeout=5)
        return FetchResult(200, json.dumps(snapshot).encode())
    first = runner_for(tmp_path, path, clock, slow)
    second = runner_for(tmp_path, path, clock, lambda _: pytest.fail("lease overlap"))
    results = []
    thread = Thread(target=lambda: results.append(first.run_once()))
    thread.start()
    try:
        assert started.wait(timeout=5)
        assert monitor_status(first.state_db, now=NOW)["sources"][0]["lease_active"] is True
        assert second.run_once()["attempted"] == 0
    finally:
        finish.set()
        thread.join(timeout=5)
    assert event(results[0]) == "snapshot_imported"


def test_expired_lease_cannot_import_late_fetch_result(tmp_path):
    path, _, snapshot = setup(tmp_path)
    clock = Clock()
    def too_late(_):
        clock.advance(121)
        return FetchResult(200, json.dumps(snapshot).encode())
    runner = runner_for(tmp_path, path, clock, too_late)
    assert event(runner.run_once()) == "lease_lost"
    assert not runner.store.read_current(now=clock())["listings"]


def test_removed_source_is_marked_without_deleting_accepted_snapshot(tmp_path):
    path, _, _ = setup(tmp_path)
    runner = runner_for(tmp_path, path, Clock())
    runner.run_once()
    path.write_text('{"schema_version":"2.0","suppliers":[]}', encoding="utf-8")
    result = runner.run_once()
    assert result["attempted"] == 0
    assert result["monitor"]["sources"][0]["contract_status"] == "removed"
    assert permitted_source_ids(path, now=NOW) == set()
    assert runner.store.read_current(now=NOW)["sources"]


@pytest.mark.parametrize("url", [
    "http://feed.example/snapshot", "https://user:pass@feed.example/snapshot",
    "https://feed.example:8443/snapshot", "https://feed.example/snapshot?token=secret",
    "https://feed.example/snapshot#fragment", "https://feed.example./snapshot",
    "https://another.example/snapshot", "https://feed.example\\@another.example/snapshot",
])
def test_https_origin_and_credential_url_restrictions(tmp_path, url):
    path, item, _ = setup(tmp_path)
    item["feed"] = {"type": "https", "url": url, "allowed_origin": "https://feed.example"}
    rewrite(path, item)
    with pytest.raises(SupplyError):
        load_suppliers(path)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "0.0.0.0", "192.168.1.2", "::1", "fc00::1", "::ffff:8.8.8.8"])
def test_dns_private_or_mapped_targets_denied(address):
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    with pytest.raises(SupplyError, match="network_target_denied"):
        _public_addresses("feed.example", 443, resolver=resolver)


def test_dns_mixed_public_private_answers_are_rejected():
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 443)) for value in ("8.8.8.8", "10.1.0.2")]
    with pytest.raises(SupplyError, match="network_target_denied"):
        _public_addresses("feed.example", 443, resolver=resolver)


def test_connection_pins_validated_ip_and_keeps_original_tls_hostname(monkeypatch):
    seen = {}
    class FakeSocket:
        def close(self):
            pass
        def do_handshake(self):
            seen["handshake"] = True
    def connect(target, timeout):
        seen["target"] = target
        return FakeSocket()
    class Context:
        def wrap_socket(self, sock, server_hostname, do_handshake_on_connect):
            seen["server_hostname"] = server_hostname
            assert do_handshake_on_connect is False
            return sock
    monkeypatch.setattr(socket, "create_connection", connect)
    connection = _PinnedHTTPSConnection("feed.example", "8.8.8.8", timeout=3)
    connection._context = Context()
    connection.connect()
    assert seen == {"target": ("8.8.8.8", 443), "server_hostname": "feed.example", "handshake": True}
    connection.close()


def test_https_redirect_is_denied_without_second_request(tmp_path, monkeypatch):
    path, item, _ = setup(tmp_path)
    item["feed"] = {"type": "https", "url": "https://feed.example/snapshot", "allowed_origin": "https://feed.example", "auth_env": "FEED_FIXTURE_TOKEN"}
    rewrite(path, item)
    monkeypatch.setenv("FEED_FIXTURE_TOKEN", "secret-value")
    import backend.v2.supply as supply
    monkeypatch.setattr(supply, "_bounded_dns", lambda *args: ["8.8.8.8"])
    requests = []
    class Response:
        status = 302
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            requests.append((args, kwargs))
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(supply, "_PinnedHTTPSConnection", Connection)
    with pytest.raises(SupplyError, match="redirect_denied"):
        fetch_feed(load_suppliers(path)[0])
    assert len(requests) == 1
    assert requests[0][1]["headers"]["Authorization"] == "Bearer secret-value"


def test_json_duplicate_keys_nonfinite_and_oversize_rejected(tmp_path):
    path, _, _ = setup(tmp_path)
    for raw in (b'{"source":{},"source":{}}', b'{"source":NaN}'):
        runner = runner_for(tmp_path, path, Clock(), lambda _, raw=raw: FetchResult(200, raw))
        runner.run_once()
        source = monitor_status(runner.state_db, now=NOW)["sources"][0]
        assert source["last_error_code"] == "invalid_json"
        runner.resume("offline-fixture")


def test_monitor_missing_db_does_not_create_file(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert monitor_status(path, now=NOW)["status"] == "not_configured"
    assert not path.exists()


def test_abandoned_lease_is_reclaimed_and_delayed_worker_is_visible(tmp_path):
    path, _, _ = setup(tmp_path)
    clock = Clock()
    first = runner_for(tmp_path, path, clock)
    items = load_suppliers(path)
    first._sync(items, NOW)
    assert first._claim(items[0], NOW)
    second = runner_for(tmp_path, path, clock)
    assert second.run_once()["attempted"] == 0
    clock.advance(121)
    pending = monitor_status(first.state_db, now=clock())
    assert pending["sources"][0]["worker_overdue"] is True
    assert event(second.run_once()) == "snapshot_imported"
    assert monitor_status(first.state_db, now=clock())["sources"][0]["worker_overdue"] is False


def test_oversized_fetch_keeps_store_empty(tmp_path, monkeypatch):
    path, _, _ = setup(tmp_path)
    import backend.v2.supply as supply
    monkeypatch.setattr(supply, "MAX_BYTES", 64)
    runner = runner_for(tmp_path, path, Clock(), lambda _: FetchResult(200, b"x" * 65))
    assert event(runner.run_once()) == "payload_too_large"
    assert not runner.store.read_current(now=NOW)["sources"]


def test_runtime_immediately_removes_revoked_contract_across_read_paths(tmp_path):
    from backend.v2.service import Runtime
    path, item, _ = setup(tmp_path)
    runner = runner_for(tmp_path, path, Clock())
    runner.run_once()
    runtime = Runtime(tmp_path / "snapshots.sqlite3", suppliers_path=path, state_db=runner.state_db)
    assert runtime.capabilities(now=NOW)["market_data_available"] is True
    assert runtime.stations("tokyo", now=NOW)["stations"]
    item["contract"]["revoked"] = True
    rewrite(path, item)
    # No ingestion pass is needed to stop usage of an early-revoked source.
    assert runtime.capabilities(now=NOW)["market_data_available"] is False
    assert not runtime.market_data(now=NOW)["sources"]
    assert not runtime.stations("tokyo", now=NOW)["stations"]
    assert runtime.health(now=NOW)["contract_excluded_source_count"] == 1
    result = runtime.evaluate({"subject": demo_subject("tokyo")}, now=NOW)
    assert result["sample"]["unit_count"] == 0
    assert result["judgment"] is None


@pytest.mark.parametrize("response,code", [(304, "not_modified_without_snapshot"), (200, "current_snapshot_missing")])
def test_purged_snapshot_cannot_be_reported_healthy_on_unchanged_response(tmp_path, response, code):
    path, _, snapshot = setup(tmp_path)
    clock = Clock()
    runner = runner_for(tmp_path, path, clock)
    runner.run_once()
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as db:
        db.execute("DELETE FROM v2_current WHERE source_id='offline-fixture'")
    runner.fetcher = lambda _: FetchResult(response, json.dumps(snapshot).encode() if response == 200 else b"")
    clock.advance(300)
    result = runner.run_once()
    assert event(result) == code
    assert result["monitor"]["status"] == "needs_attention"
    assert result["monitor"]["sources"][0]["snapshot_as_of"] is None
    assert result["monitor"]["sources"][0]["freshness"] == "unknown"
    assert not runner.store.read_current(now=clock())["sources"]


def test_second_supplier_revoked_during_first_fetch_is_not_contacted(tmp_path):
    path, first_item, snapshot = setup(tmp_path)
    second_item = deepcopy(first_item)
    second_item["source_id"] = "second-source"
    config = {"schema_version": "2.0", "suppliers": [first_item, second_item]}
    path.write_text(json.dumps(config), encoding="utf-8")
    requests = []
    def fetch(item):
        requests.append(item["source_id"])
        second_item["contract"]["revoked"] = True
        path.write_text(json.dumps(config), encoding="utf-8")
        return FetchResult(200, json.dumps(snapshot).encode())
    runner = runner_for(tmp_path, path, Clock(), fetch)
    result = runner.run_once()
    assert requests == ["offline-fixture"]
    assert result["outcomes"] == [{"source_id": "offline-fixture", "event_code": "snapshot_imported"}, {"source_id": "second-source", "event_code": "contract_changed"}]


def test_queue_and_snapshot_same_file_rejected_before_any_lease(tmp_path):
    path, _, _ = setup(tmp_path)
    store = SnapshotStore(tmp_path / "same.sqlite3")
    with pytest.raises(SupplyError, match="storage_path_conflict"):
        IngestionRunner(path, tmp_path / "same.sqlite3", store)


def test_cli_empty_config_status_and_no_network(tmp_path, monkeypatch, capsys):
    from scripts.run_v2_ingestion import main
    import backend.v2.supply as supply
    config = tmp_path / "empty.json"
    config.write_text('{"schema_version":"2.0","suppliers":[]}', encoding="utf-8")
    monkeypatch.setattr(supply, "fetch_feed", lambda _: pytest.fail("Empty configuration must not fetch"))
    db, queue_path = tmp_path / "snapshots.db", tmp_path / "queue.db"
    assert main(["--config", str(config), "--db", str(db), "--state-db", str(queue_path), "--once"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["attempted"] == 0
    assert result["monitor"]["status"] == "not_configured"
    assert main(["--state-db", str(queue_path), "--status"]) == 0
    assert json.loads(capsys.readouterr().out)["source_count"] == 0


@pytest.mark.parametrize("blocked_phase", ["headers", "body"])
def test_https_watchdog_interrupts_total_request_including_drip_headers(tmp_path, monkeypatch, blocked_phase):
    path, item, _ = setup(tmp_path)
    item["feed"] = {"type": "https", "url": "https://feed.example/snapshot", "allowed_origin": "https://feed.example"}
    rewrite(path, item)
    import backend.v2.supply as supply
    monkeypatch.setattr(supply, "_bounded_dns", lambda *args: ["8.8.8.8"])
    monkeypatch.setattr(supply, "FETCH_SECONDS", 0.05)
    interrupted = Event()
    class Transport:
        def shutdown(self, mode):
            interrupted.set()
        def settimeout(self, remaining):
            pass
    class Response:
        status = 304 if blocked_phase == "headers" else 200
        def getheader(self, name, default=None):
            return "application/json" if name == "Content-Type" else default
        def read(self, count):
            assert interrupted.wait(timeout=1)
            return b""
    class Connection:
        def __init__(self, *args, **kwargs):
            self._transport = self.sock = Transport()
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            if blocked_phase == "headers":
                assert interrupted.wait(timeout=1)
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(supply, "_PinnedHTTPSConnection", Connection)
    started = time.monotonic()
    with pytest.raises(SupplyError, match="fetch_timeout"):
        fetch_feed(load_suppliers(path)[0])
    assert interrupted.is_set()
    assert time.monotonic() - started < 0.8


def test_config_rejects_evidence_path_escape(tmp_path):
    path, item, _ = setup(tmp_path)
    item["contract"]["agreement_path"] = "../not-in-config-dir.txt"
    rewrite(path, item)
    with pytest.raises(SupplyError, match="configuration_invalid"):
        load_suppliers(path)
