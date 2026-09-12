"""Pull explicitly licensed normalized JSON feeds. One bounded pass by default."""

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.supply import IngestionRunner, SupplyError, monitor_status
from backend.v2.store import local_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="operator-owned supplier configuration JSON")
    parser.add_argument("--db", help="local SQLite snapshot storage path")
    parser.add_argument("--state-db", required=True, help="local durable SQLite work state")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one due pass (default)")
    mode.add_argument("--worker", action="store_true", help="explicit foreground worker until stopped")
    mode.add_argument("--status", action="store_true", help="read state only; no fetch/import")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--resume-source", help="explicitly resume an exhausted source")
    args = parser.parse_args(argv)
    if not 1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be 1..60")
    if args.status:
        print(json.dumps(monitor_status(args.state_db), ensure_ascii=False))
        return 0
    if not args.config or not args.db:
        parser.error("--config and --db are required for ingestion")
    try:
        from backend.v2.storage import open_store
        if local_path(args.db) == local_path(args.state_db):
            raise SupplyError("storage_path_conflict")
        runner = IngestionRunner(args.config, args.state_db, open_store(args.db))
        if args.resume_source:
            runner.resume(args.resume_source)
        while True:
            result = runner.run_once()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if not args.worker:
                return 2 if result["monitor"]["status"] in ("needs_attention", "unavailable") or any(x["event_code"] not in ("snapshot_imported", "snapshot_unchanged", "not_modified") for x in result["outcomes"]) else 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except SupplyError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error": "worker_unavailable"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
