from __future__ import annotations

import html as html_lib
import json
import os
import re
import tempfile
import threading
import unicodedata
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


def _normalize_jp_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = re.sub(r"\s+", "", t)
    return t.strip()


def _default_cache_path() -> Path:
    base = Path(tempfile.gettempdir())
    return base / "tokyo_wh_url_resolver_cache_v1.json"


_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None


def _load_cache() -> dict[str, Any]:
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is not None:
            return _CACHE
        path = Path(os.getenv("URL_RESOLVER_CACHE_PATH", str(_default_cache_path())))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == 1:
                _CACHE = data
            else:
                _CACHE = {"version": 1}
        except Exception:
            _CACHE = {"version": 1}
        return _CACHE


def _save_cache(cache: dict[str, Any]) -> None:
    path = Path(os.getenv("URL_RESOLVER_CACHE_PATH", str(_default_cache_path())))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _detect_waf_or_js_challenge(html: str) -> bool:
    h = (html or "").lower()
    return ("challenge-container" in h) or ("token.awswaf.com" in h) or ("awswafintegration" in h)


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._code: str | None = None
        self._buf: list[str] = []
        self.items: list[tuple[str, str]] = []  # (name, code)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        m = re.search(r"/area/(\d{5})/", str(href))
        if not m:
            return
        self._capture = True
        self._code = str(m.group(1))
        self._buf = []

    def handle_data(self, data: str) -> None:
        if not self._capture:
            return
        s = str(data or "")
        if s.strip():
            self._buf.append(s)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a":
            return
        if not self._capture:
            return
        name = html_lib.unescape("".join(self._buf)).strip()
        code = self._code
        self._capture = False
        self._code = None
        self._buf = []
        if not name or not code:
            return
        self.items.append((name, code))


def parse_chintai_pref_area_index(html: str) -> dict[str, str]:
    """
    Parse a CHINTAI prefecture area index page (e.g. https://www.chintai.net/tokyo/area/)
    and return a mapping: municipality_name -> area_code (5-digit JIS-like code).
    """
    parser = _AnchorCollector()
    parser.feed(html)
    out: dict[str, str] = {}
    for name, code in parser.items:
        n = _normalize_jp_text(name)
        if not n:
            continue
        if re.fullmatch(r"\d{5}", str(code)):
            out[n] = str(code)
    return out


def _fetch_html(url: str, *, timeout: int = 12) -> str:
    req = urllib.request.Request(str(url), headers=_FETCH_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # CHINTAI pages are typically UTF-8; keep it simple but robust enough.
    for enc in ("utf-8", "shift_jis", "cp932", "euc_jp"):
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _best_match_from_mapping(mapping: dict[str, str], candidates: Iterable[str]) -> str | None:
    cand_norms = [(_normalize_jp_text(c), c) for c in candidates]
    cand_norms = [(n, raw) for (n, raw) in cand_norms if n]
    if not cand_norms:
        return None

    # Exact match first.
    for n, _raw in cand_norms:
        code = mapping.get(n)
        if code:
            return code

    # Substring fuzzy match (pick closest-length).
    best_code: str | None = None
    best_score: int | None = None
    for n, _raw in cand_norms:
        for key, code in mapping.items():
            if not key:
                continue
            if (n in key) or (key in n):
                score = abs(len(key) - len(n))
                if best_score is None or score < best_score:
                    best_score = score
                    best_code = code
    return best_code


def resolve_chintai_area_code(
    prefecture: str,
    municipality_candidates: Iterable[str],
    *,
    timeout: int = 12,
) -> str | None:
    """
    Resolve CHINTAI area code for a municipality by:
      1) cache hit (URL_RESOLVER_CACHE_PATH or temp file)
      2) fetching CHINTAI prefecture area index and parsing /area/{code}/ links
    """
    pref = str(prefecture).lower().strip()
    if not pref:
        return None

    muni_cands = list(municipality_candidates or [])
    if not muni_cands:
        return None

    cache = _load_cache()
    ch = cache.setdefault("chintai", {})
    if isinstance(ch, dict):
        pref_map = ch.get(pref)
        if isinstance(pref_map, dict):
            code = _best_match_from_mapping(pref_map, muni_cands)
            if code:
                return code

    url = f"https://www.chintai.net/{pref}/area/"
    html = _fetch_html(url, timeout=timeout)
    if _detect_waf_or_js_challenge(html):
        return None

    mapping = parse_chintai_pref_area_index(html)
    if not mapping:
        return None

    # Persist mapping for future calls.
    with _CACHE_LOCK:
        cache2 = _load_cache()
        ch2 = cache2.setdefault("chintai", {})
        if isinstance(ch2, dict):
            ch2[pref] = mapping
            try:
                _save_cache(cache2)
            except Exception:
                # Best-effort cache; ignore persistence errors.
                pass

    return _best_match_from_mapping(mapping, muni_cands)
