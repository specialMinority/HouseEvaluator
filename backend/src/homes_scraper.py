"""
homes_scraper.py — LIFULL HOME'S (homes.co.jp) chintai live comparable search.

Rationale:
SUUMO search result pages have become unreliable to parse via stdlib-only HTTP fetch
(room-level rows may be missing from the fetched HTML due to dynamic rendering / layout changes).
This module provides an alternative live benchmark source using LIFULL HOME'S list pages,
which are generally server-rendered and therefore parseable without a headless browser.

Design goals:
- stdlib-only (urllib, html.parser, re, statistics)
- polite throttling & minimal requests (page-by-page, early stop)
- graceful degradation with clear error messages and attempt logs
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from backend.src.age_proximity import select_by_age_proximity, target_age_candidates
from backend.src.live_aggregate import aggregate_benchmark
from backend.src.live_quality import dedupe_listings, downsample_listings_evenly, filter_outlier_listings_iqr, filter_stale_listings
from backend.src.station_utils import normalize_station_name, station_similar
from backend.src.suumo_scraper import ComparisonResult, SuumoListing


_LAYOUT_TO_THEME_ID: dict[str, str] = {
    # Verified via HOMES "間取り別の賃貸特集" pages.
    "1R": "14121",
    "1K": "14124",
    "1DK": "14127",
    "1LDK": "14130",
}

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


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


def _decode_best_effort(raw: bytes, charset_hint: str | None) -> str:
    encs: list[str] = []
    if charset_hint:
        encs.append(str(charset_hint).strip())
    encs.extend(["utf-8", "shift_jis", "cp932", "euc_jp"])

    best_text = None
    best_repl = None
    for enc in encs:
        try:
            text = raw.decode(enc, errors="replace")
        except Exception:
            continue
        repl = text.count("\ufffd")
        if best_repl is None or repl < best_repl:
            best_text = text
            best_repl = repl
            if repl == 0:
                break
    return best_text or raw.decode("utf-8", errors="replace")


def _detect_waf_challenge(html: str) -> str | None:
    """Return error message if HTML is a WAF/JS challenge page, else None."""
    if (
        "token.awswaf.com" in html
        or "AwsWafIntegration" in html
        or "challenge-container" in html
    ):
        return "HOMES returned a WAF/JS challenge page (bot detection). Try again later."
    return None


class _HomesLineParser(HTMLParser):
    """
    Extract a reasonably line-oriented plain text representation from HOMES HTML.

    HOMES pages contain rich markup; extracting text into "lines" makes the subsequent
    state-machine parsing far more robust than brittle tag/class matching.
    """

    _BLOCK_TAGS: set[str] = {
        "tr",
        "li",
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "table",
        "tbody",
        "thead",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self._current: list[str] = []
        self._skip_depth = 0  # script/style/noscript

    def _newline(self) -> None:
        if not self._current:
            return
        line = " ".join(self._current).strip()
        self._current = []
        if line:
            self.lines.append(line)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr_dict = dict(attrs or [])
        for key in ("alt", "title", "aria-label"):
            val = attr_dict.get(key)
            if not val:
                continue
            s = str(val).strip()
            if s and len(s) <= 120:
                self._current.append(s)
        if tag == "br":
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        s = str(data or "").strip()
        if not s:
            return
        self._current.append(s)

    def close(self) -> None:  # noqa: D401
        super().close()
        self._newline()


_RENT_ADMIN_RE = re.compile(r"([\d.]+)\s*万円\s*/\s*([-\u2212－—]|[\d,]+)\s*円?")
_AREA_RE = re.compile(r"([\d.]+)\s*(?:m\s*(?:2|\u00b2)|\u33a1|平米)", re.IGNORECASE)
_LAYOUT_RE = re.compile(r"(ワンルーム|\d[RLKDS]+(?:LDK|DK|LK|K|R)?)")
_STATION_WALK_RE = re.compile(r"([^\s/()（）]+)駅\s*(?:徒歩|歩)\s*(\d+)\s*分")
_RENT_ONLY_RE = re.compile(r"([\d.]+)\s*万円")
_ADMIN_FEE_RE = re.compile(r"(?:管理費|共益費|管理費等)\s*([-\u2212－—]|[\d,]+)\s*円?")


def _parse_yen(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)\s*円", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_area(text: str) -> float | None:
    if not text:
        return None
    m = _AREA_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_layout(text: str) -> str | None:
    if not text:
        return None
    m = _LAYOUT_RE.search(text)
    if not m:
        return None
    t = m.group(1)
    if "ワンルーム" in t:
        return "1R"
    return t


def _parse_building_age_years(text: str) -> int | None:
    if not text:
        return None
    if "新築" in text:
        return 0
    m = re.search(r"(\d+)\s*年", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _parse_orientation(text: str) -> str | None:
    if not text:
        return None
    # Avoid false positives like "東京都" (contains "東").
    if ("主要採光面" not in text) and ("向き" not in text) and ("方位" not in text):
        return None
    t = re.sub(r"\s+", "", text)
    t = t.replace("主要採光面", "").replace("方位", "").replace("向き", "")
    for jp in sorted(_ORIENTATION_JP_TO_ENUM.keys(), key=len, reverse=True):
        if jp in t:
            return _ORIENTATION_JP_TO_ENUM[jp]
    return None


def _parse_building_structure_code(text: str) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", text)
    t = t.translate(_FULLWIDTH_ASCII_TRANSLATION)
    if "SRC" in t or "鉄骨鉄筋" in t:
        return "src"
    if "RC" in t or "鉄筋コンクリート" in t:
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


def _parse_building_type_label(text: str) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", str(text))
    if not t:
        return None
    # Provider-normalized: apartment|mansion|house
    if "マンション" in t:
        return "mansion"
    if ("アパート" in t) or ("ハイツ" in t) or ("コーポ" in t):
        return "apartment"
    if ("一戸建て" in t) or ("テラス" in t) or ("戸建" in t):
        return "house"
    return None


def _find_homes_price_url_from_benchmark_index(
    prefecture: str, municipality: str | None, layout_type: str, benchmark_index: dict[str, Any] | None
) -> str | None:
    if not benchmark_index or not municipality:
        return None
    by = benchmark_index.get("by_pref_muni_layout")
    if not isinstance(by, dict):
        return None

    layout_candidates = [str(layout_type).upper(), "1R", "1K", "1DK", "1LDK"]
    seen = set()
    for lt in layout_candidates:
        if lt in seen:
            continue
        seen.add(lt)
        entry = by.get(f"{prefecture}|{municipality}|{lt}")
        if not isinstance(entry, dict):
            continue
        sources = entry.get("sources") or []
        if not isinstance(sources, list):
            continue
        for s in sources:
            if not isinstance(s, dict):
                continue
            url = s.get("source_url")
            if isinstance(url, str) and "homes.co.jp/chintai/" in url:
                return url
    return None


def build_homes_theme_list_url(
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    benchmark_index: dict[str, Any] | None,
    page: int | None = None,
) -> str | None:
    """
    Build a HOMES theme list URL like:
      https://www.homes.co.jp/chintai/theme/14127/tokyo/edogawa-city/list/?page=2
    """
    theme_id = _LAYOUT_TO_THEME_ID.get(str(layout_type).upper())
    if theme_id is None:
        return None

    price_url = _find_homes_price_url_from_benchmark_index(prefecture, municipality, layout_type, benchmark_index)
    if not price_url:
        return None

    parsed = urllib.parse.urlparse(price_url)
    parts = [p for p in parsed.path.split("/") if p]
    # Expected: /chintai/{pref_slug}/{area_slug}/price/
    if len(parts) < 4 or parts[0] != "chintai":
        return None
    pref_slug = parts[1]
    area_slug = parts[2]
    base = f"https://www.homes.co.jp/chintai/theme/{theme_id}/{pref_slug}/{area_slug}/list/"

    if page is None or int(page) <= 1:
        return base
    return base + "?" + urllib.parse.urlencode({"page": str(int(page))})


def fetch_homes_listings(
    url: str,
    *,
    timeout: int = 12,
    retries: int = 1,
    retry_delay_s: float = 2.5,
    layout_hint: str | None = None,
) -> list[SuumoListing]:
    req = urllib.request.Request(url, headers=_FETCH_HEADERS)
    html = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = None
                try:
                    charset = resp.headers.get_content_charset()
                except Exception:
                    charset = None
                html = _decode_best_effort(raw, charset)
            break
        except Exception as e:
            if attempt < retries:
                time.sleep(float(retry_delay_s) * float(attempt + 1))
                continue
            raise RuntimeError(f"HOMES fetch failed: {e}") from e

    waf_err = _detect_waf_challenge(html)
    if waf_err:
        raise RuntimeError(waf_err)

    parser = _HomesLineParser()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in parser.lines if ln and ln.strip()]

    listings: list[SuumoListing] = []
    current_station_names: list[str] = []
    current_walk_min: int | None = None
    current_age_years: int | None = None
    current_structure: str | None = None
    current_orientation: str | None = None

    def reset_context() -> None:
        nonlocal current_station_names, current_walk_min, current_age_years, current_structure, current_orientation
        current_station_names = []
        current_walk_min = None
        current_age_years = None
        current_structure = None
        current_orientation = None

    dash_tokens = {"-", "−", "－", "—", "\u2212", "\u2014"}
    skip_rent_keywords = (
        "敷金",
        "礼金",
        "保証金",
        "更新料",
        "仲介手数料",
        "敷/礼",
        "敷礼",
        "初期費用",
    )

    for i, line in enumerate(lines):
        if line.startswith("所在地"):
            reset_context()

        # Station/walk context (can span multiple lines after "交通")
        for st, w in _STATION_WALK_RE.findall(line):
            st = str(st).strip()
            try:
                walk = int(w)
            except ValueError:
                continue
            if st and st not in current_station_names:
                current_station_names.append(st)
            if current_walk_min is None or walk < current_walk_min:
                current_walk_min = walk

        # Age context
        if ("築年数" in line and "年" in line) or line.startswith("築年数"):
            age = _parse_building_age_years(line)
            if age is not None:
                current_age_years = age

        # Structure/orientation context (best-effort; sometimes only appears in description text)
        if current_structure is None:
            code = _parse_building_structure_code(line)
            if code:
                current_structure = code
        if current_orientation is None:
            o = _parse_orientation(line)
            if o:
                current_orientation = o

        rent_yen: int | None = None
        admin_fee_yen = 0

        rm = _RENT_ADMIN_RE.search(line)
        if rm:
            try:
                rent_yen = int(round(float(rm.group(1)) * 10000))
            except ValueError:
                rent_yen = None
            admin_str = str(rm.group(2))
            if admin_str not in dash_tokens:
                try:
                    admin_fee_yen = int(admin_str.replace(",", ""))
                except ValueError:
                    admin_fee_yen = 0
        else:
            if any(k in line for k in skip_rent_keywords):
                continue
            # "8.7万円〜" (range) is not a single comparable listing row.
            if "〜" in line or "～" in line:
                continue
            m = _RENT_ONLY_RE.search(line)
            if not m:
                continue
            try:
                rent_yen = int(round(float(m.group(1)) * 10000))
            except ValueError:
                rent_yen = None
            if rent_yen is None:
                continue

            am = _ADMIN_FEE_RE.search(line)
            if not am:
                for j in range(i + 1, min(len(lines), i + 8)):
                    am = _ADMIN_FEE_RE.search(lines[j])
                    if am:
                        break
            if am:
                admin_str = str(am.group(1))
                if admin_str in dash_tokens:
                    admin_fee_yen = 0
                else:
                    try:
                        admin_fee_yen = int(admin_str.replace(",", ""))
                    except ValueError:
                        admin_fee_yen = 0

        if rent_yen is None:
            continue

        station_names: list[str] = []
        walk_min: int | None = None
        age_years: int | None = None
        for k in range(max(0, i - 12), i + 1):
            for st, w in _STATION_WALK_RE.findall(lines[k]):
                st = str(st).strip()
                try:
                    wmin = int(w)
                except ValueError:
                    continue
                if st and st not in station_names:
                    station_names.append(st)
                if walk_min is None or wmin < walk_min:
                    walk_min = wmin

            if age_years is None and (("築" in lines[k]) or ("新築" in lines[k]) or ("築年数" in lines[k])):
                a = _parse_building_age_years(lines[k])
                if a is not None:
                    age_years = a

        # Floor hint (often appears a few lines above)
        floor: int | None = None
        for j in range(i, max(-1, i - 10), -1):
            fm = re.search(r"(\d+)\s*階", lines[j])
            if fm:
                try:
                    floor = int(fm.group(1))
                except ValueError:
                    floor = None
                break

        # Collect core fields from a small window before the rent row, then fill missing parts
        # via look-ahead lines.
        layout: str | None = None
        area: float | None = None
        bath_sep: bool | None = None
        orientation: str | None = None
        structure: str | None = None
        building_type: str | None = None
        for k2 in range(max(0, i - 12), i + 1):
            if layout is None:
                layout = _parse_layout(lines[k2])
            if area is None:
                area = _parse_area(lines[k2])
            if bath_sep is None:
                bath_sep = _parse_bathroom_toilet_separate(lines[k2])
            if orientation is None:
                orientation = _parse_orientation(lines[k2])
            if structure is None:
                structure = _parse_building_structure_code(lines[k2])
            if building_type is None:
                building_type = _parse_building_type_label(lines[k2])

        orientation = orientation or current_orientation
        structure = structure or current_structure

        for j in range(i + 1, min(len(lines), i + 12)):
            for st, w in _STATION_WALK_RE.findall(lines[j]):
                st = str(st).strip()
                try:
                    wmin = int(w)
                except ValueError:
                    continue
                if st and st not in station_names:
                    station_names.append(st)
                if walk_min is None or wmin < walk_min:
                    walk_min = wmin

            if age_years is None and (("築" in lines[j]) or ("新築" in lines[j]) or ("築年数" in lines[j])):
                a = _parse_building_age_years(lines[j])
                if a is not None:
                    age_years = a

            if layout is None:
                layout = _parse_layout(lines[j])
            if area is None:
                area = _parse_area(lines[j])
            if bath_sep is None:
                bath_sep = _parse_bathroom_toilet_separate(lines[j])
            if orientation is None:
                orientation = _parse_orientation(lines[j])
            if structure is None:
                structure = _parse_building_structure_code(lines[j])
            if building_type is None:
                building_type = _parse_building_type_label(lines[j])
            if layout is not None and area is not None and bath_sep is not None and orientation is not None and structure is not None:
                break

        if layout is None and layout_hint:
            layout = str(layout_hint).upper()

        listings.append(
            SuumoListing(
                rent_yen=rent_yen,
                admin_fee_yen=admin_fee_yen,
                monthly_total_yen=rent_yen + admin_fee_yen,
                layout=layout or "",
                area_sqm=area,
                walk_min=walk_min,
                building_age_years=age_years,
                floor=floor,
                station_names=station_names,
                orientation=orientation,
                building_structure=structure,
                bathroom_toilet_separate=bath_sep,
                building_type=building_type,
            )
        )

    # Deduplicate (rent+admin+area) to reduce bias from repeated blocks.
    seen: set[tuple[Any, ...]] = set()
    unique: list[SuumoListing] = []
    for lst in listings:
        key = (lst.rent_yen, lst.admin_fee_yen, lst.area_sqm, lst.layout)
        if key in seen:
            continue
        seen.add(key)
        unique.append(lst)
    return unique


def _confidence_from_count(n: int, relaxation: int) -> str:
    if n < 2:
        return "none"
    if relaxation == 0:
        return "high" if n >= 3 else "mid"
    if relaxation == 1:
        return "mid" if n >= 3 else "low"
    return "low"


def search_comparable_listings(
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    benchmark_index: dict[str, Any] | None,
    rent_yen: int | None = None,  # unused for now (avoid anchoring bias)
    area_sqm: float | None = None,
    walk_min: int | None = None,
    building_age_years: int | None = None,
    nearest_station_name: str | None = None,
    orientation: str | None = None,
    building_type: str | None = None,
    building_structure: str | None = None,
    bathroom_toilet_separate: bool | None = None,
    min_listings: int = 2,
    target_listings: int = 12,
    max_listings: int = 30,
    time_budget_sec: float = 12.0,
    max_relaxation_steps: int = 3,
    fetch_timeout: int = 12,
    max_pages: int = 10,
    request_delay_s: float = 1.5,
) -> ComparisonResult:
    """
    Search HOMES for comparable listings and compute a median monthly total (rent+admin).

    Filtering is primarily done post-fetch (HTML list pages), with relaxation steps:
      0: strict (layout + area + age + walk + station + orientation + structure + building_type + bath)
      1: drop orientation strictness
      2: drop bath strictness (still keep structure/building_type)
      3: drop structure/building_type strictness (station + core numeric filters remain)
    """
    layout_type_u = str(layout_type).upper()
    if layout_type_u not in _LAYOUT_TO_THEME_ID:
        return ComparisonResult(
            benchmark_rent_yen=None,
            benchmark_rent_yen_raw=None,
            benchmark_n_sources=0,
            benchmark_confidence="none",
            matched_level="none",
            search_url=None,
            adjustments_applied={"provider": "homes", "provider_name": "LIFULL HOME'S"},
            error=f"Unsupported layout_type for HOMES theme pages: {layout_type!r}",
        )

    desired_building_type = None
    bt = str(building_type or "").lower().strip()
    if bt and bt not in ("unknown", "none", "all"):
        if bt in ("apartment", "mansion", "house"):
            desired_building_type = bt

    age_target_years: int | None = max(0, int(building_age_years)) if building_age_years is not None else None
    age_target_candidates: list[int] = (
        target_age_candidates(age_target_years, include_target_minus_one=True) if age_target_years is not None else []
    )

    def area_range_for_step(step_idx: int) -> tuple[int | None, int | None]:
        if area_sqm is None:
            return None, None
        base = max(0, int(float(area_sqm) // 5 * 5))
        lo = max(0, base - (step_idx * 5))
        hi = base + 5 + (step_idx * 5)
        return lo, hi

    def age_delta_ladder_for_step(step_idx: int) -> list[int] | None:
        if age_target_years is None:
            return None
        if int(step_idx) <= 1:
            return [0, 1, 2]
        if int(step_idx) == 2:
            return [0, 1, 2, 3]
        return [0, 1, 2, 3, 5]

    def age_max_for_step(step_idx: int) -> int | None:
        if age_target_years is None:
            return None
        ladder = age_delta_ladder_for_step(step_idx) or [0]
        max_delta = max(int(d) for d in ladder)
        return int(age_target_years) + int(max_delta)

    def walk_window_for_step(step_idx: int) -> tuple[int | None, int | None]:
        if walk_min is None:
            return None, None
        w = max(0, int(walk_min))
        # Treat walk time as a typical "X minutes or less" filter (bucketed),
        # not an exact +/- window around the listing.
        buckets = [3, 5, 7, 10, 15, 20, 30]
        base = next((b for b in buckets if b >= w), w)
        hi = base + (step_idx * 5)
        return 0, hi

    def station_matches(listing: SuumoListing) -> bool:
        if not nearest_station_name:
            return True
        if not listing.station_names:
            return False
        target = normalize_station_name(str(nearest_station_name))
        if not target:
            return True
        return any(station_similar(target, s) for s in listing.station_names)

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

    def building_type_matches(listing: SuumoListing) -> bool:
        if not desired_building_type:
            return True
        if not listing.building_type:
            # Soft filter: don't reject when the provider doesn't expose it in list pages.
            return True
        return str(listing.building_type).lower().strip() == str(desired_building_type).lower().strip()

    def matches_for_step(listing: SuumoListing, step_idx: int) -> bool:
        # Layout strict
        if not listing.layout or listing.layout.upper() != layout_type_u:
            return False

        lo_area, hi_area = area_range_for_step(step_idx)
        if lo_area is not None and hi_area is not None:
            if listing.area_sqm is None:
                return False
            if not (float(lo_area) <= float(listing.area_sqm) <= float(hi_area)):
                return False

        if age_target_years is not None:
            age_max = age_max_for_step(step_idx)
            if age_max is not None:
                if listing.building_age_years is None:
                    return False
                if int(listing.building_age_years) > int(age_max):
                    return False

        if walk_min is not None:
            wmin, wmax = walk_window_for_step(step_idx)
            if wmin is not None and wmax is not None:
                if listing.walk_min is None:
                    return False
                if not (int(wmin) <= int(listing.walk_min) <= int(wmax)):
                    return False

        if not station_matches(listing):
            return False
        if step_idx <= 0 and not orientation_matches(listing):
            return False
        if step_idx <= 2 and not building_type_matches(listing):
            return False
        if step_idx <= 2 and not structure_matches(listing):
            return False
        if step_idx <= 1 and not bath_matches(listing):
            return False
        return True

    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    last_url: str | None = None
    started_at = time.monotonic()
    target_n = max(int(min_listings), int(target_listings))
    max_keep_n = max(int(target_n), int(max_listings))
    time_budget_s = max(0.0, float(time_budget_sec))
    stop_all_steps = False

    for step_idx in range(0, max_relaxation_steps + 1):
        matched_all: list[SuumoListing] = []
        dedupe_removed_total = 0
        for page in range(1, int(max_pages) + 1):
            url = build_homes_theme_list_url(
                prefecture=str(prefecture),
                municipality=municipality,
                layout_type=layout_type_u,
                benchmark_index=benchmark_index,
                page=page,
            )
            last_url = url or last_url
            if not url:
                last_error = "HOMES URL build failed (missing area slug mapping from benchmark index)"
                attempts.append({"step": step_idx, "page": page, "url": None, "error": last_error})
                break
            try:
                if request_delay_s and (step_idx > 0 or page > 1):
                    time.sleep(float(request_delay_s))
                listings = fetch_homes_listings(url, timeout=fetch_timeout, layout_hint=layout_type_u)
            except RuntimeError as e:
                last_error = str(e)
                attempts.append({"step": step_idx, "page": page, "url": url, "error": last_error})
                continue

            matched_page = [lst for lst in listings if matches_for_step(lst, step_idx)]
            matched_all.extend(matched_page)
            # Cross-page dedupe (same rent/admin/area/layout) to reduce repeated blocks.
            if matched_all:
                matched_all, dstats = dedupe_listings(matched_all, provider="homes")
                dedupe_removed_total += int(dstats.get("removed") or 0)

            age_ladder = age_delta_ladder_for_step(step_idx) or None
            age_meta: dict[str, Any] | None = None
            age_selected: list[SuumoListing] = list(matched_all)
            if age_target_years is not None and age_ladder:
                age_selected, age_meta = select_by_age_proximity(
                    matched_all,
                    target_age_years=age_target_years,
                    min_keep=int(min_listings),
                    delta_ladder=age_ladder,
                    include_target_minus_one=True,
                )

            layout_sample = sorted({(lst.layout or "(empty)") for lst in listings[:20]})
            coverage = {
                "area": sum(1 for lst in listings if lst.area_sqm is not None),
                "walk": sum(1 for lst in listings if lst.walk_min is not None),
                "age": sum(1 for lst in listings if lst.building_age_years is not None),
                "structure": sum(1 for lst in listings if lst.building_structure is not None),
                "bath": sum(1 for lst in listings if lst.bathroom_toilet_separate is not None),
                "station": sum(1 for lst in listings if lst.station_names),
                "building_type": sum(1 for lst in listings if lst.building_type is not None),
            }
            attempts.append(
                {
                    "step": step_idx,
                    "page": page,
                    "url": url,
                    "fetched_n": len(listings),
                    "matched_n": len(matched_page),
                    "matched_total_n": len(matched_all),
                    "age_selected_n": int(len(age_selected)),
                    "age_proximity": age_meta,
                    "layout_sample": layout_sample,
                    "coverage": coverage,
                }
            )

            elapsed_s = float(time.monotonic() - started_at)
            time_exceeded = bool(time_budget_s > 0.0 and elapsed_s >= float(time_budget_s))

            if time_exceeded and len(age_selected) < int(min_listings):
                last_error = f"time_budget_exceeded (elapsed_s={elapsed_s:.1f}, min_listings={int(min_listings)})"
                stop_all_steps = True
                break

            if len(age_selected) >= int(min_listings):
                if (len(age_selected) < int(target_n)) and (page < int(max_pages)) and (not time_exceeded):
                    continue
                quality: dict[str, Any] = {
                    "matched_raw_n": int(len(matched_all) + int(dedupe_removed_total)),
                    "deduped_n": int(len(matched_all)),
                    "dedupe_removed_n": int(dedupe_removed_total),
                    "age_selected_raw_n": int(len(age_selected)),
                    "age_proximity": age_meta,
                    "sampling": {
                        "min_listings": int(min_listings),
                        "target_listings": int(target_n),
                        "max_listings": int(max_keep_n),
                        "time_budget_sec": float(time_budget_s),
                        "elapsed_sec": float(round(elapsed_s, 3)),
                        "stop_reason": "target_reached" if len(age_selected) >= int(target_n) else ("time_budget" if time_exceeded else "max_pages"),
                    },
                }
                usable = list(age_selected)
                usable_after_stale, stale_stats = filter_stale_listings(usable, min_keep=int(min_listings))
                usable_after_out, out_stats = filter_outlier_listings_iqr(
                    usable_after_stale,
                    value_attr="monthly_total_yen",
                    min_keep=int(min_listings),
                )
                usable_final = usable_after_out if len(usable_after_out) >= int(min_listings) else usable
                usable_down, down_stats = downsample_listings_evenly(
                    usable_final,
                    value_attr="monthly_total_yen",
                    max_keep=int(max_keep_n),
                    min_keep=int(min_listings),
                )
                usable_final2 = usable_down if len(usable_down) >= int(min_listings) else usable_final
                quality.update(
                    {
                        "stale": stale_stats,
                        "outlier": out_stats,
                        "after_stale_n": int(len(usable_after_stale)),
                        "after_outlier_n": int(len(usable_after_out)),
                        "downsample": down_stats,
                        "used_n": int(len(usable_final2)),
                        "used_fallback_unfiltered": bool(usable_final is usable),
                    }
                )

                totals = [lst.monthly_total_yen for lst in usable_final2]
                rents = [lst.rent_yen for lst in usable_final2]
                bench_total, method_total, stats_total = aggregate_benchmark(totals)
                bench_rent, method_rent, stats_rent = aggregate_benchmark(rents)
                conf = _confidence_from_count(len(usable_final2), step_idx)
                level = "homes_live" if step_idx == 0 else "homes_relaxed"
                return ComparisonResult(
                    benchmark_rent_yen=int(bench_total),
                    benchmark_rent_yen_raw=int(bench_rent),
                    benchmark_n_sources=len(usable_final2),
                    benchmark_confidence=conf,
                    matched_level=level,
                    relaxation_applied=step_idx,
                    listings=usable_final2,
                    search_url=url,
                    adjustments_applied={
                        "provider": "homes",
                        "provider_name": "LIFULL HOME'S",
                        "filters": {
                            "prefecture": prefecture,
                            "municipality": municipality,
                            "layout_type": layout_type_u,
                            "area_min_sqm": area_range_for_step(step_idx)[0],
                            "area_max_sqm": area_range_for_step(step_idx)[1],
                            "walk_min_window": walk_window_for_step(step_idx)[0],
                            "walk_max_window": walk_window_for_step(step_idx)[1],
                            "age_max_years": age_max_for_step(step_idx),
                            "age_mode": "proximity" if age_target_years is not None else None,
                            "age_target_years": age_target_years,
                            "age_target_candidates": age_target_candidates or None,
                            "age_delta_ladder": age_delta_ladder_for_step(step_idx) if age_target_years is not None else None,
                            "age_delta_used": (age_meta or {}).get("chosen_delta"),
                            "age_query_max_years": age_max_for_step(step_idx),
                            "nearest_station_name": nearest_station_name,
                            "orientation": orientation,
                            "building_type": desired_building_type,
                            "building_structure": building_structure,
                            "bathroom_toilet_separate": bathroom_toilet_separate,
                        },
                        "quality": quality,
                        "aggregation": {
                            "total": {"method": method_total, **stats_total},
                            "rent": {"method": method_rent, **stats_rent},
                        },
                        "attempts": attempts,
                    },
                )

        if stop_all_steps:
            break

    return ComparisonResult(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        search_url=last_url,
        adjustments_applied={
            "provider": "homes",
            "provider_name": "LIFULL HOME'S",
            "filters": {
                "prefecture": prefecture,
                "municipality": municipality,
                "layout_type": layout_type_u,
                "area_min_sqm": area_range_for_step(int(max_relaxation_steps))[0],
                "area_max_sqm": area_range_for_step(int(max_relaxation_steps))[1],
                "walk_min_window": walk_window_for_step(int(max_relaxation_steps))[0],
                "walk_max_window": walk_window_for_step(int(max_relaxation_steps))[1],
                "age_max_years": age_max_for_step(int(max_relaxation_steps)),
                "age_mode": "proximity" if age_target_years is not None else None,
                "age_target_years": age_target_years,
                "age_target_candidates": age_target_candidates or None,
                "age_delta_ladder": age_delta_ladder_for_step(int(max_relaxation_steps)) if age_target_years is not None else None,
                "age_delta_used": None,
                "age_query_max_years": age_max_for_step(int(max_relaxation_steps)),
                "nearest_station_name": nearest_station_name,
                "orientation": orientation,
                "building_type": desired_building_type,
                "building_structure": building_structure,
                "bathroom_toilet_separate": bathroom_toilet_separate,
            },
            "attempts": attempts,
        },
        error=last_error or "No comparable listings found on HOMES after applying condition matching",
    )
