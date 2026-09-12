"""Offline policy regressions; every fixture price is synthetic test data.

Explicit operator approvals here exercise arithmetic, never market accuracy.
"""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import random
import unittest

from backend.v2.comparison import POLICY, compare
from backend.v2.models import ValidationError


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def timestamp(hours=0):
    return (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def subject(**changes):
    value = {
        "city": "tokyo", "municipality": "江戸川区", "station_id": "JP:JR:KOIWA",
        "station_name": "小岩", "unit_id": "subject-unit", "building_id": "subject-building",
        "layout": "1K", "structure": "rc", "property_type": "apartment",
        "contract_type": "standard", "furnished": False, "area_sqm": 25,
        "built_year": 2015, "walk_min": 7, "floor": 3, "orientation": "S",
        "bathroom_separate": True, "rent_yen": 110000, "mgmt_fee_yen": 5000,
    }
    value.update(changes)
    return value


def observation(index, **changes):
    value = subject(
        unit_id=f"unit-{index:03}", building_id=f"building-{index // 2:03}",
        rent_yen=80000 + 1000 * index,
    )
    value.update({
        "source_id": "fixture-supplier", "listing_id": f"listing-{index:03}",
        "observation_id": f"observation-{index:03}", "status": "active",
        "source_updated_at": timestamp(-2), "received_at": timestamp(-1),
        "status_verified_at": timestamp(-2), "public_url": f"https://example.test/{index}",
        "data_kind": "observed",
    })
    value.update(changes)
    return value


def observations(count=24, **changes):
    return [observation(index, **changes) for index in range(count)]


def source(**changes):
    value = {
        "source_id": "fixture-supplier", "independent_source_id": "fixture-owner",
        "display_name": "Unit-test fixture (not a real supplier)", "data_kind": "observed",
        "rights": {"comparison": True, "storage": True, "display": True, "expires_at": timestamp(48)},
        "snapshot_id": "snapshot-1", "as_of": timestamp(-1), "complete": True,
    }
    value.update(changes)
    return value


def evaluate(rows=None, target=None, **options):
    arguments = {"now": NOW, "snapshot_version": "fixture-v1", "sources": [source()], "validated_segments": {"tokyo/1K"}}
    arguments.update(options)
    return compare(subject() if target is None else target, observations() if rows is None else rows, **arguments)


class ComparisonTests(unittest.TestCase):
    def assert_no_price(self, result):
        for field in ("benchmark_yen", "delta_yen", "delta_ratio", "judgment"):
            self.assertIsNone(result[field], field)
        for field in ("q25_yen", "q50_yen", "q75_yen"):
            self.assertIsNone(result["distribution"][field], field)

    def test_ordinary_median_quantiles_and_shared_price_basis(self):
        result = evaluate()
        self.assertEqual(result["status"], "comparable")
        self.assertEqual(result["sample"], {"unit_count": 24, "building_count": 12, "effective_n": 24.0})
        self.assertEqual(result["benchmark_yen"], 96500)
        self.assertEqual(result["distribution"]["q25_yen"], 90750)
        self.assertEqual(result["distribution"]["q50_yen"], 96500)
        self.assertEqual(result["distribution"]["q75_yen"], 102250)
        self.assertEqual(result["subject_price_yen"], 115000)
        self.assertEqual(result["delta_yen"], 18500)
        self.assertEqual(result["judgment"], "higher")
        self.assertEqual(result["matching"]["relaxed_fields"], [])

    def test_unvalidated_market_and_demo_never_issue_direction(self):
        result = evaluate(validated_segments=set())
        self.assertEqual(result["status"], "limited")
        self.assertIn("policy_unvalidated", result["reason_codes"])
        self.assertIsNone(result["judgment"])
        self.assertEqual(result["benchmark_yen"], 96500)
        demo = evaluate(observations(data_kind="synthetic", public_url=None), is_demo=True, sources=[source(data_kind="synthetic")])
        self.assertTrue(demo["is_demo"])
        self.assertIn("synthetic_data", demo["reason_codes"])
        self.assertEqual(demo["status"], "limited")
        self.assertIsNone(demo["judgment"])

    def test_three_cities_require_separate_explicit_approval(self):
        for city, municipality, station_id in (
            ("tokyo", "江戸川区", "JP:JR:KOIWA"),
            ("osaka", "大阪市北区", "JP:JR:OSAKA"),
            ("fukuoka", "福岡市博多区", "JP:JR:HAKATA"),
        ):
            with self.subTest(city=city):
                changes = {"city": city, "municipality": municipality, "station_id": station_id}
                result = evaluate(observations(**changes), subject(**changes), validated_segments={f"{city}/1K"})
                self.assertEqual(result["status"], "comparable")

    def test_price_does_not_select_or_truncate_candidates(self):
        rows = observations(41)
        rows[-1]["rent_yen"] = 999999999
        low = evaluate(rows, subject(rent_yen=1))
        high = evaluate(list(reversed(rows)), subject(rent_yen=999999999))
        self.assertEqual(low["sample"]["unit_count"], 41)
        self.assertEqual(low["comparables"], high["comparables"])
        self.assertEqual(low["benchmark_yen"], high["benchmark_yen"])
        self.assertEqual(low["judgment"], "lower")
        self.assertEqual(high["judgment"], "higher")

    def test_relaxation_changes_one_field_and_stops_as_soon_as_sufficient(self):
        rows = observations(16)
        rows += [observation(index, orientation="N") for index in range(16, 20)]
        rows += [observation(index, area_sqm=27) for index in range(20, 24)]
        result = evaluate(rows)
        self.assertEqual(result["sample"]["unit_count"], 20)
        self.assertEqual(result["matching"]["relaxed_fields"], ["orientation"])
        self.assertEqual(result["matching"]["steps"], [
            {"step": 0, "changed_field": None, "before": None, "after": None, "candidate_count": 16},
            {"step": 1, "changed_field": "orientation", "before": "S", "after": "any", "candidate_count": 20},
        ])

    def test_all_relaxations_are_bounded_and_floor_or_new_build_never_expand(self):
        rows = observations(9)
        rows += [observation(10, orientation="NW"), observation(12, area_sqm=27.5), observation(14, built_year=2010), observation(16, walk_min=12)]
        rows += [observation(18, area_sqm=27.5001), observation(20, built_year=2009), observation(22, walk_min=12.01), observation(24, floor=1), observation(26, floor=-1), observation(28, built_year=2026)]
        result = evaluate(rows)
        self.assertEqual(result["matching"]["relaxed_fields"], ["orientation", "area_sqm", "built_year", "walk_min"])
        self.assertEqual([step["candidate_count"] for step in result["matching"]["steps"]], [9, 10, 11, 12, 13])
        self.assertEqual(result["excluded_counts"]["outside_bounded_conditions"], 3)
        self.assertEqual(result["excluded_counts"]["floor_band_boundary"], 2)
        self.assertEqual(result["excluded_counts"]["new_build_boundary"], 1)

    def test_station_ids_are_exact_even_if_labels_or_substrings_match(self):
        for target_station, other_station in (
            ("JP:JR:KOIWA", "JP:JR:SHINKOIWA"),
            ("JP:JR:OSAKA", "JP:JR:SHINOSAKA"),
            ("StationCase", "stationcase"),
        ):
            result = evaluate(observations(station_id=other_station), subject(station_id=target_station))
            self.assertEqual(result["sample"]["unit_count"], 0)
            self.assertEqual(result["excluded_counts"]["mismatch_station_id"], 24)
            self.assert_no_price(result)
        result = evaluate(observations(station_id=" JP:JR:KOIWA "))
        self.assertEqual(result["sample"]["unit_count"], 24)

    def test_hard_categories_never_relax(self):
        for field, wrong in (
            ("city", "osaka"), ("municipality", "葛飾区"), ("layout", "1R"),
            ("structure", "src"), ("contract_type", "fixed_term"),
            ("furnished", True), ("bathroom_separate", False),
        ):
            with self.subTest(field=field):
                result = evaluate(observations(**{field: wrong}))
                self.assertEqual(result["excluded_counts"][f"mismatch_{field}"], 24)
                self.assert_no_price(result)

    def test_unknown_critical_subject_values_never_become_defaults(self):
        for field in ("mgmt_fee_yen", "structure", "built_year", "walk_min", "floor", "bathroom_separate", "furnished"):
            with self.subTest(field=field):
                target = subject(**{field: None})
                result = evaluate(target=target)
                self.assertEqual(result["status"], "insufficient")
                self.assertIn(field, result["matching"]["missing_fields"])
                self.assertIn("missing_critical_fields", result["reason_codes"])
                self.assert_no_price(result)
        target = subject()
        del target["furnished"]
        self.assertIn("furnished", evaluate(target=target)["matching"]["missing_fields"])

    def test_unknown_observation_values_excluded_but_orientation_explicitly_relaxes(self):
        for field in ("mgmt_fee_yen", "structure", "built_year", "walk_min", "floor", "bathroom_separate", "furnished"):
            with self.subTest(field=field):
                result = evaluate(observations(**{field: None}))
                self.assertEqual(result["excluded_counts"]["missing_critical_conditions"], 24)
                self.assert_no_price(result)
        result = evaluate(observations(orientation=None))
        self.assertEqual(result["matching"]["relaxed_fields"], ["orientation"])
        self.assertIn("orientation_unverified", result["reason_codes"])
        self.assertTrue(all("orientation:unknown" in row["differences"] for row in result["comparables"]))

    def test_self_unit_excluded_across_url_and_supplier(self):
        rows = observations()
        rows += [observation(50, unit_id=" subject-unit "), observation(51, unit_id="subject-unit", source_id="other-supplier", public_url="https://different.example.test/self")]
        result = evaluate(rows, sources=[source(), source(source_id="other-supplier")])
        self.assertEqual(result["excluded_counts"]["self_unit"], 2)
        self.assertEqual(result["sample"]["unit_count"], 24)
        self.assertNotIn("subject-unit", {row["unit_id"] for row in result["comparables"]})

    def test_building_only_identity_excludes_whole_building_and_no_identity_abstains(self):
        rows = observations()
        rows += [observation(50, building_id="subject-building"), observation(51, building_id="subject-building")]
        result = evaluate(rows, subject(unit_id=None))
        self.assertEqual(result["excluded_counts"]["self_building"], 2)
        self.assertEqual(result["sample"]["unit_count"], 24)
        self.assertIn("self_building_excluded", result["reason_codes"])
        missing = evaluate(target=subject(unit_id=None, building_id=None))
        self.assertEqual(missing["status"], "limited")
        self.assertIn("identity_unverified", missing["reason_codes"])
        self.assertIsNone(missing["judgment"])

    def test_cross_supplier_duplicates_count_one_unit_and_conflicts_exclude(self):
        rows = observations()
        duplicate = deepcopy(rows[0])
        duplicate.update(source_id="other-supplier", listing_id="other-listing", observation_id="other-observation", public_url="https://other.example.test/0")
        result = evaluate(rows + [duplicate], sources=[source(), source(source_id="other-supplier")])
        self.assertEqual(result["sample"]["unit_count"], 24)
        self.assertEqual(result["excluded_counts"]["duplicate_unit"], 1)
        duplicate["rent_yen"] += 1000
        conflict = evaluate(rows + [duplicate], sources=[source(), source(source_id="other-supplier")])
        self.assertEqual(conflict["sample"]["unit_count"], 23)
        self.assertEqual(conflict["excluded_counts"]["conflicting_active_offers"], 2)
        self.assertNotIn(rows[0]["unit_id"], {row["unit_id"] for row in conflict["comparables"]})

    def test_active_closed_conflict_is_not_chosen_by_download_time(self):
        rows = observations()
        closed = deepcopy(rows[0])
        closed.update(source_id="other-supplier", status="closed", received_at=timestamp(-3))
        result = evaluate(rows + [closed], sources=[source(), source(source_id="other-supplier")])
        self.assertEqual(result["excluded_counts"]["conflicting_status"], 2)
        self.assertEqual(result["sample"]["unit_count"], 23)

    def test_latest_history_wins_without_restoring_old_active_or_fresh_rows(self):
        for changes, excluded_code in (
            ({"status": "closed"}, "inactive_observation"),
            ({"status_verified_at": timestamp(-25)}, "stale_observation"),
        ):
            with self.subTest(changes=changes):
                rows = observations()
                latest = deepcopy(rows[0])
                latest.update(observation_id="newer-observation", received_at=timestamp(-0.5), **changes)
                result = evaluate(rows + [latest])
                self.assertEqual(result["excluded_counts"]["superseded_observation"], 1)
                self.assertEqual(result["excluded_counts"][excluded_code], 1)
                self.assertEqual(result["sample"]["unit_count"], 23)
                self.assertNotIn(rows[0]["unit_id"], {row["unit_id"] for row in result["comparables"]})
        rows = observations()
        updated = deepcopy(rows[0])
        updated.update(observation_id="new-price", received_at=timestamp(-0.5), rent_yen=90000)
        result = evaluate(rows + [updated])
        self.assertEqual(result["sample"]["unit_count"], 24)
        self.assertEqual(next(row["rent_yen"] for row in result["comparables"] if row["unit_id"] == rows[0]["unit_id"]), 90000)

    def test_same_time_conflicting_observations_quarantine_unit(self):
        rows = observations()
        conflict = deepcopy(rows[0])
        conflict.update(observation_id="conflicting-observation", rent_yen=123456)
        other_listing = deepcopy(rows[0])
        other_listing.update(listing_id="other-listing", observation_id="other-observation")
        result = evaluate(rows + [conflict, other_listing])
        self.assertEqual(result["sample"]["unit_count"], 23)
        self.assertEqual(result["excluded_counts"]["ambiguous_latest_observation"], 2)
        self.assertEqual(result["excluded_counts"]["ambiguous_canonical_unit"], 1)

    def test_freshness_uses_status_verification_and_keeps_exact_boundary(self):
        stale = evaluate(observations(status_verified_at=timestamp(-24.0001), received_at=timestamp(-0.1)))
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["excluded_counts"]["stale_observation"], 24)
        self.assert_no_price(stale)
        self.assertEqual(evaluate(observations(status_verified_at=timestamp(-24)))["status"], "comparable")
        unknown = evaluate(observations(status_verified_at=None))
        self.assertEqual(unknown["excluded_counts"]["status_unverified"], 24)
        self.assert_no_price(unknown)
        for field in ("source_updated_at", "received_at", "status_verified_at"):
            result = evaluate(observations(**{field: timestamp(0.01)}))
            self.assertEqual(result["excluded_counts"]["invalid_observation"], 24)
            self.assert_no_price(result)

    def test_status_unverified_and_nonactive_never_compare(self):
        for status in ("pending", "closed", "unknown"):
            result = evaluate(observations(status=status))
            self.assertEqual(result["excluded_counts"]["inactive_observation"], 24)
            self.assert_no_price(result)

    def test_unrelated_stale_rows_cannot_mislabel_missing_comparables(self):
        for changes in ({"floor": 1}, {"area_sqm": 35}, {"built_year": 2026}, {"mgmt_fee_yen": None}):
            with self.subTest(changes=changes):
                result = evaluate(observations(status_verified_at=timestamp(-25), **changes))
                self.assertEqual(result["status"], "insufficient")
                self.assertNotIn("freshness_expired", result["reason_codes"])
                self.assert_no_price(result)

    def test_management_is_part_of_price_and_zero_total_is_invalid(self):
        result = evaluate(observations(rent_yen=50000, mgmt_fee_yen=10000))
        self.assertEqual(result["benchmark_yen"], 60000)
        zero_fee = evaluate(observations(rent_yen=50000, mgmt_fee_yen=0))
        self.assertEqual(zero_fee["benchmark_yen"], 50000)
        invalid = evaluate(observations(rent_yen=0, mgmt_fee_yen=0))
        self.assertEqual(invalid["excluded_counts"]["invalid_observation"], 24)
        self.assert_no_price(invalid)
        with self.assertRaises(ValidationError):
            evaluate(target=subject(rent_yen=0, mgmt_fee_yen=0))

    def test_building_balanced_quantiles_and_neff_share_the_same_weights(self):
        rows = [observation(index, building_id="large-building", rent_yen=50000, mgmt_fee_yen=0) for index in range(10)]
        rows += [observation(index + 10, building_id=f"independent-{index}", rent_yen=60000 + index * 10000, mgmt_fee_yen=0) for index in range(11)]
        result = evaluate(rows)
        self.assertEqual(result["status"], "comparable")
        self.assertEqual(result["benchmark_yen"], 105000)
        self.assertEqual(result["distribution"]["q25_yen"], 75000)
        self.assertEqual(result["distribution"]["q75_yen"], 135000)
        weights_by_building = defaultdict(float)
        for row in result["comparables"]:
            weights_by_building[row["building_id"]] += row["weight"]
        self.assertAlmostEqual(sum(weights_by_building.values()), 1)
        for weight in weights_by_building.values():
            self.assertAlmostEqual(weight, 1 / 12)
        effective_n = 1 / sum(row["weight"] ** 2 for row in result["comparables"])
        self.assertAlmostEqual(result["sample"]["effective_n"], effective_n, places=5)
        self.assertAlmostEqual(effective_n, float(Fraction(1440, 111)))

    def test_one_building_is_not_independent_evidence_and_small_sample_nulls(self):
        concentrated = evaluate(observations(100, building_id="only-one-building"))
        self.assertEqual(concentrated["sample"]["building_count"], 1)
        self.assertEqual(concentrated["status"], "insufficient")
        self.assert_no_price(concentrated)
        limited = evaluate(observations(10))
        self.assertEqual(limited["status"], "limited")
        self.assertIsNone(limited["judgment"])
        self.assertIsNotNone(limited["benchmark_yen"])
        self.assert_no_price(evaluate(observations(9)))

    def test_effective_n_gate_cannot_be_replaced_by_raw_counts(self):
        rows = [observation(index, building_id="large-building") for index in range(20)]
        rows += [observation(index + 20, building_id=f"other-{index}") for index in range(9)]
        result = evaluate(rows)
        self.assertEqual(result["sample"]["unit_count"], 29)
        self.assertEqual(result["sample"]["building_count"], 10)
        self.assertLess(result["sample"]["effective_n"], 12)
        self.assertEqual(result["status"], "limited")
        self.assertIsNone(result["judgment"])

    def test_source_rights_expiry_and_metadata_conflict_disable_usage(self):
        for metadata in (
            [], [source(rights={"comparison": True, "storage": True, "display": False, "expires_at": timestamp(48)})],
            [source(rights={"comparison": True, "storage": True, "display": True, "expires_at": timestamp(0)})],
            [source(complete=False)], [source(), source(display_name="Conflicting metadata")],
        ):
            result = evaluate(sources=metadata)
            self.assertEqual(result["excluded_counts"]["source_not_authorized"], 24)
            self.assert_no_price(result)
        missing = evaluate(sources=None)
        self.assertIn("source_rights_unverified", missing["reason_codes"])
        self.assertIsNone(missing["sources"][0]["display_name"])
        self.assertIsNone(missing["sources"][0]["independent_source_id"])

    def test_synthetic_rows_do_not_mix_with_market_even_with_valid_rights(self):
        rows = observations()
        rows += [observation(50, data_kind="synthetic", public_url=None)]
        result = evaluate(rows)
        self.assertEqual(result["sample"]["unit_count"], 24)
        self.assertEqual(result["excluded_counts"]["data_kind_mismatch"], 1)
        only_demo = evaluate(observations(data_kind="synthetic", public_url=None))
        self.assert_no_price(only_demo)

    def test_nearest_integer_rounding_and_five_percent_judgment_boundary(self):
        rows = observations(20, rent_yen=100000, mgmt_fee_yen=0)
        self.assertEqual(evaluate(rows, subject(rent_yen=105000, mgmt_fee_yen=0))["judgment"], "higher")
        self.assertEqual(evaluate(rows, subject(rent_yen=104999, mgmt_fee_yen=0))["judgment"], "similar")
        self.assertEqual(evaluate(rows, subject(rent_yen=95000, mgmt_fee_yen=0))["judgment"], "lower")
        self.assertEqual(evaluate(rows, subject(rent_yen=95001, mgmt_fee_yen=0))["judgment"], "similar")
        for index, row in enumerate(rows):
            row["rent_yen"] = 100000 + int(index >= 10)
        result = evaluate(rows)
        self.assertEqual(result["benchmark_yen"], 100001)
        self.assertEqual(result["delta_yen"], result["subject_price_yen"] - result["benchmark_yen"])

    def test_result_and_assessment_id_are_order_invariant_and_input_sensitive(self):
        rows = observations()
        original = evaluate(rows)
        shuffled = deepcopy(rows)
        random.Random(24).shuffle(shuffled)
        self.assertEqual(original, evaluate(shuffled))
        self.assertNotEqual(original["assessment_id"], evaluate(rows, now=NOW + timedelta(seconds=1))["assessment_id"])
        self.assertNotEqual(original["assessment_id"], evaluate(rows, snapshot_version="fixture-v2")["assessment_id"])
        self.assertNotEqual(original["assessment_id"], evaluate(rows, validated_segments=set())["assessment_id"])
        changed = deepcopy(rows)
        changed[0]["rent_yen"] += 1
        self.assertNotEqual(original["assessment_id"], evaluate(changed)["assessment_id"])
        self.assertNotEqual(original["assessment_id"], evaluate(rows, sources=[source(display_name="Changed public name")])["assessment_id"])
        prior = POLICY["meaningful_delta_fraction"]
        try:
            POLICY["meaningful_delta_fraction"] = 0.06
            self.assertNotEqual(original["assessment_id"], evaluate(rows)["assessment_id"])
        finally:
            POLICY["meaningful_delta_fraction"] = prior

    def test_new_building_does_not_mix_with_age_two_even_inside_strict_age(self):
        rows = observations(built_year=2024)
        result = evaluate(rows, subject(built_year=2025))
        self.assertEqual(result["excluded_counts"]["new_build_boundary"], 24)
        self.assert_no_price(result)

    def test_tiny_area_relaxation_does_not_shrink_initial_one_square_meter(self):
        result = evaluate(observations(10, area_sqm=5.9), subject(area_sqm=5))
        counts = [step["candidate_count"] for step in result["matching"]["steps"]]
        self.assertEqual(counts, [10, 10, 10, 10])
        self.assertNotIn("area_sqm", result["matching"]["relaxed_fields"])


if __name__ == "__main__":
    unittest.main()
