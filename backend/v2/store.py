"""Atomic local snapshots, with rights checked again at every read.

The operator owns the database and imports authorized files using the CLI.
This module grants no ingestion capability to HTTP callers. It keeps the
current payload only; the receipt ledger stores hashes for replay detection.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .models import ValidationError, parse_timestamp, utc_now, validate_snapshot


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload):
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def local_path(value):
    """Accept filesystem paths, never URLs, UNC/network shares or SQLite URIs."""
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("a local filesystem path is required") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw or "://" in raw or raw.lower().startswith("file:") or raw.startswith(("\\\\", "//")):
        raise ValidationError("a local filesystem path is required")
    resolved = Path(raw).expanduser().resolve()
    if str(resolved).startswith(("\\\\", "//")):
        raise ValidationError("a local filesystem path is required")
    return resolved


def _permitted(source, now):
    rights = source.get("rights", {})
    if not isinstance(rights, dict) or not all(rights.get(key) is True for key in ("comparison", "display", "storage")):
        return False
    try:
        return parse_timestamp(rights.get("expires_at")) > now
    except ValidationError:
        return False


class SnapshotStore:
    def __init__(self, path):
        self._anchor = None
        if str(path) == ":memory:":
            self._path = f"file:houseevaluator-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._anchor = sqlite3.connect(self._path, uri=True, check_same_thread=False)
        else:
            resolved = local_path(path)
            if resolved.is_dir():
                raise ValidationError("database path must name a file")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(resolved)
            self._uri = False
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS v2_current (source_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, as_of TEXT NOT NULL, data_kind TEXT NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v2_receipts (source_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, content_hash TEXT NOT NULL, as_of TEXT NOT NULL, PRIMARY KEY (source_id, snapshot_id))")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self._path, uri=self._uri, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_snapshot(self, payload, *, now=None, allow_empty=False, allow_synthetic=False):
        now = utc_now(now)
        if type(allow_empty) is not bool or type(allow_synthetic) is not bool:
            raise ValidationError("import options must be booleans")
        snapshot = validate_snapshot(payload, now=now)
        source = snapshot["source"]
        if source["data_kind"] == "synthetic" and not allow_synthetic:
            raise ValidationError("synthetic import requires allow_synthetic=True")
        if not snapshot["listings"] and not allow_empty:
            raise ValidationError("empty authoritative snapshot requires allow_empty=True")
        # Input row order does not change dataset identity.
        snapshot["listings"].sort(key=lambda row: (row["observation_id"], row["listing_id"]))
        content_hash = _hash(snapshot)
        source_id, snapshot_id = source["source_id"], snapshot["snapshot_id"]
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM v2_current WHERE source_id = ?", (source_id,)).fetchone()
            receipt = db.execute("SELECT * FROM v2_receipts WHERE source_id = ? AND snapshot_id = ?", (source_id, snapshot_id)).fetchone()
            if receipt:
                if receipt["content_hash"] != content_hash:
                    raise ValidationError("snapshot_id already exists with different content")
                # A replay of a historical receipt never replaces current data.
                return {"status": "unchanged", "source_id": source_id, "snapshot_id": snapshot_id, "current_snapshot_id": current["snapshot_id"] if current else None, "listing_count": len(snapshot["listings"]), "content_hash": content_hash}
            if current:
                if source["data_kind"] != current["data_kind"]:
                    raise ValidationError("an existing source_id cannot change data_kind")
                if parse_timestamp(snapshot["as_of"]) <= parse_timestamp(current["as_of"]):
                    raise ValidationError("snapshot as_of must be newer than the current source snapshot")
            db.execute("INSERT INTO v2_receipts (source_id, snapshot_id, content_hash, as_of) VALUES (?, ?, ?, ?)", (source_id, snapshot_id, content_hash, snapshot["as_of"]))
            db.execute("INSERT INTO v2_current (source_id, snapshot_id, as_of, data_kind, content_hash, payload) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET snapshot_id=excluded.snapshot_id, as_of=excluded.as_of, data_kind=excluded.data_kind, content_hash=excluded.content_hash, payload=excluded.payload", (source_id, snapshot_id, snapshot["as_of"], source["data_kind"], content_hash, _canonical(snapshot)))
        return {"status": "imported", "source_id": source_id, "snapshot_id": snapshot_id, "current_snapshot_id": snapshot_id, "listing_count": len(snapshot["listings"]), "content_hash": content_hash}

    def read_current(self, *, now=None, include_synthetic=False):
        now = utc_now(now)
        if type(include_synthetic) is not bool:
            raise ValidationError("include_synthetic must be a boolean")
        kind = "synthetic" if include_synthetic else "observed"
        with self._connection() as db:
            rows = db.execute("SELECT payload, content_hash FROM v2_current WHERE data_kind = ? ORDER BY source_id", (kind,)).fetchall()
        sources, listings, hashes = [], [], []
        for row in rows:
            try:
                snapshot = json.loads(row["payload"])
                source = snapshot["source"]
                if not isinstance(source, dict) or source.get("data_kind") != kind or not _permitted(source, now):
                    continue
                if parse_timestamp(snapshot["as_of"]) > now:
                    continue
                # Fail closed on corruption/tampering, including rights edits.
                if _hash(snapshot) != row["content_hash"]:
                    continue
                source_meta = dict(source)
                source_meta.update({key: snapshot[key] for key in ("schema_version", "snapshot_id", "as_of", "complete")})
                sources.append(source_meta)
                listings.extend(snapshot["listings"])
                hashes.append((source["source_id"], row["content_hash"]))
            except (KeyError, TypeError, ValueError):
                continue
        return {"listings": listings, "sources": sources, "snapshot_version": _hash({"mode": kind, "snapshots": hashes}), "is_demo": include_synthetic}

    def health(self, *, now=None):
        now = utc_now(now)
        observed = self.read_current(now=now)
        synthetic = self.read_current(now=now, include_synthetic=True)
        with self._connection() as db:
            total = db.execute("SELECT COUNT(*) FROM v2_current").fetchone()[0]
        available = len(observed["sources"]) + len(synthetic["sources"])
        return {"status": "ready" if observed["sources"] else "no_market_data", "source_count": len(observed["sources"]), "listing_count": len(observed["listings"]), "synthetic_source_count": len(synthetic["sources"]), "synthetic_listing_count": len(synthetic["listings"]), "unavailable_source_count": max(0, total - available), "market_data_available": bool(observed["sources"] and observed["listings"])}
