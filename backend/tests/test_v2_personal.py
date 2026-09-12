from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import unittest

from backend.v2.personal import CORE, ENUMS, compare, safe_url, station_key, validate_listing


NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def subject(**changes):
    row = {
        'city': 'tokyo', 'municipality': '新宿区', 'station_name': '新宿',
        'layout': '1K', 'area_sqm': 25, 'structure': 'rc', 'built_year': 2016,
        'walk_min': 6, 'floor': 3, 'rent_yen': 85000, 'mgmt_fee_yen': 5000,
        'orientation': 'S', 'bathroom_separate': True, 'furnished': False,
        'contract_type': 'standard', 'property_type': 'apartment',
        'building_type': 'mansion', 'building_floors': 8, 'elevator': True,
        'title': 'Test subject', 'building_name': 'Subject building',
        'address': '東京都新宿区対象1番',
        'source_url': 'https://suumo.jp/chintai/bc_100/',
        'fetched_at': NOW.isoformat(),
    }
    row.update(changes)
    return row


def listing(number, **changes):
    row = subject(title=f'Test listing {number}', building_name=f'Building {number}',
                  address=f'東京都新宿区比較{number}番',
                  source_url=f'https://suumo.jp/chintai/bc_{200 + number}/')
    row.update(changes)
    return row


def sample(**changes):
    return [listing(i, **changes) for i in range(1, 4)]


