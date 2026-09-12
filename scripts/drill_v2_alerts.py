"""Exercise persisted local incident/recovery transitions in a disposable fixture."""
import argparse
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.v2.alerts import AlertStore, checked_paths, write_fresh
from backend.v2.maintenance import _safe_path
from backend.v2.models import iso_timestamp, utc_now
from backend.v2.release import context_fingerprint


def run_drill(*, suppliers=None, now=None):
    now = utc_now(now)
    suppliers_path = _safe_path(suppliers) if suppliers else None
    # A missing or invalid config does not prevent synthetic logic verification.
    context = context_fingerprint(SimpleNamespace(suppliers_path=suppliers_path), set())
    with TemporaryDirectory(prefix="houseevaluator-alert-drill-") as temporary:
        path = Path(temporary) / "alerts.sqlite3"
        healthy = AlertStore(path).reconcile([], now=now)
        incident = AlertStore(path).reconcile(["worker_failed", "listings_stale"], now=now + timedelta(seconds=1))
        duplicate = AlertStore(path).reconcile(["worker_failed", "listings_stale"], now=now + timedelta(seconds=2))
        partial = AlertStore(path).reconcile(["listings_stale"], now=now + timedelta(seconds=3))
        recovered = AlertStore(path).reconcile([], now=now + timedelta(seconds=4))
        quiet = AlertStore(path).reconcile([], now=now + timedelta(seconds=5))
        with closing(sqlite3.connect(path)) as db:
            persisted = db.execute("SELECT code,event FROM alert_events ORDER BY event_id").fetchall()
        checks = {"initially_quiet": healthy["new_event_count"] == 0,
                  "incident_persisted": incident["new_event_count"] == 2 and len(duplicate["active_incidents"]) == 2,
                  "restart_deduplicates_unchanged": duplicate["new_event_count"] == 0,
                  "partial_recovery_recorded": partial["events"][0]["event"] == "recovered" and len(partial["active_incidents"]) == 1,
                  "full_recovery_recorded": recovered["events"][0]["event"] == "recovered" and recovered["active_incidents"] == [],
                  "recovery_remains_quiet": quiet["new_event_count"] == 0,
                  "history_survives_restart": persisted == [("listings_stale", "opened"), ("worker_failed", "opened"), ("worker_failed", "recovered"), ("listings_stale", "recovered")]}
    return {"schema_version": "operations-report-1.0", "kind": "alerts_drill",
            "checked_at": iso_timestamp(now), "context_fingerprint": context,
            "scope": "synthetic_smoke", "passed": all(checks.values()), "checks": checks,
            "persisted_event_count": len(persisted), "external_delivery_verified": False,
            "human_receipt_verified": False, "notification_sent": False, "delivery_scope": "local_only",
            "operational_database_modified": False, "temporary_fixture_removed": not Path(temporary).exists(),
            "evidence_registered": False,
            "limitations": ["Synthetic local state transitions only.", "External delivery and human receipt were not tested.",
                            "This report cannot satisfy the private-pilot alerts gate."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suppliers", help="optional configuration for the context hash; never modified")
    parser.add_argument("--output", required=True, help="fresh local JSON report")
    args = parser.parse_args(argv)
    try:
        output = _safe_path(args.output)
        checked_paths(db=output, state_db=None, suppliers=args.suppliers)
        if output.exists():
            raise ValueError("output exists")
        report = run_drill(suppliers=args.suppliers)
        write_fresh(output, report)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        print(json.dumps({"error": "local_alert_drill_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
