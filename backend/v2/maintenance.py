"""Operator-only SQLite backup, fresh-path restore and scoped retention cleanup.

Manifest hashes detect corruption, not an attacker able to replace both files.
No function recursively deletes a directory or overwrites a live database.
"""

from contextlib import closing
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .models import ValidationError, iso_timestamp, parse_timestamp, utc_now, validate_snapshot
from .store import _hash, local_path


_NAME = re.compile(r"^he-v2-\d{8}T\d{12}Z-[0-9a-f]{32}$")
_SCHEMA = {
    "v2_current": ("source_id", "snapshot_id", "as_of", "data_kind", "content_hash", "payload"),
    "v2_receipts": ("source_id", "snapshot_id", "content_hash", "as_of"),
}
_MAX_MANIFEST = 1024 * 1024


def _safe_path(value, *, exists=False):
    # Inspect before resolve, because resolve would conceal a symlink/junction.
    try:
        lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    except (TypeError, ValueError) as exc:
        raise ValidationError("a local filesystem path is required") from exc
    resolved = local_path(value)
    for part in (lexical, *lexical.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValidationError("maintenance paths must not traverse symlinks or junctions")
    if exists and not resolved.exists():
        raise ValidationError("required local maintenance file does not exist")
    return resolved


def _settings(now, retention_days, revoked_source_ids):
    now = utc_now(now)
    if type(retention_days) is not int or not 1 <= retention_days <= 365:
        raise ValidationError("retention_days must be an integer from 1 to 365")
    if isinstance(revoked_source_ids, (str, bytes)):
        raise ValidationError("revoked_source_ids must be a collection of source identifiers")
    try:
        revoked = set(revoked_source_ids)
    except TypeError as exc:
        raise ValidationError("revoked_source_ids must be a collection of source identifiers") from exc
    if any(not isinstance(item, str) or not item.strip() for item in revoked):
        raise ValidationError("revoked_source_ids must contain nonempty strings")
    return now, timedelta(days=retention_days), revoked


def _connect(path, *, write=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    if not write:
        db.execute("PRAGMA query_only=ON")
    return db


def _locations(db_path, backup_dir):
    database, directory = _safe_path(db_path, exists=True), _safe_path(backup_dir)
    if not database.is_file() or database.is_relative_to(directory) or (directory.exists() and not directory.is_dir()):
        raise ValidationError("use a database file outside a separate managed backup directory")
    return database, directory


def _schema_check(db):
    if db.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok":
        raise ValidationError("database integrity check failed")
    objects = db.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    allowed = set(_SCHEMA) | {"v2_maintenance_audit"}
    if any(row["type"] != "table" or row["name"] not in allowed for row in objects):
        raise ValidationError("database contains unsupported schema objects")
    for table, columns in _SCHEMA.items():
        actual = tuple(row["name"] for row in db.execute(f"PRAGMA table_info({table})"))
        if actual != columns:
            raise ValidationError("database does not have the v2 snapshot schema")


def _source_summary(db, now, retention, revoked, *, strict):
    summaries, actions = [], []
    for row in db.execute("SELECT * FROM v2_current ORDER BY source_id"):
        reason, count = None, 0
        try:
            payload = json.loads(row["payload"])
            count = len(payload["listings"])
            source = payload["source"]
            expires = parse_timestamp(source["rights"]["expires_at"])
            as_of = parse_timestamp(row["as_of"])
            if _hash(payload) != row["content_hash"] or source["source_id"] != row["source_id"] or payload["snapshot_id"] != row["snapshot_id"] or payload["as_of"] != row["as_of"] or source["data_kind"] != row["data_kind"]:
                reason = "invalid_snapshot"
            elif row["source_id"] in revoked:
                reason = "revoked_source"
            elif not all(source["rights"].get(key) is True for key in ("comparison", "display", "storage")) or expires <= now:
                reason = "expired_or_missing_rights"
            elif as_of + retention <= now:
                reason = "snapshot_retention_elapsed"
            elif strict:
                validate_snapshot(payload, now=now)
            summary = {"source_id": row["source_id"], "snapshot_id": row["snapshot_id"], "as_of": row["as_of"], "rights_expires_at": iso_timestamp(expires), "listing_count": count}
            summaries.append(summary)
        except (KeyError, TypeError, ValueError):
            reason = "invalid_snapshot"
        if reason:
            if strict:
                raise ValidationError(f"backup source is ineligible: {reason}")
            actions.append({"source_id": row["source_id"], "content_hash": row["content_hash"], "listing_count": count, "reason": reason})
    return summaries, actions


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _empty_private(path):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)


def _publish(temporary, destination):
    # Unlike os.replace/Unix rename, link cannot overwrite an existing path.
    os.link(temporary, destination)
    temporary.unlink()


def _copy_database(source, destination):
    with closing(_connect(source)) as src, closing(sqlite3.connect(destination, timeout=10)) as dst:
        src.backup(dst, pages=128, sleep=0.01)


def backup_store(db_path, backup_dir, *, now=None, retention_days=7, revoked_source_ids=()):
    now, retention, revoked = _settings(now, retention_days, revoked_source_ids)
    source, directory = _locations(db_path, backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    directory = _safe_path(directory, exists=True)
    name = "he-v2-" + now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    target, manifest_path = directory / (name + ".sqlite3"), directory / (name + ".manifest.json")
    temporary = directory / (name + ".partial")
    manifest_temp = directory / (name + ".manifest.partial")
    published = False
    try:
        _empty_private(temporary)
        _copy_database(source, temporary)
        with closing(_connect(temporary)) as db:
            _schema_check(db)
            summaries, _ = _source_summary(db, now, retention, revoked, strict=True)
        deadline = min([now + retention] + [min(parse_timestamp(s["as_of"]) + retention, parse_timestamp(s["rights_expires_at"])) for s in summaries])
        manifest = {"format": "houseevaluator-sqlite-backup-v1", "schema_version": "2.0", "database_file": target.name, "created_at": iso_timestamp(now), "expires_at": iso_timestamp(deadline), "retention_days": retention_days, "sha256": _digest(temporary), "size_bytes": temporary.stat().st_size, "sources": summaries}
        _empty_private(manifest_temp)
        with manifest_temp.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _publish(temporary, target)
        published = True
        _publish(manifest_temp, manifest_path)
        return {"status": "backed_up", "manifest_file": manifest_path.name, **manifest}
    except BaseException:
        if published and not manifest_path.exists():
            target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)


def _manifest(path):
    path = _safe_path(path, exists=True)
    name = path.name.removesuffix(".manifest.json")
    if not path.name.endswith(".manifest.json") or not _NAME.fullmatch(name) or not path.is_file():
        raise ValidationError("manifest must be a managed v2 backup manifest")
    with path.open("rb") as stream:
        data = stream.read(_MAX_MANIFEST + 1)
    if len(data) > _MAX_MANIFEST:
        raise ValidationError("backup manifest is too large")
    try:
        manifest = json.loads(data)
        required = {"format", "schema_version", "database_file", "created_at", "expires_at", "retention_days", "sha256", "size_bytes", "sources"}
        if set(manifest) != required or manifest["format"] != "houseevaluator-sqlite-backup-v1" or manifest["schema_version"] != "2.0" or manifest["database_file"] != name + ".sqlite3":
            raise ValidationError("backup manifest schema is invalid")
        if not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"]) or type(manifest["size_bytes"]) is not int or manifest["size_bytes"] <= 0:
            raise ValidationError("backup checksum metadata is invalid")
        if not isinstance(manifest["sources"], list):
            raise ValidationError("backup source metadata is invalid")
        created, expires = parse_timestamp(manifest["created_at"]), parse_timestamp(manifest["expires_at"])
        _settings(created, manifest["retention_days"], ())
        if expires <= created or expires > created + timedelta(days=manifest["retention_days"]):
            raise ValidationError("backup retention metadata is invalid")
        source_ids = set()
        for source in manifest["sources"]:
            if set(source) != {"source_id", "snapshot_id", "as_of", "rights_expires_at", "listing_count"} or any(not isinstance(source[field], str) or not source[field] for field in ("source_id", "snapshot_id")) or type(source["listing_count"]) is not int or source["listing_count"] < 0 or source["source_id"] in source_ids:
                raise ValidationError("backup source metadata is invalid")
            source_ids.add(source["source_id"])
            source_time = parse_timestamp(source["as_of"])
            source_expiry = parse_timestamp(source["rights_expires_at"])
            if source_time > created or expires > min(source_expiry, source_time + timedelta(days=manifest["retention_days"])):
                raise ValidationError("backup exceeds source rights or retention")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("backup manifest is invalid") from exc
    database = _safe_path(path.parent / manifest["database_file"], exists=True)
    if database.parent != path.parent or not database.is_file():
        raise ValidationError("backup database is outside its managed directory")
    return manifest, database


def restore_backup(manifest_path, destination, *, now=None, revoked_source_ids=()):
    now, _, revoked = _settings(now, 7, revoked_source_ids)
    manifest_path = _safe_path(manifest_path, exists=True)
    target = _safe_path(destination)
    if target.exists() or any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValidationError("restore requires a fresh destination; live databases are never overwritten")
    manifest, database = _manifest(manifest_path)
    if parse_timestamp(manifest["created_at"]) > now or parse_timestamp(manifest["expires_at"]) <= now:
        raise ValidationError("backup is expired or future-dated")
    if database.stat().st_size != manifest["size_bytes"] or _digest(database) != manifest["sha256"]:
        raise ValidationError("backup checksum does not match")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / (".restore-" + uuid.uuid4().hex + ".partial")
    try:
        _empty_private(temporary)
        # Verify the copied database, so later file changes cannot bypass checks.
        _copy_database(database, temporary)
        with closing(_connect(temporary)) as db:
            _schema_check(db)
            summaries, _ = _source_summary(db, now, timedelta(days=manifest["retention_days"]), revoked, strict=True)
        if summaries != manifest["sources"]:
            raise ValidationError("backup source metadata does not match database contents")
        # Recheck the source after copying; both before/after hashes must match.
        if database.stat().st_size != manifest["size_bytes"] or _digest(database) != manifest["sha256"]:
            raise ValidationError("backup changed during restore")
        _publish(temporary, target)
        return {"status": "restored", "source_count": len(summaries), "listing_count": sum(s["listing_count"] for s in summaries)}
    finally:
        temporary.unlink(missing_ok=True)


def _backup_actions(directory, now, retention, revoked):
    actions, blocked = [], []
    if not directory.exists():
        return actions, blocked
    if not directory.is_dir():
        raise ValidationError("managed backup path must be a directory")
    for path in sorted(directory.glob("he-v2-*.manifest.json")):
        try:
            manifest, database = _manifest(path)
            reason = None
            if any(s["source_id"] in revoked for s in manifest["sources"]):
                reason = "revoked_source"
            elif parse_timestamp(manifest["expires_at"]) <= now:
                reason = "backup_expired"
            elif parse_timestamp(manifest["created_at"]) + retention <= now:
                reason = "backup_retention_elapsed"
            elif any(parse_timestamp(s["as_of"]) + retention <= now for s in manifest["sources"]):
                reason = "snapshot_retention_elapsed"
            if reason:
                actions.append({"manifest_file": path.name, "database_file": database.name, "reason": reason})
        except (ValidationError, OSError):
            blocked.append({"manifest_file": path.name, "reason": "invalid_or_unsafe_backup_requires_operator_review"})
    return actions, blocked


def maintenance_plan(db_path, backup_dir, *, now=None, retention_days=7, revoked_source_ids=()):
    now, retention, revoked = _settings(now, retention_days, revoked_source_ids)
    database, directory = _locations(db_path, backup_dir)
    with closing(_connect(database)) as db:
        _schema_check(db)
        _, snapshots = _source_summary(db, now, retention, revoked, strict=False)
    backups, blocked = _backup_actions(directory, now, retention, revoked)
    return {"status": "plan_only", "evaluated_at": iso_timestamp(now), "retention_days": retention_days, "snapshot_actions": snapshots, "backup_actions": backups, "blocked_backups": blocked, "snapshot_count": len(snapshots), "backup_count": len(backups), "listing_count": sum(s["listing_count"] for s in snapshots)}


def apply_maintenance(db_path, backup_dir, *, now=None, retention_days=7, revoked_source_ids=()):
    now, retention, revoked = _settings(now, retention_days, revoked_source_ids)
    database, directory = _locations(db_path, backup_dir)
    # Calculate live eligibility under the same write lock as deletion. A newer
    # permitted import cannot be deleted using a stale dry-run observation.
    with closing(_connect(database, write=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _schema_check(db)
        _, snapshots = _source_summary(db, now, retention, revoked, strict=False)
        db.execute("PRAGMA secure_delete=ON")
        for item in snapshots:
            db.execute("DELETE FROM v2_current WHERE source_id=? AND content_hash=?", (item["source_id"], item["content_hash"]))
    backups, blocked = _backup_actions(directory, now, retention, revoked)
    deleted = 0
    for item in backups:
        try:
            # Re-read and re-check scope immediately before each exact unlink.
            manifest_path = _safe_path(directory / item["manifest_file"], exists=True)
            _, backup = _manifest(manifest_path)
            if backup == database or manifest_path.parent != directory or backup.parent != directory:
                raise ValidationError("cleanup cannot target the live database or another directory")
            backup.unlink()
            manifest_path.unlink()
            deleted += 1
        except (ValidationError, OSError):
            blocked.append({"manifest_file": item["manifest_file"], "reason": "cleanup_failed_requires_operator_review"})
    with closing(_connect(database, write=True)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS v2_maintenance_audit (performed_at TEXT NOT NULL, deleted_snapshots INTEGER NOT NULL, deleted_listings INTEGER NOT NULL, deleted_backups INTEGER NOT NULL, blocked_backups INTEGER NOT NULL)")
        db.execute("INSERT INTO v2_maintenance_audit VALUES (?,?,?,?,?)", (iso_timestamp(now), len(snapshots), sum(s["listing_count"] for s in snapshots), deleted, len(blocked)))
    return {"status": "applied" if not blocked else "partially_applied", "evaluated_at": iso_timestamp(now), "deleted_snapshot_count": len(snapshots), "deleted_listing_count": sum(s["listing_count"] for s in snapshots), "deleted_backup_count": deleted, "blocked_backups": blocked, "secure_erasure_guaranteed": False}
