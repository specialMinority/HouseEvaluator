"""Deterministic synthetic examples. These are not market observations.

Prices and characteristics are fixed development fixtures. Only simulation
timestamps vary with the supplied aware clock. No property portal is queried.
"""

from datetime import timedelta
import hashlib

from .models import ValidationError, iso_timestamp, utc_now


_CITIES = {
    "tokyo": ("東京都新宿区", "demo:tokyo:station", "합성 도쿄역권", 90000),
    "osaka": ("大阪府大阪市北区", "demo:osaka:station", "합성 오사카역권", 72000),
    "fukuoka": ("福岡県福岡市博多区", "demo:fukuoka:station", "합성 후쿠오카역권", 60000),
}


def demo_subject(city):
    if not isinstance(city, str) or city not in _CITIES:
        raise ValidationError("unsupported demo city")
    municipality, station, name, rent = _CITIES[city]
    return {"city": city, "municipality": municipality, "station_id": station, "station_name": name, "unit_id": f"demo:{city}:subject-unit", "building_id": f"demo:{city}:subject-building", "layout": "1K", "structure": "rc", "property_type": "apartment", "contract_type": "standard", "furnished": False, "area_sqm": 25.0, "built_year": 2016, "walk_min": 6, "floor": 3, "orientation": "S", "bathroom_separate": True, "rent_yen": rent, "mgmt_fee_yen": 5000}


def make_demo_snapshot(city, *, now=None):
    now = utc_now(now)
    subject = demo_subject(city)
    source_id = f"synthetic-demo-{city}"
    stamp = iso_timestamp(now)
    observations = []
    # Twenty-four units across twelve buildings. All identities differ from
    # the subject; every building contains two offers of equally small weight.
    for index in range(24):
        unit = dict(subject)
        unit.update({
            "unit_id": f"demo:{city}:unit-{index:02d}",
            "building_id": f"demo:{city}:building-{index // 2:02d}",
            "source_id": source_id,
            "listing_id": f"demo:{city}:listing-{index:02d}",
            "observation_id": f"demo:{city}:observation-{index:02d}",
            "rent_yen": subject["rent_yen"] + (index % 7 - 3) * 1000,
            "area_sqm": 25 + (index % 3 - 1) * 0.5,
            "built_year": 2016 + index % 3 - 1,
            "walk_min": 5 + index % 3,
            "status": "active", "data_kind": "synthetic",
            "source_updated_at": iso_timestamp(now - timedelta(hours=2)),
            "received_at": iso_timestamp(now - timedelta(hours=1)),
            "status_verified_at": iso_timestamp(now - timedelta(minutes=30)),
            "public_url": None,
        })
        observations.append(unit)
    return {
        "schema_version": "2.0", "snapshot_id": f"demo-{city}-{hashlib.sha256(stamp.encode()).hexdigest()[:16]}",
        "as_of": stamp, "complete": True,
        "source": {"source_id": source_id, "independent_source_id": source_id, "display_name": f"합성 데모 / {city} / 실제 매물 아님", "data_kind": "synthetic", "rights": {"comparison": True, "display": True, "storage": True, "expires_at": iso_timestamp(now + timedelta(days=1))}},
        "listings": observations,
    }
