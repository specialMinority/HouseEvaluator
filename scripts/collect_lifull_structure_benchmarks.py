from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]


LIFULL_PO_BY_STRUCTURE: dict[str, str] = {
    "wood": "wood",
    "light_steel": "light",
    "steel": "steel",
    "rc": "rc",
    "src": "src",
}


@dataclass(frozen=True)
class Municipality:
    prefecture: str  # tokyo|osaka
    municipality: str  # e.g. 江戸川区, 大阪市北区
    lifull_area_code: str  # JIS code string, e.g. 13123


TOKYO_23_WARDS: list[Municipality] = [
    Municipality("tokyo", "千代田区", "13101"),
    Municipality("tokyo", "中央区", "13102"),
    Municipality("tokyo", "港区", "13103"),
    Municipality("tokyo", "新宿区", "13104"),
    Municipality("tokyo", "文京区", "13105"),
    Municipality("tokyo", "台東区", "13106"),
    Municipality("tokyo", "墨田区", "13107"),
    Municipality("tokyo", "江東区", "13108"),
    Municipality("tokyo", "品川区", "13109"),
    Municipality("tokyo", "目黒区", "13110"),
    Municipality("tokyo", "大田区", "13111"),
    Municipality("tokyo", "世田谷区", "13112"),
    Municipality("tokyo", "渋谷区", "13113"),
    Municipality("tokyo", "中野区", "13114"),
    Municipality("tokyo", "杉並区", "13115"),
    Municipality("tokyo", "豊島区", "13116"),
    Municipality("tokyo", "北区", "13117"),
    Municipality("tokyo", "荒川区", "13118"),
    Municipality("tokyo", "板橋区", "13119"),
    Municipality("tokyo", "練馬区", "13120"),
    Municipality("tokyo", "足立区", "13121"),
    Municipality("tokyo", "葛飾区", "13122"),
    Municipality("tokyo", "江戸川区", "13123"),
]


OSAKA_CITY_WARDS: list[Municipality] = [
    Municipality("osaka", "大阪市都島区", "27102"),
    Municipality("osaka", "大阪市福島区", "27103"),
    Municipality("osaka", "大阪市此花区", "27104"),
    Municipality("osaka", "大阪市西区", "27106"),
    Municipality("osaka", "大阪市港区", "27107"),
    Municipality("osaka", "大阪市大正区", "27108"),
    Municipality("osaka", "大阪市天王寺区", "27109"),
    Municipality("osaka", "大阪市浪速区", "27111"),
    Municipality("osaka", "大阪市西淀川区", "27113"),
    Municipality("osaka", "大阪市東淀川区", "27114"),
    Municipality("osaka", "大阪市東成区", "27115"),
    Municipality("osaka", "大阪市生野区", "27116"),
    Municipality("osaka", "大阪市旭区", "27117"),
    Municipality("osaka", "大阪市城東区", "27118"),
    Municipality("osaka", "大阪市阿倍野区", "27119"),
    Municipality("osaka", "大阪市住吉区", "27120"),
    Municipality("osaka", "大阪市東住吉区", "27121"),
    Municipality("osaka", "大阪市西成区", "27122"),
    Municipality("osaka", "大阪市淀川区", "27123"),
    Municipality("osaka", "大阪市鶴見区", "27124"),
    Municipality("osaka", "大阪市住之江区", "27125"),
    Municipality("osaka", "大阪市平野区", "27126"),
    Municipality("osaka", "大阪市北区", "27127"),
    Municipality("osaka", "大阪市中央区", "27128"),
]


