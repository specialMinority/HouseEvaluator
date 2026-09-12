"""Check operator-owned external acceptance records, not their human authenticity."""
from datetime import timedelta
import hashlib
from pathlib import Path
import re

from .models import parse_timestamp


CITIES = frozenset(("tokyo", "osaka", "fukuoka"))
TASKS = frozenset(("city_input_tokyo", "city_input_osaka", "city_input_fukuoka", "higher_price", "lower_price",
                   "abstention", "synthetic_distinction", "evidence_relaxation", "initial_cost", "duration_cost", "mobile_access"))
REAL_TASKS = frozenset(("higher_price", "lower_price", "evidence_relaxation"))
ALERT_CHECKS = frozenset(("incident_delivered", "recovery_delivered", "within_time_budget", "restart_deduplicated",
                        "unchanged_deduplicated", "delivery_failure_recovered"))


def _text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 2000


def _integer(value, minimum=0, maximum=10000):
    return type(value) is int and minimum <= value <= maximum


class _Evidence:
    """Small, local attachments must exist and still match their registered hash."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.cached = {}
        self.total_bytes = 0

    def check(self, item):
        if not isinstance(item, dict) or not _text(item.get("path")) or not isinstance(item.get("sha256"), str):
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            return False
        relative = Path(item["path"])
        if relative.is_absolute() or relative.drive:
            return False
        path = (self.directory / relative).resolve()
        if not path.is_relative_to(self.directory) or not path.is_file():
            return False
        key = (path, item["sha256"])
        if key in self.cached:
            return self.cached[key]
        if len(self.cached) >= 32 or not 0 < path.stat().st_size <= 262144:
            return False
        with path.open("rb") as handle:
            raw = handle.read(262145)
        self.total_bytes += len(raw)
        valid = 0 < len(raw) <= 262144 and self.total_bytes <= 2000000 and hashlib.sha256(raw).hexdigest() == item["sha256"]
        self.cached[key] = valid
        return valid


def _alert_records(report, evidence, frozen, checked):
    if report.get("delivery_scope") != "external" or any(report.get(key) is not True for key in
            ("notification_sent", "external_delivery_verified", "human_receipt_verified")):
        return False
    if not _text(report.get("operator_id")):
        return False
    checks = report.get("checks", {})
    if not isinstance(checks, dict) or not ALERT_CHECKS <= checks.keys() or any(value is not True for value in checks.values()):
        return False
    budget = report.get("max_delivery_seconds")
    if not _integer(budget, 1, 86400):
        return False
    rows = report.get("deliveries")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 100:
        return False
    event_ids, receipts, pairs = set(), set(), {}
    for row in rows:
        if not isinstance(row, dict) or any(not _text(row.get(key)) for key in
                ("event_id", "incident_id", "code", "provider_receipt_id", "recipient_id")):
            return False
        if row["event_id"] in event_ids or row["provider_receipt_id"] in receipts or row.get("event") not in ("opened", "recovered"):
            return False
        occurred, delivered, acknowledged = (parse_timestamp(row[key]) for key in ("occurred_at", "delivered_at", "acknowledged_at"))
        if not frozen <= occurred <= delivered <= acknowledged <= checked or acknowledged - occurred > timedelta(seconds=budget):
            return False
        if not evidence.check(row.get("delivery_evidence")) or not evidence.check(row.get("receipt_evidence")):
            return False
        event_ids.add(row["event_id"])
        receipts.add(row["provider_receipt_id"])
        pair = pairs.setdefault((row["incident_id"], row["code"], row["recipient_id"]), {})
        if row["event"] in pair:
            return False
        pair[row["event"]] = occurred
    return bool(pairs) and all(set(pair) == {"opened", "recovered"} and pair["opened"] < pair["recovered"] for pair in pairs.values())


def _usability_records(report, evidence, frozen, checked):
    if any(report.get(key) is not True for key in ("planned_tasks_completed", "human_participants_verified")):
        return False
    if type(report.get("open_blocker_count")) is not int or report["open_blocker_count"] != 0:
        return False
    if any(not _text(report.get(key)) for key in ("facilitator_id", "reviewer_id")):
        return False
    protocol = report.get("protocol", {})
    if not isinstance(protocol, dict) or any(not _integer(protocol.get(key), 1, 100) for key in
            ("minimum_participants", "minimum_mobile_participants", "minimum_novice_participants")):
        return False
    if set(protocol.get("required_cities", [])) != CITIES or set(protocol.get("required_task_ids", [])) != TASKS:
        return False
    participants = report.get("participants")
    observations = report.get("observations")
    issues = report.get("issues")
    segments = report.get("approved_segments")
    allowed = {f"{city}/{layout}" for city in CITIES for layout in ("1R", "1K", "1DK", "1LDK")}
    if not isinstance(segments, list) or not segments or not set(segments) <= allowed:
        return False
    if not isinstance(participants, list) or not 1 <= len(participants) <= 100:
        return False
    if not isinstance(observations, list) or not len(TASKS) <= len(observations) <= 1000 or not isinstance(issues, list) or len(issues) > 1000:
        return False
    people = {}
    for row in participants:
        if not isinstance(row, dict) or not _text(row.get("participant_id")) or row["participant_id"] in people:
            return False
        if row.get("target_user") is not True or type(row.get("japan_housing_novice")) is not bool:
            return False
        if not isinstance(row.get("devices"), list) or not row["devices"] or not set(row["devices"]) <= {"mobile", "desktop"}:
            return False
        people[row["participant_id"]] = row
    rows, completed, observed_people, mobile_people, novice_people = {}, set(), set(), set(), set()
    for row in observations:
        if not isinstance(row, dict) or not _text(row.get("observation_id")) or row["observation_id"] in rows:
            return False
        person, task = row.get("participant_id"), row.get("task_id")
        if person not in people or task not in TASKS or row.get("city") not in CITIES:
            return False
        if row.get("device") not in people[person]["devices"] or row.get("result") not in ("completed", "help_needed", "not_run"):
            return False
        if row.get("data_kind") not in ("observed", "synthetic", "none") or not _text(row.get("notes")) or not evidence.check(row.get("evidence")):
            return False
        started, ended = parse_timestamp(row["started_at"]), parse_timestamp(row["ended_at"])
        if not frozen <= started <= ended <= checked:
            return False
        if task.startswith("city_input_") and row["city"] != task.removeprefix("city_input_"):
            return False
        if task == "mobile_access" and row["device"] != "mobile":
            return False
        if task == "synthetic_distinction" and row["data_kind"] != "synthetic":
            return False
        if task in REAL_TASKS and (row["data_kind"] != "observed" or row.get("segment") not in segments
                or row["segment"].split("/")[0] != row["city"]):
            return False
        rows[row["observation_id"]] = row
        if row["result"] == "completed":
            completed.add(task)
            observed_people.add(person)
            if row["device"] == "mobile":
                mobile_people.add(person)
            if people[person]["japan_housing_novice"]:
                novice_people.add(person)
    if completed != TASKS or len(observed_people) < protocol["minimum_participants"] or len(mobile_people) < protocol["minimum_mobile_participants"] or len(novice_people) < protocol["minimum_novice_participants"]:
        return False
    accounted, issue_ids = set(), set()
    for issue in issues:
        if not isinstance(issue, dict) or not _text(issue.get("issue_id")) or issue["issue_id"] in issue_ids:
            return False
        original = rows.get(issue.get("original_observation_id"))
        if not original or issue.get("severity") not in ("blocker", "help_needed", "improvement") or type(issue.get("resolved")) is not bool:
            return False
        issue_ids.add(issue["issue_id"])
        if issue["severity"] in ("blocker", "help_needed") or original["result"] == "help_needed":
            if issue["resolved"] is not True or not _text(issue.get("resolution")) or not evidence.check(issue.get("resolution_evidence")):
                return False
            retests = issue.get("retest_observation_ids")
            if not isinstance(retests, list) or not retests:
                return False
            for retest in retests:
                row = rows.get(retest)
                if not row or row["result"] != "completed" or row["task_id"] != original["task_id"] or parse_timestamp(row["started_at"]) <= parse_timestamp(original["ended_at"]):
                    return False
            accounted.add(original["observation_id"])
    return all(row["observation_id"] in accounted for row in rows.values() if row["result"] == "help_needed")


def acceptance_records_valid(report, *, kind, directory):
    """Fail closed for missing/contradictory records; genuine execution remains a human review."""
    if kind not in ("alerts_drill", "usability_review"):
        return True
    try:
        if report.get("schema_version") != "operations-report-1.0":
            return False
        sources = report.get("source_ids")
        if not isinstance(sources, list) or not sources or any(not _text(source) for source in sources) or len(set(sources)) != len(sources):
            return False
        frozen, checked = parse_timestamp(report["protocol_frozen_at"]), parse_timestamp(report["checked_at"])
        evidence = _Evidence(directory)
        if frozen > checked or not evidence.check(report.get("protocol_evidence")):
            return False
        return (_alert_records if kind == "alerts_drill" else _usability_records)(report, evidence, frozen, checked)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False
