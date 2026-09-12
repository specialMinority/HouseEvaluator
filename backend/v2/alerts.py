"""One-shot local availability checks and durable, deduplicated incident history.

No delivery transports or scheduler are provided. Snapshot and ingestion databases
are read only; the separate alerts database contains allowlisted codes and times.
"""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

from .maintenance import _connect, _empty_private, _publish, _safe_path, _schema_check
from .models import ValidationError, iso_timestamp, parse_timestamp, utc_now, validate_snapshot
from .release import context_fingerprint
from .store import _hash, _permitted
from .supply import contract_status, load_suppliers, monitor_status


CODES = frozenset({"supplier_not_configured", "supplier_configuration_invalid", "supplier_contract_invalid", "snapshot_unavailable",
                   "snapshot_invalid", "market_data_unavailable", "listings_stale", "worker_unavailable",
                   "worker_not_configured", "worker_failed", "worker_overdue", "worker_contract_invalid"})
_SCHEMA = {"alert_incidents": ("code", "opened_at", "last_seen_at"),
           "alert_events": ("event_id", "code", "event", "occurred_at"),
           "alert_metadata": ("key", "value")}


def checked_paths(*, db, state_db, alerts_db=None, suppliers=None, output=None):
    """Reject aliases and SQLite sidecars before writing any operational file."""
    paths = {name: _safe_path(value) for name, value in
             {"db": db, "state_db": state_db, "alerts_db": alerts_db,
              "suppliers": suppliers, "output": output}.items() if value is not None}
    items = list(paths.items())
    for index, (name, path) in enumerate(items):
        if path.exists() and not path.is_file():
            raise ValidationError("monitor paths must name local files")
        for other_name, other in items[:index]:
            aliases = path == other or (path.exists() and other.exists() and path.samefile(other))
            sidecars = {Path(str(other) + suffix) for suffix in ("-wal", "-shm", "-journal")}
            reverse = {Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")}
            if aliases or path in sidecars or other in reverse:
                raise ValidationError("monitor files and SQLite sidecars must be distinct")
    return paths


def inspect_local(db_path, state_db, *, suppliers=None, now=None):
    """Inspect permitted observed data and worker state without creating either DB."""
    now = utc_now(now)
    paths = checked_paths(db=db_path, state_db=state_db, suppliers=suppliers)
    codes, permitted, source_ids = set(), set(), set()
    fresh_count = listing_count = 0
    if not suppliers:
        codes.add("supplier_not_configured")
    else:
        try:
            configured = load_suppliers(paths["suppliers"])
            for supplier in configured:
                status = contract_status(supplier, now=now)
                if status == "valid":
                    permitted.add(supplier["source_id"])
                elif supplier["enabled"]:
                    codes.add("supplier_contract_invalid")
            if not permitted:
                codes.add("supplier_not_configured")
        except (OSError, ValueError, TypeError):
            codes.add("supplier_configuration_invalid")
    try:
        with closing(_connect(paths["db"])) as db:
            _schema_check(db)
            rows = db.execute("SELECT * FROM v2_current WHERE data_kind = 'observed'").fetchall()
        for row in rows:
            if len(row["payload"]) > 50_000_000:
                raise ValidationError("invalid snapshot")
            payload = json.loads(row["payload"])
            if (_hash(payload) != row["content_hash"]
                    or payload["source"]["source_id"] != row["source_id"]
                    or payload["source"]["data_kind"] != row["data_kind"]
                    or payload["snapshot_id"] != row["snapshot_id"] or payload["as_of"] != row["as_of"]):
                raise ValidationError("invalid snapshot")
            if not _permitted(payload["source"], now) or row["source_id"] not in permitted:
                continue
            snapshot = validate_snapshot(payload, now=now)
            source_ids.add(row["source_id"])
            active = [item for item in snapshot["listings"] if item["status"] == "active"]
            listing_count += len(active)
            fresh_count += sum(bool(item.get("status_verified_at")) and
                               timedelta(0) <= now - parse_timestamp(item["status_verified_at"]) <= timedelta(hours=24)
                               for item in active)
    except (OSError, sqlite3.Error):
        codes.add("snapshot_unavailable")
        source_ids, listing_count, fresh_count = set(), 0, 0
    except (ValueError, TypeError, KeyError, RecursionError):
        codes.add("snapshot_invalid")
        source_ids, listing_count, fresh_count = set(), 0, 0
    # Every permitted supplier needs a current usable snapshot. Another source's
    # healthy listings must not mask deletion or expired snapshot-level rights.
    if permitted - source_ids:
        codes.add("snapshot_unavailable")
    if not listing_count:
        codes.add("market_data_unavailable")
    elif fresh_count < listing_count:
        codes.add("listings_stale")
    try:
        monitor = monitor_status(paths["state_db"], now=now)
        if monitor["status"] == "unavailable":
            codes.add("worker_unavailable")
        monitored = {row["source_id"]: row for row in monitor["sources"]}
        if not permitted or any(source not in monitored for source in permitted):
            codes.add("worker_not_configured")
        for source in permitted:
            row = monitored.get(source)
            if row is None:
                continue
            if row["contract_status"] != "valid":
                codes.add("worker_contract_invalid")
            if row["consecutive_failures"] or row["last_error_code"] or row["retry_exhausted"]:
                codes.add("worker_failed")
            if row["worker_overdue"] or not row["last_success"] or not (
                    timedelta(0) <= now - parse_timestamp(row["last_success"]) <= timedelta(hours=24)):
                codes.add("worker_overdue")
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
        codes.add("worker_unavailable")
    context = context_fingerprint(SimpleNamespace(suppliers_path=paths.get("suppliers")), source_ids)
    return {"checked_at": iso_timestamp(now), "codes": sorted(codes), "context_fingerprint": context,
            "source_count": len(source_ids), "active_listing_count": listing_count,
            "fresh_listing_count": fresh_count}


def _check_alert_schema(db):
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValidationError("invalid alerts database")
    objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if {row["name"] for row in objects} != set(_SCHEMA) or any(row["type"] != "table" for row in objects):
        raise ValidationError("unsupported alerts database")
    for table, columns in _SCHEMA.items():
        if tuple(row["name"] for row in db.execute(f"PRAGMA table_info({table})")) != columns:
            raise ValidationError("unsupported alerts database")


class AlertStore:
    def __init__(self, path):
        self.path = _safe_path(path)
        if self.path.exists():
            with closing(_connect(self.path)) as db:
                _check_alert_schema(db)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _empty_private(self.path)
            with closing(_connect(self.path, write=True)) as db, db:
                db.execute("CREATE TABLE alert_incidents (code TEXT PRIMARY KEY, opened_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)")
                db.execute("CREATE TABLE alert_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, event TEXT NOT NULL, occurred_at TEXT NOT NULL)")
                db.execute("CREATE TABLE alert_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def reconcile(self, codes, *, now=None):
        now = utc_now(now)
        if isinstance(codes, (str, bytes)):
            raise ValidationError("incident codes must be an allowlisted collection")
        wanted = set(codes)
        if not wanted <= CODES:
            raise ValidationError("incident codes must be allowlisted")
        stamp = iso_timestamp(now)
        with closing(_connect(self.path, write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _check_alert_schema(db)
            previous = db.execute("SELECT value FROM alert_metadata WHERE key='last_check'").fetchone()
            if previous and parse_timestamp(previous["value"]) > now:
                raise ValidationError("alert checks cannot go backwards in time")
            active = {row["code"] for row in db.execute("SELECT code FROM alert_incidents")}
            if not active <= CODES:
                raise ValidationError("invalid stored incident code")
            new_events = []
            for code, event in [(code, "opened") for code in sorted(wanted - active)] + [(code, "recovered") for code in sorted(active - wanted)]:
                event_id = db.execute("INSERT INTO alert_events(code,event,occurred_at) VALUES(?,?,?)", (code, event, stamp)).lastrowid
                new_events.append({"event_id": event_id, "code": code, "event": event, "occurred_at": stamp})
                if event == "opened":
                    db.execute("INSERT INTO alert_incidents VALUES(?,?,?)", (code, stamp, stamp))
                else:
                    db.execute("DELETE FROM alert_incidents WHERE code=?", (code,))
            for code in wanted:
                db.execute("UPDATE alert_incidents SET last_seen_at=? WHERE code=?", (stamp, code))
            db.execute("INSERT INTO alert_metadata VALUES('last_check',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (stamp,))
            db.execute("DELETE FROM alert_events WHERE event_id NOT IN (SELECT event_id FROM alert_events ORDER BY event_id DESC LIMIT 1000)")
            incidents = [{"code": row["code"], "opened_at": iso_timestamp(parse_timestamp(row["opened_at"])),
                          "last_seen_at": iso_timestamp(parse_timestamp(row["last_seen_at"]))}
                         for row in db.execute("SELECT * FROM alert_incidents ORDER BY code")]
        return {"status": "needs_attention" if wanted else "healthy", "checked_at": stamp,
                "new_event_count": len(new_events), "events": new_events, "active_incidents": incidents,
                "external_delivery_attempted": False, "notification_sent": False}


def check_once(db_path, state_db, alerts_db, *, suppliers=None, now=None):
    now = utc_now(now)
    paths = checked_paths(db=db_path, state_db=state_db, alerts_db=alerts_db, suppliers=suppliers)
    inspected = inspect_local(paths["db"], paths["state_db"], suppliers=paths.get("suppliers"), now=now)
    events = AlertStore(paths["alerts_db"]).reconcile(inspected.pop("codes"), now=now)
    return {"schema_version": "local-alert-status-1.0", **inspected, **events,
            "release_readiness_checked": False, "delivery_scope": "local_only"}


def write_fresh(path, report):
    path = _safe_path(path)
    if path.exists():
        raise ValidationError("report output must be a fresh file")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".alerts-report-" + uuid.uuid4().hex + ".partial")
    try:
        _empty_private(temporary)
        temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _publish(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
