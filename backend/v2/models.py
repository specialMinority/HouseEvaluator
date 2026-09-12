"""Strict, dependency-free validation for the v2 local ingestion boundary.

Unknown facts remain None. Identifiers are trimmed, never case-folded or
matched by substring. All returned timestamps are normalized to UTC.
"""

from datetime import datetime, timezone
import math
import re
from urllib.parse import urlsplit


class ValidationError(ValueError):
    """Invalid or unsupported v2 input; safe to show to the operator."""


CITIES = ("tokyo", "osaka", "fukuoka")
LAYOUTS = ("1R", "1K", "1DK", "1LDK")
MAX_LISTINGS = 10000
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_SUBJECT_FIELDS = frozenset((
    "city", "municipality", "station_id", "station_name", "unit_id",
    "building_id", "layout", "structure", "property_type", "contract_type",
    "furnished", "area_sqm", "built_year", "walk_min", "floor",
    "orientation", "bathroom_separate", "rent_yen", "mgmt_fee_yen",
))
_OBSERVATION_FIELDS = _SUBJECT_FIELDS | frozenset((
    "source_id", "listing_id", "observation_id", "status", "source_updated_at",
    "received_at", "status_verified_at", "public_url", "data_kind",
))


def parse_timestamp(value):
    """Parse an ISO timestamp with an explicit offset; return aware UTC."""
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValidationError("timestamp must be ISO 8601 with time and timezone")
    # datetime accepts some out-of-range offset components; reject explicitly.
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValidationError("invalid timestamp timezone offset")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValidationError("invalid timestamp date or timezone") from exc


def utc_now(now=None):
    """Normalize an explicitly supplied aware clock, useful for offline tests."""
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def iso_timestamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _object(payload, allowed, name):
    if not isinstance(payload, dict):
        raise ValidationError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in payload):
        raise ValidationError(f"{name} keys must be strings")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValidationError(f"{name} contains unsupported fields: {', '.join(unknown)}")


def _text(value, field, *, nullable=False, max_length=200):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a nonempty string")
    result = value.strip()
    if len(result) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValidationError(f"{field} contains invalid characters or is too long")
    return result


def _enum(value, field, choices, *, nullable=False):
    if value is None and nullable:
        return None
    result = _text(value, field)
    if result not in choices:
        raise ValidationError(f"unsupported {field}: expected one of {', '.join(choices)}")
    return result


