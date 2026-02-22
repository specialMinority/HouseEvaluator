from __future__ import annotations

from typing import Any, Protocol, Sequence, TypeVar


class HasBuildingAgeYears(Protocol):
    building_age_years: int | None


TListing = TypeVar("TListing", bound=HasBuildingAgeYears)


def target_age_candidates(target_age_years: int, *, include_target_minus_one: bool = True) -> list[int]:
    t = max(0, int(target_age_years))
    out = [t]
    if include_target_minus_one and t > 0:
        out.append(t - 1)
    return out


def age_delta_years(age_years: int, *, target_candidates: Sequence[int]) -> int:
    a = max(0, int(age_years))
    if not target_candidates:
        return 0
    return min(abs(a - int(t)) for t in target_candidates)


def select_by_age_proximity(
    listings: Sequence[TListing],
    *,
    target_age_years: int,
    min_keep: int,
    delta_ladder: Sequence[int],
    include_target_minus_one: bool = True,
) -> tuple[list[TListing], dict[str, Any]]:
    """
    Select listings closest to the subject building age.

    - target age is expressed in years (築年数), typically derived from built_year.
    - because providers may display age +/- 1 depending on month, we treat
      {target_age, target_age-1} as equivalent candidates by default.
    - delta ladder is cumulative: choose the smallest d such that count(delta<=d) >= min_keep.
    """
    min_keep_i = max(1, int(min_keep))
    ladder = sorted({max(0, int(d)) for d in delta_ladder})
    candidates = target_age_candidates(int(target_age_years), include_target_minus_one=include_target_minus_one)

    missing_age_n = 0
    deltas: list[int] = []
    for lst in listings:
        if lst.building_age_years is None:
            missing_age_n += 1
            continue
        deltas.append(age_delta_years(int(lst.building_age_years), target_candidates=candidates))

    counts_by_delta: dict[int, int] = {}
    for d in deltas:
        counts_by_delta[int(d)] = int(counts_by_delta.get(int(d), 0) + 1)

    chosen_delta: int | None = None
    for d in ladder:
        within = sum(1 for dd in deltas if dd <= int(d))
        if within >= min_keep_i:
            chosen_delta = int(d)
            break

    selected: list[TListing] = []
    if chosen_delta is not None:
        for lst in listings:
            if lst.building_age_years is None:
                continue
            d = age_delta_years(int(lst.building_age_years), target_candidates=candidates)
            if d <= int(chosen_delta):
                selected.append(lst)

    meta: dict[str, Any] = {
        "target_age_candidates": candidates,
        "delta_ladder": ladder,
        "chosen_delta": chosen_delta,
        "counts_by_delta": counts_by_delta,
        "missing_age_n": missing_age_n,
        "selected_n": int(len(selected)),
    }
    return selected, meta

