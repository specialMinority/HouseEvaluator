"""Regression proofs for independently reviewed release and request boundaries."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPConnection
import json
import socket
import threading
import time

import pytest

from backend.src.server import create_server
from backend.v2.approval import REQUIRED_MARKET_GATES, approved_segments, policy_digest
from backend.v2.demo import make_demo_snapshot
from backend.v2.release import readiness

NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
TOKEN = "test-only-release-review-access-token-0000"


def approval_fixture(folder, gates):
    report = {"schema_version": "market-validation-1.0", "report_kind": "market", "policy_version": "direct-v2.0",
              "policy_sha256": policy_digest(), "source_ids": ["test-only-source"], "generated_at": NOW.isoformat(),
              "eligibility": {"passed": True}, "eligible_segments": ["tokyo/1K"],
              "segments": {"tokyo/1K": {"eligible_for_approval": True, "gates": gates}}}
    raw = json.dumps(report).encode()
    (folder / "report.json").write_bytes(raw)
    entry = {"policy_version": "direct-v2.0", "approved": True, "approved_by": "test-only-operator",
             "source_ids": ["test-only-source"], "validated_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat(),
             "report_path": "report.json", "segments": ["tokyo/1K"], "report_sha256": hashlib.sha256(raw).hexdigest()}
    registry = folder / "registry.json"
    registry.write_text(json.dumps({"schema_version": "2.0", "reports": [entry]}), encoding="utf-8")
    return registry


@pytest.mark.parametrize("failed_gate", sorted(REQUIRED_MARKET_GATES))
def test_every_market_evidence_gate_must_pass_even_when_summary_claims_success(tmp_path, failed_gate):
    gates = dict.fromkeys(REQUIRED_MARKET_GATES, True)
    gates[failed_gate] = False
    registry = approval_fixture(tmp_path, gates)
    assert approved_segments(registry, {"test-only-source"}, NOW) == set()


@pytest.mark.parametrize("gates", [{}, {"thirty_day_supply": True}, dict.fromkeys(REQUIRED_MARKET_GATES, 1)])
def test_missing_or_nonboolean_evidence_gate_never_approves(tmp_path, gates):
    registry = approval_fixture(tmp_path, gates)
    assert approved_segments(registry, {"test-only-source"}, NOW) == set()


def test_complete_positive_evidence_structure_can_be_reviewed(tmp_path):
    registry = approval_fixture(tmp_path, dict.fromkeys(REQUIRED_MARKET_GATES, True))
    assert approved_segments(registry, {"test-only-source"}, NOW) == {"tokyo/1K"}


def fake_runtime(tmp_path, rows):
    evidence = tmp_path / "release.json"
    evidence.write_text(json.dumps({"budget_confirmed_by": "test-only", "monthly_cost_yen": 0, "operator_release_approved": True}), encoding="utf-8")
    class FixtureRuntime:
        suppliers_path = tmp_path / "suppliers.json"
        registry_path = tmp_path / "registry.json"
        release_evidence_path = evidence
        demo_enabled = False

        def market_data(self, *, now):
            return {"sources": [{"source_id": "test-only-source"}], "listings": rows}

        def supply_status(self, *, now):
            return {"sources": [{"source_id": "test-only-source", "last_success": now.isoformat(), "contract_status": "valid",
                                 "worker_overdue": False, "consecutive_failures": 0}]}
    return FixtureRuntime()


@pytest.mark.parametrize("bad_case", ["stale", "too_few_units", "single_building", "missing_management_fee", "missing_furnished"])
def test_fresh_unapproved_city_cannot_hide_unavailable_approved_segment(tmp_path, monkeypatch, bad_case):
    tokyo = copy.deepcopy(make_demo_snapshot("tokyo", now=NOW)["listings"])
    osaka = make_demo_snapshot("osaka", now=NOW)["listings"]
    if bad_case == "stale":
        for row in tokyo:
            row["status_verified_at"] = (NOW - timedelta(days=2)).isoformat()
    elif bad_case == "too_few_units":
        tokyo = tokyo[:19]
    elif bad_case == "single_building":
        for row in tokyo:
            row["building_id"] = "test-only-one-building"
    else:
        field = "mgmt_fee_yen" if bad_case == "missing_management_fee" else "furnished"
        for row in tokyo:
            row[field] = None
    monkeypatch.setattr("backend.v2.service.approved_segments", lambda *args: {"tokyo/1K"})
    monkeypatch.setattr("backend.v2.release.checked_evidence", lambda *args, **kwargs: True)
    monkeypatch.setattr("backend.v2.release.context_fingerprint", lambda *args: "test-only-context")
    result = readiness(fake_runtime(tmp_path, tokyo + osaka), now=NOW, access_protected=True)
    assert result["fresh_listing_count"] > 0
    assert result["checks"]["fresh_observed_listings"] is False
    assert result["ready"] is False
    assert result["segment_availability"]["tokyo/1K"]["minimum_available"] is False


def test_current_complete_approved_segment_can_satisfy_availability(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.v2.service.approved_segments", lambda *args: {"tokyo/1K"})
    monkeypatch.setattr("backend.v2.release.checked_evidence", lambda *args, **kwargs: True)
    monkeypatch.setattr("backend.v2.release.context_fingerprint", lambda *args: "test-only-context")
    result = readiness(fake_runtime(tmp_path, make_demo_snapshot("tokyo", now=NOW)["listings"]), now=NOW, access_protected=True)
    assert result["checks"]["fresh_observed_listings"] is True
    assert result["segment_availability"]["tokyo/1K"]["unit_count"] == 24
    assert result["ready"] is True  # all external proofs intentionally mocked; this is not market evidence


@pytest.mark.parametrize("phase", ["header", "body"])
def test_absolute_request_deadline_releases_slot_despite_continuous_drip(tmp_path, phase):
    server = create_server(port=0, db_path=tmp_path / "db.sqlite3", access_token=TOKEN)
    server.request_deadline_seconds = 0.2
    server._slots = threading.BoundedSemaphore(1)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    attacker = socket.create_connection(server.server_address, timeout=1)
    stopped = threading.Event()
    disconnected = threading.Event()
    if phase == "header":
        initial = b"GET /api/v2/health HTTP/1.1\r\nHost: localhost\r\nX-Test: "
    else:
        initial = ("POST /api/v2/costs HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + TOKEN
                   + "\r\nContent-Type: application/json\r\nContent-Length: 10000\r\n\r\n{").encode()
    attacker.sendall(initial)

    def drip():
        while not stopped.wait(0.03):
            try:
                attacker.sendall(b" ")
            except OSError:
                disconnected.set()
                return

    dripper = threading.Thread(target=drip, daemon=True)
    dripper.start()
    try:
        time.sleep(0.6)
        connection = HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request("GET", "/api/v2/health", headers={"Authorization": "Bearer " + TOKEN})
            response = connection.getresponse()
            response.read()
            assert response.status == 200
        finally:
            connection.close()
        assert disconnected.is_set(), "continuous bytes must not extend the absolute deadline"
    finally:
        stopped.set()
        attacker.close()
        dripper.join(1)
        server.shutdown()
        server.server_close()
        worker.join(2)
