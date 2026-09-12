"""Server-owned settings, data mode isolation and assessment receipts."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from backend.v2.comparison import compare
from backend.v2.models import ValidationError, parse_timestamp, validate_subject
from backend.v2.storage import open_store
from backend.v2.approval import approved_segments

ROOT = Path(__file__).resolve().parents[2]
CITIES = [{"id": "tokyo", "label": "도쿄"}, {"id": "osaka", "label": "오사카"}, {"id": "fukuoka", "label": "후쿠오카"}]
POLICY_VERSION = "direct-v2.0"


class Runtime:
    def __init__(self, db_path: str | Path | None = None, *, demo_enabled: bool = False, registry_path: str | Path | None = None,
                 suppliers_path=None, state_db=None, release_evidence_path=None):
        path = Path(db_path or os.getenv("HOUSE_EVALUATOR_DB", str(ROOT / ".runtime" / "v2.sqlite3")))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.store = open_store(path)
        self.demo_enabled = demo_enabled
        self.registry_path = Path(registry_path) if registry_path else None
        self.suppliers_path = Path(suppliers_path) if suppliers_path else None
        self.state_db = Path(state_db) if state_db else ROOT / ".runtime" / "ingestion.sqlite3"
        self.release_evidence_path = Path(release_evidence_path) if release_evidence_path else None
        self._seed_lock = RLock()
        self._seeded_at = None

    def market_data(self, *, now=None):
        now = now or datetime.now(timezone.utc)
        data = self.store.read_current(now=now)
        if self.suppliers_path is None:
            return data
        from backend.v2.supply import permitted_source_ids
        try:
            permitted = permitted_source_ids(self.suppliers_path, now=now)
        except (OSError, ValueError, TypeError):
            permitted = set()
        sources = [s for s in data["sources"] if s["source_id"] in permitted]
        listings = [r for r in data["listings"] if r["source_id"] in permitted]
        version = hashlib.sha256(json.dumps({"snapshot": data["snapshot_version"], "permitted": sorted(permitted)}, sort_keys=True).encode()).hexdigest()
        return {"sources": sources, "listings": listings, "snapshot_version": version, "is_demo": False}

    def supply_status(self, *, now=None):
        from backend.v2.supply import monitor_status
        return monitor_status(self.state_db, now=now)

    def health(self, *, now=None):
        summary = self.store.health(now=now)
        market = self.market_data(now=now)
        summary["contract_excluded_source_count"] = max(0, summary["source_count"] - len(market["sources"]))
        summary.update(source_count=len(market["sources"]), listing_count=len(market["listings"]),
                       market_data_available=bool(market["listings"]), status="ready" if market["listings"] else "no_market_data")
        return summary

    def readiness(self, *, now=None, access_protected=False, legacy_enabled=False):
        from backend.v2.release import readiness
        return readiness(self, now=now or datetime.now(timezone.utc), access_protected=access_protected, legacy_enabled=legacy_enabled)

    def seed_demo(self, *, now: datetime | None = None) -> None:
        if not self.demo_enabled:
            return
        from backend.v2.demo import make_demo_snapshot
        with self._seed_lock:
            now = now or datetime.now(timezone.utc)
            if self._seeded_at is not None and now - self._seeded_at < timedelta(hours=1):
                return
            for city in CITIES:
                self.store.import_snapshot(make_demo_snapshot(city["id"], now=now), now=now, allow_synthetic=True)
            self._seeded_at = now

    def stations(self, city: str, *, mode: str = "market", now: datetime | None = None) -> dict:
        if not isinstance(city, str) or city not in {c["id"] for c in CITIES}:
            raise ValidationError("지원되지 않는 도시입니다.")
        if not isinstance(mode, str) or mode not in {"market", "demo"}:
            raise ValidationError("mode는 market 또는 demo여야 합니다.")
        if mode == "demo" and not self.demo_enabled:
            raise PermissionError("합성 시연이 비활성화되어 있습니다.")
        now = now or datetime.now(timezone.utc)
        if mode == "demo":
            self.seed_demo(now=now)
        data = self.store.read_current(now=now, include_synthetic=True) if mode == "demo" else self.market_data(now=now)
        stations = {}
        for row in data["listings"]:
            if row["city"] != city or row["status"] != "active" or not row["status_verified_at"]:
                continue
            verified = parse_timestamp(row["status_verified_at"])
            if not timedelta(0) <= now - verified <= timedelta(hours=24):
                continue
            key = (row["station_id"], row["municipality"])
            item = {field: row[field] for field in ("station_id", "station_name", "municipality")}
            if key not in stations or not stations[key]["station_name"]:
                stations[key] = item
        return {"stations": sorted(stations.values(), key=lambda s: (s["municipality"], s["station_name"] or "", s["station_id"])), "mode": mode, "city": city}

    def capabilities(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        data = self.market_data(now=now)
        ids = {s["source_id"] for s in data["sources"]}
        return {
            "schema_version": "2.0", "cities": CITIES,
            "demo_enabled": self.demo_enabled, "source_count": len(data["sources"]),
            "market_data_available": bool(data["listings"]),
            "validation_status": "configured" if approved_segments(self.registry_path, ids, now) else "unvalidated",
            "limits": {"body_bytes": 65536, "max_status_age_hours": 24},
        }

    def evaluate(self, payload: dict, *, now: datetime | None = None) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"subject", "mode"}:
            raise ValidationError("subject와 mode만 전송할 수 있습니다.")
        mode = payload.get("mode", "market")
        if not isinstance(mode, str) or mode not in {"market", "demo"}:
            raise ValidationError("mode는 market 또는 demo여야 합니다.")
        if mode == "demo" and not self.demo_enabled:
            raise PermissionError("합성 시연은 이 서버에서 활성화되지 않았습니다.")
        now = now or datetime.now(timezone.utc)
        subject = validate_subject(payload.get("subject"), now=now)
        if mode == "demo":
            self.seed_demo(now=now)
        data = self.store.read_current(now=now, include_synthetic=True) if mode == "demo" else self.market_data(now=now)
        ids = {s["source_id"] for s in data["sources"]}
        approved = approved_segments(self.registry_path, ids, now) if mode == "market" else set()
        result = compare(subject, data["listings"], now=now,
                         snapshot_version=data["snapshot_version"], sources=data["sources"],
                         is_demo=mode == "demo", validated_segments=approved)
        result["data_mode"] = mode
        result["validation_status"] = "approved_segment" if f"{subject['city']}/{subject['layout']}" in approved else "unvalidated"
        # Include the approved policy configuration in the audit identity, never a client value.
        receipt_input = {"subject": subject, "mode": mode, "approved_segments": sorted(approved),
                         "snapshot_version": data["snapshot_version"], "policy": POLICY_VERSION,
                         "evaluated_at": result["evaluated_at"]}
        result["assessment_id"] = hashlib.sha256(json.dumps(receipt_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        result["receipt"] = receipt_input
        return result
