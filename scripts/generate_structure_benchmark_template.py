from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_RAW_JSON = ROOT / "agents" / "agent_D_benchmark_data" / "out" / "benchmark_rent_raw.json"

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

STRUCTURE_LABEL_JA: dict[str, str] = {
    "wood": "木造",
    "light_steel": "軽量鉄骨造",
    "steel": "鉄骨造",
    "rc": "RC造",
    "src": "SRC造",
    "other": "その他/不明",
    "all": "構造不問",
}

OSAKA_CITY_PREFIX = "大阪市"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark_rent_raw.json must be a list")
    out: list[dict[str, Any]] = []
    for r in data:
        if isinstance(r, dict):
            out.append(r)
    return out


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a CSV template for collecting structure-specific benchmark rents "
        "(Phase2/3: wood/rc/light_steel/steel/src)."
    )
    parser.add_argument("--raw-json", type=Path, default=DEFAULT_RAW_JSON, help="Path to benchmark_rent_raw.json")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV path (template)")
    parser.add_argument(
        "--structures",
        nargs="+",
        required=True,
        help="Structures to generate (e.g. wood rc light_steel steel src). 'all'/'other' are ignored.",
    )
    parser.add_argument(
        "--prefectures",
        nargs="*",
        default=[],
        help="Optional prefecture filters (e.g. tokyo osaka). If omitted, uses all prefectures in raw data.",
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        default=[],
        help="Optional source filters (substring match, case-insensitive). Example: chintai lifull nifty.",
    )
    parser.add_argument(
        "--layouts",
        nargs="*",
        default=["1R", "1K", "1DK", "1LDK"],
        help="Layouts to generate (default: 1R 1K 1DK 1LDK). Only used with --full-grid.",
    )
    parser.add_argument(
        "--full-grid",
        action="store_true",
        help="Generate a full grid across municipalities/layouts/sources found in raw data "
        "(fills URL/notes when a matching 'all' row exists).",
    )
    parser.add_argument(
        "--osaka-city-only",
        action="store_true",
        help="If set, only keep Osaka rows whose municipality starts with '大阪市' (wards).",
    )
    args = parser.parse_args(argv)

    raw_path: Path = args.raw_json
    if not raw_path.exists():
        raise FileNotFoundError(str(raw_path))

    today = date.today().isoformat()

    structures_in = [_norm(s) for s in args.structures]
    structures = [s for s in structures_in if s and s not in ("all", "other")]
    if not structures:
        raise SystemExit("No valid --structures provided (try: wood rc)")

    pref_filter = {_norm(p) for p in args.prefectures if _norm(p)} if args.prefectures else None
    src_tokens = [_norm(s) for s in args.sources if _norm(s)]

    base_rows = _load_rows(raw_path)
    out_rows: list[dict[str, Any]] = []

    def keep_base_row(r: dict[str, Any]) -> bool:
        bs = _norm(str(r.get("building_structure") or "all"))
        if bs != "all":
            return False

        pref = _norm(str(r.get("prefecture") or ""))
        muni = str(r.get("municipality") or "").strip()
        src_name = str(r.get("source_name") or "").strip()

        if pref_filter is not None and pref not in pref_filter:
            return False
        if args.osaka_city_only and pref == "osaka" and muni and not muni.startswith(OSAKA_CITY_PREFIX):
            return False
        if src_tokens and not any(t in _norm(src_name) for t in src_tokens):
            return False
        return True

    kept_base = [r for r in base_rows if keep_base_row(r)]

    # Lookup existing 'all' rows to prefill URL/notes when full-grid contains missing combos.
    base_lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in kept_base:
        pref = str(r.get("prefecture") or "").strip()
        muni = str(r.get("municipality") or "").strip()
        layout = str(r.get("layout_type") or "").strip()
        src_name = str(r.get("source_name") or "").strip()
        if pref and muni and layout and src_name:
            base_lookup[(pref, muni, layout, src_name)] = r

    if args.full_grid:
        layouts = [str(x).strip() for x in (args.layouts or []) if str(x).strip()]
        if not layouts:
            raise SystemExit("No layouts provided (try: --layouts 1R 1K 1DK 1LDK)")

        # Municipality list: derived from raw data (within current filters).
        municipalities_by_pref: dict[str, list[str]] = {}
        for r in kept_base:
            pref = str(r.get("prefecture") or "").strip()
            muni = str(r.get("municipality") or "").strip()
            if pref and muni:
                municipalities_by_pref.setdefault(pref, []).append(muni)
        for p in list(municipalities_by_pref.keys()):
            municipalities_by_pref[p] = sorted(set(municipalities_by_pref[p]))

        # Source names: derived from raw data (within current filters).
        sources_by_pref: dict[str, list[str]] = {}
        for r in kept_base:
            pref = str(r.get("prefecture") or "").strip()
            src_name = str(r.get("source_name") or "").strip()
            if pref and src_name:
                sources_by_pref.setdefault(pref, []).append(src_name)
        for p in list(sources_by_pref.keys()):
            sources_by_pref[p] = sorted(set(sources_by_pref[p]))

        for pref, munis in municipalities_by_pref.items():
            src_names = sources_by_pref.get(pref, [])
            for muni in munis:
                for layout in layouts:
                    for src_name in src_names:
                        base_row = base_lookup.get((pref, muni, layout, src_name))
                        base_notes = (str((base_row or {}).get("method_notes") or "")).strip()
                        base_url = str((base_row or {}).get("source_url") or "").strip()
                        region_country = str((base_row or {}).get("region_country") or "JP").strip() or "JP"

                        for structure in structures:
                            struct_ja = STRUCTURE_LABEL_JA.get(structure, structure)
                            notes_parts = []
                            if base_notes:
                                notes_parts.append(base_notes)
                            notes_parts.append(f"構造: {struct_ja}のみ")
                            notes_parts.append(f"TODO: 구조 필터 적용 후 평균임대료/URL 갱신 ({today})")

                            out_rows.append(
                                {
                                    "region_country": region_country,
                                    "prefecture": pref,
                                    "municipality": muni,
                                    "layout_type": layout,
                                    "building_structure": structure,
                                    "avg_rent_yen": "",
                                    "source_name": src_name,
                                    "source_url": base_url,
                                    "source_updated_at": "",
                                    "collected_at": "",
                                    "method_notes": " | ".join(notes_parts),
                                }
                            )
    else:
        # Clone from existing base rows only.
        for r in kept_base:
            pref = str(r.get("prefecture") or "").strip()
            muni = str(r.get("municipality") or "").strip()
            layout = str(r.get("layout_type") or "").strip()
            src_name = str(r.get("source_name") or "").strip()
            if not pref or not muni or not layout or not src_name:
                continue

            base_notes = (str(r.get("method_notes") or "")).strip()
            base_url = str(r.get("source_url") or "").strip()
            region_country = str(r.get("region_country") or "JP").strip() or "JP"

            for structure in structures:
                struct_ja = STRUCTURE_LABEL_JA.get(structure, structure)
                notes_parts = []
                if base_notes:
                    notes_parts.append(base_notes)
                notes_parts.append(f"構造: {struct_ja}のみ")
                notes_parts.append(f"TODO: 구조 필터 적용 후 평균임대료/URL 갱신 ({today})")

                out_rows.append(
                    {
                        "region_country": region_country,
                        "prefecture": pref,
                        "municipality": muni,
                        "layout_type": layout,
                        "building_structure": structure,
                        "avg_rent_yen": "",
                        "source_name": src_name,
                        "source_url": base_url,
                        "source_updated_at": "",
                        "collected_at": "",
                        "method_notes": " | ".join(notes_parts),
                    }
                )

    def sort_key(row: dict[str, Any]) -> tuple:
        return (
            str(row.get("prefecture", "")),
            str(row.get("municipality", "")),
            str(row.get("layout_type", "")),
            str(row.get("building_structure", "")),
            str(row.get("source_name", "")),
            str(row.get("source_url", "")),
        )

    out_rows.sort(key=sort_key)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    print(f"Wrote template: {args.output} ({len(out_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
