"""Independent regressions for HTTP review findings and runtime boundaries."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
import socket
import threading

import pytest

from backend.src.server import MAX_BODY, create_server
from backend.v2.demo import demo_subject, make_demo_snapshot
from backend.v2.models import iso_timestamp, parse_timestamp
from backend.v2.service import Runtime


NOW = datetime(2026, 9, 13, 3, tzinfo=timezone.utc)


@contextmanager
def running_server(path, *, demo_enabled=False, legacy_enabled=False):
    server = create_server(port=0, db_path=path, demo_enabled=demo_enabled, legacy_enabled=legacy_enabled)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method, path, body=None):
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw) if raw and "application/json" in response.getheader("Content-Type", "") else raw
        return response.status, parsed, dict(response.getheaders())
    finally:
        connection.close()


@pytest.mark.parametrize("endpoint", ["/api/v2/evaluate", "/api/v2/costs"])
def test_deep_json_below_body_limit_returns_400(tmp_path, endpoint):
    # Python 3.14's JSON parser accepts thousands of nested arrays before its
    # native stack guard trips; 30,000 reproduces the reviewed 500 regression.
    body = b'{"subject":' + b"[" * 30000 + b"]" * 30000 + b"}"
    assert len(body) < MAX_BODY
    with running_server(tmp_path / "nested.sqlite") as server:
        status, response, _ = request(server, "POST", endpoint, body)
        assert status == 400
        assert response["error"] != "internal_error"
        assert "Traceback" not in json.dumps(response)
        # A malformed body must not poison subsequent requests or worker slots.
        assert request(server, "GET", "/api/v2/health")[0] == 200


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_malformed_absolute_request_target_returns_json_400(tmp_path, method):
    with running_server(tmp_path / "url.sqlite") as server:
        with socket.create_connection(server.server_address, timeout=5) as connection:
            wire = f"{method} http://[ HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n".encode("ascii")
            if method == "POST":
                wire += b"Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            else:
                wire += b"\r\n"
            connection.sendall(wire)
            chunks = []
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        raw = b"".join(chunks)
        assert raw, "Malformed URL must receive an HTTP response rather than an abrupt close"
        head, body = raw.split(b"\r\n\r\n", 1)
        assert b" 400 " in head.splitlines()[0]
        assert json.loads(body)["error"] == "bad_request"


def test_explicit_legacy_serves_actual_entrypoint_dependencies(tmp_path):
    with running_server(tmp_path / "legacy.sqlite", legacy_enabled=True) as server:
        assert request(server, "GET", "/frontend/")[0] == 200
        for path in ("/frontend/src/main.js", "/frontend/src/styles.css", "/frontend/src/ui/app.js", "/frontend/src/lib/api.js"):
            status, content, headers = request(server, "GET", path)
            assert status == 200, path
            assert content, path
            assert headers["X-Content-Type-Options"] == "nosniff"
        # Enabling the legacy UI must not enable repository-root serving.
        for path in ("/backend/src/server.py", "/docs/V2_CONTRACT.md", "/.git/config", "/frontend/src/../../backend/src/server.py"):
            assert request(server, "GET", path)[0] == 404


def test_legacy_modules_are_unavailable_without_opt_in(tmp_path):
    with running_server(tmp_path / "default.sqlite") as server:
        for path in ("/frontend/src/main.js", "/frontend/src/styles.css", "/frontend/src/ui/app.js"):
            assert request(server, "GET", path)[0] == 404
        assert request(server, "GET", "/frontend/v2/")[0] == 200


@pytest.mark.parametrize("city", ["tokyo", "osaka", "fukuoka"])
def test_long_running_demo_refreshes_after_25_hours_without_market_fallback(tmp_path, city):
    runtime = Runtime(tmp_path / "demo.sqlite", demo_enabled=True)
    payload = {"subject": demo_subject(city), "mode": "demo"}
    first = runtime.evaluate(payload, now=NOW)
    later = NOW + timedelta(hours=25)
    refreshed = runtime.evaluate(payload, now=later)
    assert first["sample"]["unit_count"] >= 24
    assert refreshed["sample"]["unit_count"] >= 24
    assert refreshed["sample"]["building_count"] >= 12
    assert refreshed["is_demo"] is True
    assert refreshed["judgment"] is None
    assert refreshed["versions"]["snapshot"] != first["versions"]["snapshot"]
    for comparable in refreshed["comparables"]:
        assert timedelta(0) <= later - parse_timestamp(comparable["status_verified_at"]) <= timedelta(hours=24)
        assert comparable["public_url"] is None
    market = runtime.evaluate({**payload, "mode": "market"}, now=later)
    assert market["sample"]["unit_count"] == 0
    assert market["benchmark_yen"] is None
    assert market["is_demo"] is False
    assert runtime.stations(city, now=later)["stations"] == []


def test_station_catalog_is_exact_current_and_isolated_by_mode(tmp_path):
    runtime = Runtime(tmp_path / "stations.sqlite", demo_enabled=True)
    assert runtime.stations("tokyo", now=NOW)["stations"] == []
    demo = runtime.stations("tokyo", mode="demo", now=NOW)
    assert {s["station_id"] for s in demo["stations"]} == {"demo:tokyo:station"}

    # Test-only fixture transformation: it does not claim a real supplier or
    # license. Distinct small/new station names exercise exact-ID preservation.
    snapshot = make_demo_snapshot("tokyo", now=NOW)
    snapshot["source"].update(source_id="test-market", independent_source_id="test-market", data_kind="observed")
    snapshot["source"]["rights"]["expires_at"] = iso_timestamp(NOW + timedelta(hours=1))
    for row in snapshot["listings"]:
        row.update(source_id="test-market", data_kind="observed", station_id="JP:小岩", station_name="小岩")
    snapshot["listings"][1].update(station_id="JP:新小岩", station_name="新小岩")
    snapshot["listings"][2].update(station_id="closed", station_name="closed", status="closed")
    snapshot["listings"][3].update(station_id="stale", station_name="stale", status_verified_at=iso_timestamp(NOW - timedelta(hours=25)))
    snapshot["listings"][4].update(station_id="unverified", station_name="unverified", status_verified_at=None)
    snapshot["listings"][5].update(city="osaka", municipality="大阪府大阪市北区", station_id="JP:大阪", station_name="大阪")
    runtime.store.import_snapshot(snapshot, now=NOW)

    market = runtime.stations("tokyo", now=NOW)
    assert market["mode"] == "market" and market["city"] == "tokyo"
    assert {s["station_id"] for s in market["stations"]} == {"JP:小岩", "JP:新小岩"}
    assert {s["station_id"] for s in runtime.stations("osaka", now=NOW)["stations"]} == {"JP:大阪"}
    assert {s["station_id"] for s in runtime.stations("tokyo", mode="demo", now=NOW)["stations"]} == {"demo:tokyo:station"}
    assert runtime.stations("tokyo", now=NOW + timedelta(hours=1))["stations"] == []


def test_http_station_modes_do_not_implicitly_enable_demo(tmp_path):
    with running_server(tmp_path / "station-http.sqlite", demo_enabled=True) as server:
        status, market, _ = request(server, "GET", "/api/v2/stations?city=fukuoka")
        assert status == 200 and market["stations"] == [] and market["mode"] == "market"
        status, demo, _ = request(server, "GET", "/api/v2/stations?city=fukuoka&mode=demo")
        assert status == 200
        assert {s["station_id"] for s in demo["stations"]} == {"demo:fukuoka:station"}
        assert request(server, "GET", "/api/v2/stations?city=fukuoka")[1]["stations"] == []
        for path in ("/api/v2/stations?city=kyoto", "/api/v2/stations?city=tokyo&mode=invalid"):
            assert request(server, "GET", path)[0] == 400


def test_disabled_demo_rejects_all_public_entrypoints_without_seeding(tmp_path):
    with running_server(tmp_path / "disabled.sqlite") as server:
        assert request(server, "GET", "/api/v2/stations?city=tokyo&mode=demo")[0] == 403
        assert request(server, "GET", "/api/v2/demo-subject?city=tokyo")[0] == 403
        assert request(server, "POST", "/api/v2/evaluate", {"subject": demo_subject("tokyo"), "mode": "demo"})[0] == 403
        assert request(server, "GET", "/api/v2/stations?city=tokyo")[1]["stations"] == []
        assert request(server, "GET", "/api/v2/capabilities")[1]["demo_enabled"] is False
        assert server.runtime.store.read_current(include_synthetic=True)["listings"] == []


def test_receipt_reproducibility_includes_normalized_subject_and_evaluation_clock(tmp_path):
    runtime = Runtime(tmp_path / "receipt.sqlite", demo_enabled=True)
    subject = demo_subject("tokyo")
    padded = {**subject, "station_id": f" {subject['station_id']} "}
    first = runtime.evaluate({"subject": subject, "mode": "demo"}, now=NOW)
    normalized = runtime.evaluate({"subject": padded, "mode": "demo"}, now=NOW)
    later = runtime.evaluate({"subject": subject, "mode": "demo"}, now=NOW + timedelta(seconds=1))
    assert first["assessment_id"] == normalized["assessment_id"]
    assert first["receipt"] == normalized["receipt"]
    assert first["versions"]["snapshot"] == later["versions"]["snapshot"]
    assert first["assessment_id"] != later["assessment_id"]
    assert later["receipt"]["evaluated_at"] == later["evaluated_at"]