RAW_FIELDS = [
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


def _today_iso() -> str:
    return date.today().isoformat()


def _slug_layout(layout_type: str) -> str:
    return layout_type.strip().lower()


def _lifull_list_url(*, prefecture: str, area_code: str, layout_type: str, building_structure: str) -> str:
    po = LIFULL_PO_BY_STRUCTURE.get(building_structure)
    if not po:
        raise ValueError(f"Unsupported structure for LIFULL: {building_structure}")
    query = urllib.parse.urlencode({"po": po, "m": _slug_layout(layout_type)})
    return f"https://www.homes.co.jp/chintai/{prefecture}/{area_code}/list/?{query}"


def _cache_path(cache_dir: Path, url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return cache_dir / f"{h}.html"


def _fetch(url: str, *, cache_dir: Path | None, sleep_s: float, timeout_s: float) -> str:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cp = _cache_path(cache_dir, url)
        if cp.exists():
            return cp.read_text(encoding="utf-8", errors="replace")

    if sleep_s > 0:
        time.sleep(sleep_s)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (user-provided URL, but fixed domains)
        raw = resp.read()
    # LIFULL pages are UTF-8; decode with fallback.
    html = raw.decode("utf-8", errors="replace")

    if cache_dir is not None:
        cp = _cache_path(cache_dir, url)
        cp.write_text(html, encoding="utf-8")
    return html


def _parse_avg_rent_yen_from_html(html: str) -> int | None:
    # Heuristic: LIFULL list pages show "平均賃料 8.6万円" near the top.
    patterns = [
        r"平均賃料[^0-9]*([0-9]+(?:\\.[0-9]+)?)\\s*万円",
        r"平均家賃[^0-9]*([0-9]+(?:\\.[0-9]+)?)\\s*万円",
        r"平均賃料[^0-9]*([0-9,]+)\\s*円",
        r"平均家賃[^0-9]*([0-9,]+)\\s*円",
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        s = m.group(1).replace(",", "").strip()
        try:
            if "万円" in pat:
                return int(round(float(s) * 10000))
            return int(float(s))
        except Exception:
            continue
    return None


def _iter_targets(*, prefectures: set[str], osaka_city_only: bool) -> Iterable[Municipality]:
    if "tokyo" in prefectures:
        yield from TOKYO_23_WARDS
    if "osaka" in prefectures:
        if osaka_city_only:
            yield from OSAKA_CITY_WARDS
        else:
            # For now, only Osaka City wards are supported. (Phase2/3 scope)
            yield from OSAKA_CITY_WARDS


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Collect structure-segmented benchmark rents from LIFULL HOME'S list pages (平均賃料)."
    )
    parser.add_argument(
        "--prefectures",
        nargs="+",
        default=["tokyo", "osaka"],
        help="Target prefectures: tokyo, osaka",
    )
    parser.add_argument(
        "--structures",
        nargs="+",
        default=["wood", "rc"],
        help="Structures to collect: wood, light_steel, steel, rc, src",
    )
    parser.add_argument(
        "--layouts",
        nargs="+",
        default=["1r", "1k", "1dk", "1ldk"],
        help="Layouts to collect: 1r, 1k, 1dk, 1ldk",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output CSV path")
    parser.add_argument("--sleep-seconds", type=float, default=0.8, help="Sleep between requests (rate-limit)")
    parser.add_argument("--timeout-seconds", type=float, default=20.0, help="HTTP timeout per request")
    parser.add_argument("--no-cache", action="store_true", help="Disable on-disk caching")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / ".cache" / "lifull",
        help="Cache directory (default: .cache/lifull)",
    )
    parser.add_argument(
        "--osaka-city-only",
        action="store_true",
        help="Only Osaka City wards (default true for now)",
    )
    args = parser.parse_args(argv[1:])

    prefectures = {p.strip().lower() for p in args.prefectures if p.strip()}
    structures = [s.strip() for s in args.structures if s.strip()]
    layouts = [l.strip().lower() for l in args.layouts if l.strip()]

    unknown_structures = [s for s in structures if s not in LIFULL_PO_BY_STRUCTURE]
    if unknown_structures:
        raise SystemExit(f"Unsupported structures: {', '.join(unknown_structures)}")

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cache_dir: Path | None = None if args.no_cache else args.cache_dir
    today = _today_iso()

    total = 0
    ok = 0
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()

        for muni in _iter_targets(prefectures=prefectures, osaka_city_only=bool(args.osaka_city_only)):
            for lt in layouts:
                layout_type = lt.upper()
                for bs in structures:
                    total += 1
                    url = _lifull_list_url(
                        prefecture=muni.prefecture,
                        area_code=muni.lifull_area_code,
                        layout_type=layout_type,
                        building_structure=bs,
                    )
                    try:
                        html = _fetch(
                            url,
                            cache_dir=cache_dir,
                            sleep_s=float(args.sleep_seconds),
                            timeout_s=float(args.timeout_seconds),
                        )
                        avg = _parse_avg_rent_yen_from_html(html)
                    except Exception as e:  # noqa: BLE001
                        print(f"[WARN] fetch/parse failed: {muni.municipality} {layout_type} {bs} -> {e}", file=sys.stderr)
                        continue

                    if not avg:
                        print(f"[WARN] avg not found: {muni.municipality} {layout_type} {bs}", file=sys.stderr)
                        continue

                    ok += 1
                    w.writerow(
                        {
                            "region_country": "JP",
                            "prefecture": muni.prefecture,
                            "municipality": muni.municipality,
                            "layout_type": layout_type,
                            "building_structure": bs,
                            "avg_rent_yen": avg,
                            "source_name": "LIFULL HOME'S",
                            "source_url": url,
                            "source_updated_at": today,
                            "collected_at": today,
                            "method_notes": f"平均賃料 (LIFULL HOME'S 検索結果ページ表示). 構造={bs}.",
                        }
                    )
                    f.flush()

    print(f"Done. Wrote {ok} rows (attempted {total}). Output: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
