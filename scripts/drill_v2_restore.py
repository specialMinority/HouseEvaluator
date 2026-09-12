"""Back up and restore into a disposable path; never replace the live database."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.maintenance import (
    _connect, _digest, _empty_private, _publish, _safe_path, _schema_check,
    backup_store, restore_backup,
)
from backend.v2.models import ValidationError, iso_timestamp, utc_now
from backend.v2.release import context_fingerprint
from backend.v2.service import Runtime
from backend.v2.supply import permitted_source_ids


def _inventory(path):
    """Compare logical snapshot/receipt identity, not SQLite page counters."""
    with closing(_connect(path)) as db:
        _schema_check(db)
        snapshots = [dict(row) for row in db.execute("SELECT source_id,snapshot_id,as_of,data_kind,content_hash FROM v2_current ORDER BY source_id")]
        receipts = [dict(row) for row in db.execute("SELECT source_id,snapshot_id,content_hash,as_of FROM v2_receipts ORDER BY source_id,snapshot_id")]
        counts = {}
        for row in db.execute("SELECT source_id,payload FROM v2_current ORDER BY source_id"):
            payload = json.loads(row["payload"])
            counts[row["source_id"]] = len(payload["listings"])
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"snapshot_hash": digest(snapshots), "receipt_hash": digest(receipts), "counts": counts,
            "observed_ids": {row["source_id"] for row in snapshots if row["data_kind"] == "observed"},
            "synthetic_ids": {row["source_id"] for row in snapshots if row["data_kind"] == "synthetic"}}


def run_drill(db_path, backup_dir, *, suppliers=None, state_db=None, now=None):
    now = utc_now(now)
    original = _safe_path(db_path, exists=True)
    directory = _safe_path(backup_dir)
    suppliers_path = _safe_path(suppliers, exists=True) if suppliers else None
    state_path = _safe_path(state_db) if state_db else None
    # Explicit configured denial must not create a fresh backup of that source.
    before = _inventory(original)
    permitted = permitted_source_ids(suppliers_path, now=now) if suppliers_path else set()
    revoked = before["observed_ids"] - permitted if suppliers_path else set()
    created = backup_store(original, directory, now=now, revoked_source_ids=revoked)
    manifest_path = directory / created["manifest_file"]
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    backup_path = directory / manifest["database_file"]
    expected = _inventory(backup_path)
    # A source may have been imported while backup was taking its snapshot.
    # Use the copied state, never the earlier live counts, for verification.
    if suppliers_path:
        permitted = permitted_source_ids(suppliers_path, now=now)
        revoked = expected["observed_ids"] - permitted
    with TemporaryDirectory(prefix="houseevaluator-restore-drill-") as temporary:
        restored_path = Path(temporary) / "restored.sqlite3"
        restored = restore_backup(manifest_path, restored_path, now=now, revoked_source_ids=revoked)
        actual = _inventory(restored_path)
        expected_counts = {source["source_id"]: source["listing_count"] for source in manifest["sources"]}
        checks = {
            "backup_checksum_matches": _digest(backup_path) == manifest["sha256"],
            "backup_size_matches": backup_path.stat().st_size == manifest["size_bytes"],
            "manifest_counts_match": expected["counts"] == expected_counts,
            "restored_counts_match": actual["counts"] == expected_counts,
            "snapshot_hashes_match": actual["snapshot_hash"] == expected["snapshot_hash"],
            "receipt_hashes_match": actual["receipt_hash"] == expected["receipt_hash"],
            "restore_summary_matches": restored["source_count"] == len(expected_counts) and restored["listing_count"] == sum(expected_counts.values()),
        }
        runtime = Runtime(restored_path, suppliers_path=suppliers_path, state_db=state_path)
        market = runtime.market_data(now=now)
        market_ids = {source["source_id"] for source in market["sources"]}
        real = bool(suppliers_path and market_ids and market["listings"] and not actual["synthetic_ids"])
        context = context_fingerprint(runtime, market_ids if real else set())
        report = {
            "schema_version": "operations-report-1.0", "kind": "restore_drill",
            "checked_at": iso_timestamp(now), "scope": "market_operations" if real else "synthetic_smoke",
            "context_fingerprint": context, "passed": all(checks.values()), "checks": checks,
            "restored_source_count": restored["source_count"], "restored_listing_count": restored["listing_count"],
            "permitted_market_source_count": len(market_ids) if suppliers_path else 0,
            "source_ids": sorted(market_ids) if real else [],
            "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
            "backup_database_sha256": manifest["sha256"],
            "restored_snapshot_hash": actual["snapshot_hash"], "restored_receipt_hash": actual["receipt_hash"],
            "backup_created_at": manifest["created_at"], "backup_expires_at": manifest["expires_at"],
            "live_database_replaced": False, "temporary_restore_removed": True,
            "evidence_registered": False,
        }
    return report


def _write_fresh(path, report):
    path = _safe_path(path)
    if path.exists():
        raise ValidationError("report output must be a fresh file")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".restore-report-" + uuid.uuid4().hex + ".partial")
    try:
        _empty_private(temporary)
        temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _publish(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--suppliers", help="operator-owned supplier configuration; absent means smoke scope")
    parser.add_argument("--state-db", help="optional ingestion state; never modified by this drill")
    parser.add_argument("--output", required=True, help="fresh JSON report path; no evidence registry is changed")
    args = parser.parse_args(argv)
    try:
        output = _safe_path(args.output)
        if output.exists():
            raise ValidationError("report output must be a fresh file")
        report = run_drill(args.db, args.backup_dir, suppliers=args.suppliers, state_db=args.state_db)
        _write_fresh(output, report)
    except ValidationError as exc:
        print(json.dumps({"error": "restore_drill_rejected", "message": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "restore_drill_failed"}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
