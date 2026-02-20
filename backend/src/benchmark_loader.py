from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable


@dataclass(frozen=True)
class BenchmarkRow:
    prefecture: str
    municipality: str
    layout_type: str
    building_structure: str  # wood|light_steel|steel|rc|src|other|all
    avg_rent_yen: int
    source_name: str | None = None
    source_url: str | None = None
    source_updated_at: str | None = None
    collected_at: str | None = None
    method_notes: str | None = None


def _parse_int(value: Any) -> int:
    if value is None:
        raise ValueError("missing int")
    if isinstance(value, int):
        return value
    value_str = str(value).strip()
    if value_str == "":
        raise ValueError("empty int")
    return int(float(value_str))


def load_benchmark_rent_raw(path: str | os.PathLike[str]) -> list[BenchmarkRow]:
    """
    Load benchmark_rent_raw.(csv|json) into canonical internal rows.
    Does NOT crawl; only parses provided files.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    if p.suffix.lower() == ".json":
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("benchmark_rent_raw.json must be a list")
        rows: list[BenchmarkRow] = []
        for r in data:
            rows.append(
                BenchmarkRow(
                    prefecture=str(r.get("prefecture")).strip(),
                    municipality=str(r.get("municipality")).strip(),
                    layout_type=str(r.get("layout_type")).strip(),
                    building_structure=str(r.get("building_structure") or "all").strip() or "all",
                    avg_rent_yen=_parse_int(r.get("avg_rent_yen")),
                    source_name=(str(r.get("source_name")).strip() if r.get("source_name") is not None else None),
                    source_url=(str(r.get("source_url")).strip() if r.get("source_url") is not None else None),
                    source_updated_at=(
                        str(r.get("source_updated_at")).strip() if r.get("source_updated_at") is not None else None
                    ),
                    collected_at=(str(r.get("collected_at")).strip() if r.get("collected_at") is not None else None),
                    method_notes=(str(r.get("method_notes")) if r.get("method_notes") is not None else None),
                )
            )
        return rows

    if p.suffix.lower() == ".csv":
        rows = []
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(
                    BenchmarkRow(
                        prefecture=str(r.get("prefecture", "")).strip(),
                        municipality=str(r.get("municipality", "")).strip(),
                        layout_type=str(r.get("layout_type", "")).strip(),
                        building_structure=str(r.get("building_structure") or "all").strip() or "all",
                        avg_rent_yen=_parse_int(r.get("avg_rent_yen")),
                        source_name=(str(r.get("source_name")).strip() if r.get("source_name") else None),
                        source_url=(str(r.get("source_url")).strip() if r.get("source_url") else None),
                        source_updated_at=(str(r.get("source_updated_at")).strip() if r.get("source_updated_at") else None),
                        collected_at=(str(r.get("collected_at")).strip() if r.get("collected_at") else None),
                        method_notes=(str(r.get("method_notes")) if r.get("method_notes") else None),
                    )
                )
        return rows

    raise ValueError(f"Unsupported raw benchmark format: {p.suffix}")


def _group_key_pref_muni_layout(row: BenchmarkRow) -> str:
    return f"{row.prefecture}|{row.municipality}|{row.layout_type}"


def _group_key_pref_muni_layout_structure(row: BenchmarkRow) -> str:
    return f"{row.prefecture}|{row.municipality}|{row.layout_type}|{row.building_structure}"


def _group_key_pref_layout(row: BenchmarkRow) -> str:
    return f"{row.prefecture}|{row.layout_type}"


def build_benchmark_index(rows: Iterable[BenchmarkRow]) -> dict[str, Any]:
    """
    Build an index for matching:
    - Exact (structure): (prefecture + municipality + layout_type + building_structure)
    - Exact: (prefecture + municipality + layout_type)
    - Fallback: (prefecture + layout_type)

    Representative benchmark value uses median(avg_rent_yen) across sources/rows.
    """
    by_pref_muni_layout: dict[str, dict[str, Any]] = {}
    by_pref_muni_layout_structure: dict[str, dict[str, Any]] = {}
    by_pref_layout_acc: dict[str, list[BenchmarkRow]] = {}
    by_pref_muni_layout_acc: dict[str, list[BenchmarkRow]] = {}
    by_pref_muni_layout_structure_acc: dict[str, list[BenchmarkRow]] = {}

    for row in rows:
        if not row.prefecture or not row.layout_type:
            continue
        if not row.municipality:
            continue
        if row.building_structure and row.building_structure != "all":
            by_pref_muni_layout_structure_acc.setdefault(_group_key_pref_muni_layout_structure(row), []).append(row)
        by_pref_muni_layout_acc.setdefault(_group_key_pref_muni_layout(row), []).append(row)
        by_pref_layout_acc.setdefault(_group_key_pref_layout(row), []).append(row)

    def pack_group(group_rows: list[BenchmarkRow]) -> dict[str, Any]:
        values = [r.avg_rent_yen for r in group_rows]
        srcs = []
        for r in group_rows:
            srcs.append(
                {
                    "source_name": r.source_name,
                    "source_url": r.source_url,
                    "source_updated_at": r.source_updated_at,
                    "collected_at": r.collected_at,
                    "method_notes": r.method_notes,
                }
            )
        return {
            "benchmark_rent_yen_median": int(median(values)),
            "n_rows": len(group_rows),
            "sources": srcs,
        }

    by_pref_muni_layout = {k: pack_group(v) for k, v in by_pref_muni_layout_acc.items()}
    by_pref_muni_layout_structure = {k: pack_group(v) for k, v in by_pref_muni_layout_structure_acc.items()}
    by_pref_layout = {k: pack_group(v) for k, v in by_pref_layout_acc.items()}

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "by_pref_muni_layout_structure": by_pref_muni_layout_structure,
        "by_pref_muni_layout": by_pref_muni_layout,
        "by_pref_layout": by_pref_layout,
    }


def load_or_build_benchmark_index(
    *,
    index_path: str | os.PathLike[str],
    raw_paths: list[str | os.PathLike[str]],
    write_if_missing: bool = True,
) -> dict[str, Any]:
    """
    Prefer a prebuilt benchmark index JSON; otherwise build from raw CSV/JSON.
    """
    idx_p = Path(index_path)
    if idx_p.exists():
        with idx_p.open("r", encoding="utf-8") as f:
            return json.load(f)

    last_error: Exception | None = None
    for rp in raw_paths:
        try:
            rows = load_benchmark_rent_raw(rp)
            index = build_benchmark_index(rows)
            if write_if_missing:
                idx_p.parent.mkdir(parents=True, exist_ok=True)
                with idx_p.open("w", encoding="utf-8") as f:
                    json.dump(index, f, ensure_ascii=False, indent=2)
            return index
        except Exception as e:  # noqa: BLE001 (intentionally collects parse failures)
            last_error = e
            continue

    raise RuntimeError(f"Failed to load any benchmark raw file; last_error={last_error!r}")
