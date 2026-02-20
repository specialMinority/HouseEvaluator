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
import unicodedata
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
    "1K":   "02",
    "1DK":  "03",
    "1LDK": "04",
    "2K":   "05",
    "2DK":  "06",
    "2LDK": "07",
    "3K":   "08",
    "3DK":  "09",
    "3LDK": "10",
    "4K":   "11",
    "4DK":  "12",
    "4LDK": "13",
}

# Legacy mapping kept as fallback: SUUMO has changed md codes in the past (or differs by endpoint).
_LAYOUT_TO_MD_LEGACY: dict[str, str] = {
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


# ── SUUMO enumerated parameter grids ─────────────────────────────────────────
# SUUMO only accepts specific discrete values for search filter params.
# Using arbitrary values (e.g. 9999999) causes "必要な情報が不足" rejection.

_SUUMO_RENT_GRID: list[float] = [
    3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5,
    8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0, 11.5, 12.0, 12.5,
    13.0, 13.5, 14.0, 14.5, 15.0, 15.5, 16.0, 16.5, 17.0, 17.5,
    18.0, 18.5, 19.0, 19.5, 20.0, 25.0, 30.0, 35.0, 50.0, 100.0,
]
_SUUMO_WALK_GRID: list[int] = [1, 3, 5, 7, 10, 15, 20]
_SUUMO_AREA_GRID: list[int] = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100]
_SUUMO_AGE_GRID: list[int] = [1, 3, 5, 7, 10, 15, 20, 25, 30]


def _snap_grid_floor(value: float, grid: list) -> float | None:
    """Snap value DOWN to nearest grid entry. Returns None if below all entries."""
    candidates = [g for g in grid if g <= value]
    return max(candidates) if candidates else None


