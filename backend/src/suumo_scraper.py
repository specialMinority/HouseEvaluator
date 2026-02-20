"""
suumo_scraper.py — SUUMO chintai live comparable search.

Fetches real-time rental listings from SUUMO matching the input conditions,
computes a benchmark rent from comparable listings, and returns a result
compatible with the existing BenchmarkMatch interface.

Design principles:
- Zero external dependencies (stdlib only: urllib, html.parser, re)
- Graceful degradation: parsing errors → ComparisonResult with confidence=none
- Relaxation strategy: up to 3 steps of condition loosening before giving up
- Respects SUUMO's robots.txt spirit: single-user on-demand fetch, not bulk crawl
"""

from __future__ import annotations

import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from statistics import median
from typing import Any


# ── Prefecture → SUUMO area/ta codes ─────────────────────────────────────────

_PREF_TO_AR_TA: dict[str, tuple[str, str]] = {
    "tokyo":     ("030", "13"),
    "kanagawa":  ("030", "14"),
    "saitama":   ("030", "11"),
    "chiba":     ("030", "12"),
    "osaka":     ("060", "27"),
    "kyoto":     ("060", "26"),
    "hyogo":     ("060", "28"),
    "nara":      ("060", "29"),
    "aichi":     ("050", "23"),
}

# Municipality name → SUUMO sc code (partial — major areas)
# Format: (prefecture, municipality_keyword) → sc code
_MUNI_TO_SC: dict[tuple[str, str], str] = {
    # Tokyo wards
    ("tokyo", "千代田"): "13101",
    ("tokyo", "中央"):   "13102",
    ("tokyo", "港"):     "13103",
    ("tokyo", "新宿"):   "13104",
    ("tokyo", "文京"):   "13105",
    ("tokyo", "台東"):   "13106",
    ("tokyo", "墨田"):   "13107",
    ("tokyo", "江東"):   "13108",
    ("tokyo", "品川"):   "13109",
    ("tokyo", "目黒"):   "13110",
    ("tokyo", "大田"):   "13111",
    ("tokyo", "世田谷"): "13112",
    ("tokyo", "渋谷"):   "13113",
    ("tokyo", "中野"):   "13114",
    ("tokyo", "杉並"):   "13115",
    ("tokyo", "豊島"):   "13116",
    ("tokyo", "北"):     "13117",
    ("tokyo", "荒川"):   "13118",
    ("tokyo", "板橋"):   "13119",
    ("tokyo", "練馬"):   "13120",
    ("tokyo", "足立"):   "13121",
    ("tokyo", "葛飾"):   "13122",
    ("tokyo", "江戸川"): "13123",
    # Osaka wards
    ("osaka", "北"):     "27127",
    ("osaka", "中央"):   "27128",
    ("osaka", "浪速"):   "27129",
    ("osaka", "天王寺"): "27130",
    ("osaka", "西"):     "27131",
    ("osaka", "港"):     "27132",
    ("osaka", "淀川"):   "27120",
    ("osaka", "東淀川"): "27121",
}

# Layout type → SUUMO md codes
_LAYOUT_TO_MD: dict[str, str] = {
    "1R":   "01",
    "1K":   "03",
    "1DK":  "04",
    "1LDK": "06",
    "2K":   "07",
    "2DK":  "08",
    "2LDK": "09",
    "3K":   "10",
    "3DK":  "11",
    "3LDK": "12",
    "4K":   "13",
    "4DK":  "14",
    "4LDK": "15",
}


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class SuumoListing:
    rent_yen: int
    admin_fee_yen: int
    monthly_total_yen: int
    layout: str
    area_sqm: float | None
    walk_min: int | None
    building_age_years: int | None
    floor: int | None
    building_name: str | None = None


@dataclass
class ComparisonResult:
    """Drop-in replacement for BenchmarkMatch fields (for evaluate.py)."""
    benchmark_rent_yen: int | None          # median monthly total (rent+admin)
    benchmark_rent_yen_raw: int | None      # median rent only (without admin)
    benchmark_n_sources: int                # number of comparable listings found
    benchmark_confidence: str               # live|high|mid|low|none
    matched_level: str                      # suumo_live|suumo_relaxed|none
    relaxation_applied: int = 0             # 0=exact, 1-3=steps relaxed
    listings: list[SuumoListing] = field(default_factory=list)
    search_url: str | None = None
    adjustments_applied: dict[str, Any] | None = None
    error: str | None = None


# ── HTML parser ───────────────────────────────────────────────────────────────

