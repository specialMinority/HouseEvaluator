"""
backend.src.station_utils -- shared station name normalization/matching utilities.

Why:
- Providers often emit station names in slightly different forms (e.g. kana vs kanji).
- Users may input either form, and strict string equality causes false mismatches.

Policy:
- Normalize (NFKC, strip whitespace, drop the "駅" suffix).
- Apply a small alias table for common kana/kanji variants.
- Similarity is substring-based over normalized variants to keep it robust.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_station_name(st: str) -> str:
    t = unicodedata.normalize("NFKC", str(st or ""))
    t = re.sub(r"\s+", "", t)
    t = t.replace("駅", "")
    return t


_STATION_ALIASES: dict[str, set[str]] = {
    # Minimal alias set for common kana/kanji variants.
    "難波": {"なんば"},
    "なんば": {"難波"},
}


def station_variants(st: str) -> set[str]:
    base = normalize_station_name(st)
    if not base:
        return set()
    out: set[str] = {base}
    for alt in _STATION_ALIASES.get(base, set()):
        alt_norm = normalize_station_name(alt)
        if alt_norm:
            out.add(alt_norm)
    return out


def station_similar(a: str, b: str) -> bool:
    """
    Best-effort similarity for station names.

    - Uses normalization + a small alias table.
    - Uses substring match to handle common prefixes (e.g., "大阪難波" vs "難波").
    """
    if not a or not b:
        return False
    a_vars = station_variants(a)
    b_vars = station_variants(b)
    for av in a_vars:
        for bv in b_vars:
            if not av or not bv:
                continue
            if (av in bv) or (bv in av):
                return True
    return False