def _boolean(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not bool:
        raise ValidationError(f"{field} must be a boolean")
    return value


def _number(value, field, *, minimum, maximum, integer=False, nullable=False):
    if value is None and nullable:
        return None
    allowed = (int,) if integer else (int, float)
    if type(value) not in allowed:
        raise ValidationError(f"{field} must be {'an integer' if integer else 'a number'}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < minimum or value > maximum:
        raise ValidationError(f"{field} is outside the supported range")
    return value


def _time(value, field, now, *, nullable=False):
    if value is None and nullable:
        return None
    try:
        result = parse_timestamp(value)
    except ValidationError as exc:
        raise ValidationError(f"{field}: {exc}") from exc
    if result > now:
        raise ValidationError(f"{field} cannot be in the future")
    return iso_timestamp(result)


def validate_subject(payload, *, now=None):
    now = utc_now(now)
    _object(payload, _SUBJECT_FIELDS, "subject")
    result = {}
    for field in ("municipality", "station_id"):
        result[field] = _text(payload.get(field), field)
    for field in ("station_name", "unit_id", "building_id"):
        result[field] = _text(payload.get(field), field, nullable=True)
    result["city"] = _enum(payload.get("city"), "city", CITIES)
    result["layout"] = _enum(payload.get("layout"), "layout", LAYOUTS)
    result["structure"] = _enum(payload.get("structure"), "structure", ("wood", "light_steel", "steel", "rc", "src"), nullable=True)
    result["property_type"] = _enum(payload.get("property_type", "apartment"), "property_type", ("apartment",))
    result["contract_type"] = _enum(payload.get("contract_type", "standard"), "contract_type", ("standard", "fixed_term"))
    result["furnished"] = _boolean(payload.get("furnished"), "furnished", nullable=True)
    result["bathroom_separate"] = _boolean(payload.get("bathroom_separate"), "bathroom_separate", nullable=True)
    result["orientation"] = _enum(payload.get("orientation"), "orientation", ("N", "NE", "E", "SE", "S", "SW", "W", "NW"), nullable=True)
    result["area_sqm"] = _number(payload.get("area_sqm"), "area_sqm", minimum=0.01, maximum=10000)
    result["built_year"] = _number(payload.get("built_year"), "built_year", minimum=1800, maximum=now.year, integer=True, nullable=True)
    result["walk_min"] = _number(payload.get("walk_min"), "walk_min", minimum=0, maximum=1440, nullable=True)
    result["floor"] = _number(payload.get("floor"), "floor", minimum=-20, maximum=200, integer=True, nullable=True)
    result["rent_yen"] = _number(payload.get("rent_yen"), "rent_yen", minimum=1, maximum=1000000000, integer=True)
    result["mgmt_fee_yen"] = _number(payload.get("mgmt_fee_yen"), "mgmt_fee_yen", minimum=0, maximum=1000000000, integer=True, nullable=True)
    return result


def validate_observation(payload, *, now=None):
    now = utc_now(now)
    _object(payload, _OBSERVATION_FIELDS, "observation")
    result = validate_subject({key: value for key, value in payload.items() if key in _SUBJECT_FIELDS}, now=now)
    for field in ("unit_id", "building_id", "source_id", "listing_id", "observation_id"):
        result[field] = _text(payload.get(field), field)
    result["status"] = _enum(payload.get("status"), "status", ("active", "pending", "closed", "unknown"))
    result["data_kind"] = _enum(payload.get("data_kind"), "data_kind", ("observed", "synthetic"))
    result["received_at"] = _time(payload.get("received_at"), "received_at", now)
    for field in ("source_updated_at", "status_verified_at"):
        result[field] = _time(payload.get(field), field, now, nullable=True)
    url = _text(payload.get("public_url"), "public_url", nullable=True, max_length=2048)
    if url is not None:
        try:
            parts = urlsplit(url)
            valid = parts.scheme == "https" and parts.hostname and not parts.username and not parts.password and not any(c.isspace() for c in url) and "\\" not in url
            # Force port parsing, which rejects malformed/non-numeric ports.
            parts.port
        except ValueError:
            valid = False
        if not valid:
            raise ValidationError("public_url must be an HTTPS URL without credentials")
        if result["data_kind"] == "synthetic":
            raise ValidationError("synthetic observations must not contain public URLs")
    result["public_url"] = url
    return result


def validate_snapshot(payload, *, now=None):
    now = utc_now(now)
    _object(payload, {"schema_version", "snapshot_id", "as_of", "complete", "source", "listings"}, "snapshot")
    if payload.get("schema_version") != "2.0":
        raise ValidationError("schema_version must be 2.0")
    if payload.get("complete") is not True:
        raise ValidationError("complete must be true; partial snapshots are not authoritative")
    source = payload.get("source")
    _object(source, {"source_id", "independent_source_id", "display_name", "rights", "data_kind"}, "source")
    normalized_source = {field: _text(source.get(field), field) for field in ("source_id", "independent_source_id", "display_name")}
    normalized_source["data_kind"] = _enum(source.get("data_kind"), "data_kind", ("observed", "synthetic"))
    rights = source.get("rights")
    _object(rights, {"comparison", "display", "storage", "expires_at"}, "rights")
    for permission in ("comparison", "display", "storage"):
        if rights.get(permission) is not True:
            raise ValidationError(f"rights.{permission} must be true")
    expiry = parse_timestamp(rights.get("expires_at"))
    if expiry <= now:
        raise ValidationError("source rights have expired")
    normalized_source["rights"] = {"comparison": True, "display": True, "storage": True, "expires_at": iso_timestamp(expiry)}
    raw_listings = payload.get("listings")
    if not isinstance(raw_listings, list) or len(raw_listings) > MAX_LISTINGS:
        raise ValidationError(f"listings must be an array with at most {MAX_LISTINGS} observations")
    as_of = _time(payload.get("as_of"), "as_of", now)
    as_of_dt = parse_timestamp(as_of)
    listings = []
    observation_ids, listing_ids = set(), set()
    for index, raw in enumerate(raw_listings):
        try:
            listing = validate_observation(raw, now=now)
            if listing["source_id"] != normalized_source["source_id"] or listing["data_kind"] != normalized_source["data_kind"]:
                raise ValidationError("listing source_id and data_kind must match its source")
            for field in ("received_at", "status_verified_at", "source_updated_at"):
                if listing[field] is not None and parse_timestamp(listing[field]) > as_of_dt:
                    raise ValidationError(f"{field} cannot be after snapshot as_of")
            if listing["observation_id"] in observation_ids or listing["listing_id"] in listing_ids:
                raise ValidationError("duplicate observation_id or listing_id in source snapshot")
            observation_ids.add(listing["observation_id"])
            listing_ids.add(listing["listing_id"])
            listings.append(listing)
        except ValidationError as exc:
            raise ValidationError(f"listings[{index}]: {exc}") from exc
    return {"schema_version": "2.0", "snapshot_id": _text(payload.get("snapshot_id"), "snapshot_id"), "as_of": as_of, "complete": True, "source": normalized_source, "listings": listings}