class _SuumoListingParser(HTMLParser):
    """
    Extracts per-listing data from SUUMO search result pages.

    SUUMO renders listings as "cassette" divs. The parsed text content
    contains rent, admin fee, layout, and area in a predictable pattern.
    """

    def __init__(self) -> None:
        super().__init__()
        self._text_buffer: list[str] = []
        self._in_cassette = False
        self._cassette_depth = 0
        self._depth = 0
        self.raw_blocks: list[str] = []  # one per cassette

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""
        if "cassetteitem" in classes and "cassetteitem_other" not in classes:
            self._in_cassette = True
            self._cassette_depth = self._depth
            self._text_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_cassette and self._depth == self._cassette_depth:
            self.raw_blocks.append(" ".join(self._text_buffer))
            self._in_cassette = False
            self._text_buffer = []
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_cassette:
            stripped = data.strip()
            if stripped:
                self._text_buffer.append(stripped)


def _parse_man_yen(text: str) -> int | None:
    """'12.8万円' → 128000"""
    m = re.search(r"([\d.]+)万円", text)
    if m:
        try:
            return int(round(float(m.group(1)) * 10000))
        except ValueError:
            return None
    return None


def _parse_yen(text: str) -> int | None:
    """'12000円' → 12000"""
    m = re.search(r"(\d+)円", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_area(text: str) -> float | None:
    """'25.22m2' or '25m2' → 25.22"""
    m = re.search(r"([\d.]+)\s*m2", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_walk(text: str) -> int | None:
    """'歩4分' → 4"""
    m = re.search(r"歩(\d+)分", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_building_age(text: str) -> int | None:
    """'築4年' → 4, '新築' → 0"""
    if "新築" in text:
        return 0
    m = re.search(r"築(\d+)年", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_floor(text: str) -> int | None:
    """'3階' → 3"""
    m = re.search(r"(\d+)階", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


_LAYOUT_PATTERN = re.compile(r"\b(\d[RLKDS]+(?:LDK|DK|LK|K|R)?)\b")


def _extract_listings_from_block(block: str) -> list[SuumoListing]:
    """Extract one or more SuumoListing from a raw cassette text block."""
    # Find all rent amounts in this block — SUUMO shows one per room row
    rent_matches = list(re.finditer(r"([\d.]+)万円", block))
    if not rent_matches:
        return []

    # Per-room rows: parse structured sub-blocks separated by floor markers
    # Each row has: floor / rent / admin / shikikin / reikin / layout / area
    rows = re.split(r"(?=\d+階)", block)

    listings = []
    building_age = _parse_building_age(block)
    walk = _parse_walk(block)

    for row in rows:
        rent = _parse_man_yen(row)
        if rent is None:
            continue
        # Admin fee: first 円 value after 万円 that's ≤ 50000
        admin_m = re.search(r"万円\s*([\d,]+)円", row)
        admin = 0
        if admin_m:
            try:
                admin = int(admin_m.group(1).replace(",", ""))
                if admin > 50000:
                    admin = 0
            except ValueError:
                admin = 0

        area = _parse_area(row)
        floor = _parse_floor(row)

        # Layout: find things like 1DK, 1LDK, 2LDK etc.
        layout_m = re.search(r"\b(\d[A-Z]+)\b", row)
        layout = layout_m.group(1) if layout_m else ""

        listings.append(SuumoListing(
            rent_yen=rent,
            admin_fee_yen=admin,
            monthly_total_yen=rent + admin,
            layout=layout,
            area_sqm=area,
            walk_min=walk,
            building_age_years=building_age,
            floor=floor,
        ))

    return listings


# ── URL builder ───────────────────────────────────────────────────────────────

def _municipality_to_sc(prefecture: str, municipality: str | None) -> str | None:
    if not municipality:
        return None
    pref = prefecture.lower()
    for (p, kw), sc in _MUNI_TO_SC.items():
        if p == pref and kw in municipality:
            return sc
    return None


def build_suumo_search_url(
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    rent_min_man: float | None = None,
    rent_max_man: float | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    walk_max: int | None = None,
    building_age_max: int | None = None,
) -> str | None:
    """Build a SUUMO chintai search URL from human-readable parameters."""
    pref_lower = prefecture.lower()
    ar_ta = _PREF_TO_AR_TA.get(pref_lower)
    if ar_ta is None:
        return None
    ar, ta = ar_ta
    md = _LAYOUT_TO_MD.get(layout_type.upper())
    if md is None:
        # try partial match e.g. "1LDK" → "06"
        for k, v in _LAYOUT_TO_MD.items():
            if k in layout_type.upper():
                md = v
                break
    if md is None:
        return None

    sc = _municipality_to_sc(pref_lower, municipality)

    params: dict[str, str] = {
        "ar": ar,
        "bs": "040",
        "ta": ta,
        "cb": str(rent_min_man) if rent_min_man is not None else "0.0",
        "ct": str(rent_max_man) if rent_max_man is not None else "9999999",
        "md": md,
        "et": str(walk_max) if walk_max is not None else "9999999",
        "mb": str(int(area_min)) if area_min is not None else "0",
        "mt": str(int(area_max)) if area_max is not None else "9999999",
        "cn": str(building_age_max) if building_age_max is not None else "9999999",
        "pc": "50",
    }
    if sc:
        params["sc"] = sc

    base = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
    return base + "?" + urllib.parse.urlencode(params)


# ── Fetcher ───────────────────────────────────────────────────────────────────

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch_suumo_listings(url: str, *, timeout: int = 12) -> list[SuumoListing]:
    """Fetch SUUMO search page and return parsed listings."""
    req = urllib.request.Request(url, headers=_FETCH_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"SUUMO fetch failed: {e}") from e

    parser = _SuumoListingParser()
    parser.feed(html)

    all_listings: list[SuumoListing] = []
    for block in parser.raw_blocks:
        all_listings.extend(_extract_listings_from_block(block))

    # Deduplicate: same rent+admin+area combination
    seen: set[tuple] = set()
    unique: list[SuumoListing] = []
    for lst in all_listings:
        key = (lst.rent_yen, lst.admin_fee_yen, lst.area_sqm)
        if key not in seen:
            seen.add(key)
            unique.append(lst)

    return unique


# ── Main search function ──────────────────────────────────────────────────────

def _confidence_from_count(n: int, relaxation: int) -> str:
    if n == 0:
        return "none"
    if relaxation == 0:
        return "high" if n >= 3 else "mid"
    if relaxation == 1:
        return "mid"
    return "low"


def search_comparable_listings(
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    rent_yen: int | None = None,
    area_sqm: float | None = None,
    walk_min: int | None = None,
    building_age_years: int | None = None,
    min_listings: int = 3,
    max_relaxation_steps: int = 3,
    fetch_timeout: int = 12,
) -> ComparisonResult:
    """
    Search SUUMO for comparable listings.

    Relaxation strategy (loosens constraints step by step):
      0: all filters (area ±30%, walk ±5min, age+5)
      1: drop walk filter
      2: drop area filter
      3: drop age filter → bare (ward + layout only)

    Returns ComparisonResult. If no listings found after all steps,
    benchmark_confidence='none' (caller should fall back to CSV index).
    """
    # Compute search filter ranges from input
    area_min: float | None = None
    area_max: float | None = None
    if area_sqm is not None:
        area_min = math.floor(area_sqm * 0.70)
        area_max = math.ceil(area_sqm * 1.30)

    walk_max_filter: int | None = None
    if walk_min is not None:
        walk_max_filter = walk_min + 5

    age_max_filter: int | None = None
    if building_age_years is not None:
        age_max_filter = building_age_years + 10

    # Relaxation schedule
    steps = [
        # (use_area, use_walk, use_age)
        (True,  True,  True),   # step 0 — exact
        (True,  False, True),   # step 1 — drop walk
        (False, False, True),   # step 2 — drop area
        (False, False, False),  # step 3 — drop age (ward+layout only)
    ]

    last_error: str | None = None
    for step_idx, (use_area, use_walk, use_age) in enumerate(steps):
        if step_idx > max_relaxation_steps:
            break

        url = build_suumo_search_url(
            prefecture,
            municipality,
            layout_type,
            area_min=area_min if use_area else None,
            area_max=area_max if use_area else None,
            walk_max=walk_max_filter if use_walk else None,
            building_age_max=age_max_filter if use_age else None,
        )
        if url is None:
            return ComparisonResult(
                benchmark_rent_yen=None,
                benchmark_rent_yen_raw=None,
                benchmark_n_sources=0,
                benchmark_confidence="none",
                matched_level="none",
                error="Unsupported prefecture or layout type",
            )

        try:
            time.sleep(0.5)  # polite delay
            listings = fetch_suumo_listings(url, timeout=fetch_timeout)
        except RuntimeError as e:
            last_error = str(e)
            continue

        if len(listings) >= min_listings:
            rents = [lst.rent_yen for lst in listings]
            totals = [lst.monthly_total_yen for lst in listings]
            confidence = _confidence_from_count(len(listings), step_idx)
            level = "suumo_live" if step_idx == 0 else "suumo_relaxed"

            return ComparisonResult(
                benchmark_rent_yen=int(median(totals)),
                benchmark_rent_yen_raw=int(median(rents)),
                benchmark_n_sources=len(listings),
                benchmark_confidence=confidence,
                matched_level=level,
                relaxation_applied=step_idx,
                listings=listings,
                search_url=url,
            )

    # All steps exhausted
    return ComparisonResult(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        error=last_error or "No comparable listings found after relaxation",
    )
