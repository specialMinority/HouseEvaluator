from dataclasses import dataclass

from backend.src.age_proximity import select_by_age_proximity


@dataclass
class _Listing:
    building_age_years: int | None


def test_age_proximity_picks_delta0_when_enough():
    listings = [_Listing(8), _Listing(7), _Listing(6), _Listing(10)]
    selected, meta = select_by_age_proximity(listings, target_age_years=8, min_keep=2, delta_ladder=[0, 1, 2])
    assert [x.building_age_years for x in selected] == [8, 7]
    assert meta["chosen_delta"] == 0
    assert meta["selected_n"] == 2


def test_age_proximity_picks_smallest_delta_that_meets_min_keep():
    # target candidates: [10, 9]
    # deltas: 10->0, 8->1, 7->2
    listings = [_Listing(10), _Listing(8), _Listing(7)]
    selected, meta = select_by_age_proximity(listings, target_age_years=10, min_keep=2, delta_ladder=[2, 0, 1])
    assert [x.building_age_years for x in selected] == [10, 8]
    assert meta["chosen_delta"] == 1


def test_age_proximity_tracks_missing_ages():
    listings = [_Listing(None), _Listing(5), _Listing(None)]
    selected, meta = select_by_age_proximity(listings, target_age_years=5, min_keep=1, delta_ladder=[0])
    assert [x.building_age_years for x in selected] == [5]
    assert meta["missing_age_n"] == 2
    assert meta["counts_by_delta"] == {0: 1}


def test_age_proximity_returns_empty_when_not_enough_within_ladder():
    listings = [_Listing(0), _Listing(20)]
    selected, meta = select_by_age_proximity(listings, target_age_years=8, min_keep=2, delta_ladder=[0, 1, 2, 3, 5])
    assert selected == []
    assert meta["chosen_delta"] is None
    assert meta["selected_n"] == 0

