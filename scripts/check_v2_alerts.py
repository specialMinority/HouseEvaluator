"""Check local data/worker availability once; store local incidents, send nothing."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.v2.alerts import check_once, checked_paths, write_fresh
from backend.v2.models import ValidationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--alerts-db", required=True)
    parser.add_argument("--suppliers")
    parser.add_argument("--output", help="optional fresh JSON file")
    args = parser.parse_args(argv)
    try:
        paths = checked_paths(db=args.db, state_db=args.state_db, alerts_db=args.alerts_db,
                              suppliers=args.suppliers, output=args.output)
        if args.output and paths["output"].exists():
            raise ValidationError("report output must be fresh")
        report = check_once(args.db, args.state_db, args.alerts_db, suppliers=args.suppliers)
        if args.output:
            write_fresh(paths["output"], report)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        print(json.dumps({"error": "local_alert_check_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
