"""
suumo_url_parser.py — Parse SUUMO listing/search URLs into evaluate() input fields.

Supports two URL types:
  1) Listing detail page: https://suumo.jp/chintai/jnc_XXXXXXXX/?bc=...
     → fetches the detail page HTML and extracts rent, area, layout, etc.
  2) Search result URL: https://suumo.jp/jj/chintai/ichiran/FR301FC001/?...
     → reverse-maps query parameters to input fields
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


# ── ta code → prefecture slug ─────────────────────────────────────────────────
_TA_TO_PREF: dict[str, str] = {
    "13": "tokyo",
    "14": "kanagawa",
    "11": "saitama",
    "12": "chiba",
    "27": "osaka",
    "26": "kyoto",
    "28": "hyogo",
    "29": "nara",
    "23": "aichi",
}

# md code → layout type
_MD_TO_LAYOUT: dict[str, str] = {
    "01": "1R",
    "03": "1K",
    "04": "1DK",
    "06": "1LDK",
    "07": "2K",
    "08": "2DK",
    "09": "2LDK",
    "10": "3K",
    "11": "3DK",
    "12": "3LDK",
    "13": "4K",
    "14": "4DK",
    "15": "4LDK",
}

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}


# ── Detail page parser ─────────────────────────────────────────────────────────

class _DetailPageParser(HTMLParser):
    """Extracts key fields from a SUUMO listing detail page."""

    def __init__(self) -> None:
        super().__init__()
        self._texts: list[str] = []
        self._capture = False
        self._in_table = False
        self._current_label = ""
        self.data: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        cls = attr_dict.get("class", "") or ""
        if tag == "table":
            self._in_table = True
        if "property_view_table" in cls or "bukken-detail" in cls:
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture or self._in_table:
            s = data.strip()
            if s:
                self._texts.append(s)


def _parse_man_yen(text: str) -> int | None:
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        try:
            return int(round(float(m.group(1)) * 10000))
        except ValueError:
            return None
    return None


def _parse_yen(text: str) -> int | None:
    m = re.search(r"(\d[\d,]*)\s*円", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_area(text: str) -> float | None:
    m = re.search(r"([\d.]+)\s*m2", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_walk(text: str) -> int | None:
    m = re.search(r"歩(\d+)分", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_building_age(text: str) -> int | None:
    if "新築" in text:
        return 0
    m = re.search(r"築(\d+)年", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def parse_suumo_search_url(url: str) -> dict[str, Any]:
    """
    Reverse-parse a SUUMO search result URL into evaluate() input fields.

    Returns a partial dict of input fields that can be merged into the form.
    Unknown/unsupported params are silently ignored.
    """
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)

    def first(key: str) -> str | None:
        vals = params.get(key)
        return vals[0] if vals else None

    result: dict[str, Any] = {}

    # Prefecture
    ta = first("ta")
    if ta and ta in _TA_TO_PREF:
        result["prefecture"] = _TA_TO_PREF[ta]

    # Layout
    md = first("md")
    if md and md in _MD_TO_LAYOUT:
        result["layout_type"] = _MD_TO_LAYOUT[md]

    # Rent min/max (man-en → yen)
    cb = first("cb")
    if cb:
        try:
            v = float(cb)
            if 0 < v < 9999:
                result["_rent_min_man"] = v  # internal hint, not evaluate() field
        except ValueError:
            pass
    ct = first("ct")
    if ct:
        try:
            v = float(ct)
            if 0 < v < 9999:
                result["_rent_max_man"] = v
        except ValueError:
            pass

    # Area min/max
    mb = first("mb")
    if mb:
        try:
            v = float(mb)
            if v > 0:
                result["_area_min"] = v
        except ValueError:
            pass
    mt = first("mt")
    if mt:
        try:
            v = float(mt)
            if 0 < v < 9999:
                result["_area_max"] = v
        except ValueError:
            pass

    # Walk max
    et = first("et")
    if et:
        try:
            v = int(et)
            if 0 < v < 9999:
                result["station_walk_min"] = v
        except ValueError:
            pass

    # Building age max
    cn = first("cn")
    if cn:
        try:
            v = int(cn)
            if 0 < v < 9999:
                result["_building_age_max"] = v
        except ValueError:
            pass

    return result


def parse_suumo_listing_url(url: str, *, timeout: int = 12) -> dict[str, Any]:
    """
    Fetch a SUUMO listing detail page and extract fillable input fields.

    Returns a partial dict matching evaluate() input field names.
    On fetch/parse error, returns {"_error": "<message>"}.
    """
    if "suumo.jp/chintai/jnc_" not in url and "suumo.jp/chintai/bc_" not in url:
        return {"_error": "Not a recognized SUUMO listing detail URL"}

    req = urllib.request.Request(url, headers=_FETCH_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"_error": f"Fetch failed: {e}"}

    result: dict[str, Any] = {"_source_url": url}

    # Rent (家賃)
    rent_m = re.search(r"家賃[^0-9]*([\d.]+)万円", html)
    if rent_m:
        try:
            result["rent_yen"] = int(round(float(rent_m.group(1)) * 10000))
        except ValueError:
            pass

    # Management fee (管理費/共益費)
    admin_m = re.search(r"(?:管理費|共益費)[^\d]*([\d,]+)\s*円", html)
    if admin_m:
        try:
            result["management_fee_yen"] = int(admin_m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Area (専有面積)
    area_m = re.search(r"専有面積[^\d]*([\d.]+)\s*m", html, re.IGNORECASE)
    if area_m:
        try:
            result["area_sqm"] = float(area_m.group(1))
        except ValueError:
            pass

    # Layout (間取り)
    layout_m = re.search(r"間取り[^1-9]*(\d[RLKDS]+(?:LDK|DK|LK|K|R)?)", html)
    if layout_m:
        result["layout_type"] = layout_m.group(1)

    # Station walk (交通 歩X分)
    walk_m = re.search(r"歩(\d+)分", html)
    if walk_m:
        try:
            result["station_walk_min"] = int(walk_m.group(1))
        except ValueError:
            pass

    # Building age (築年数)
    if "新築" in html:
        result["building_age_years"] = 0
    else:
        age_m = re.search(r"築(\d+)年", html)
        if age_m:
            try:
                result["building_age_years"] = int(age_m.group(1))
            except ValueError:
                pass

    # Building structure (構造) — map Japanese to our codes
    structure_map = {
        "木造": "wood",
        "軽量鉄骨": "light_steel",
        "鉄骨造": "steel",
        "鉄筋コンクリート": "rc",
        "RC": "rc",
        "SRC": "src",
        "鉄骨鉄筋": "src",
    }
    for jp, code in structure_map.items():
        if jp in html:
            result["building_structure"] = code
            break

    # Reikin/Shikikin (礼金/敷金)
    reikin_m = re.search(r"礼金[^\d]*([\d.]+)万円", html)
    if reikin_m:
        try:
            result["reikin_yen"] = int(round(float(reikin_m.group(1)) * 10000))
        except ValueError:
            pass

    shikikin_m = re.search(r"敷金[^\d]*([\d.]+)万円", html)
    if shikikin_m:
        try:
            result["_shikikin_yen"] = int(round(float(shikikin_m.group(1)) * 10000))
        except ValueError:
            pass

    # Nearest station name
    station_m = re.search(r"([^\s/「」]+駅)\s*歩\d+分", html)
    if station_m:
        result["nearest_station_name"] = station_m.group(1).replace("駅", "")

    return result


def parse_suumo_url(url: str, *, timeout: int = 12) -> dict[str, Any]:
    """
    Unified entry point: auto-detect search URL vs listing detail URL.
    """
    if "/ichiran/" in url or "FR301FC" in url:
        return parse_suumo_search_url(url)
    return parse_suumo_listing_url(url, timeout=timeout)
