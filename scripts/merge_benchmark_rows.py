from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]

RAW_JSON = ROOT / "agents" / "agent_D_benchmark_data" / "out" / "benchmark_rent_raw.json"
RAW_CSV = ROOT / "agents" / "agent_D_benchmark_data" / "out" / "benchmark_rent_raw.csv"
INDEX_JSON = ROOT / "backend" / "data" / "benchmark_index.json"

FIELDS = [
    "region_country",
    "prefecture",
    "municipality",
    "layout_type",
    "building_structure",
    "avg_rent_yen",
    "source_name",
    "source_url",
    "source_updated_at",
    "collected_at",
    "method_notes",
]

FIELDS_V1 = [
    "region_country",
    "prefecture",
    "municipality",
    "layout_type",
    "avg_rent_yen",
    "source_name",
    "source_url",
    "source_updated_at",
    "collected_at",
    "method_notes",
]

ALLOWED_BUILDING_STRUCTURES = {
    "wood",
    "light_steel",
    "steel",
    "rc",
    "src",
    "other",
    "all",
}


def _load_existing_rows() -> list[dict]:
    if not RAW_JSON.exists():
        return []
    rows = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rr = dict(r)
        bs = str(rr.get("building_structure") or "").strip()
        rr["building_structure"] = bs or "all"
        out.append(rr)
    return out


def _dedupe_key(row: dict) -> tuple:
    return tuple(row.get(k) for k in FIELDS)


def _parse_stdin_rows(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []

    # Allow pasting a header row or raw data rows.
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    first_line = lines[0].strip()
    has_header = first_line.startswith("region_country,")
    if not has_header:
        try:
            sample_cols = next(csv.reader([first_line]))
        except Exception:
            sample_cols = []
        if len(sample_cols) == len(FIELDS_V1):
            text = ",".join(FIELDS_V1) + "\n" + text
        else:
            text = ",".join(FIELDS) + "\n" + text

    reader = csv.DictReader(io.StringIO(text))
    out: list[dict] = []
    for r in reader:
        if not r:
            continue
        row = {k: (r.get(k) or "").strip() for k in FIELDS}
        row["building_structure"] = row["building_structure"] or "all"
        if row["building_structure"] not in ALLOWED_BUILDING_STRUCTURES:
            continue
        if not row["region_country"] or not row["prefecture"] or not row["municipality"] or not row["layout_type"]:
            continue
        try:
            row["avg_rent_yen"] = int(float(row["avg_rent_yen"]))
        except Exception:
            continue
        if not isinstance(row["avg_rent_yen"], int) or row["avg_rent_yen"] <= 0:
            continue
        out.append(row)
    return out


def _write_raw(rows: list[dict]) -> None:
    RAW_JSON.parent.mkdir(parents=True, exist_ok=True)
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)

    RAW_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with RAW_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _rebuild_index() -> None:
    sys.path.insert(0, str(ROOT))
    from backend.src.benchmark_loader import build_benchmark_index, load_benchmark_rent_raw  # noqa: PLC0415

    rows = load_benchmark_rent_raw(RAW_JSON)
    index = build_benchmark_index(rows)
    INDEX_JSON.parent.mkdir(parents=True, exist_ok=True)
    INDEX_JSON.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge benchmark CSV rows into raw JSON/CSV and rebuild index.")
    parser.add_argument("--input", type=Path, help="UTF-8 CSV file to read (defaults to stdin)")
    args = parser.parse_args()

    if args.input:
        incoming = args.input.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.buffer.read()
        try:
            incoming = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            incoming = raw.decode("utf-8", errors="replace")
    new_rows = _parse_stdin_rows(incoming)
    if not new_rows:
        print("No rows parsed from stdin.")
        return 2

    existing = _load_existing_rows()
    existing_keys = {_dedupe_key(r) for r in existing if isinstance(r, dict)}

    added = 0
    for r in new_rows:
        k = _dedupe_key(r)
        if k in existing_keys:
            continue
        existing.append(r)
        existing_keys.add(k)
        added += 1

    # Stable-ish ordering (group by prefecture/municipality/layout/source)
    def sort_key(row: dict) -> tuple:
        return (
            str(row.get("prefecture", "")),
            str(row.get("municipality", "")),
            str(row.get("layout_type", "")),
            str(row.get("source_name", "")),
            str(row.get("source_updated_at", "")),
            str(row.get("source_url", "")),
        )

    existing = [r for r in existing if isinstance(r, dict)]
    existing.sort(key=sort_key)

    _write_raw(existing)
    _rebuild_index()

    print(f"Added {added} rows. Total rows now: {len(existing)}")
    print(f"Updated: {RAW_JSON}")
    print(f"Updated: {RAW_CSV}")
    print(f"Updated: {INDEX_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