class PersonalComparisonTest(unittest.TestCase):
    def compare(self, rows=None, target=None):
        return compare({'subject': target or subject(), 'listings': sample() if rows is None else rows}, now=NOW)

    def test_reference_is_sample_only_and_never_a_licensed_judgment(self):
        result = self.compare([listing(1, rent_yen=75000), listing(2), listing(3, rent_yen=95000)])
        self.assertEqual(result['status'], 'reference')
        self.assertEqual(result['mode'], 'personal')
        self.assertIsNone(result['judgment'])
        self.assertEqual(result['subject_total_yen'], 90000)
        self.assertEqual(result['summary'], {
            'median_yen': 90000, 'q1_yen': 80000, 'q3_yen': 100000,
            'delta_pct': 0, 'sample_count': 3, 'building_groups': 3,
        })
        self.assertIn('시장 전체', result['scope'])
        self.assertEqual(result['steps'][0]['id'], 'exact')
        self.assertTrue(result['steps'][0]['selected'])

    def test_each_relaxation_changes_only_the_declared_condition(self):
        cases = [({}, 'exact'), ({'orientation': 'N'}, 'orientation'),
                 ({'area_sqm': 27}, 'area'), ({'built_year': 2012}, 'year'),
                 ({'walk_min': 10}, 'walk'),
                 ({'orientation': 'N', 'area_sqm': 27, 'built_year': 2012, 'walk_min': 10}, 'walk')]
        steps = ['exact', 'orientation', 'area', 'year', 'walk']
        for changes, selected in cases:
            with self.subTest(changes=changes):
                result = self.compare(sample(**changes))
                self.assertEqual(result['status'], 'reference')
                self.assertEqual([step['id'] for step in result['steps']], steps[:steps.index(selected) + 1])
                self.assertEqual([step['id'] for step in result['steps'] if step['selected']], [selected])
                if selected != 'exact':
                    self.assertTrue(any('완화' in warning for warning in result['warnings']))

    def test_stops_when_stricter_group_is_sufficient(self):
        result = self.compare(sample() + [listing(4, area_sqm=27, rent_yen=10000)])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(len(result['steps']), 1)
        self.assertEqual(result['summary']['median_yen'], 90000)

    def test_target_rent_cannot_change_candidate_selection_or_reference_prices(self):
        rows = [listing(1, rent_yen=50000), listing(2, rent_yen=85000), listing(3, rent_yen=150000)]
        low = self.compare(rows, subject(rent_yen=10000))
        high = self.compare(rows, subject(rent_yen=9000000))
        self.assertEqual([row['id'] for row in low['comparables']], [row['id'] for row in high['comparables']])
        for key in ('median_yen', 'q1_yen', 'q3_yen', 'sample_count', 'building_groups'):
            self.assertEqual(low['summary'][key], high['summary'][key])
        self.assertNotEqual(low['summary']['delta_pct'], high['summary']['delta_pct'])

    def test_dense_building_does_not_dominate_the_weighted_median(self):
        dense = [listing(i, rent_yen=45000, floor=3, area_sqm=24 + i / 20,
                         building_name='Dense building', address='東京都新宿区密集1番')
                 for i in range(1, 21)]
        result = self.compare(dense + [listing(30, rent_yen=95000), listing(31, rent_yen=105000)])
        self.assertEqual(result['status'], 'reference')
        self.assertEqual(result['summary']['sample_count'], 22)
        self.assertEqual(result['summary']['building_groups'], 3)
        self.assertEqual(result['summary']['median_yen'], 100000)

    def test_similar_station_names_never_match_by_substring(self):
        for name in ('西新宿', '新宿三丁目', '南新宿', '新宿西口'):
            with self.subTest(name=name):
                result = self.compare(sample(station_name=name))
                self.assertEqual(result['matched_count'], 0)
                self.assertIsNone(result['summary'])
        self.assertEqual(self.compare(sample(station_name='新宿駅'))['status'], 'reference')
        self.assertEqual(station_key('Ｓｈｉｎｊｕｋｕ駅'), station_key('Shinjuku'))

    def test_city_municipality_and_layout_never_relax(self):
        for field, value in (('city', 'osaka'), ('municipality', '渋谷区'),
                             ('layout', '1DK')):
            with self.subTest(field=field):
                result = self.compare(sample(**{field: value}))
                self.assertEqual(result['matched_count'], 0)
                self.assertIsNone(result['summary'])

    def test_basement_stays_excluded_ground_is_separate_and_new_build_is_a_named_step(self):
        basement = self.compare(sample(floor=-1))
        self.assertEqual(basement['matched_count'], 0)
        self.assertEqual(basement['reference_count'], 0)
        for floor in (0, 1):
            with self.subTest(floor=floor):
                result = self.compare(sample(floor=floor))
                self.assertEqual(result['matched_count'], 0)
                self.assertEqual(result['reference_count'], 3)
                self.assertEqual(result['reference_groups'][0]['relation'], 'different')
                self.assertIsNone(result['summary'])
        self.assertEqual(self.compare(sample(floor=6))['status'], 'reference')
        result = self.compare(sample(built_year=2024), subject(built_year=2025))
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['steps'][-1]['id'], 'year_any')
        self.assertEqual(result['comparison_level'], 'broad')
        self.assertEqual(self.compare(sample(built_year=2026), subject(built_year=2025))['status'], 'reference')

    def test_basic_unknowns_stay_excluded_and_relaxable_unknowns_are_never_imputed(self):
        for field in CORE:
            with self.subTest(field=field):
                result = self.compare(sample(**{field: None}))
                if field in ('structure', 'floor'):
                    self.assertEqual(result['matched_count'], 0)
                    self.assertEqual(result['reference_count'], 3)
                    self.assertIsNone(result['summary'])
                    group = result['reference_groups'][0]
                    self.assertEqual(group['relation'], 'unverified')
                    self.assertIn(field, group['unverified_fields'])
                    self.assertTrue(all(row[field] is None for row in group['comparables']))
                elif field in ('built_year', 'walk_min', 'mgmt_fee_yen'):
                    self.assertEqual(result['matched_count'], 3)
                    self.assertEqual(result['comparison_level'], 'partial')
                    self.assertIn(field, result['unverified_fields'])
                    self.assertTrue(all(row[field] is None for row in result['comparables']))
                    if field == 'mgmt_fee_yen':
                        self.assertEqual(result['price_basis'], 'rent')
                        self.assertTrue(all(row['monthly_total_yen'] is None for row in result['comparables']))
                    else:
                        self.assertEqual(result['steps'][-1]['id'], 'unverified')
                else:
                    self.assertEqual(result['matched_count'], 0)
                    self.assertIsNone(result['summary'])
                    self.assertTrue(all('미확인' in row['reason'] for row in result['excluded']))
        for field in ('structure', 'built_year', 'walk_min', 'floor', 'mgmt_fee_yen'):
            with self.subTest(subject_field=field):
                result = self.compare(target=subject(**{field: None}))
                self.assertIn(field, result['missing_subject_fields'])
                self.assertIn(field, result['unverified_fields'])
                if field in ('structure', 'floor'):
                    self.assertIsNone(result['summary'])
                    self.assertEqual(result['matched_count'], 0)
                    self.assertEqual(result['reference_count'], 3)
                    self.assertIn(field, result['reference_groups'][0]['unverified_fields'])
                else:
                    self.assertEqual(result['status'], 'reference')
                    self.assertEqual(result['comparison_level'], 'partial')
        self.assertIsNone(self.compare(target=subject(mgmt_fee_yen=None))['subject_total_yen'])

    def test_explicit_zero_management_fee_is_valid_but_absence_is_not_zero(self):
        result = self.compare(sample(mgmt_fee_yen=0), subject(mgmt_fee_yen=0))
        self.assertEqual(result['status'], 'reference')
        self.assertEqual(result['summary']['median_yen'], 85000)
        self.assertEqual(result['subject_total_yen'], 85000)

    def test_unknown_optional_fields_warn_and_known_contradictions_stay_excluded(self):
        for field in ('orientation', 'bathroom_separate', 'furnished', 'contract_type', 'property_type'):
            with self.subTest(field=field):
                result = self.compare(sample(**{field: None}))
                self.assertEqual(result['status'], 'reference')
                self.assertTrue(any('완전히 같은 조건' in warning for warning in result['warnings']))
                self.assertTrue(all(any('미확인' in difference for difference in row['differences']) for row in result['comparables']))
        bathroom = self.compare(sample(bathroom_separate=False))
        self.assertEqual(bathroom['steps'][-1]['id'], 'bathroom')
        self.assertEqual(bathroom['comparison_level'], 'broad')
        for field, value in (('furnished', True),
                             ('contract_type', 'fixed_term'), ('property_type', 'shared')):
            with self.subTest(conflicting_field=field):
                self.assertEqual(self.compare(sample(**{field: value}))['matched_count'], 0)

    def test_duplicate_tracking_urls_do_not_increase_sample_count(self):
        rows = sample()
        duplicate = deepcopy(rows[0])
        duplicate['source_url'] += '?utm_source=another#advertisement'
        result = self.compare(rows + [duplicate])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(len(result['excluded']), 1)
        self.assertIn('중복', result['excluded'][0]['reason'])

    def test_query_identified_non_suumo_rooms_are_distinct(self):
        rows = [listing(i, source_url=f'https://example.invalid/listing?id={i}', rent_yen=70000 + i * 10000)
                for i in range(1, 4)]
        result = self.compare(rows)
        self.assertEqual(result['status'], 'reference')
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['excluded'], [])
        for row in rows:
            row['source_url'] = row['source_url'].replace('example.invalid/listing', 'suumo.jp/unknown')
        self.assertEqual(self.compare(rows)['matched_count'], 3)

    def test_price_or_core_conflict_on_same_url_rejects_every_version(self):
        for field, value in (('rent_yen', 10000), ('mgmt_fee_yen', 0), ('structure', 'wood'), ('area_sqm', 27)):
            with self.subTest(field=field):
                first = listing(10)
                conflict = deepcopy(first)
                conflict[field] = value
                result = self.compare(sample() + [first, conflict])
                self.assertEqual(result['matched_count'], 3)
                self.assertEqual(len(result['excluded']), 2)
                self.assertTrue(all('충돌' in row['reason'] for row in result['excluded']))

    def test_same_suspected_room_across_distinct_urls_is_deduplicated(self):
        rows = sample()
        duplicate = deepcopy(rows[0])
        duplicate['source_url'] = 'https://example.invalid/another-ad'
        self.assertEqual(self.compare(rows + [duplicate])['matched_count'], 3)
        duplicate['rent_yen'] += 10000
        result = self.compare(rows + [duplicate])
        self.assertEqual(result['matched_count'], 2)
        self.assertIsNone(result['summary'])
        self.assertTrue(all('충돌' in row['reason'] for row in result['excluded']))

    def test_conflict_rejects_the_entire_transitively_connected_duplicate_group(self):
        first = listing(10)
        second = listing(11, source_url=first['source_url'])
        third = listing(12, building_name=second['building_name'],
                        address=second['address'], rent_yen=45000)
        result = self.compare(sample() + [first, second, third])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(len(result['excluded']), 3)
        self.assertTrue(all('충돌' in row['reason'] for row in result['excluded']))

    def test_subject_url_or_suspected_subject_building_is_never_a_comparable(self):
        same_url = listing(10, source_url=subject()['source_url'] + '?tracking=1')
        same_building = listing(11, building_name=subject()['building_name'],
                                address=subject()['address'], floor=8, rent_yen=10000)
        result = self.compare(sample() + [same_url, same_building])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(len(result['excluded']), 2)
        self.assertTrue(any('같은 URL' in row['reason'] for row in result['excluded']))
        self.assertTrue(any('같은 건물' in row['reason'] for row in result['excluded']))

    def test_self_building_exclusion_also_removes_its_less_complete_url_duplicate(self):
        less_complete = listing(10, building_name=None, address=None)
        same_building = listing(11, source_url=less_complete['source_url'],
                                building_name=subject()['building_name'], address=subject()['address'])
        result = self.compare(sample() + [less_complete, same_building])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(len(result['excluded']), 2)
        self.assertTrue(all('같은 건물' in row['reason'] for row in result['excluded']))

    def test_no_self_identity_and_missing_recency_are_explicitly_warned(self):
        result = self.compare(sample(fetched_at=None), subject(source_url=None, building_name=None, address=None))
        self.assertEqual(result['status'], 'reference')
        self.assertTrue(any('자기 매물' in warning for warning in result['warnings']))
        self.assertTrue(any('최신성' in warning for warning in result['warnings']))

    def test_stale_ads_are_excluded_but_exact_24_hour_boundary_is_accepted(self):
        stale = (NOW - timedelta(hours=24, microseconds=1)).isoformat()
        result = self.compare(sample(fetched_at=stale))
        self.assertEqual(result['matched_count'], 0)
        self.assertTrue(all('24시간' in row['reason'] for row in result['excluded']))
        self.assertEqual(self.compare(sample(fetched_at=(NOW - timedelta(hours=24)).isoformat()))['status'], 'reference')

    def test_unknown_building_group_cannot_supply_the_third_identifiable_building(self):
        unknown = listing(3, building_name=None, address=None)
        result = self.compare([listing(1), listing(2), unknown])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['status'], 'insufficient')
        self.assertIsNone(result['summary'])
        self.assertTrue(any('독립된 건물' in warning for warning in result['warnings']))
        result = self.compare(sample() + [listing(4, building_name=None, address=None)])
        self.assertEqual(result['status'], 'reference')
        self.assertEqual(result['summary']['sample_count'], 4)
        self.assertEqual(result['summary']['building_groups'], 3)

    def test_insufficient_and_empty_samples_do_not_emit_prices_or_judgments(self):
        for rows in ([], [listing(1)], [listing(1), listing(2)], sample(building_name=None, address=None)):
            with self.subTest(size=len(rows)):
                result = self.compare(rows)
                self.assertEqual(result['status'], 'insufficient')
                self.assertIsNone(result['summary'])
                self.assertIsNone(result['judgment'])

    def test_comparison_does_not_mutate_callers_input(self):
        payload = {'subject': subject(), 'listings': sample()}
        original = deepcopy(payload)
        result = compare(payload, now=NOW)
        self.assertEqual(payload, original)
        self.assertTrue(all('_group' not in row for row in result['comparables']))

    def test_numeric_boolean_nonfinite_string_and_out_of_range_inputs_are_rejected(self):
        for field in ('rent_yen', 'mgmt_fee_yen', 'area_sqm', 'built_year', 'walk_min', 'floor'):
            for value in (True, False, math.nan, math.inf, -math.inf, '12', {}, []):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_listing(subject(**{field: value}), now=NOW)
        for field, value in (('rent_yen', 0), ('rent_yen', 12.5), ('mgmt_fee_yen', -1),
                             ('area_sqm', 0), ('built_year', NOW.year + 1),
                             ('built_year', 2010.5), ('walk_min', 181), ('floor', -11), ('floor', 2.5)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_listing(subject(**{field: value}), now=NOW)

    def test_false_and_zero_enums_are_invalid_instead_of_becoming_unknown(self):
        for field in ENUMS:
            for value in (False, 0, [], {}, 'unsupported'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_listing(subject(**{field: value}), now=NOW)

    def test_input_shapes_and_timestamps_are_bounded(self):
        for payload in (None, [], {}, {'subject': subject(), 'listings': [] , 'extra': True},
                        {'subject': subject(), 'listings': {}},
                        {'subject': subject(), 'listings': [listing(i) for i in range(61)]}):
            with self.subTest(payload_type=type(payload)), self.assertRaises(ValueError):
                compare(payload, now=NOW)
        for stamp in (True, 12, '', '2026-09-13T10:00:00', (NOW + timedelta(seconds=1)).isoformat()):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_listing(subject(fetched_at=stamp), now=NOW)
        for field in ('station_name', 'municipality', 'address', 'building_name'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_listing(subject(**{field: 'a' * 241}), now=NOW)

    def test_source_links_reject_script_credentials_controls_and_browser_backslashes(self):
        for value in ('javascript:alert(1)', 'file:///tmp/private', 'https://user:pass@example.invalid/',
                      'https://example.invalid/\nsecret', 'https://example.invalid:wrong/',
                      'https://example.invalid\\other/path', '//example.invalid/path'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_url(value)
        self.assertEqual(safe_url('https://EXAMPLE.invalid/path?id=2#section'), 'https://example.invalid/path?id=2')


if __name__ == '__main__':
    unittest.main()
