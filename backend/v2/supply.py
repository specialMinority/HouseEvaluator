"""Operator-owned, contract-gated JSON feeds and a durable local ingestion queue.

This is a normalized feed boundary, not a portal scraper or a license grant.
Only codes, IDs and dates are persisted in the operational journal. Network
requests pin a validated public DNS address and keep TLS hostname verification.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit
import uuid

from .models import ValidationError, iso_timestamp, parse_timestamp, utc_now
from .store import local_path


MAX_BYTES = 20 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
LEASE_SECONDS = 120
FETCH_SECONDS = 30
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
_DNS_SLOTS = threading.BoundedSemaphore(8)
_ERROR_CODES = frozenset((
    "invalid_json", "payload_too_large", "configuration_invalid", "endpoint_denied",
    "network_target_denied", "dns_failed", "dns_busy", "dns_timeout",
    "local_feed_unavailable", "credentials_unavailable", "redirect_denied", "http_error",
    "encoding_denied", "content_type_denied", "fetch_timeout", "fetch_failed",
    "contract_changed", "lease_lost", "source_mismatch", "snapshot_invalid",
    "rights_exceed_contract", "not_modified_without_snapshot", "ingestion_failed",
    "source_unavailable", "current_snapshot_missing", "storage_path_conflict",
))


class SupplyError(ValueError):
    """A safe, fixed operational code, without exception/endpoint details."""

    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in _ERROR_CODES else "ingestion_failed"
        super().__init__(self.code)


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes = b""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SupplyError("invalid_json")
        result[key] = value
    return result


def _constant(_value):
    raise SupplyError("invalid_json")


def _json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise SupplyError("payload_too_large")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SupplyError):
            raise
        raise SupplyError("invalid_json") from None


def _keys(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise SupplyError("configuration_invalid")


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SupplyError("configuration_invalid")
    return value


def _origin(url, *, origin_only=False):
    _text(url)
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.query
                or "\\" in url or any(c.isspace() for c in url)
                or (origin_only and parts.path not in ("", "/"))):
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port or 443
        if port != 443 or host.endswith(".") or "%" in host:
            raise ValueError()
        return host, port
    except (ValueError, UnicodeError):
        raise SupplyError("endpoint_denied") from None


def _resolve_relative(base, value, *, contained=False):
    raw = _text(value)
    path = local_path(Path(base) / raw) if not Path(raw).is_absolute() else local_path(raw)
    if contained and (Path(raw).is_absolute() or not path.is_relative_to(base)):
        raise SupplyError("configuration_invalid")
    return path


def load_suppliers(config_path):
    """Strict configuration; disabled examples need no existing evidence/feed."""
    try:
        path = local_path(config_path)
        with path.open("rb") as stream:
            payload = _json(stream.read(MAX_CONFIG_BYTES + 1), MAX_CONFIG_BYTES)
        _keys(payload, {"schema_version", "suppliers"}, {"schema_version", "suppliers"})
        if payload["schema_version"] != "2.0" or not isinstance(payload["suppliers"], list) or len(payload["suppliers"]) > 100:
            raise SupplyError("configuration_invalid")
        suppliers, seen = [], set()
        for raw in payload["suppliers"]:
            fields = {"source_id", "enabled", "interval_seconds", "max_attempts", "allow_empty", "contract", "feed"}
            _keys(raw, fields, fields)
            source_id = raw["source_id"]
            if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id) or source_id in seen:
                raise SupplyError("configuration_invalid")
            seen.add(source_id)
            if type(raw["enabled"]) is not bool or type(raw["allow_empty"]) is not bool:
                raise SupplyError("configuration_invalid")
            for key, low, high in (("interval_seconds", 60, 86400), ("max_attempts", 1, 10)):
                if type(raw[key]) is not int or not low <= raw[key] <= high:
                    raise SupplyError("configuration_invalid")
            contract = raw["contract"]
            contract_fields = {"agreement_path", "agreement_sha256", "comparison", "display", "storage", "expires_at", "revoked"}
            _keys(contract, contract_fields, contract_fields)
            for key in ("comparison", "display", "storage", "revoked"):
                if type(contract[key]) is not bool:
                    raise SupplyError("configuration_invalid")
            parse_timestamp(contract["expires_at"])
            if not isinstance(contract["agreement_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", contract["agreement_sha256"]):
                raise SupplyError("configuration_invalid")
            _resolve_relative(path.parent, contract["agreement_path"], contained=True)
            feed = raw["feed"]
            if not isinstance(feed, dict):
                raise SupplyError("configuration_invalid")
            if feed.get("type") == "local":
                _keys(feed, {"type", "path"}, {"type", "path"})
                _resolve_relative(path.parent, feed["path"])
            elif feed.get("type") == "https":
                _keys(feed, {"type", "url", "allowed_origin", "auth_env"}, {"type", "url", "allowed_origin"})
                if _origin(feed["url"]) != _origin(feed["allowed_origin"], origin_only=True):
                    raise SupplyError("endpoint_denied")
                if "auth_env" in feed and (not isinstance(feed["auth_env"], str) or not _ENV_NAME.fullmatch(feed["auth_env"])):
                    raise SupplyError("configuration_invalid")
            else:
                raise SupplyError("configuration_invalid")
            normalized = dict(raw)
            normalized["_base"] = path.parent
            normalized["_fingerprint"] = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            suppliers.append(normalized)
        return suppliers
    except (OSError, ValidationError, TypeError, KeyError):
        raise SupplyError("configuration_invalid") from None


def contract_status(supplier, *, now=None):
    """Read the evidence again, so revocation or file replacement fails closed."""
    now = utc_now(now)
    if not supplier["enabled"]:
        return "disabled"
    contract = supplier["contract"]
    if contract["revoked"]:
        return "revoked"
    if not all(contract[key] is True for key in ("comparison", "display", "storage")):
        return "permissions_missing"
    if parse_timestamp(contract["expires_at"]) <= now:
        return "expired"
    try:
        path = _resolve_relative(supplier["_base"], contract["agreement_path"], contained=True)
        with path.open("rb") as stream:
            evidence = stream.read(MAX_BYTES + 1)
        if not evidence or len(evidence) > MAX_BYTES:
            return "evidence_missing"
        return "valid" if hashlib.sha256(evidence).hexdigest() == contract["agreement_sha256"] else "evidence_mismatch"
    except (OSError, ValueError):
        return "evidence_missing"


def permitted_source_ids(config_path, *, now=None):
    """Fail closed; None, disabled or malformed config permits no sources."""
    if config_path is None:
        return set()
    try:
        return {item["source_id"] for item in load_suppliers(config_path) if contract_status(item, now=now) == "valid"}
    except (SupplyError, OSError, ValueError):
        return set()


def _public_addresses(host, port, *, resolver=socket.getaddrinfo):
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
        addresses = []
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global or getattr(address, "ipv4_mapped", None) is not None:
                raise SupplyError("network_target_denied")
            if str(address) not in addresses:
                addresses.append(str(address))
        if not addresses:
            raise SupplyError("dns_failed")
        return addresses
    except SupplyError:
        raise
    except (socket.gaierror, OSError, ValueError):
        raise SupplyError("dns_failed") from None


def _bounded_dns(host, port, timeout):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise SupplyError("dns_busy")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((True, _public_addresses(host, port)))
        except SupplyError as exc:
            result.put((False, exc.code))
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True).start()
    try:
        ok, value = result.get(timeout=min(timeout, 5))
    except queue.Empty:
        raise SupplyError("dns_timeout") from None
    if not ok:
        raise SupplyError(value)
    return value


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, *, timeout):
        super().__init__(hostname, port=443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        # Connect to the checked literal IP, never resolve the hostname again.
        sock = socket.create_connection((self._address, 443), self.timeout)
        self._transport = sock
        try:
            if time.monotonic() >= getattr(self, "_absolute_deadline", float("inf")):
                raise TimeoutError()
            # Publish the transport before the handshake so the absolute
            # deadline watchdog can interrupt TLS as well as HTTP I/O.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
            self._transport = self.sock
            self.sock.do_handshake()
        except BaseException:
            sock.close()
            raise


def fetch_feed(supplier):
    """Bounded local/HTTPS fetch. No redirects, proxies or caller-set headers."""
    feed = supplier["feed"]
    if feed["type"] == "local":
        try:
            path = _resolve_relative(supplier["_base"], feed["path"])
            if not path.is_file():
                raise SupplyError("local_feed_unavailable")
            with path.open("rb") as stream:
                body = stream.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SupplyError("payload_too_large")
            return FetchResult(200, body)
        except OSError:
            raise SupplyError("local_feed_unavailable") from None
    host, port = _origin(feed["url"])
    if (host, port) != _origin(feed["allowed_origin"], origin_only=True):
        raise SupplyError("endpoint_denied")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "HouseEvaluator-PermittedFeed/2.0"}
    if feed.get("auth_env"):
        token = os.environ.get(feed["auth_env"])
        if not token or len(token) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise SupplyError("credentials_unavailable")
        headers["Authorization"] = "Bearer " + token
    deadline = time.monotonic() + FETCH_SECONDS
    address = _bounded_dns(host, port, FETCH_SECONDS)[0]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SupplyError("fetch_timeout")
    connection = _PinnedHTTPSConnection(host, address, timeout=remaining)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        connection.close()
        raise SupplyError("fetch_timeout")
    connection.timeout = remaining
    connection._absolute_deadline = deadline
    expired = threading.Event()

    def cancel_request():
        # A socket timeout alone limits idle time, so a server dripping bytes
        # could otherwise keep readline/read alive beyond the total budget.
        expired.set()
        transport = getattr(connection, "_transport", None) or getattr(connection, "sock", None)
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except OSError:
            pass

    def check_deadline():
        if expired.is_set() or time.monotonic() >= deadline:
            raise SupplyError("fetch_timeout")

    watchdog = threading.Timer(remaining, cancel_request)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.request("GET", urlsplit(feed["url"]).path or "/", headers=headers)
        check_deadline()
        response = connection.getresponse()
        check_deadline()
        if response.status == 304:
            return FetchResult(304)
        if 300 <= response.status < 400:
            raise SupplyError("redirect_denied")
        if response.status != 200:
            raise SupplyError("http_error")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise SupplyError("encoding_denied")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise SupplyError("content_type_denied")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
            raise SupplyError("payload_too_large")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SupplyError("fetch_timeout")
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise SupplyError("payload_too_large")
        check_deadline()
        return FetchResult(200, b"".join(chunks))
    except (OSError, http.client.HTTPException):
        raise SupplyError("fetch_timeout" if expired.is_set() else "fetch_failed") from None
    finally:
        watchdog.cancel()
        connection.close()


class IngestionRunner:
    """One host, durable SQLite leases. Inject store, fetcher and clock in tests."""

    def __init__(self, config_path, state_db, store, *, fetcher=None, clock=None):
        self.config_path = local_path(config_path)
        self.state_db = local_path(state_db)
        if self.config_path == self.state_db:
            raise SupplyError("configuration_invalid")
        store_path = getattr(store, "_path", None)
        if isinstance(store_path, str) and not store_path.startswith("file:") and local_path(store_path) == self.state_db:
            raise SupplyError("storage_path_conflict")
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.fetcher = fetcher or fetch_feed
        self.clock = clock or utc_now
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS supply_state (
                source_id TEXT PRIMARY KEY, config_hash TEXT NOT NULL, enabled INTEGER NOT NULL,
                contract_status TEXT NOT NULL, contract_expires_at TEXT,
                last_success TEXT, last_attempt TEXT, next_due TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0, last_error_code TEXT,
                snapshot_as_of TEXT, oldest_status_verified_at TEXT, latest_status_verified_at TEXT,
                lease_token TEXT, lease_until TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS supply_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                source_id TEXT NOT NULL, event_code TEXT NOT NULL, attempt INTEGER NOT NULL)""")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.state_db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _journal(self, db, source_id, code, attempt, now):
        db.execute("INSERT INTO supply_journal(at,source_id,event_code,attempt) VALUES (?,?,?,?)", (iso_timestamp(now), source_id, code, attempt))
        db.execute("DELETE FROM supply_journal WHERE id <= (SELECT COALESCE(MAX(id),0)-1000 FROM supply_journal)")

    def _sync(self, suppliers, now):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            present = {item["source_id"] for item in suppliers}
            for row in db.execute("SELECT source_id FROM supply_state").fetchall():
                if row["source_id"] not in present:
                    db.execute("UPDATE supply_state SET enabled=0,contract_status='removed',next_due=NULL WHERE source_id=?", (row["source_id"],))
            for item in suppliers:
                status = contract_status(item, now=now)
                previous = db.execute("SELECT * FROM supply_state WHERE source_id=?", (item["source_id"],)).fetchone()
                if previous is None:
                    db.execute("INSERT INTO supply_state(source_id,config_hash,enabled,contract_status,contract_expires_at,next_due) VALUES(?,?,?,?,?,?)", (item["source_id"], item["_fingerprint"], item["enabled"], status, item["contract"]["expires_at"], iso_timestamp(now) if status == "valid" else None))
                else:
                    reset = previous["config_hash"] != item["_fingerprint"] or previous["contract_status"] != status
                    db.execute("UPDATE supply_state SET config_hash=?,enabled=?,contract_status=?,contract_expires_at=? WHERE source_id=?", (item["_fingerprint"], item["enabled"], status, item["contract"]["expires_at"], item["source_id"]))
                    if reset:
                        db.execute("UPDATE supply_state SET next_due=?,consecutive_failures=0,last_error_code=NULL WHERE source_id=?", (iso_timestamp(now) if status == "valid" else None, item["source_id"]))
                if previous is None or previous["contract_status"] != status:
                    self._journal(db, item["source_id"], "contract_" + status, 0, now)

    def _claim(self, item, now):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM supply_state WHERE source_id=?", (item["source_id"],)).fetchone()
            if (row["contract_status"] != "valid" or row["config_hash"] != item["_fingerprint"]
                    or not row["next_due"] or parse_timestamp(row["next_due"]) > now
                    or (row["lease_until"] and parse_timestamp(row["lease_until"]) > now)):
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE supply_state SET lease_token=?,lease_until=?,last_attempt=? WHERE source_id=?", (token, iso_timestamp(now + timedelta(seconds=LEASE_SECONDS)), iso_timestamp(now), item["source_id"]))
            self._journal(db, item["source_id"], "attempt_started", row["consecutive_failures"] + 1, now)
            return token

    def _guard_supplier(self, item, now):
        # Check immediately before each fetch AND after I/O. Another source's
        # slow request must not leave a later source using an old permission.
        current = next((x for x in load_suppliers(self.config_path) if x["source_id"] == item["source_id"]), None)
        if current is None or current["_fingerprint"] != item["_fingerprint"] or contract_status(current, now=now) != "valid":
            raise SupplyError("contract_changed")

    def _current_dates(self, source_id, now):
        available = self.store.read_current(now=now)
        source = next((x for x in available["sources"] if x["source_id"] == source_id), None)
        if source is None:
            return None
        verified = sorted(parse_timestamp(x["status_verified_at"]) for x in available["listings"] if x["source_id"] == source_id and x.get("status_verified_at"))
        return (iso_timestamp(parse_timestamp(source["as_of"])), iso_timestamp(verified[0]) if verified else None, iso_timestamp(verified[-1]) if verified else None)

    def _apply(self, item, token, fetched):
        now = utc_now(self.clock())
        self._guard_supplier(item, now)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM supply_state WHERE source_id=?", (item["source_id"],)).fetchone()
            if row["lease_token"] != token or parse_timestamp(row["lease_until"]) <= now:
                raise SupplyError("lease_lost")
            if not isinstance(fetched, FetchResult) or fetched.status not in (200, 304):
                raise SupplyError("fetch_failed")
            code = "not_modified"
            snapshot_dates = None
            if fetched.status == 200:
                payload = _json(fetched.body, MAX_BYTES)
                if not isinstance(payload, dict) or not isinstance(payload.get("source"), dict) or payload["source"].get("source_id") != item["source_id"]:
                    raise SupplyError("source_mismatch")
                try:
                    expiry = parse_timestamp(payload["source"]["rights"]["expires_at"])
                except (KeyError, TypeError, ValidationError):
                    raise SupplyError("snapshot_invalid") from None
                if expiry > parse_timestamp(item["contract"]["expires_at"]):
                    raise SupplyError("rights_exceed_contract")
                try:
                    result = self.store.import_snapshot(payload, now=now, allow_empty=item["allow_empty"], allow_synthetic=False)
                except ValidationError:
                    raise SupplyError("snapshot_invalid") from None
                code = "snapshot_imported" if result["status"] == "imported" else "snapshot_unchanged"
                # Read the actual current snapshot, including after a crash
                # between import and queue commit. An old idempotent replay
                # must never overwrite monitor dates with historical dates.
                snapshot_dates = self._current_dates(item["source_id"], now)
                if snapshot_dates is None:
                    raise SupplyError("current_snapshot_missing")
            else:
                snapshot_dates = self._current_dates(item["source_id"], now)
                if snapshot_dates is None:
                    raise SupplyError("not_modified_without_snapshot")
            if snapshot_dates:
                db.execute("UPDATE supply_state SET snapshot_as_of=?,oldest_status_verified_at=?,latest_status_verified_at=? WHERE source_id=?", (*snapshot_dates, item["source_id"]))
            db.execute("UPDATE supply_state SET last_success=?,next_due=?,consecutive_failures=0,last_error_code=NULL,lease_token=NULL,lease_until=NULL WHERE source_id=? AND lease_token=?", (iso_timestamp(now), iso_timestamp(now + timedelta(seconds=item["interval_seconds"])), item["source_id"], token))
            self._journal(db, item["source_id"], code, 0, now)
        return code

    def _failed(self, item, token, code):
        now = utc_now(self.clock())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM supply_state WHERE source_id=?", (item["source_id"],)).fetchone()
            if row["lease_token"] != token:
                return
            failures = row["consecutive_failures"] + 1
            exhausted = failures >= item["max_attempts"]
            delay = min(3600, 30 * (2 ** min(failures - 1, 8)))
            due = None if exhausted else iso_timestamp(now + timedelta(seconds=delay))
            db.execute("UPDATE supply_state SET consecutive_failures=?,last_error_code=?,next_due=?,lease_token=NULL,lease_until=NULL WHERE source_id=?", (failures, code, due, item["source_id"]))
            if code in ("current_snapshot_missing", "not_modified_without_snapshot"):
                db.execute("UPDATE supply_state SET snapshot_as_of=NULL,oldest_status_verified_at=NULL,latest_status_verified_at=NULL WHERE source_id=?", (item["source_id"],))
            self._journal(db, item["source_id"], code, failures, now)
            if exhausted:
                self._journal(db, item["source_id"], "retry_exhausted", failures, now)

    def resume(self, source_id):
        """Explicit operator retry after diagnosis; leaves the failure journal."""
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise SupplyError("configuration_invalid")
        now = utc_now(self.clock())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM supply_state WHERE source_id=?", (source_id,)).fetchone()
            if row is None or (row["lease_until"] and parse_timestamp(row["lease_until"]) > now):
                raise SupplyError("source_unavailable")
            db.execute("UPDATE supply_state SET next_due=?,consecutive_failures=0,last_error_code=NULL WHERE source_id=?", (iso_timestamp(now), source_id))
            self._journal(db, source_id, "operator_resumed", 0, now)

    def run_once(self):
        suppliers = load_suppliers(self.config_path)
        now = utc_now(self.clock())
        self._sync(suppliers, now)
        outcomes = []
        for item in suppliers:
            token = self._claim(item, utc_now(self.clock()))
            if not token:
                continue
            try:
                self._guard_supplier(item, utc_now(self.clock()))
                code = self._apply(item, token, self.fetcher(item))
            except SupplyError as exc:
                code = exc.code
                self._failed(item, token, code)
            except Exception:
                # Do not store repr/str(exc): URLs, credentials and local paths
                # from transports/drivers are not operational event data.
                code = "ingestion_failed"
                self._failed(item, token, code)
            outcomes.append({"source_id": item["source_id"], "event_code": code})
        return {"attempted": len(outcomes), "outcomes": outcomes, "monitor": monitor_status(self.state_db, now=utc_now(self.clock()))}


