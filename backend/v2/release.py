"""Inspectable release gates; synthetic demonstrations can never pass them."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path

from .models import parse_timestamp
from .acceptance import acceptance_records_valid


def context_fingerprint(runtime, source_ids):
    """Tie drills to the same code and supplier configuration, without exposing it."""
    package = Path(__file__).parent
    files = sorted(package.glob("*.py"))
    files.append(package.parent / "src" / "server.py")
    root = package.parent.parent
    files.extend(sorted((root / "frontend" / "v2").glob("*.js")))
    files.extend(sorted((root / "frontend" / "v2").glob("*.html")))
    files.extend(sorted((root / "frontend" / "v2").glob("*.css")))
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if runtime.suppliers_path:
        try:
            hashes["supplier_config"] = hashlib.sha256(runtime.suppliers_path.read_bytes()).hexdigest()
        except OSError:
            hashes["supplier_config"] = "unavailable"
    return hashlib.sha256(json.dumps({"code": hashes, "source_ids": sorted(source_ids)}, sort_keys=True).encode()).hexdigest()


def checked_evidence(path, *, now, kind, context, max_age_days=7):
    """Operator-owned manifest pins a machine report by content hash."""
    try:
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            return False
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entry = manifest.get("reports", {}).get(kind, {})
        report_path = (path.parent / entry["path"]).resolve()
        if not report_path.is_relative_to(path.parent.resolve()) or report_path.stat().st_size > 2_000_000:
            return False
        raw = report_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            return False
        report = json.loads(raw)
        stamp = parse_timestamp(report["checked_at"])
        if not acceptance_records_valid(report, kind=kind, directory=report_path.parent):
            return False
        if kind == "load_test" and not (report.get("workload") == "evaluate_market" and report.get("request_count", 0) >= 60
                and report.get("concurrency", 0) >= 4 and report.get("unique_subject_count", 0) >= 10
                and report.get("subject_building_count", 0) >= 5 and report.get("reference_result_count", 0) >= report.get("request_count", 0) * .95):
            return False
        return (manifest.get("schema_version") == "release-evidence-1.0"
                and report.get("kind") == kind and report.get("passed") is True
                and report.get("scope") == "market_operations" and report.get("context_fingerprint") == context
                and timedelta(0) <= now - stamp <= timedelta(days=max_age_days))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def readiness(runtime, *, now, access_protected=False, legacy_enabled=False):
    data = runtime.market_data(now=now)
    sources = {s["source_id"] for s in data["sources"]}
    context = context_fingerprint(runtime, sources)
    from .service import approved_segments
    approved = approved_segments(runtime.registry_path, sources, now)
    fresh = [r for r in data["listings"] if r.get("status") == "active" and r.get("status_verified_at") and timedelta(0) <= now - parse_timestamp(r["status_verified_at"]) <= timedelta(hours=24)]
    segment_availability = {}
    for segment in sorted(approved):
        rows = [r for r in fresh if f"{r['city']}/{r['layout']}" == segment and all(r.get(field) is not None for field in ("mgmt_fee_yen", "structure", "built_year", "walk_min", "floor", "bathroom_separate", "furnished"))]
        units, buildings = len({r["unit_id"] for r in rows}), len({r["building_id"] for r in rows})
        segment_availability[segment] = {"unit_count": units, "building_count": buildings, "minimum_available": units >= 20 and buildings >= 10}
    monitor = runtime.supply_status(now=now)
    monitored = {s.get("source_id"): s for s in monitor.get("sources", [])}
    healthy_worker = bool(sources) and all(s in monitored and monitored[s].get("last_success") and monitored[s].get("contract_status") == "valid" and not monitored[s].get("worker_overdue") and monitored[s].get("consecutive_failures") == 0 and timedelta(0) <= now - parse_timestamp(monitored[s]["last_success"]) <= timedelta(hours=24) for s in sources)
    checks = {
        "supplier_contracts_configured": runtime.suppliers_path is not None and bool(sources),
        "fresh_observed_listings": bool(segment_availability) and all(s["minimum_available"] for s in segment_availability.values()),
        "market_segments_validated": bool(approved),
        "worker_success": bool(healthy_worker),
        "access_protected": bool(access_protected),
        "demo_disabled": not runtime.demo_enabled,
        "legacy_disabled": not legacy_enabled,
        "restore_drill": checked_evidence(runtime.release_evidence_path, now=now, kind="restore_drill", context=context) if runtime.release_evidence_path else False,
        "load_test": checked_evidence(runtime.release_evidence_path, now=now, kind="load_test", context=context) if runtime.release_evidence_path else False,
        "alerts_drill": checked_evidence(runtime.release_evidence_path, now=now, kind="alerts_drill", context=context) if runtime.release_evidence_path else False,
        "usability_review": checked_evidence(runtime.release_evidence_path, now=now, kind="usability_review", context=context, max_age_days=30) if runtime.release_evidence_path else False,
    }
    if runtime.release_evidence_path:
        try:
            settings = json.loads(runtime.release_evidence_path.read_text(encoding="utf-8"))
            # Zero subscription spending is the user's current constraint.
            checks["budget_confirmed"] = isinstance(settings.get("budget_confirmed_by"), str) and bool(settings["budget_confirmed_by"].strip()) and settings.get("monthly_cost_yen") == 0 and type(settings.get("monthly_cost_yen")) is int
            checks["operator_release_approved"] = settings.get("operator_release_approved") is True
        except (OSError, ValueError, AttributeError):
            checks["budget_confirmed"] = checks["operator_release_approved"] = False
    else:
        checks["budget_confirmed"] = checks["operator_release_approved"] = False
    return {"schema_version": "release-readiness-1.0", "ready": all(checks.values()),
            "status": "ready_for_private_pilot" if all(checks.values()) else "not_ready",
            "checked_at": now.isoformat(), "checks": checks,
            "blockers": [key for key, passed in checks.items() if not passed],
            "eligible_segments": sorted(approved), "fresh_listing_count": len(fresh),
            "context_fingerprint": context, "source_ids": sorted(sources),
            "segment_availability": segment_availability,
            "deployment_scope": "single_instance_private_pilot", "monthly_subscription_budget_yen": 0}
