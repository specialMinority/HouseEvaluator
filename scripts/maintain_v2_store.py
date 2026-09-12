"""Operator-only backup, fresh-path restore, and dry-run-first retention CLI."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.maintenance import (
    _connect, _digest, _locations, _manifest, _safe_path, _schema_check,
    apply_maintenance, backup_store, maintenance_plan, restore_backup,
)
from backend.v2.models import ValidationError
from backend.v2.supply import SupplyError, contract_status, load_suppliers


def _permitted_sources(path):
    # Unlike the read-path permitted_source_ids helper, malformed configuration
    # must raise here: treating a parse failure as an empty set could delete data.
    return {item["source_id"] for item in load_suppliers(path)
            if contract_status(item) == "valid"}


def _source_inventory(path):
    """Read only bounded identifiers from a validated local snapshot database."""
    path = _safe_path(path, exists=True)
    with closing(_connect(path)) as database:
        _schema_check(database)
        rows = database.execute("SELECT source_id,data_kind FROM v2_current").fetchmany(10001)
    if len(rows) > 10000 or any(
        not isinstance(row["source_id"], str) or not row["source_id"]
        or len(row["source_id"]) > 2048 or row["data_kind"] not in ("observed", "synthetic")
        for row in rows
    ):
        raise ValidationError("maintenance source inventory is invalid or too large")
    return {row["source_id"] for row in rows}, {row["source_id"] for row in rows if row["data_kind"] == "observed"}


def _backup_inventory(manifest_path):
    # _manifest validates the managed filename, metadata and contained database
    # path before any SQLite access; never open a path from unvalidated JSON.
    manifest, database = _manifest(manifest_path)
    if database.stat().st_size != manifest["size_bytes"] or _digest(database) != manifest["sha256"]:
        raise ValidationError("backup checksum does not match")
    all_ids, observed = _source_inventory(database)
    if all_ids != {source["source_id"] for source in manifest["sources"]}:
        raise ValidationError("backup source metadata does not match database contents")
    return observed


def _revoked_sources(args):
    revoked = set(args.revoke_source)
    if not args.suppliers:
        return revoked
    config = _safe_path(args.suppliers, exists=True)
    _permitted_sources(config)  # Reject invalid configuration before inspecting storage.
    if args.command == "restore":
        observed = _backup_inventory(args.manifest)
    else:
        database, directory = _locations(args.db, args.backup_dir)
        _, observed = _source_inventory(database)
        if args.command == "cleanup" and directory.exists():
            for manifest in sorted(directory.glob("he-v2-*.manifest.json")):
                # Do not delete even an expired backup whose contents cannot be
                # verified well enough to distinguish observed/synthetic data.
                observed.update(_backup_inventory(manifest))
    # Re-read the configuration AND evidence after potentially expensive DB and
    # backup checks, immediately before dispatching the maintenance operation.
    return revoked | (observed - _permitted_sources(config))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--backup-dir", required=True)
        command.add_argument("--retention-days", type=int, default=7)
        command.add_argument("--revoke-source", action="append", default=[])
        command.add_argument("--suppliers", help="supplier configuration; deny observed sources without current permitted contracts")
        if name == "cleanup":
            command.add_argument("--apply", action="store_true", help="delete only eligible current snapshots and managed backup pairs")
    restore = commands.add_parser("restore")
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--destination", required=True, help="a fresh path; an existing database is never overwritten")
    restore.add_argument("--revoke-source", action="append", default=[])
    restore.add_argument("--suppliers", help="supplier configuration checked again before restoring observed sources")
    args = parser.parse_args(argv)
    try:
        revoked = _revoked_sources(args)
        if args.command == "restore":
            result = restore_backup(args.manifest, args.destination, revoked_source_ids=revoked)
        else:
            action = backup_store if args.command == "backup" else apply_maintenance if args.apply else maintenance_plan
            result = action(args.db, args.backup_dir, retention_days=args.retention_days, revoked_source_ids=revoked)
    except SupplyError:
        print("Maintenance rejected: supplier configuration is invalid", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"Maintenance rejected: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("Maintenance failed: local file or database unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("status") == "partially_applied" or result.get("blocked_backups") else 0


if __name__ == "__main__":
    raise SystemExit(main())
