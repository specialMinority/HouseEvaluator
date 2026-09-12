"""Keep unlike building profiles visible without pooling their prices."""

from copy import deepcopy
import math
import unittest

from backend.v2.personal import compare, validate_listing
from backend.tests.test_v2_personal import NOW, listing, sample, subject


def reference_rows(result):
    return [row for group in result['reference_groups'] for row in group['comparables']]


class PersonalBuildingCohortTest(unittest.TestCase):
    def compare(self, rows, target=None):
        result = compare({'subject': target or subject(), 'listings': rows}, now=NOW)
        self.assertEqual(result['input_count'], result['matched_count'] + result['reference_count'] + len(result['excluded']))
        self.assertEqual(result['reference_count'], sum(group['sample_count'] for group in result['reference_groups']))
        ids = [row['id'] for row in result['comparables']] + [row['id'] for row in reference_rows(result)] + [row['id'] for row in result['excluded']]
        self.assertEqual(len(ids), len(set(ids)))
        return result

    def test_three_storey_apartments_do_not_lower_eight_storey_mansion_primary_median(self):
        mansion = [listing(i, rent_yen=100000) for i in range(1, 4)]
        apartment = [listing(i, building_type='apartment', building_floors=3, floor=2,
                             structure='wood', elevator=False, rent_yen=50000) for i in range(4, 7)]
        alone = self.compare(mansion)
        together = self.compare(mansion + apartment)
        self.assertEqual(together['matched_count'], 3)
        self.assertEqual(together['reference_count'], 3)
        self.assertEqual(together['summary'], alone['summary'])
        self.assertEqual(together['summary']['median_yen'], 105000)
        self.assertEqual(together['reference_groups'][0]['summary']['median_yen'], 55000)
        self.assertTrue(all(row['building_type'] == 'mansion' for row in together['comparables']))
        self.assertTrue(all(row['building_type'] == 'apartment' for row in reference_rows(together)))

    def test_each_known_building_profile_difference_is_a_separate_reference(self):
        cases = [({'building_type': 'apartment'}, 'different'),
                 ({'building_floors': 3, 'floor': 2}, 'different'),
                 ({'structure': 'wood'}, 'different'),
                 ({'elevator': False}, 'different'),
                 ({'floor': 8}, 'different'),
                 ({'floor': 1}, 'different')]
        for changes, relation in cases:
            with self.subTest(changes=changes):
                result = self.compare(sample(**changes))
                self.assertEqual(result['matched_count'], 0)
                self.assertIsNone(result['summary'])
                self.assertEqual(result['reference_count'], 3)
                self.assertEqual(len(result['reference_groups']), 1)
                group = result['reference_groups'][0]
                self.assertEqual(group['relation'], relation)
                self.assertTrue(group['reasons'])
                self.assertNotIn('delta_pct', group['summary'])

    def test_rc_src_can_use_the_named_family_step_without_joining_steel_or_wood(self):
        concrete = sample(structure='src')
        steel = [listing(i, structure='steel', rent_yen=10000) for i in range(4, 7)]
        result = self.compare(concrete + steel)
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['reference_count'], 3)
        self.assertEqual([step['id'] for step in result['steps'] if step['selected']], ['structure_family'])
        self.assertTrue(all(row['structure'] == 'src' for row in result['comparables']))
        self.assertEqual(result['summary']['median_yen'], 90000)

    def test_total_storey_count_and_occupied_storey_are_independent(self):
        target = subject(building_floors=6, floor=3)
        near_height = self.compare(sample(building_floors=8, floor=3), target)
        self.assertEqual(near_height['matched_count'], 3)
        distant_height = self.compare(sample(building_floors=9, floor=3), target)
        self.assertEqual(distant_height['matched_count'], 0)
        self.assertEqual(distant_height['reference_count'], 3)
        self.assertTrue(all(row['floor'] == 3 and row['building_floors'] == 9 for row in reference_rows(distant_height)))
        top = self.compare(sample(building_floors=8, floor=8))
        self.assertEqual(top['matched_count'], 0)
        self.assertEqual(top['reference_count'], 3)

    def test_a_one_storey_difference_cannot_cross_a_declared_building_height_band(self):
        for subject_height, other_height in ((3, 4), (5, 6), (10, 11), (19, 20)):
            with self.subTest(subject_height=subject_height, other_height=other_height):
                result = self.compare(sample(building_floors=other_height, floor=2),
                                      subject(building_floors=subject_height, floor=2))
                self.assertEqual(result['matched_count'], 0)
                self.assertEqual(result['reference_count'], 3)
                self.assertIsNone(result['summary'])
                self.assertTrue(any('총층수' in reason for reason in result['reference_groups'][0]['reasons']))

    def test_middle_storey_gap_relaxes_to_four_but_not_five(self):
        target = subject(building_floors=10, floor=2)
        near = self.compare(sample(building_floors=10, floor=6), target)
        self.assertEqual(near['matched_count'], 3)
        self.assertEqual(near['reference_count'], 0)
        self.assertEqual([step['id'] for step in near['steps'] if step['selected']], ['floor'])
        distant = self.compare(sample(building_floors=10, floor=7), target)
        self.assertEqual(distant['matched_count'], 0)
        self.assertEqual(distant['reference_count'], 3)
        self.assertTrue(any('거주 층 차이' in reason for reason in distant['reference_groups'][0]['reasons']))

    def test_unknown_building_type_height_structure_or_position_is_not_promoted_to_primary(self):
        for field in ('building_type', 'building_floors', 'structure', 'floor'):
            with self.subTest(field=field):
                result = self.compare(sample(**{field: None}))
                self.assertEqual(result['matched_count'], 0)
                self.assertEqual(result['reference_count'], 3)
                group = result['reference_groups'][0]
                self.assertEqual(group['relation'], 'unverified')
                self.assertIn(field, group['unverified_fields'])
                self.assertTrue(all(row[field] is None for row in group['comparables']))

    def test_unknown_subject_metadata_never_pools_known_different_building_profiles(self):
        target = subject(building_type=None, building_floors=None, structure=None)
        rows = sample() + [listing(i, building_type='apartment', building_floors=3, floor=2,
                                  structure='wood', elevator=False, rent_yen=50000) for i in range(4, 7)]
        result = self.compare(rows, target)
        self.assertEqual(result['matched_count'], 0)
        self.assertIsNone(result['summary'])
        self.assertIsNone(result['price_range'])
        self.assertEqual(result['reference_count'], 6)
        self.assertEqual(len(result['reference_groups']), 2)
        self.assertTrue(all(group['summary'] is not None and 'delta_pct' not in group['summary'] for group in result['reference_groups']))

    def test_elevator_true_false_and_unknown_are_never_mixed(self):
        rows = sample(elevator=None) + [listing(i, elevator=True) for i in range(4, 7)] + [listing(i, elevator=False) for i in range(7, 10)]
        result = self.compare(rows, subject(elevator=None))
        self.assertEqual(result['matched_count'], 3)
        self.assertTrue(all(row['elevator'] is None for row in result['comparables']))
        self.assertIn('elevator', result['unverified_fields'])
        self.assertEqual(len(result['reference_groups']), 2)
        for group in result['reference_groups']:
            self.assertEqual(len({row['elevator'] for row in group['comparables']}), 1)

    def test_reference_group_prices_use_a_single_basis_and_never_include_target_delta(self):
        rows = [listing(1, building_type='apartment', rent_yen=80000, mgmt_fee_yen=None),
                listing(2, building_type='apartment', rent_yen=90000, mgmt_fee_yen=20000),
                listing(3, building_type='apartment', rent_yen=100000, mgmt_fee_yen=30000)]
        result = self.compare(rows)
        group = result['reference_groups'][0]
        self.assertEqual(group['price_basis'], 'rent')
        self.assertEqual(group['price_range'], {'min_yen': 80000, 'max_yen': 100000, 'sample_count': 3})
        self.assertEqual(group['summary']['median_yen'], 90000)
        self.assertNotIn('delta_pct', group['summary'])
        self.assertNotIn('delta_pct', group)
        self.assertNotIn('subject_comparison_yen', group)
        self.assertTrue(all(row['comparison_price_yen'] == row['rent_yen'] for row in group['comparables']))
        self.assertIsNone(next(row for row in group['comparables'] if row['id'] == 'listing-1')['mgmt_fee_yen'])

    def test_one_reference_row_has_a_range_without_a_fake_median(self):
        result = self.compare([listing(1, building_type='apartment')])
        group = result['reference_groups'][0]
        self.assertEqual(group['sample_count'], 1)
        self.assertEqual(group['price_range'], {'min_yen': 90000, 'max_yen': 90000, 'sample_count': 1})
        self.assertIsNone(group['summary'])
        self.assertIsNone(result['summary'])
        self.assertIsNone(result['judgment'])

    def test_unidentified_reference_buildings_do_not_manufacture_a_third_independent_group(self):
        rows = [listing(1, building_type='apartment'), listing(2, building_type='apartment'),
                listing(3, building_type='apartment', address=None, building_name=None)]
        result = self.compare(rows)
        group = result['reference_groups'][0]
        self.assertEqual(group['sample_count'], 3)
        self.assertEqual(group['building_groups'], 2)
        self.assertEqual(group['price_range']['sample_count'], 3)
        self.assertIsNone(group['summary'])

    def test_conflicting_metadata_for_the_same_ad_is_rejected_before_profile_separation(self):
        for field, different in (('building_type', 'apartment'), ('building_floors', 7), ('elevator', False)):
            with self.subTest(field=field):
                original = listing(4)
                conflict = deepcopy(original)
                conflict[field] = different
                result = self.compare(sample() + [original, conflict])
                self.assertEqual(result['matched_count'], 3)
                self.assertEqual(result['reference_count'], 0)
                self.assertEqual(len(result['excluded']), 2)
                self.assertTrue(all('충돌' in row['reason'] for row in result['excluded']))

    def test_nearby_hard_location_contract_and_basement_exclusions_apply_to_reference_groups(self):
        rows = [listing(1, building_type='apartment', station_name='新宿三丁目'),
                listing(2, building_type='apartment', municipality='渋谷区'),
                listing(3, building_type='apartment', contract_type='fixed_term'),
                listing(4, building_type='apartment', floor=-1),
                listing(5, building_type='apartment', area_sqm=30.1),
                listing(6, building_type='apartment', walk_min=16.1)]
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 0)
        self.assertEqual(len(result['excluded']), 6)

    def test_changing_reference_prices_does_not_change_primary_or_profile_assignment(self):
        rows = sample() + [listing(i, building_type='apartment') for i in range(4, 7)]
        before = self.compare(rows)
        changed = deepcopy(rows)
        for row, value in zip(changed[3:], (1000, 9000000, 120000)):
            row['rent_yen'] = value
        after = self.compare(changed)
        self.assertEqual(before['summary'], after['summary'])
        self.assertEqual([group['id'] for group in before['reference_groups']], [group['id'] for group in after['reference_groups']])
        self.assertEqual([[row['id'] for row in group['comparables']] for group in before['reference_groups']],
                         [[row['id'] for row in group['comparables']] for group in after['reference_groups']])

    def test_no_building_type_or_elevator_is_inferred_from_name_height_or_structure(self):
        row = validate_listing(subject(title='Test Mansion', building_name='マンション',
                                       building_type=None, building_floors=8, elevator=None), now=NOW)
        self.assertIsNone(row['building_type'])
        self.assertIsNone(row['elevator'])
        self.assertEqual(row['building_floors'], 8)

    def test_building_metadata_types_ranges_and_occupied_floor_consistency(self):
        for field, values in {
            'building_type': ('wood', 'house', False, 0),
            'building_floors': (0, -1, 101, 3.5, True, math.nan, '8'),
            'elevator': (0, 1, 'false', [], {}),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_listing(subject(**{field: value}), now=NOW)
        with self.assertRaises(ValueError):
            validate_listing(subject(floor=4, building_floors=3), now=NOW)
        self.assertEqual(validate_listing(subject(floor=-1, building_floors=3), now=NOW)['building_floors'], 3)


if __name__ == '__main__':
    unittest.main()
