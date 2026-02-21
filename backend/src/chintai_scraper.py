"""
chintai_scraper.py — CHINTAI live comparable search.

Fetches rental listings from CHINTAI (chintai.net) for a municipality + layout,
applies condition matching (area/walk/age/station/structure/orientation/bath),
and computes a median monthly total (rent + admin fee).

Constraints:
- stdlib only (urllib, html.parser, re)
- best-effort: if the provider blocks (WAF/JS challenge), return confidence=none
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from statistics import median
from typing import Any

from backend.src.suumo_scraper import ComparisonResult, SuumoListing


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
    "北西": "NW",
    "南東": "SE",
    "南西": "SW",
    "北": "N",
    "東": "E",
    "南": "S",
    "西": "W",
}

_LAYOUT_TO_M: dict[str, str] = {
    "1R": "0",  # ワンルーム
    "1K": "1",
    "1DK": "2",
    "1LDK": "3",
    "2K": "4",
    "2DK": "5",
    "2LDK": "6",
    "3K": "7",
    "3DK": "8",
    "3LDK": "9",
    "4K": "A",
    "4DK": "B",
    "4LDK": "C",
}


def _detect_waf_challenge(html: str) -> bool:
    h = (html or "").lower()
    return ("token.awswaf.com" in h) or ("challenge-container" in h) or ("awswafintegration" in h)


def _decode_best_effort(data: bytes, charset: str | None) -> str:
    # CHINTAI pages are typically UTF-8, but be defensive.
    candidates = [c for c in [charset, "utf-8", "cp932", "shift_jis"] if c]
    for enc in candidates:
        try:
            return data.decode(str(enc), errors="strict")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _parse_int(text: str) -> int | None:
    try:
        return int(text)
    except Exception:
        return None


def _parse_yen(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)\s*円", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _parse_man_yen(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*万円", text)
    if not m:
        return None
    try:
        return int(round(float(m.group(1)) * 10000))
    except Exception:
        return None


def _parse_area_sqm(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*(?:m(?:2|²)|㎡)", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _parse_walk_min(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"(?:徒歩|歩)\s*(\d+)\s*分", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_building_age_years(text: str) -> int | None:
    if not text:
        return None
    if "新築" in text:
        return 0
    m = re.search(r"築\s*(\d+)\s*年", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_orientation_code(text: str) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", str(text))
    # Accept "南向き" and "南西向き" etc.
    t = t.replace("向き", "")
    for jp, code in _ORIENTATION_JP_TO_ENUM.items():
        if jp in t:
            return code
    return None


def _parse_building_structure_code(text: str) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", "", str(text)).translate(_FULLWIDTH_ASCII_TRANSLATION).upper()
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


def _bucket_area_range(area_sqm: float, step_idx: int) -> tuple[int, int]:
    # Base area bucket is a 5㎡ grid; relaxation expands by ±5㎡ per step.
    # Step0 stays tight; stepN expands progressively.
    # searches should stick to the nearest bucket (e.g. 25.2㎡ -> 25~30㎡).
    base = max(0, int(float(area_sqm) // 5 * 5))
    expand = max(0, int(step_idx)) * 5
    lo = max(0, int(base) - int(expand))
    hi = int(base + 5 + expand)
    return lo, hi


def _bucket_walk_max(walk_min: int, step_idx: int) -> int:
    buckets = [1, 3, 5, 7, 10, 15, 20]
    base = next((b for b in buckets if b >= int(walk_min)), None)
    # If the listing walk time exceeds the largest supported bucket (typically
    # 20 minutes), omit the filter rather than excluding the subject listing.
    if base is None:
        return int(walk_min)
    base_i = buckets.index(int(base))
    i = min(len(buckets) - 1, int(base_i) + max(0, int(step_idx)))
    return int(buckets[i])


def _bucket_age_max(age_years: int, step_idx: int) -> int:
    # CHINTAI supports discrete buckets: <=1/<=3/<=5/<=7/<=10/<=15/<=20/<=25/<=30.
    # Step-based relaxation widens by moving to the next bucket each step.
    age = max(0, int(age_years))
    buckets = [1, 3, 5, 7, 10, 15, 20, 25, 30]
    base = next((x for x in buckets if x >= age), None)
    if base is None:
        return int(age)
    base_i = buckets.index(int(base))
    i = min(len(buckets) - 1, int(base_i) + max(0, int(step_idx)))
    return int(buckets[i])


def _walk_bucket_to_chintai_j(max_walk: int) -> str | None:
    # CHINTAI select 'j' maps to (<=1,<=3,<=5,<=7,<=10,<=15,<=20) minutes.
    if max_walk <= 1:
        return "1"
    if max_walk <= 3:
        return "2"
    if max_walk <= 5:
        return "3"
    if max_walk <= 7:
        return "4"
    if max_walk <= 10:
        return "5"
    if max_walk <= 15:
        return "6"
    if max_walk <= 20:
        return "7"
    return None


def _age_bucket_to_chintai_h(max_age: int) -> str | None:
    # CHINTAI select 'h' maps to (new,<=1,<=3,<=5,<=7,<=10,<=15,<=20,<=25,<=30).
    if max_age <= 0:
        return "0"
    if max_age <= 1:
        return "1"
    if max_age <= 3:
        return "2"
    if max_age <= 5:
        return "3"
    if max_age <= 7:
        return "3.5"
    if max_age <= 10:
        return "4"
    if max_age <= 15:
        return "5"
    if max_age <= 20:
        return "6"
    if max_age <= 25:
        return "7"
    if max_age <= 30:
        return "8"
    return None


def _structure_code_to_chintai_kz(structure: str) -> str | None:
    s = str(structure or "").lower().strip()
    if not s or s in ("all", "other"):
        return None
    if s in ("rc", "src"):
        return "1"  # 鉄筋系
    if s in ("steel", "light_steel"):
        return "2"  # 鉄骨系
    if s == "wood":
        return "3"  # 木造
    return "4"      # ブロック・その他


def _normalize_station(st: str) -> str:
    t = re.sub(r"\s+", "", str(st or ""))
    t = t.replace("駅", "")
    return t


def _extract_hidden_value(block_html: str, class_name: str) -> str | None:
    if not block_html:
        return None
    # value="..." ... class="X"
    m = re.search(
        rf'<input[^>]*value="([^"]+)"[^>]*class="{re.escape(class_name)}"[^>]*>',
        block_html,
        flags=re.IGNORECASE,
    )
    if m:
        return str(m.group(1)).strip()
    # class="X" ... value="..."
    m = re.search(
        rf'<input[^>]*class="{re.escape(class_name)}"[^>]*value="([^"]+)"[^>]*>',
        block_html,
        flags=re.IGNORECASE,
    )
    if m:
        return str(m.group(1)).strip()
    return None


@dataclass
class _DetailFields:
    orientation: str | None
    bath_sep: bool | None
    structure: str | None


def fetch_chintai_detail_fields(url: str, *, timeout: int = 12) -> _DetailFields:
    req = urllib.request.Request(url, headers=_FETCH_HEADERS)
    html = ""
    for attempt in range(0, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = None
                try:
                    charset = resp.headers.get_content_charset()
                except Exception:
                    charset = None
                html = _decode_best_effort(resp.read(), charset)
            break
        except urllib.error.HTTPError as e:
            if attempt == 0 and int(getattr(e, "code", 0) or 0) in (502, 503):
                time.sleep(2.5)
                continue
            raise RuntimeError(f"CHINTAI fetch failed: HTTP {getattr(e, 'code', '?')}") from e
        except Exception as e:
            err = str(e)
            if attempt == 0 and ("502" in err or "503" in err):
                time.sleep(2.5)
                continue
            raise RuntimeError(f"CHINTAI fetch failed: {e}") from e

    if _detect_waf_challenge(html):
        raise RuntimeError("CHINTAI returned a WAF/JS challenge page (bot detection). Try again later.")

    text = _strip_tags(html).translate(_FULLWIDTH_ASCII_TRANSLATION)
    bath_sep: bool | None = None
    if "バス・トイレ別" in text or "バストイレ別" in text:
        bath_sep = True
    elif ("バス・トイレ同室" in text) or ("ユニットバス" in text) or ("3点ユニット" in text):
        bath_sep = False

    orientation: str | None = None
    m = re.search(r"(?:方位|向き)\s*[:：]?\s*([北南東西]{1,2})", text)
    if m:
        orientation = _parse_orientation_code(m.group(1))
    if orientation is None:
        m2 = re.search(r"([北南東西]{1,2})向き", text)
        if m2:
            orientation = _parse_orientation_code(m2.group(1))

    structure: str | None = None
    m3 = re.search(r"(?:建物構造|構造)\s*[:：]?\s*([^\s]{2,20})", text)
    if m3:
        structure = _parse_building_structure_code(m3.group(1))

    return _DetailFields(orientation=orientation, bath_sep=bath_sep, structure=structure)


_JP_PREF_CODE_TO_NAME: dict[str, str] = {
    "tokyo": "\u6771\u4eac\u90fd",
    "kanagawa": "\u795e\u5948\u5ddd\u770c",
    "saitama": "\u57fc\u7389\u770c",
    "chiba": "\u5343\u8449\u770c",
    "osaka": "\u5927\u962a\u5e9c",
}


def _extract_jp_municipality_from_freeform(prefecture: str, text: str) -> str | None:
    """
    Best-effort extraction of a JP municipality string from a free-form address.
    Examples:
      東京都江戸川区南小岩5 -> 江戸川区
      神奈川県横浜市港北区... -> 横浜市港北区
    """
    if not text:
        return None
    t = re.sub(r"\s+", "", str(text))

    pref_jp = _JP_PREF_CODE_TO_NAME.get(str(prefecture).lower().strip())
    if pref_jp and pref_jp in t:
        t = t.split(pref_jp, 1)[1]

    city = "\u5e02"
    ward = "\u533a"
    town = "\u753a"
    village = "\u6751"

    city_i = t.find(city)
    if city_i != -1:
        ward_i = t.find(ward, city_i + 1)
        if ward_i != -1:
            return t[: ward_i + 1]
        return t[: city_i + 1]

    for suffix in (ward, town, village):
        i = t.find(suffix)
        if i != -1:
            return t[: i + 1]
    return None


def _municipality_candidates(prefecture: str, municipality: str) -> list[str]:
    raw = str(municipality or "").strip()
    if not raw:
        return []

    cands: list[str] = [raw]
    compact = re.sub(r"\s+", "", raw)
    if compact and compact != raw:
        cands.append(compact)

    extracted = _extract_jp_municipality_from_freeform(prefecture, raw)
    if extracted and extracted not in cands:
        cands.append(extracted)
        # Also include ward-only part for city+ward strings (e.g. 横浜市港北区 -> 港北区)
        city = "\u5e02"
        city_i = extracted.find(city)
        if city_i != -1 and (city_i + 1) < len(extracted):
            ward_only = extracted[city_i + 1 :]
            if ward_only and ward_only not in cands:
                cands.append(ward_only)

    # Unique-preserving
    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        c = str(c).strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _infer_jis_code_from_source_url(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(str(url))
    except Exception:
        return None

    m = re.search(r"/area/(\d{5})/", parsed.path or "")
    if m:
        return str(m.group(1))

    q = urllib.parse.parse_qs(parsed.query or "", keep_blank_values=True)
    sc = q.get("sc")
    if isinstance(sc, list) and sc:
        v = str(sc[0]).strip()
        if re.fullmatch(r"\d{5}", v):
            return v
    return None


def _find_chintai_list_url_from_benchmark_index(
    prefecture: str, municipality: str | None, layout_type: str, benchmark_index: dict[str, Any] | None
) -> str | None:
    if not benchmark_index or not municipality:
        return None
    by = benchmark_index.get("by_pref_muni_layout")
    if not isinstance(by, dict):
        return None

    layout_u = str(layout_type).upper()
    muni_cands = _municipality_candidates(prefecture, municipality)
    if not muni_cands:
        return None

    def url_from_entry(entry: dict[str, Any]) -> str | None:
        sources = entry.get("sources") or []
        if not isinstance(sources, list):
            return None
        for s in sources:
            if not isinstance(s, dict):
                continue
            url = s.get("source_url")
            if isinstance(url, str) and "chintai.net/" in url:
                return url
        # Fallback: infer municipality code (JIS) from other provider URLs (e.g. SUUMO sc=13123).
        for s in sources:
            if not isinstance(s, dict):
                continue
            url = s.get("source_url")
            if not isinstance(url, str) or not url:
                continue
            code = _infer_jis_code_from_source_url(url)
            if code:
                pref = str(prefecture).lower().strip()
                return f"https://www.chintai.net/{pref}/area/{code}/list/"
        return None

    # Exact match candidates first.
    for muni in muni_cands:
        entry = by.get(f"{prefecture}|{muni}|{layout_u}")
        if isinstance(entry, dict):
            url = url_from_entry(entry)
            if url:
                return url

    # Fuzzy match against existing index keys (handles inputs like "江戸川区南小岩5").
    best_entry: dict[str, Any] | None = None
    best_score: int | None = None
    prefix = f"{prefecture}|"
    suffix = f"|{layout_u}"
    for key, entry in by.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        parts = key.split("|")
        if len(parts) != 3:
            continue
        muni_key = parts[1]
        for cand in muni_cands:
            if not cand:
                continue
            if (cand in muni_key) or (muni_key in cand):
                # Prefer the closest-length match.
                score = abs(len(muni_key) - len(cand))
                if best_score is None or score < best_score:
                    best_score = score
                    best_entry = entry
                break

    if best_entry is not None:
        return url_from_entry(best_entry)

    return None


def build_chintai_list_url(
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    benchmark_index: dict[str, Any] | None,
    page: int | None = None,
    area_min_sqm: int | None = None,
    area_max_sqm: int | None = None,
    walk_max_min: int | None = None,
    age_max_years: int | None = None,
    building_structure: str | None = None,
    bathroom_toilet_separate: bool | None = None,
) -> str | None:
    """
    Build a CHINTAI list URL, using benchmark_index as the source of the base
    municipality path (contains the area code).

    Example base from index:
      https://www.chintai.net/tokyo/area/13123/list/page3/?m=2
    """
    src = _find_chintai_list_url_from_benchmark_index(prefecture, municipality, layout_type, benchmark_index)
    if not src:
        return None

    parsed = urllib.parse.urlparse(src)
    path = parsed.path or "/"
    parts = [p for p in path.split("/") if p]
    # Drop trailing page segment if present.
    if parts and re.fullmatch(r"page\d+", parts[-1]):
        parts = parts[:-1]
    # Ensure we keep the base ".../list/" location.
    if "list" in parts:
        list_i = parts.index("list")
        parts = parts[: list_i + 1]
    else:
        return None

    # Popular feature filters are implemented as path segments (e.g. /bath-toilet/).
    if bathroom_toilet_separate is True:
        parts.append("bath-toilet")

    if page is not None and int(page) > 1:
        parts.append(f"page{int(page)}")
    path = "/" + "/".join(parts) + "/"

    # Preserve original query but override/append with our known parameters.
    q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    m_val = _LAYOUT_TO_M.get(str(layout_type).upper())
    if m_val is not None:
        q["m"] = [m_val]

    # Results per page and "include admin fee" toggles (both are safe, best-effort).
    q.setdefault("rt", ["50"])
    q.setdefault("k", ["1"])

    if area_min_sqm is not None:
        q["sf"] = [str(int(area_min_sqm))]
    if area_max_sqm is not None:
        q["st"] = [str(int(area_max_sqm))]

    if walk_max_min is not None:
        j = _walk_bucket_to_chintai_j(int(walk_max_min))
        if j is not None:
            q["j"] = [str(j)]

    if age_max_years is not None:
        h = _age_bucket_to_chintai_h(int(age_max_years))
        if h is not None:
            q["h"] = [str(h)]

    if building_structure:
        kz = _structure_code_to_chintai_kz(str(building_structure))
        if kz is not None:
            q["kz"] = [kz]

    query = urllib.parse.urlencode(q, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc or "www.chintai.net", path, "", query, ""))


def fetch_chintai_listings(url: str, *, timeout: int = 12) -> list[SuumoListing]:
    """
    Fetch and parse CHINTAI list page → per-room listings.
    """
    req = urllib.request.Request(url, headers=_FETCH_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = None
        try:
            charset = resp.headers.get_content_charset()
        except Exception:
            charset = None
        html = _decode_best_effort(resp.read(), charset)

    if _detect_waf_challenge(html):
        raise RuntimeError("CHINTAI returned a WAF/JS challenge page (bot detection). Try again later.")

    listings: list[SuumoListing] = []
    assume_bath_sep_true = False
    try:
        if "/bath-toilet/" in urllib.parse.urlparse(url).path:
            assume_bath_sep_true = True
    except Exception:
        assume_bath_sep_true = False
    # Build sections contain structure/age + a room table.
    starts = [m.start() for m in re.finditer(r'<section[^>]*class="cassette_item build"', html)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if (i + 1) < len(starts) else len(html)
        section = html[start:end]

        structure_text = None
        m_struct = re.search(r"<th>\s*構造\s*</th>\s*<td[^>]*>\s*([^<]+?)\s*</td>", section)
        if m_struct:
            structure_text = m_struct.group(1)
        structure_code = _parse_building_structure_code(structure_text or "")

        age_years = None
        m_age = re.search(r"<th>\s*築年\s*</th>\s*<td[^>]*>\s*([^<]+?)\s*</td>", section)
        if m_age:
            age_years = _parse_building_age_years(m_age.group(1))

        # Each <tbody ... data-detailurl="..."> corresponds to a room listing.
        for tb in re.finditer(r'<tbody[^>]*data-detailurl="([^"]+)"[^>]*>(.*?)</tbody>', section, flags=re.S):
            detail_rel = str(tb.group(1))
            detail_url = detail_rel
            if detail_rel.startswith("/"):
                detail_url = "https://www.chintai.net" + detail_rel
            block = tb.group(2)

            rent_yen = None
            rent_h = _extract_hidden_value(block, "chinRyo")
            if rent_h:
                rent_yen = _parse_int(rent_h)
            if rent_yen is None:
                rent_yen = _parse_man_yen(block)
            if rent_yen is None:
                continue

            layout = _extract_hidden_value(block, "madori") or ""
            if not layout:
                m_layout = re.search(r">\s*([1234]R|[1234]K|[1234]DK|[1234]LDK)\s*<", block)
                layout = m_layout.group(1) if m_layout else ""

            area_sqm = None
            area_h = _extract_hidden_value(block, "senMenseki")
            if area_h:
                try:
                    area_sqm = float(area_h)
                except Exception:
                    area_sqm = None
            if area_sqm is None:
                area_sqm = _parse_area_sqm(block)

            station = _extract_hidden_value(block, "ekiName") or ""
            station_norm = _normalize_station(station)

            walk_min = None
            walk_h = _extract_hidden_value(block, "ekiToho")
            if walk_h:
                walk_min = _parse_int(walk_h)
            if walk_min is None:
                walk_min = _parse_walk_min(block)

            floor = None
            m_floor = re.search(r"(\d+)\s*階", _strip_tags(block))
            if m_floor:
                try:
                    floor = int(m_floor.group(1))
                except Exception:
                    floor = None

            admin_fee_yen = 0
            m_price = re.search(r'<td class="price[^>]*>(.*?)</td>', block, flags=re.S)
            if m_price:
                price_text = _strip_tags(m_price.group(1))
                fee = _parse_yen(price_text)
                if fee is not None:
                    admin_fee_yen = int(fee)

            orientation = None
            m_ori = re.search(r"([北南東西]{1,2})向き", _strip_tags(block))
            if m_ori:
                orientation = _parse_orientation_code(m_ori.group(1))

            listings.append(
                SuumoListing(
                    rent_yen=int(rent_yen),
                    admin_fee_yen=int(admin_fee_yen),
                    monthly_total_yen=int(rent_yen) + int(admin_fee_yen),
                    layout=str(layout).strip(),
                    area_sqm=area_sqm,
                    walk_min=walk_min,
                    building_age_years=age_years,
                    floor=floor,
                    station_names=[station_norm] if station_norm else [],
                    orientation=orientation,
                    building_structure=structure_code,
                    bathroom_toilet_separate=True if assume_bath_sep_true else None,
                    detail_url=detail_url,
                )
            )

    # Deduplicate by (rent+admin+area+layout) to reduce repeated blocks.
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
    if n == 0:
        return "none"
    if relaxation == 0:
        return "high" if n >= 3 else "mid"
    if relaxation == 1:
        return "mid"
    return "low"


def search_comparable_listings(  # noqa: PLR0913
    prefecture: str,
    municipality: str | None,
    layout_type: str,
    *,
    benchmark_index: dict[str, Any] | None,
    rent_yen: int | None = None,  # unused (avoid anchoring bias)
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
    max_pages: int = 6,
    request_delay_s: float = 1.0,
) -> ComparisonResult:
    """
    Search CHINTAI for comparable listings and compute a median monthly total (rent+admin).

    Filtering is primarily done post-fetch (HTML list pages), with relaxation steps:
      0: strict (layout + area + age + walk + station + orientation + structure + bath)
      1: drop orientation strictness
      2: drop structure strictness
      3: drop bath strictness
    """
    layout_type_u = str(layout_type).upper()
    if layout_type_u not in _LAYOUT_TO_M:
        return ComparisonResult(
            benchmark_rent_yen=None,
            benchmark_rent_yen_raw=None,
            benchmark_n_sources=0,
            benchmark_confidence="none",
            matched_level="none",
            search_url=None,
            adjustments_applied={"provider": "chintai", "provider_name": "CHINTAI"},
            error=f"Unsupported layout_type for CHINTAI: {layout_type!r}",
        )

    target_station = _normalize_station(nearest_station_name or "")

    def area_range_for_step(step_idx: int) -> tuple[int | None, int | None]:
        if area_sqm is None:
            return None, None
        lo, hi = _bucket_area_range(float(area_sqm), step_idx)
        return lo, hi

    def walk_max_for_step(step_idx: int) -> int | None:
        if walk_min is None:
            return None
        w = _bucket_walk_max(int(walk_min), step_idx)
        # If the bucket exceeds CHINTAI's supported max (typically 20 min), don't filter.
        return int(w) if int(w) <= 20 else None

    def age_max_for_step(step_idx: int) -> int | None:
        if building_age_years is None:
            return None
        a = _bucket_age_max(int(building_age_years), step_idx)
        return int(a) if int(a) <= 30 else None

    def station_matches(listing: SuumoListing) -> bool:
        if not target_station:
            return True
        if listing.station_names:
            return any((target_station in s) or (s in target_station) for s in listing.station_names)
        return False

    def orientation_matches(listing: SuumoListing, *, step_idx: int) -> bool:
        if not orientation or str(orientation).upper() == "UNKNOWN":
            return True
        if not listing.orientation:
            return False
        want = str(orientation).upper()
        have = str(listing.orientation).upper()
        if want == have:
            return True
        # Step 1+: accept same main direction (e.g., SW ~ S) if provider only exposes coarse labels.
        if step_idx >= 1 and want and have and want[0] == have[0]:
            return True
        return False

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

    detail_cache: dict[str, _DetailFields] = {}

    def enrich_from_detail(listing: SuumoListing) -> None:
        if not listing.detail_url:
            return
        if listing.detail_url in detail_cache:
            det = detail_cache[listing.detail_url]
        else:
            det = fetch_chintai_detail_fields(listing.detail_url, timeout=fetch_timeout)
            detail_cache[listing.detail_url] = det
        if listing.orientation is None and det.orientation is not None:
            listing.orientation = det.orientation
        if listing.bathroom_toilet_separate is None and det.bath_sep is not None:
            listing.bathroom_toilet_separate = det.bath_sep
        if listing.building_structure is None and det.structure is not None:
            listing.building_structure = det.structure

    def matches_for_step(listing: SuumoListing, step_idx: int) -> bool:
        if not listing.layout or listing.layout.upper() != layout_type_u:
            return False

        lo_area, hi_area = area_range_for_step(step_idx)
        if lo_area is not None and hi_area is not None:
            if listing.area_sqm is None:
                return False
            if not (float(lo_area) <= float(listing.area_sqm) <= float(hi_area)):
                return False

        age_max = age_max_for_step(step_idx)
        if age_max is not None:
            if listing.building_age_years is None:
                return False
            if int(listing.building_age_years) > int(age_max):
                return False

        wmax = walk_max_for_step(step_idx)
        if wmax is not None:
            if listing.walk_min is None:
                return False
            if int(listing.walk_min) > int(wmax):
                return False

        if not station_matches(listing):
            return False

        # Enrich only when needed to satisfy strict checks.
        needs_detail = False
        if (step_idx <= 0) and (orientation and str(orientation).upper() != "UNKNOWN") and (listing.orientation is None):
            needs_detail = True
        if (step_idx <= 2) and (bathroom_toilet_separate is not None) and (listing.bathroom_toilet_separate is None):
            needs_detail = True
        if (step_idx <= 1) and building_structure and str(building_structure).lower() not in ("other", "all") and (listing.building_structure is None):
            needs_detail = True
        if needs_detail:
            enrich_from_detail(listing)

        if step_idx <= 0 and not orientation_matches(listing, step_idx=step_idx):
            return False
        if step_idx <= 1 and not structure_matches(listing):
            return False
        if step_idx <= 2 and not bath_matches(listing):
            return False

        return True

    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    last_url: str | None = None

    for step_idx in range(0, max_relaxation_steps + 1):
        matched_all: list[SuumoListing] = []
        for page in range(1, int(max_pages) + 1):
            lo_area, hi_area = area_range_for_step(step_idx)
            wmax = walk_max_for_step(step_idx)
            amax = age_max_for_step(step_idx)
            url = build_chintai_list_url(
                prefecture=str(prefecture),
                municipality=municipality,
                layout_type=layout_type_u,
                benchmark_index=benchmark_index,
                page=page,
                area_min_sqm=lo_area,
                area_max_sqm=hi_area,
                walk_max_min=wmax,
                age_max_years=amax,
                building_structure=building_structure if step_idx <= 1 else None,
                bathroom_toilet_separate=bathroom_toilet_separate if step_idx <= 2 else None,
            )
            last_url = url or last_url
            if not url:
                if not municipality:
                    last_error = "CHINTAI URL build failed: missing municipality (市区町村)"
                else:
                    last_error = (
                        "CHINTAI URL build failed: missing/unknown municipality mapping in benchmark index "
                        f"({prefecture}|{municipality}|{layout_type_u})"
                    )
                attempts.append({"step": step_idx, "page": page, "url": None, "error": last_error})
                break
            try:
                if request_delay_s and (step_idx > 0 or page > 1):
                    time.sleep(float(request_delay_s))
                listings = fetch_chintai_listings(url, timeout=fetch_timeout)
            except RuntimeError as e:
                last_error = str(e)
                attempts.append({"step": step_idx, "page": page, "url": url, "error": last_error})
                continue

            matched_page: list[SuumoListing] = []
            for lst in listings:
                if matches_for_step(lst, step_idx):
                    matched_page.append(lst)

            matched_all.extend(matched_page)
            if matched_all:
                seen: set[tuple[Any, ...]] = set()
                deduped: list[SuumoListing] = []
                for lst in matched_all:
                    key = (lst.rent_yen, lst.admin_fee_yen, lst.area_sqm, lst.layout)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(lst)
                matched_all = deduped

            coverage = {
                "area": sum(1 for lst in listings if lst.area_sqm is not None),
                "walk": sum(1 for lst in listings if lst.walk_min is not None),
                "age": sum(1 for lst in listings if lst.building_age_years is not None),
                "structure": sum(1 for lst in listings if lst.building_structure is not None),
                "bath": sum(1 for lst in listings if lst.bathroom_toilet_separate is not None),
                "station": sum(1 for lst in listings if lst.station_names),
            }
            attempts.append(
                {
                    "step": step_idx,
                    "page": page,
                    "url": url,
                    "fetched_n": len(listings),
                    "matched_n": len(matched_page),
                    "matched_total_n": len(matched_all),
                    "coverage": coverage,
                }
            )

            if len(matched_all) >= int(min_listings):
                totals = [lst.monthly_total_yen for lst in matched_all]
                rents = [lst.rent_yen for lst in matched_all]
                conf = _confidence_from_count(len(matched_all), step_idx)
                level = "chintai_live" if step_idx == 0 else "chintai_relaxed"
                return ComparisonResult(
                    benchmark_rent_yen=int(median(totals)),
                    benchmark_rent_yen_raw=int(median(rents)),
                    benchmark_n_sources=len(matched_all),
                    benchmark_confidence=conf,
                    matched_level=level,
                    relaxation_applied=step_idx,
                    listings=matched_all,
                    search_url=url,
                    adjustments_applied={
                        "provider": "chintai",
                        "provider_name": "CHINTAI",
                        "filters": {
                            "prefecture": prefecture,
                            "municipality": municipality,
                            "layout_type": layout_type_u,
                            "area_min_sqm": lo_area,
                            "area_max_sqm": hi_area,
                            "walk_min_window": 0 if wmax is not None else None,
                            "walk_max_window": wmax,
                            "age_max_years": amax,
                            "nearest_station_name": nearest_station_name,
                            "orientation": orientation,
                            "building_structure": building_structure,
                            "bathroom_toilet_separate": bathroom_toilet_separate,
                        },
                        "attempts": attempts,
                    },
                )

    return ComparisonResult(
        benchmark_rent_yen=None,
        benchmark_rent_yen_raw=None,
        benchmark_n_sources=0,
        benchmark_confidence="none",
        matched_level="none",
        search_url=last_url,
        adjustments_applied={
            "provider": "chintai",
            "provider_name": "CHINTAI",
            "filters": {
                "prefecture": prefecture,
                "municipality": municipality,
                "layout_type": layout_type_u,
                "area_sqm": area_sqm,
                "walk_min": walk_min,
                "building_age_years": building_age_years,
                "nearest_station_name": nearest_station_name,
                "orientation": orientation,
                "building_structure": building_structure,
                "bathroom_toilet_separate": bathroom_toilet_separate,
            },
            "attempts": attempts,
        },
        error=last_error or "No comparable listings found on CHINTAI after applying condition matching",
    )