def monitor_status(state_db, *, now=None):
    """Read-only safe monitoring. Never creates a missing state database."""
    now = utc_now(now)
    empty = {"status": "not_configured", "source_count": 0, "sources": []}
    if state_db is None:
        return empty
    try:
        path = local_path(state_db)
        if not path.is_file():
            return empty
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute("SELECT * FROM supply_state ORDER BY source_id").fetchall()
        finally:
            db.close()
        sources = []
        for row in rows:
            status = row["contract_status"]
            if status == "valid" and parse_timestamp(row["contract_expires_at"]) <= now:
                status = "expired"
            earliest, latest = row["oldest_status_verified_at"], row["latest_status_verified_at"]
            freshness = "unknown"
            if latest:
                freshness = "stale" if now - parse_timestamp(latest) > timedelta(hours=24) else "fresh"
                if freshness == "fresh" and earliest and now - parse_timestamp(earliest) > timedelta(hours=24):
                    freshness = "mixed"
            source = {key: row[key] for key in ("source_id", "last_success", "last_attempt", "next_due", "consecutive_failures", "last_error_code", "contract_expires_at", "snapshot_as_of", "oldest_status_verified_at", "latest_status_verified_at")}
            leased = bool(row["lease_until"] and parse_timestamp(row["lease_until"]) > now)
            overdue = bool(status == "valid" and row["next_due"] and not leased and now > parse_timestamp(row["next_due"]) + timedelta(seconds=60))
            source.update(contract_status=status, freshness=freshness, lease_active=leased, retry_exhausted=bool(row["last_error_code"] and row["next_due"] is None), worker_overdue=overdue)
            sources.append(source)
        actionable = any(x["contract_status"] not in ("valid", "disabled", "removed") or x["last_error_code"] or x["worker_overdue"] or (x["contract_status"] == "valid" and x["freshness"] != "fresh") for x in sources)
        return {"status": "needs_attention" if actionable else ("healthy" if any(x["contract_status"] == "valid" for x in sources) else "not_configured"), "source_count": len(sources), "sources": sources}
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return {"status": "unavailable", "source_count": 0, "sources": []}