def _snap_grid_ceil(value: float, grid: list) -> float | None:
    """Snap value UP to nearest grid entry. Returns None if above all entries."""
    candidates = [g for g in grid if g >= value]
    return min(candidates) if candidates else None


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
    station_names: list[str] = field(default_factory=list)
    orientation: str | None = None              # N/NE/E/SE/S/SW/W/NW
    building_structure: str | None = None       # wood|light_steel|steel|rc|src
    bathroom_toilet_separate: bool | None = None


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

        # SUUMO often encodes amenities as icon alt/title attributes.
        if self._in_cassette:
            for key in ("alt", "title", "aria-label"):
                val = attr_dict.get(key)
                if val:
                    s = str(val).strip()
                    if s:
                        self._text_buffer.append(s)

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
    """'25.22m2' or '25.22㎡' → 25.22"""
    m = re.search(r"([\d.]+)\s*(?:m\s*(?:2|\u00b2)|\u33a1)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_walk(text: str) -> int | None:
    """Return nearest walk minutes found in text (min). Accepts 歩/徒歩."""
    mins = re.findall(r"(?:歩|徒歩)\s*(\d+)\s*分", text)
    values: list[int] = []
    for raw in mins:
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return min(values) if values else None


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


_LAYOUT_PATTERN = re.compile(r"(?<![A-Z0-9])(\d[RLKDS]+(?:LDK|DK|LK|K|R)?)(?![A-Z])")


_FULLWIDTH_ASCII_TRANSLATION = str.maketrans(
    {
        "\uff32": "R",  # Ｒ
        "\uff23": "C",  # Ｃ
        "\uff33": "S",  # Ｓ
    }
)

_ORIENTATION_JP_TO_ENUM: dict[str, str] = {
    "北東": "NE",
    "東北": "NE",
    "南東": "SE",
    "東南": "SE",
    "南西": "SW",
    "西南": "SW",
    "北西": "NW",
    "西北": "NW",
    "北": "N",
    "東": "E",
    "南": "S",
    "西": "W",
}


def _parse_station_names(text: str) -> list[str]:
    if not text:
        return []
    names = re.findall(r"([^\s/「」()（）]+)駅", text)
    # Deduplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        n = str(n).strip()
        if not n:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _parse_orientation_from_row(row: str) -> str | None:
    if not row:
        return None
    compact = re.sub(r"\s+", "", row)
    # Prefer matching near the area token ("m2", "㎡") to avoid false positives.
    m = re.search(r"(?:m\s*(?:2|\u00b2)|\u33a1)", compact, re.IGNORECASE)
    tail = compact[m.end() :] if m else compact
    tail = tail.replace("向き", "").replace("方位", "")
    for jp in sorted(_ORIENTATION_JP_TO_ENUM.keys(), key=len, reverse=True):
        if tail.startswith(jp):
            return _ORIENTATION_JP_TO_ENUM[jp]
    # Fallback: look within the first few chars after area.
    for jp in sorted(_ORIENTATION_JP_TO_ENUM.keys(), key=len, reverse=True):
        idx = tail.find(jp)
        if 0 <= idx <= 3:
            return _ORIENTATION_JP_TO_ENUM[jp]
    return None


def _parse_building_structure_code(text: str) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", text)
    t = t.translate(_FULLWIDTH_ASCII_TRANSLATION)

    if "SRC" in t or "鉄骨鉄筋" in t:
        return "src"
    if "RC" in t or "鉄筋コンクリート" in t or "鉄筋コン" in t:
        return "rc"
    if "軽量鉄骨" in t:
        return "light_steel"
    if "鉄骨" in t:
        return "steel"
    if "木造" in t:
        return "wood"
    return None


def _parse_bathroom_toilet_separate(text: str) -> bool | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", text)
    if "バス・トイレ別" in t or "バストイレ別" in t:
        return True
    if "バス・トイレ同室" in t or "ユニットバス" in t or "3点ユニット" in t:
        return False
    return None


def _extract_listings_from_block(block: str) -> list[SuumoListing]:
    """Extract one or more SuumoListing from a raw cassette text block."""
    # Normalize fullwidth chars (１ＤＫ→1DK, ８.７万円→8.7万円, ３階→3階 etc.)
    # so all downstream regex patterns work on halfwidth ASCII.
    block = unicodedata.normalize("NFKC", block)
    # Find all rent amounts in this block — SUUMO shows one per room row
    rent_matches = list(re.finditer(r"([\d.]+)万円", block))
    if not rent_matches:
        return []

    # Per-room rows: parse structured sub-blocks separated by floor markers
    # Each row has: floor / rent / admin / shikikin / reikin / layout / area
    rows = re.split(r"(?=\d+階)", block)

    listings: list[SuumoListing] = []
    building_age = _parse_building_age(block)
    walk = _parse_walk(block)
    station_names = _parse_station_names(block)
    structure_code = _parse_building_structure_code(block)
    bath_sep = _parse_bathroom_toilet_separate(block)

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
        orient = _parse_orientation_from_row(row)

        layout_m = _LAYOUT_PATTERN.search(row)
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
            station_names=station_names,
            orientation=orient,
            building_structure=structure_code,
            bathroom_toilet_separate=bath_sep,
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
    layout_md_map: dict[str, str] | None = None,
    include_md: bool = True,
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
    md_map = layout_md_map or _LAYOUT_TO_MD
    md: str | None = None
    if include_md:
        md = md_map.get(layout_type.upper())
        if md is None:
            # try partial match e.g. "1LDK" → "06"
            for k, v in md_map.items():
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
        # Common required/default params observed in working SUUMO ichiran URLs.
        "shkr1": "03",
        "shkr2": "03",
        "shkr3": "03",
        "shkr4": "03",
        "fw2": "",
        "sngz": "",
        "smk": "",
        "srch_navi": "1",
        "page": "1",
        "pc": "50",
    }
    # Snap filter values to SUUMO's enumerated grids, or omit the key entirely
    # when the filter is absent. Omitting is safe (means "no limit"); using
    # out-of-grid values (e.g. 9999999) causes SUUMO to reject the request.
    if rent_min_man is not None:
        snapped = _snap_grid_floor(float(rent_min_man), _SUUMO_RENT_GRID)
        if snapped is not None:
            params["cb"] = str(snapped)
    if rent_max_man is not None:
        snapped = _snap_grid_ceil(float(rent_max_man), _SUUMO_RENT_GRID)
        if snapped is not None:
            params["ct"] = str(snapped)
    if walk_max is not None:
        snapped = _snap_grid_ceil(float(walk_max), _SUUMO_WALK_GRID)
        if snapped is not None:
            params["et"] = str(int(snapped))
    if area_min is not None:
        snapped = _snap_grid_floor(float(area_min), _SUUMO_AREA_GRID)
        if snapped is not None:
            params["mb"] = str(int(snapped))
    if area_max is not None:
        snapped = _snap_grid_ceil(float(area_max), _SUUMO_AREA_GRID)
        if snapped is not None:
            params["mt"] = str(int(snapped))
    if building_age_max is not None:
        snapped = _snap_grid_ceil(float(building_age_max), _SUUMO_AGE_GRID)
        if snapped is not None:
            params["cn"] = str(int(snapped))
    if include_md and md is not None:
        params["md"] = md
    if sc:
        params["sc"] = sc

    base = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
    return base + "?" + urllib.parse.urlencode(params, doseq=True)


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

    # SUUMO sometimes returns an error page when query params are missing/invalid.
    if "必要な情報が不足しているため、画面を表示することができません" in html:
        raise RuntimeError("SUUMO rejected search URL (missing/invalid parameters)")

    # SUUMO may also return an AWS WAF/JS challenge page which cannot be parsed via urllib.
    if ("token.awswaf.com" in html) or ("AwsWafIntegration" in html) or ("challenge-container" in html):
        raise RuntimeError("SUUMO blocked scraper (AWS WAF / JS challenge)")

    parser = _SuumoListingParser()
    parser.feed(unicodedata.normalize("NFKC", html))

    all_listings: list[SuumoListing] = []
    for block in parser.raw_blocks:
        all_listings.extend(_extract_listings_from_block(block))

    # Diagnostic: if no layout parsed at all, print first raw block sample
    if parser.raw_blocks and not any(lst.layout for lst in all_listings[:5]):
        print(f"[debug-block-sample] {parser.raw_blocks[0][:400]!r}", flush=True)

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
    nearest_station_name: str | None = None,
    orientation: str | None = None,
    building_structure: str | None = None,
    bathroom_toilet_separate: bool | None = None,
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
    def area_range_for_step(step_idx: int) -> tuple[int | None, int | None]:
        if area_sqm is None:
            return None, None
        base = max(0, int(math.floor(float(area_sqm) / 5.0) * 5))
        lo = max(0, base - (step_idx * 5))
        hi = base + 5 + (step_idx * 5)
        return lo, hi

    def age_max_for_step(step_idx: int) -> int | None:
        if building_age_years is None:
            return None
        age = max(0, int(building_age_years))
        # Use 5-year buckets: 0-5, 6-10, 11-15 ...
        bucket = max(5, int(math.ceil(age / 5.0) * 5))
        return bucket + (step_idx * 5)

    def walk_window_for_step(step_idx: int) -> tuple[int | None, int | None, int | None]:
        if walk_min is None:
            return None, None, None
        w = max(0, int(walk_min))
        tol = 3 + (step_idx * 2)
        window_min = max(0, w - 3)
        window_max = w + tol
        # Note: We intentionally avoid encoding walk_max into the SUUMO URL
        # because the endpoint may reject non-enumerated values.
        return window_min, window_max, None

    def station_matches(listing: SuumoListing) -> bool:
        if not nearest_station_name:
            return True
        if not listing.station_names:
            return False
        target = str(nearest_station_name).strip()
        if not target:
            return True
        return any((target in s) or (s in target) for s in listing.station_names)

    def orientation_matches(listing: SuumoListing) -> bool:
        if not orientation or str(orientation).upper() == "UNKNOWN":
            return True
        if not listing.orientation:
            return False
        return str(listing.orientation).upper() == str(orientation).upper()

    def structure_matches(listing: SuumoListing) -> bool:
        if not building_structure or str(building_structure).lower() in ("other", "all"):
            return True
        if not listing.building_structure:
            return False
        return str(listing.building_structure).lower() == str(building_structure).lower()

    def bath_matches(listing: SuumoListing) -> bool:
        if bathroom_toilet_separate is None:
            return True
        if listing.bathroom_toilet_separate is None:
            return False
        return bool(listing.bathroom_toilet_separate) == bool(bathroom_toilet_separate)

    def matches_for_step(listing: SuumoListing, step_idx: int) -> bool:
        # Layout is always strict (SUUMO md filter isn't perfect; enforce anyway).
        if not listing.layout or listing.layout.upper() != str(layout_type).upper():
            return False

        # Progressive relaxation:
        #   step 0: all filters
        #   step 1: drop walk
        #   step 2: drop area, station, bath
        #   step 3: drop age → bare (layout only)

        if step_idx <= 1:
            lo_area, hi_area = area_range_for_step(step_idx)
            if lo_area is not None and hi_area is not None:
                if listing.area_sqm is None:
                    return False
                if not (float(lo_area) <= float(listing.area_sqm) <= float(hi_area)):
                    return False

        if step_idx <= 2 and building_age_years is not None:
            age_max = age_max_for_step(step_idx)
            if age_max is not None:
                if listing.building_age_years is None:
                    return False
                if int(listing.building_age_years) > int(age_max):
                    return False

        if step_idx == 0 and walk_min is not None:
            window_min, window_max, _ = walk_window_for_step(step_idx)
            if window_min is not None and window_max is not None:
                if listing.walk_min is None:
                    return False
                if not (int(window_min) <= int(listing.walk_min) <= int(window_max)):
                    return False

        if step_idx <= 1 and not station_matches(listing):
            return False
        if step_idx == 0 and not orientation_matches(listing):
            return False
        if step_idx <= 1 and not structure_matches(listing):
            return False
        if step_idx <= 1 and not bath_matches(listing):
            return False

        return True

    last_error: str | None = None
    last_url: str | None = None
    attempts: list[dict[str, Any]] = []
    for step_idx in range(0, max_relaxation_steps + 1):
        rent_min_man, rent_max_man = None, None  # avoid rent-anchoring bias
        area_min, area_max = area_range_for_step(step_idx)
        _, _, walk_max = walk_window_for_step(step_idx)
        age_max = age_max_for_step(step_idx)

        md_strategies: list[tuple[str, dict[str, str] | None, bool]] = [
            ("v2", _LAYOUT_TO_MD, True),
            ("legacy", _LAYOUT_TO_MD_LEGACY, True),
            ("no_md", None, False),
        ]

        for md_strategy, md_map, include_md in md_strategies:
            variants: list[tuple[str, dict[str, Any]]] = [
                (
                    "filtered",
                    {
                        "area_min": area_min,
                        "area_max": area_max,
                        "building_age_max": age_max,
                    },
                ),
                (
                    "minimal",
                    {
                        "area_min": None,
                        "area_max": None,
                        "building_age_max": None,
                    },
                ),
            ]

            listings: list[SuumoListing] | None = None
            last_variant_url: str | None = None
            for variant_name, v in variants:
                url = build_suumo_search_url(
                    prefecture,
                    municipality,
                    layout_type,
                    layout_md_map=md_map,
                    include_md=include_md,
                    rent_min_man=rent_min_man,
                    rent_max_man=rent_max_man,
                    area_min=v["area_min"],
                    area_max=v["area_max"],
                    walk_max=None,
                    building_age_max=v["building_age_max"],
                )
                if url is None:
                    last_error = "Unsupported prefecture or layout type"
                    attempts.append(
                        {"step": step_idx, "md_strategy": md_strategy, "variant": variant_name, "url": None, "error": last_error}
                    )
                    continue

                last_variant_url = url
                if include_md or last_url is None:
                    last_url = url
                try:
                    time.sleep(0.5)  # polite delay
                    listings = fetch_suumo_listings(url, timeout=fetch_timeout)
                    break
                except RuntimeError as e:
                    last_error = str(e)
                    attempts.append(
                        {"step": step_idx, "md_strategy": md_strategy, "variant": variant_name, "url": url, "error": last_error}
                    )
                    # If SUUMO rejects the URL, try the minimal variant before giving up.
                    listings = None
                    continue

            if listings is None:
                # All variants failed
                continue

            matched = [lst for lst in listings if matches_for_step(lst, step_idx)]
            layout_sample = sorted({lst.layout or "(empty)" for lst in listings[:20]})
            if len(matched) == 0 and listings:
                print(
                    f"[debug] step{step_idx}/{md_strategy}: matched=0, "
                    f"layout_type={layout_type!r}, layout_sample={layout_sample}",
                    flush=True,
                )

            attempts.append(
                {
                    "step": step_idx,
                    "md_strategy": md_strategy,
                    "variant": "matched",
                    "url": last_variant_url,
                    "fetched_n": len(listings),
                    "matched_n": len(matched),
                    "layout_sample": layout_sample,
                }
            )
            if len(matched) >= min_listings:
                rents = [lst.rent_yen for lst in matched]
                totals = [lst.monthly_total_yen for lst in matched]
                confidence = _confidence_from_count(len(matched), step_idx)
                level = "suumo_live" if step_idx == 0 else "suumo_relaxed"

                return ComparisonResult(
                    benchmark_rent_yen=int(median(totals)),
                    benchmark_rent_yen_raw=int(median(rents)),
                    benchmark_n_sources=len(matched),
                    benchmark_confidence=confidence,
                    matched_level=level,
                    relaxation_applied=step_idx,
                    listings=matched,
                    search_url=url,
                    adjustments_applied={
                        "filters": {
                            "prefecture": prefecture,
                            "municipality": municipality,
                            "layout_type": str(layout_type).upper(),
                            "md_strategy": md_strategy,
                            "include_md": include_md,
                            "area_min_sqm": area_min,
                            "area_max_sqm": area_max,
                            "walk_min_window": walk_window_for_step(step_idx)[0],
                            "walk_max_window": walk_window_for_step(step_idx)[1],
                            "age_max_years": age_max,
                            "nearest_station_name": nearest_station_name,
                            "orientation": orientation,
                            "building_structure": building_structure,
                            "bathroom_toilet_separate": bathroom_toilet_separate,
                        },
                        "attempts": attempts,
                    },
                )

    # All steps exhausted
    return ComparisonResult(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        search_url=last_url,
        adjustments_applied={"attempts": attempts},
        error=last_error or "No comparable listings found after applying condition matching",
    )
