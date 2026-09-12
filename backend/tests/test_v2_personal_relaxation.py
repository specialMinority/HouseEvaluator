"""Regression cases for useful, explicitly broader personal sample comparisons.

The 60-row case reproduces the user's reported condition/missingness pattern.
Addresses, identifiers and building groups below are synthetic test metadata,
not independently verified identities for the advertisements in the attachment.
"""

from copy import deepcopy
from datetime import timedelta
import unittest

from backend.v2.personal import compare
from backend.tests.test_v2_personal import NOW, listing, sample, subject


class PersonalRelaxationTest(unittest.TestCase):
    def compare(self, rows, target=None):
        result = compare({'subject': target or subject(), 'listings': rows}, now=NOW)
        self.assertEqual(result['input_count'], result['matched_count'] + result['reference_count'] + len(result['excluded']))
        return result

    def test_each_extended_step_is_named_and_preserves_the_strict_count(self):
        cases = [({'built_year': None}, 'unverified', 'partial'),
                 ({'floor': 6}, 'floor', 'broad'),
                 ({'bathroom_separate': False}, 'bathroom', 'broad'),
                 ({'built_year': 2007}, 'year_wide', 'broad'),
                 ({'walk_min': 15}, 'walk_wide', 'broad'),
                 ({'area_sqm': 29}, 'area_wide', 'broad'),
                 ({'structure': 'src'}, 'structure_family', 'broad'),
                 ({'built_year': 1980}, 'year_any', 'broad'),
                 ({'built_year': NOW.year}, 'year_any', 'broad')]
        for changes, expected_step, expected_level in cases:
            with self.subTest(changes=changes):
                result = self.compare(sample(**changes))
                self.assertEqual(result['strict_count'], 0)
                self.assertEqual(result['status'], 'reference')
                self.assertEqual(result['steps'][-1]['id'], expected_step)
                self.assertEqual(result['comparison_level'], expected_level)
                self.assertIsNone(result['judgment'])
                self.assertTrue(all(row['differences'] for row in result['comparables']))

    def test_unknown_permission_does_not_erase_known_structure_contradictions(self):
        rows = sample(built_year=None, structure='wood')
        result = self.compare(rows)
        unknown_step = next(step for step in result['steps'] if step['id'] == 'unverified')
        self.assertEqual(unknown_step['rent_count'], 0)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 3)
        self.assertIsNone(result['summary'])
        group = result['reference_groups'][0]
        self.assertIn('built_year', group['unverified_fields'])
        self.assertTrue(all(row['structure'] == 'wood' and row['built_year'] is None for row in group['comparables']))
        self.assertNotIn('delta_pct', group['summary'])

    def test_unverified_subject_data_is_kept_unknown_instead_of_blocking_all_samples(self):
        target = subject(structure=None, built_year=None, walk_min=None, floor=None)
        result = self.compare(sample(), target)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 3)
        self.assertIsNone(result['summary'])
        group = result['reference_groups'][0]
        self.assertEqual(group['relation'], 'unverified')
        self.assertTrue({'structure', 'built_year', 'walk_min', 'floor'}.issubset(group['unverified_fields']))
        self.assertTrue(all(target[field] is None for field in ('structure', 'built_year', 'walk_min', 'floor')))

    def test_matching_rent_basis_is_preferred_to_broader_total_price_samples(self):
        strict = [listing(1, rent_yen=80000, mgmt_fee_yen=None),
                  listing(2, rent_yen=90000, mgmt_fee_yen=5000),
                  listing(3, rent_yen=100000, mgmt_fee_yen=8000)]
        wider = [listing(i, area_sqm=27, rent_yen=30000) for i in range(4, 7)]
        result = self.compare(strict + wider)
        self.assertEqual(result['price_basis'], 'rent')
        self.assertEqual(result['steps'][-1]['id'], 'exact_rent')
        self.assertEqual(result['subject_total_yen'], 90000)
        self.assertEqual(result['subject_comparison_yen'], 85000)
        self.assertEqual(result['summary']['median_yen'], 90000)
        self.assertEqual(result['summary']['delta_pct'], -5.6)
        self.assertEqual(result['fee_coverage'], {'known_count': 2, 'missing_count': 1})
        self.assertEqual({row['id'] for row in result['comparables']}, {'listing-1', 'listing-2', 'listing-3'})
        self.assertTrue(all(row['comparison_price_yen'] == row['rent_yen'] for row in result['comparables']))
        self.assertIsNone(next(row for row in result['comparables'] if row['id'] == 'listing-1')['monthly_total_yen'])

    def test_sufficient_total_price_samples_take_priority_and_do_not_zero_unknown_fees(self):
        rows = sample() + [listing(4, rent_yen=1000, mgmt_fee_yen=None)]
        result = self.compare(rows)
        self.assertEqual(result['price_basis'], 'total')
        self.assertEqual(result['steps'][-1]['id'], 'exact')
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['summary']['median_yen'], 90000)
        self.assertEqual(result['fee_coverage'], {'known_count': 3, 'missing_count': 0})
        self.assertTrue(all(row['comparison_price_yen'] == row['rent_yen'] + row['mgmt_fee_yen'] for row in result['comparables']))
        self.assertEqual(result['excluded'][0]['id'], 'listing-4')
        self.assertIn('관리비 미확인', result['excluded'][0]['reason'])

    def test_unknown_subject_fee_makes_every_comparison_use_rent(self):
        result = self.compare(sample(mgmt_fee_yen=25000), subject(mgmt_fee_yen=None))
        self.assertEqual(result['price_basis'], 'rent')
        self.assertIsNone(result['subject_total_yen'])
        self.assertEqual(result['subject_comparison_yen'], 85000)
        self.assertEqual(result['summary']['median_yen'], 85000)
        self.assertEqual(result['summary']['delta_pct'], 0)
        self.assertTrue(all(row['comparison_price_yen'] == 85000 for row in result['comparables']))
        self.assertTrue(any('총 주거비 차이가 아닙니다' in warning for warning in result['warnings']))

    def test_one_or_two_nearby_rows_return_actual_ranges_without_a_median_or_judgment(self):
        for count in (1, 2):
            with self.subTest(count=count):
                rows = [listing(i, rent_yen=70000 + i * 10000) for i in range(1, count + 1)]
                result = self.compare(rows)
                self.assertEqual(result['matched_count'], count)
                self.assertEqual(result['status'], 'insufficient')
                self.assertIsNone(result['summary'])
                self.assertIsNone(result['judgment'])
                self.assertEqual(result['price_range'], {'min_yen': 85000, 'max_yen': 75000 + count * 10000, 'sample_count': count})
        mixed = self.compare([listing(1, rent_yen=80000, mgmt_fee_yen=None), listing(2, rent_yen=90000, mgmt_fee_yen=30000)])
        self.assertEqual(mixed['price_basis'], 'rent')
        self.assertEqual(mixed['price_range'], {'min_yen': 80000, 'max_yen': 90000, 'sample_count': 2})

    def test_few_strict_rows_keep_the_narrowest_useful_selected_policy(self):
        for count in (1, 2):
            with self.subTest(count=count):
                result = self.compare([listing(i) for i in range(1, count + 1)])
                self.assertEqual(result['comparison_level'], 'exact')
                self.assertEqual(result['relaxed_fields'], [])
                self.assertEqual([step['id'] for step in result['steps'] if step['selected']], ['exact'])
                self.assertGreater(len(result['steps']), 1)
                self.assertIsNone(result['summary'])
                self.assertFalse(any('일부 조건을 완화했습니다' in warning for warning in result['warnings']))
        result = self.compare([listing(1), listing(2, floor=6)])
        self.assertEqual([step['id'] for step in result['steps'] if step['selected']], ['floor'])
        self.assertEqual(result['matched_count'], 2)
        self.assertEqual(result['comparison_level'], 'broad')
        self.assertNotIn('bathroom_separate', result['relaxed_fields'])

    def test_condition_notes_show_actual_ranges_and_missingness_without_inventing_values(self):
        unknown = {'built_year': None, 'walk_min': None}
        rows = [listing(1), listing(2, **unknown), listing(3, built_year=2002, walk_min=16, floor=5)]
        result = self.compare(rows, subject(**unknown))
        notes = result['condition_notes']
        self.assertEqual(len(notes), 7)
        self.assertIn('맨션 3개', notes[0])
        self.assertIn('RC 3개', notes[1])
        self.assertIn('8~8층', notes[2])
        self.assertIn('2002~2016년', notes[3])
        self.assertIn('6~16분', notes[4])
        self.assertIn('3~5층', notes[5])
        self.assertIn('있음 3개', notes[6])
        self.assertTrue(all('대상 미상' in note and '미상 1개' in note for note in notes[3:5]))
        profile_unknown = dict(unknown, structure=None, floor=None, building_type=None, building_floors=None, elevator=None)
        all_unknown = self.compare(sample(**profile_unknown), subject(**profile_unknown))
        self.assertEqual(all_unknown['condition_notes'], [])
        unknown_notes = all_unknown['reference_groups'][0]['condition_notes']
        self.assertEqual(len(unknown_notes), 7)
        self.assertTrue(all('대상 미상' in note and 'None' not in note and 'nan' not in note for note in unknown_notes))
        self.assertEqual(self.compare([])['condition_notes'], [])

    def test_unknown_buildings_still_get_a_range_without_manufactured_independence(self):
        result = self.compare(sample(address=None, building_name=None))
        self.assertEqual(result['matched_count'], 3)
        self.assertIsNone(result['summary'])
        self.assertEqual(result['price_range']['sample_count'], 3)
        self.assertTrue(any('독립된 건물' in warning for warning in result['warnings']))
        self.assertIsNone(result['judgment'])

    def test_location_contract_and_basement_barriers_survive_every_relaxation(self):
        cases = [('city', 'osaka'), ('municipality', '渋谷区'), ('station_name', '新宿三丁目'),
                 ('layout', '1DK'), ('contract_type', 'fixed_term'), ('property_type', 'shared'),
                 ('furnished', True), ('floor', -1), ('area_sqm', 30.1), ('walk_min', 16.1)]
        for field, value in cases:
            with self.subTest(field=field):
                result = self.compare(sample(**{field: value}))
                self.assertEqual(result['matched_count'], 0)
                self.assertIsNone(result['price_range'])
                self.assertIsNone(result['summary'])

    def test_price_changes_never_change_broader_candidate_order_or_relaxation(self):
        original = sample(structure='wood', built_year=1980, area_sqm=29, walk_min=15, floor=1)
        changed = deepcopy(original)
        for row, price in zip(changed, (1000, 9000000, 50000)):
            row['rent_yen'] = price
        before = self.compare(original, subject(rent_yen=10000))
        after = self.compare(changed, subject(rent_yen=9000000))
        self.assertEqual(before['steps'], after['steps'])
        self.assertEqual(before['matched_count'], 0)
        self.assertEqual(after['matched_count'], 0)
        old_group, new_group = before['reference_groups'][0], after['reference_groups'][0]
        self.assertEqual(old_group['id'], new_group['id'])
        self.assertEqual([row['id'] for row in old_group['comparables']], [row['id'] for row in new_group['comparables']])
        self.assertNotEqual(old_group['summary']['median_yen'], new_group['summary']['median_yen'])
        self.assertNotIn('delta_pct', new_group['summary'])

    def test_duplicate_with_missing_fields_retains_the_more_complete_original_without_merging(self):
        incomplete = listing(1, structure=None, built_year=None, mgmt_fee_yen=None)
        complete = listing(1)
        complete['source_url'] += '?tracking=second'
        result = self.compare([incomplete, complete, listing(2), listing(3)])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['price_basis'], 'total')
        self.assertEqual(result['excluded'][0]['id'], 'listing-1')
        kept = next(row for row in result['comparables'] if row['id'] == 'listing-2')
        self.assertEqual((kept['structure'], kept['built_year'], kept['mgmt_fee_yen']), ('rc', 2016, 5000))
        first = listing(1, structure=None)
        second = listing(1, built_year=None)
        result = self.compare([first, second, listing(2), listing(3)])
        kept = next(row for group in result['reference_groups'] for row in group['comparables']
                    if row['source_url'] == first['source_url'])
        self.assertIsNone(kept['structure'])
        self.assertEqual(kept['built_year'], 2016)
        self.assertEqual(result['matched_count'], 2)
        self.assertEqual(result['reference_count'], 1)
        self.assertIsNone(result['summary'])

    def test_known_conflicts_self_advertisements_and_stale_rows_remain_excluded(self):
        unknown = listing(10, structure=None)
        conflicting = listing(10, rent_yen=10000)
        stale = listing(11, fetched_at=(NOW - timedelta(days=2)).isoformat())
        self_ad = listing(12, source_url=subject()['source_url'])
        result = self.compare(sample(structure='wood') + [unknown, conflicting, stale, self_ad])
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 3)
        self.assertIsNone(result['summary'])
        self.assertEqual(len(result['excluded']), 4)
        self.assertEqual(sum('충돌' in row['reason'] for row in result['excluded']), 2)
        self.assertTrue(any('24시간' in row['reason'] for row in result['excluded']))
        self.assertTrue(any('같은 URL' in row['reason'] for row in result['excluded']))

    def test_reported_koiwa_sixty_row_pattern_no_longer_discards_all_prices(self):
        target = subject(municipality='江戸川区', station_name='小岩駅', layout='1DK', area_sqm=33.8,
                         rent_yen=105800, mgmt_fee_yen=10000, built_year=2002, walk_min=10,
                         orientation='SE', building_name=None, address=None, source_url=None)
        def row(number, **changes):
            return listing(number, municipality='江戸川区', station_name='小岩', layout='1DK',
                           orientation='SE', **changes)
        rows = [
            row(1, title='Near A', rent_yen=90000, mgmt_fee_yen=5000, area_sqm=34.83,
                structure=None, built_year=2006, walk_min=20, floor=1),
            row(2, title='Near B', rent_yen=91000, mgmt_fee_yen=4000, area_sqm=30.91,
                structure='steel', built_year=2012, walk_min=7, floor=1),
            row(3, title='Near C', rent_yen=102000, mgmt_fee_yen=6000, area_sqm=30.91,
                structure=None, built_year=None, walk_min=19, floor=2),
            row(4, rent_yen=80000, mgmt_fee_yen=None, area_sqm=34.43,
                structure='steel', built_year=1989, walk_min=10, floor=1),
        ]
        rows += [row(i, area_sqm=29.34, structure='wood', built_year=2024, walk_min=15, floor=2) for i in (5, 6)]
        rows += [row(7, area_sqm=30.91, structure='light_steel', built_year=2024, walk_min=19, floor=2)]
        rows += [row(i, area_sqm=27, structure='steel', built_year=1995, walk_min=6, floor=2) for i in range(8, 12)]
        rows += [row(12, area_sqm=25, structure=None, built_year=None, mgmt_fee_yen=None, walk_min=4, floor=1)]
        rows += [row(i, area_sqm=26.2, structure=None, built_year=None, walk_min=8, floor=2 + i % 7,
                     building_name='Synthetic dense building', address='Synthetic dense address') for i in range(13, 40)]
        stations = ['瑞江'] * 6 + ['平井'] * 4 + ['篠崎'] + ['新小岩'] * 3 + ['西葛西'] + ['葛西'] * 3 + ['一之江'] * 3
        for number, station in enumerate(stations, 40):
            item = row(number, area_sqm=33.8, structure=None, built_year=None)
            item['station_name'] = station
            rows.append(item)
        self.assertEqual(len(rows), 60)
        self.assertEqual(sum(item['station_name'] == '小岩' for item in rows), 39)
        self.assertEqual(sum(item['structure'] is None for item in rows), 51)
        self.assertEqual(sum(item['built_year'] is None for item in rows), 50)
        self.assertEqual(sum(item['mgmt_fee_yen'] is None for item in rows), 2)
        result = self.compare(rows, target)
        self.assertEqual(result['strict_count'], 0)
        self.assertEqual(result['matched_count'], 0)
        self.assertIsNone(result['summary'])
        self.assertIsNone(result['price_range'])
        self.assertEqual(result['reference_count'], 7)
        self.assertEqual(len(result['reference_groups']), 5)
        reference_rows = [item for group in result['reference_groups'] for item in group['comparables']]
        self.assertTrue({'Near A', 'Near B', 'Near C'}.issubset(item['title'] for item in reference_rows))
        self.assertTrue(all(item['station_name'] == '小岩' for item in reference_rows))
        for group in result['reference_groups']:
            self.assertNotIn('delta_pct', group)
            self.assertIsNone(group['summary'])
            self.assertEqual(group['price_range']['sample_count'], group['sample_count'])
        self.assertEqual(sum('역명 불일치' in row['reason'] for row in result['excluded']), 21)
        self.assertIsNone(result['judgment'])
        self.assertTrue(any('별도 참고군' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()
