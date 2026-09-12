"""Import one bounded, complete, locally supplied JSON snapshot; no HTTP."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.models import ValidationError
from backend.v2.store import SnapshotStore, local_path


MAX_INPUT_BYTES = 20 * 1024 * 1024


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("JSON contains duplicate object keys")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValidationError("JSON must not contain non-finite numbers")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="local UTF-8 JSON snapshot")
    parser.add_argument("--db", required=True, help="local SQLite database path")
    parser.add_argument("--allow-empty", action="store_true", help="explicitly authorize a complete empty snapshot")
    parser.add_argument("--allow-synthetic", action="store_true", help="explicitly import isolated development fixtures")
    args = parser.parse_args(argv)
    try:
        source_path, db_path = local_path(args.path), local_path(args.db)
        if source_path == db_path:
            raise ValidationError("snapshot and database paths must be different")
        if not source_path.is_file():
            raise ValidationError("snapshot path must name a local regular file")
        with source_path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValidationError("snapshot exceeds the 20 MiB import limit")
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
        result = SnapshotStore(db_path).import_snapshot(payload, allow_empty=args.allow_empty, allow_synthetic=args.allow_synthetic)
    except (ValidationError, json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        print(f"Import rejected: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("Import failed: local file or database unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
