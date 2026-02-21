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
from datetime import datetime
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


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
    "02": "1K",
    "03": "1DK",
    "04": "1LDK",
    "05": "2K",
    "06": "2DK",
    "07": "2LDK",
    "08": "3K",
    "09": "3DK",
    "10": "3LDK",
    "11": "4K",
    "12": "4DK",
    "13": "4LDK",
}

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://suumo.jp/",
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
    # SUUMO pages may render the squared symbol as a separate <sup> tag:
    #   "25.21m<sup>2</sup>" -> "25.21m 2" after tag-stripping.
    # Accept: m2, m 2, m², ㎡
    m = re.search("([\\d.]+)\\s*(?:m\\s*(?:2|\u00b2)|\u33a1)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_walk(text: str) -> int | None:
    # "徒歩7分" or "新宿駅 歩7分"
    m = re.search(r"(?:徒歩|歩)\s*(\d+)\s*分", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _strip_tags(text: str) -> str:
    # Good-enough for small extractions; we don't need a full HTML sanitizer here.
    return re.sub(r"<[^>]+>", " ", text or "")


def _extract_kv_pairs_from_html(html_raw: str) -> list[tuple[str, str]]:
    """
    Extract simple (label, value) pairs from common SUUMO detail layouts.

    SUUMO frequently renders "物件概要" as either:
      - <tr><th>LABEL</th><td>VALUE</td></tr> tables, or
      - <dt>LABEL</dt><dd>VALUE</dd> definition lists.

    This helper keeps extraction dependency-free and robust to nested tags
    inside <th>/<td>/<dt>/<dd> (e.g., spans that split text).
    """
    if not html_raw:
        return []

    flags = re.IGNORECASE | re.DOTALL
    pairs: list[tuple[str, str]] = []

    # Table rows.
    for row_m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", html_raw, flags):
        row = row_m.group(1)
        th_m = re.search(r"<th\b[^>]*>(.*?)</th>", row, flags)
        td_m = re.search(r"<td\b[^>]*>(.*?)</td>", row, flags)
        label = ""
        value = ""
        if th_m and td_m:
            label = re.sub(r"\s+", "", _strip_tags(th_m.group(1)))
            value = _strip_tags(td_m.group(1)).strip()
        else:
            # Some SUUMO tables use <td>LABEL</td><td>VALUE</td> (no <th>).
            tds = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags)
            if len(tds) == 2:
                label = re.sub(r"\s+", "", _strip_tags(tds[0]))
                value = _strip_tags(tds[1]).strip()
        if label and value:
            pairs.append((label, value))

    # Definition lists.
    for m in re.finditer(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", html_raw, flags):
        label = re.sub(r"\s+", "", _strip_tags(m.group(1)))
        value = _strip_tags(m.group(2)).strip()
        if label and value:
            pairs.append((label, value))

    return pairs


def _find_value_in_kv_pairs(pairs: list[tuple[str, str]], keywords: tuple[str, ...]) -> str | None:
    if not pairs or not keywords:
        return None
    for label, value in pairs:
        for kw in keywords:
            if kw and kw in label:
                return value
    return None


def _find_value_after_label(texts: list[str], labels: set[str], *, lookahead: int = 10) -> str | None:
    if not texts or not labels:
        return None

    labels_sorted = sorted({str(x) for x in labels if x}, key=len, reverse=True)

    def is_label_token(token: str) -> bool:
        if not token:
            return False
        if token in labels:
            return True
        # Common SUUMO variants: "建物構造：" "構造・階建" etc.
        stripped = re.sub(r"[\s\u3000:：/／・]+$", "", token)
        if stripped in labels:
            return True
        return any(lbl in token for lbl in labels_sorted)

    def try_inline_value(token: str) -> str | None:
        # Handle patterns like "建物構造：RC造" (label and value in same text node)
        for lbl in labels_sorted:
            if not token.startswith(lbl):
                continue
            rest = token[len(lbl) :]
            rest = re.sub(r"^[\s\u3000:：/／・]+", "", rest)
            rest = rest.strip()
            if rest and rest not in labels:
                return rest
        return None

    for i, t in enumerate(texts):
        inline = try_inline_value(t)
        if inline:
            return inline

        if is_label_token(t):
            for j in range(i + 1, min(len(texts), i + 1 + lookahead)):
                cand = texts[j].strip()
                if not cand or is_label_token(cand):
                    continue
                return cand
    return None


_JP_PREF_TO_CODE: dict[str, str] = {
    "東京都": "tokyo",
    "神奈川県": "kanagawa",
    "埼玉県": "saitama",
    "千葉県": "chiba",
    "大阪府": "osaka",
}


def _extract_prefecture_and_municipality(text: str) -> tuple[str | None, str | None]:
    """
    Try to extract (prefecture_code, municipality_jp) from a free-form address string.

    Examples:
      "東京都新宿区西新宿..." -> ("tokyo", "新宿区")
      "神奈川県横浜市西区..." -> ("kanagawa", "横浜市西区")
    """
    if not text:
        return None, None

    t = _strip_tags(text)
    t = re.sub(r"\s+", "", t)

    pref_jp = None
    pref_code = None
    for jp, code in _JP_PREF_TO_CODE.items():
        if jp in t:
            pref_jp = jp
            pref_code = code
            break
    if pref_code is None or pref_jp is None:
        return None, None

    rest = t.split(pref_jp, 1)[1]
    if not rest:
        return pref_code, None

    # City + ward (e.g. 横浜市西区, 大阪市中央区)
    city_i = rest.find("市")
    if city_i != -1:
        ward_i = rest.find("区", city_i + 1)
        if ward_i != -1:
            return pref_code, rest[: ward_i + 1]
        return pref_code, rest[: city_i + 1]

    # Ward / town / village (e.g. 新宿区, 葉山町)
    for suffix in ("区", "町", "村"):
        i = rest.find(suffix)
        if i != -1:
            return pref_code, rest[: i + 1]

    return pref_code, None


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


_ORIENTATION_JP_TO_ENUM: dict[str, str] = {
    "\u5317\u6771": "NE",  # 北東
    "\u6771\u5317": "NE",  # 東北
    "\u5357\u6771": "SE",  # 南東
    "\u6771\u5357": "SE",  # 東南
    "\u5357\u897f": "SW",  # 南西
    "\u897f\u5357": "SW",  # 西南
    "\u5317\u897f": "NW",  # 北西
    "\u897f\u5317": "NW",  # 西北
    "\u5317": "N",
    "\u6771": "E",
    "\u5357": "S",
    "\u897f": "W",
}

_FULLWIDTH_ASCII_TRANSLATION = str.maketrans(
    {
        "\uff32": "R",  # Ｒ
        "\uff23": "C",  # Ｃ
        "\uff33": "S",  # Ｓ
    }
)


def _parse_orientation(text: str) -> str | None:
    if not text:
        return None
    t = _strip_tags(text)
    t = re.sub(r"\s+", "", t)
    t = t.replace("\u5411\u304d", "")  # 向き
    t = t.replace("\u65b9\u5411", "")  # 方向

    for jp in sorted(_ORIENTATION_JP_TO_ENUM.keys(), key=len, reverse=True):
        if jp in t:
            return _ORIENTATION_JP_TO_ENUM[jp]
    return None


def _parse_built_year(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d{4})\s*年", text)
    if not m:
        return None
    try:
        y = int(m.group(1))
    except ValueError:
        return None
    current = datetime.now().year
    if 1900 <= y <= current + 2:
        return y
    return None


def _structure_text_to_code(text: str) -> str | None:
    if not text:
        return None
    t = _strip_tags(text)
    t = re.sub(r"\s+", "", t)
    t = t.translate(_FULLWIDTH_ASCII_TRANSLATION)
    t = t.replace("\u9020", "")  # 造

    # Prefer longer/more specific matches first
    if "SRC" in t or "\u9244\u9aa8\u9244\u7b4b" in t:  # 鉄骨鉄筋
        return "src"
    if "RC" in t or "\u9244\u7b4b\u30b3\u30f3\u30af\u30ea\u30fc\u30c8" in t or "\u9244\u7b4b\u30b3\u30f3" in t:  # 鉄筋コンクリート / 鉄筋コン (abbreviated)
        return "rc"
    if "\u8efd\u91cf\u9244\u9aa8" in t:  # 軽量鉄骨
        return "light_steel"
    if "\u9244\u9aa8" in t:  # 鉄骨
        return "steel"
    if "\u6728\u9020" in t:  # 木造
        return "wood"
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


def _detect_waf_challenge(html: str) -> str | None:
    """Return error message if HTML is a WAF/JS challenge page, else None."""
    if (
        "token.awswaf.com" in html
        or "AwsWafIntegration" in html
        or "challenge-container" in html
    ):
        return "SUUMO returned a WAF/JS challenge page (bot detection). Try again later."
    return None


def _extract_json_ld_fields(html_raw: str) -> dict[str, Any]:
    """Extract fields from JSON-LD <script> blocks (more stable than HTML layout)."""
    import json

    result: dict[str, Any] = {}
    for m in re.finditer(
        r'<script\s+type=["\']application/ld\+json["\']\s*>(.*?)</script>',
        html_raw,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        # Price / rent
        offers = data.get("offers", {})
        if isinstance(offers, dict):
            price = offers.get("price")
            if price is not None:
                try:
                    result.setdefault("rent_yen", int(float(price)))
                except (ValueError, TypeError):
                    pass
        # Floor size / area
        floor_size = data.get("floorSize")
        if isinstance(floor_size, dict):
            val = floor_size.get("value")
            if val is not None:
                try:
                    result.setdefault("area_sqm", float(val))
                except (ValueError, TypeError):
                    pass
        # Address
        address = data.get("address")
        if isinstance(address, dict):
            region = address.get("addressRegion", "")
            locality = address.get("addressLocality", "")
            pref_code, muni = _extract_prefecture_and_municipality(region + locality)
            if pref_code:
                result.setdefault("prefecture", pref_code)
            if muni:
                result.setdefault("municipality", muni)
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
            raw = resp.read()
            charset = None
            try:
                charset = resp.headers.get_content_charset()
            except Exception:
                charset = None
            html = _decode_best_effort(raw, charset)
    except Exception as e:
        return {"_error": f"Fetch failed: {e}"}

    # Detect WAF/JS challenge before attempting any extraction.
    waf_err = _detect_waf_challenge(html)
    if waf_err:
        return {"_error": waf_err}

    result: dict[str, Any] = {"_source_url": url}
    html_raw = html

    # Try JSON-LD structured data first (stable across HTML layout changes).
    json_ld_fields = _extract_json_ld_fields(html_raw)
    if json_ld_fields:
        result.update(json_ld_fields)

    # Tokenize key parts of the detail page. This is more robust than regex across HTML tags.
    tokens: list[str] = []
    try:
        p = _DetailPageParser()
        p.feed(html_raw)
        tokens = list(getattr(p, "_texts", []) or [])
    except Exception:
        tokens = []

    # Strip tags so regex scans don't get tripped by digits inside HTML attributes.
    html = _strip_tags(html_raw)
    if not tokens:
        tokens = [t.strip() for t in html.split() if t.strip()]

    # Location (prefecture / municipality) if present
    loc_text = _find_value_after_label(tokens, {"所在地"}) or ""
    pref_code, muni = _extract_prefecture_and_municipality(_strip_tags(loc_text))
    if pref_code:
        result["prefecture"] = pref_code
    if muni:
        result["municipality"] = muni

    # Rent (家賃)
    rent_text = _find_value_after_label(tokens, {"賃料", "家賃"})
    if rent_text:
        rent_yen = _parse_man_yen(rent_text)
        if rent_yen is not None:
            result["rent_yen"] = rent_yen
    if "rent_yen" not in result:
        rent_m = re.search(r"(?:賃料|家賃)[^0-9]*([\d.]+)\s*万円", html)
        if rent_m:
            try:
                result["rent_yen"] = int(round(float(rent_m.group(1)) * 10000))
            except ValueError:
                pass

    # Management fee (管理費/共益費)
    mgmt_text = _find_value_after_label(tokens, {"管理費", "共益費", "管理費・共益費"})
    if mgmt_text:
        if mgmt_text in ("-", "－", "なし", "無し", "無"):
            result["mgmt_fee_yen"] = 0
        else:
            mgmt_yen = _parse_yen(mgmt_text)
            if mgmt_yen is not None:
                result["mgmt_fee_yen"] = mgmt_yen
    if "mgmt_fee_yen" not in result:
        admin_m = re.search(r"(?:管理費|共益費)[^\d]*([\d,]+)\s*円", html)
        if admin_m:
            try:
                result["mgmt_fee_yen"] = int(admin_m.group(1).replace(",", ""))
            except ValueError:
                pass

    # Backward-compat for older frontend mapping
    if "mgmt_fee_yen" in result:
        result["management_fee_yen"] = result["mgmt_fee_yen"]

    # Orientation (向き)
    orient_text = _find_value_after_label(tokens, {"\u5411\u304d", "\u65b9\u89d2"})
    if orient_text:
        o = _parse_orientation(orient_text)
        if o:
            result["orientation"] = o
    if "orientation" not in result:
        orient_m = re.search(
            "(?:\u5411\u304d|\u65b9\u89d2)\\s*([\u5317\u5357\u6771\u897f]{1,2})",
            html,
        )
        if orient_m:
            o = _parse_orientation(orient_m.group(1))
            if o:
                result["orientation"] = o

    # Area (専有面積)
    area_text = _find_value_after_label(tokens, {"専有面積", "使用部分面積"})
    if area_text:
        area_sqm = _parse_area(area_text)
        if area_sqm is not None:
            result["area_sqm"] = area_sqm
    if "area_sqm" not in result:
        area_m = re.search(r"(?:専有面積|使用部分面積)[^\d]*([\d.]+)\s*(?:m2|m²|㎡)", html, re.IGNORECASE)
        if area_m:
            try:
                result["area_sqm"] = float(area_m.group(1))
            except ValueError:
                pass

    # Fallback: handle patterns like "25.21m<sup>2</sup>" -> "25.21m 2"
    if "area_sqm" not in result:
        area_any_m = re.search("([\\d.]+)\\s*m\\s*(?:2|\u00b2)", html, re.IGNORECASE)
        if area_any_m:
            try:
                v = float(area_any_m.group(1))
                if 5 <= v <= 300:
                    result["area_sqm"] = v
            except ValueError:
                pass

    # Layout (間取り)
    layout_text = _find_value_after_label(tokens, {"間取り"})
    if layout_text:
        m = re.search(r"(\d[RLKDS]+(?:LDK|DK|LK|K|R)?)", layout_text)
        if m:
            result["layout_type"] = m.group(1)
    if "layout_type" not in result:
        layout_m = re.search(r"間取り[^1-9]*(\d[RLKDS]+(?:LDK|DK|LK|K|R)?)", html)
        if layout_m:
            result["layout_type"] = layout_m.group(1)

    # Station walk (交通 歩X分)
    if tokens:
        for t in tokens:
            w = _parse_walk(t)
            if w is not None:
                result["station_walk_min"] = w
                break
    if "station_walk_min" not in result:
        walk_m = re.search(r"(?:徒歩|歩)\s*(\d+)\s*分", html)
        if walk_m:
            try:
                result["station_walk_min"] = int(walk_m.group(1))
            except ValueError:
                pass

    # Built year (築年月)
    built_text = _find_value_after_label(tokens, {"\u7bc9\u5e74\u6708"})
    built_year = _parse_built_year(built_text or "")
    if built_year is not None:
        result["building_built_year"] = built_year
    if "building_built_year" not in result:
        built_m = re.search("(?:\u7bc9\u5e74\u6708)[^0-9]*(\\d{4})\\s*\u5e74", html)
        if built_m:
            built_year = _parse_built_year(str(built_m.group(1)) + "\u5e74")
            if built_year is not None:
                result["building_built_year"] = built_year

    # Building age (築年数)
    if "新築" in html:
        result["building_age_years"] = 0
    else:
        if tokens:
            for t in tokens:
                a = _parse_building_age(t)
                if a is not None:
                    result["building_age_years"] = a
                    break
        if "building_age_years" not in result:
            age_m = re.search(r"築(\d+)年", html)
            if age_m:
                try:
                    result["building_age_years"] = int(age_m.group(1))
                except ValueError:
                    pass

    # Building structure (構造) — map Japanese to our codes
    structure_text = _find_value_after_label(tokens, {"\u69cb\u9020", "\u5efa\u7269\u69cb\u9020"})
    if structure_text:
        code = _structure_text_to_code(structure_text)
        if code:
            result["building_structure"] = code

    if "building_structure" not in result and tokens:
        # Some pages split the structure value across multiple small text nodes
        # (e.g., "鉄筋" + "コンクリート" + "造"). If we saw a structure label-like token,
        # try joining a small lookahead window and re-parse.
        for i, t in enumerate(tokens):
            if "\u69cb\u9020" not in t:
                continue
            # Skip common false positives (not the building structure label).
            if any(x in t for x in ("\u8010\u9707\u69cb\u9020", "\u514d\u9707\u69cb\u9020", "\u9632\u706b\u69cb\u9020")):
                continue
            window = "".join(tokens[i + 1 : i + 7])
            code = _structure_text_to_code(window)
            if code:
                result["building_structure"] = code
                break

    if "building_structure" not in result:
        # Table/dl kv extraction (robust to nested tags that split the value into multiple tokens)
        kv = _extract_kv_pairs_from_html(html_raw)
        structure_text = _find_value_in_kv_pairs(kv, ("建物構造", "構造"))
        if structure_text:
            code = _structure_text_to_code(structure_text)
            if code:
                result["building_structure"] = code

    if "building_structure" not in result:
        # Regex fallback on full page text (handles dl/inline patterns).
        # Use negative lookbehind to skip false positives like "耐震構造".
        html_norm = _strip_tags(html).translate(_FULLWIDTH_ASCII_TRANSLATION)
        m = re.search(r"(?:建物構造|(?<![耐震防火鉄木])構造)[\s\u3000:：/／・]{0,10}(.{1,80})", html_norm)
        if m:
            code = _structure_text_to_code(m.group(1))
            if code:
                result["building_structure"] = code

    if "building_structure" not in result:
        # Normalize fullwidth ASCII (ＲＣ/ＳＲＣ) and remove whitespace before scanning.
        html_norm = _strip_tags(html).translate(_FULLWIDTH_ASCII_TRANSLATION)
        html_compact = re.sub(r"\s+", "", html_norm)
        structure_map = {
            "木造": "wood",
            "軽量鉄骨": "light_steel",
            "鉄骨造": "steel",
            "鉄筋コンクリート": "rc",
            "鉄筋コン": "rc",           # common abbreviation
            "RC": "rc",
            "SRC": "src",
            "鉄骨鉄筋": "src",
        }
        for jp, code in structure_map.items():
            if jp in html_compact:
                result["building_structure"] = code
                break

    if "building_structure" not in result:
        # Last-chance: locate the label in compact text and parse a short tail window.
        # Skip false positives where "構造" is part of a compound like "耐震構造".
        html_norm = _strip_tags(html).translate(_FULLWIDTH_ASCII_TRANSLATION)
        html_compact = re.sub(r"\s+", "", html_norm)
        for lbl in ("建物構造", "構造"):
            start = 0
            while True:
                idx = html_compact.find(lbl, start)
                if idx == -1:
                    break
                # Skip false positives: "耐震構造", "免震構造", etc.
                if lbl == "構造" and idx > 0 and html_compact[idx - 1] in "耐震防火免鉄木":
                    start = idx + len(lbl)
                    continue
                tail = html_compact[idx + len(lbl) : idx + len(lbl) + 60]
                tail = re.sub(r"^[\u3000\s:：/／・]+", "", tail)
                code = _structure_text_to_code(tail)
                if code:
                    result["building_structure"] = code
                    break
                start = idx + len(lbl)
            if "building_structure" in result:
                break

    if "building_structure" not in result:
        # Add diagnostic snippet so callers can understand why extraction failed.
        # Search for standalone "構造" label (skip compound words like "耐震構造").
        html_flat = re.sub(r"\s+", " ", _strip_tags(html_raw))
        idx = 0
        debug_idx = -1
        while True:
            pos = html_flat.find("構造", idx)
            if pos == -1:
                break
            if pos > 0 and html_flat[pos - 1] in "耐震防火免鉄木":
                idx = pos + 2
                continue
            debug_idx = pos
            break
        if debug_idx != -1:
            s = max(0, debug_idx - 30)
            e = min(len(html_flat), debug_idx + 80)
            result["_structure_debug"] = html_flat[s:e]
        else:
            result["_structure_debug"] = "standalone '構造' label not found in page text"

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
    if tokens and "nearest_station_name" not in result:
        for i, t in enumerate(tokens):
            if t.endswith("駅") and len(t) >= 2:
                result["nearest_station_name"] = t[:-1]
                break

    if "nearest_station_name" not in result:
        station_m = re.search(r"([^\s/「」]+)駅\s*(?:徒歩|歩)\s*\d+\s*分", html)
        if station_m:
            result["nearest_station_name"] = station_m.group(1)

    # If we couldn't extract anything meaningful, treat as parse failure so the UI shows a clear error.
    fillable_keys = {
        "prefecture",
        "municipality",
        "nearest_station_name",
        "station_walk_min",
        "layout_type",
        "building_structure",
        "area_sqm",
        "building_built_year",
        "orientation",
        "rent_yen",
        "mgmt_fee_yen",
        "management_fee_yen",
        "building_age_years",
    }
    if not any(k in result for k in fillable_keys):
        return {"_error": "No fillable fields extracted from SUUMO detail page (page layout may have changed or blocked)."}

    return result


def parse_suumo_url(url: str, *, timeout: int = 12) -> dict[str, Any]:
    """
    Unified entry point: auto-detect search URL vs listing detail URL.
    """
    if "/ichiran/" in url or "FR301FC" in url:
        return parse_suumo_search_url(url)
    return parse_suumo_listing_url(url, timeout=timeout)
