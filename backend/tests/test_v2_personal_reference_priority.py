"""Related reference groups stay separate and are ordered without using price."""

from copy import deepcopy
import unittest

from backend.tests.test_v2_personal import NOW, listing, sample, subject
from backend.v2.personal import compare


class PersonalReferencePriorityTest(unittest.TestCase):
    def compare(self, rows, target=None):
        result = compare({'subject': target or subject(), 'listings': rows}, now=NOW)
        self.assertEqual(result['input_count'], result['matched_count'] + result['reference_count'] + len(result['excluded']))
        self.assertEqual(result['related_reference_count'],
                         sum(group['sample_count'] for group in result['reference_groups'] if group['same_building_features']))
        self.assertTrue(all(type(group['same_building_features']) is bool for group in result['reference_groups']))
        self.assertIsNone(result['judgment'])
        return result

    def test_five_storey_rc_and_twelve_storey_src_precede_lowrise_steel_apartment_and_unknown_groups(self):
        rows = [listing(1, title='steel', structure='steel', building_floors=9),
                listing(2, title='apartment', building_type='apartment', building_floors=3, floor=2),
                listing(3, title='SRC twelve', structure='src', building_floors=12),
                listing(4, title='unknown', structure=None, building_floors=9),
                listing(5, title='RC five', building_floors=5),
                listing(6, title='RC lowrise', building_floors=3, floor=2)]
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 6)
        self.assertEqual(result['related_reference_count'], 2)
        groups = result['reference_groups']
        self.assertEqual([group['comparables'][0]['title'] for group in groups[:2]], ['RC five', 'SRC twelve'])
        self.assertEqual([group['same_building_features'] for group in groups], [True, True, False, False, False, False])
        self.assertIsNone(result['summary'])
        self.assertIsNone(result['price_range'])
        self.assertTrue(all(group['reasons'] for group in groups[:2]))

    def test_priority_axes_use_observed_characteristics_before_height_or_area_closeness(self):
        cases = [
            ('kind', {'structure': 'steel', 'building_floors': 9},
             {'building_type': 'apartment', 'building_floors': 9}),
            ('structure family', {'building_floors': 3, 'floor': 2},
             {'structure': 'steel', 'building_floors': 9}),
            ('lowrise class', {'building_floors': 12, 'elevator': False},
             {'building_floors': 3, 'floor': 2}),
            ('elevator', {'building_floors': 12, 'floor': 12},
             {'building_floors': 12, 'elevator': None}),
            ('occupied position', {'building_floors': 12, 'elevator': False},
             {'building_floors': 8, 'floor': 8, 'elevator': False}),
            ('height before area', {'building_floors': 5, 'area_sqm': 27},
             {'building_floors': 12, 'area_sqm': 25}),
            ('unknown before contradiction', {'building_type': None, 'building_floors': 5},
             {'building_type': 'apartment', 'building_floors': 5}),
        ]
        for axis, preferred, later in cases:
            with self.subTest(axis=axis):
                # Reverse the desired order to rule out accidental input order.
                result = self.compare([listing(1, title='later', **later), listing(2, title='preferred', **preferred)])
                self.assertEqual(result['matched_count'], 0)
                self.assertEqual(result['reference_count'], 2)
                self.assertEqual(len(result['reference_groups']), 2)
                self.assertEqual(result['reference_groups'][0]['comparables'][0]['title'], 'preferred')

    def test_unknown_features_in_either_side_cannot_make_a_positive_same_feature_claim(self):
        for field in ('building_type', 'structure', 'building_floors', 'floor', 'elevator'):
            for unknown_side in ('target', 'candidate', 'both'):
                with self.subTest(field=field, unknown_side=unknown_side):
                    target = subject()
                    row = listing(1, building_floors=5)
                    if unknown_side in ('target', 'both'):
                        target[field] = None
                    if unknown_side in ('candidate', 'both'):
                        row[field] = None
                    result = self.compare([row], target)
                    self.assertEqual(result['reference_count'], 1)
                    self.assertEqual(result['related_reference_count'], 0)
                    self.assertFalse(result['reference_groups'][0]['same_building_features'])
        # Ground position is known without total storeys, but the lowrise class
        # still cannot be checked. Two unknown heights must not mean "same".
        result = self.compare([listing(1, floor=1, building_floors=None)], subject(floor=1, building_floors=None))
        self.assertEqual(result['related_reference_count'], 0)
        self.assertFalse(result['reference_groups'][0]['same_building_features'])

    def test_family_match_is_explicit_and_never_crosses_the_lowrise_boundary(self):
        for target_structure, candidate_structure in (('rc', 'src'), ('steel', 'light_steel')):
            with self.subTest(structures=(target_structure, candidate_structure)):
                result = self.compare([listing(1, structure=candidate_structure, building_floors=5)],
                                      subject(structure=target_structure))
                self.assertEqual(result['related_reference_count'], 1)
                self.assertTrue(result['reference_groups'][0]['same_building_features'])
        result = self.compare([listing(1, building_floors=5, floor=2)], subject(building_floors=3, floor=2))
        self.assertEqual(result['related_reference_count'], 0)
        self.assertFalse(result['reference_groups'][0]['same_building_features'])

    def test_positive_flag_does_not_mean_matching_height_year_area_or_a_target_price_delta(self):
        result = self.compare([listing(i, building_floors=5, area_sqm=29, built_year=1980) for i in range(1, 4)])
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['related_reference_count'], 3)
        self.assertIsNone(result['summary'])
        group = result['reference_groups'][0]
        self.assertTrue(group['same_building_features'])
        self.assertEqual(group['summary']['sample_count'], 3)
        self.assertNotIn('delta_pct', group)
        self.assertNotIn('delta_pct', group['summary'])
        self.assertNotIn('subject_comparison_yen', group)
        self.assertTrue(all(any('준공 연도' in note for note in row['differences']) for row in group['comparables']))

    def test_related_references_cannot_change_primary_statistics_and_count_rows_not_groups(self):
        primary = [listing(i, rent_yen=price) for i, price in ((1, 95000), (2, 100000), (3, 105000))]
        references = [listing(i, building_floors=5, rent_yen=45000) for i in range(4, 7)]
        references += [listing(i, building_floors=12, structure='src', rent_yen=60000) for i in range(7, 10)]
        alone = self.compare(primary)
        result = self.compare(primary + references)
        self.assertEqual(result['summary'], alone['summary'])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['reference_count'], 6)
        self.assertEqual(result['related_reference_count'], 6)
        self.assertEqual(len(result['reference_groups']), 2)
        self.assertEqual(result['summary']['median_yen'], 105000)
        self.assertEqual([group['summary']['median_yen'] for group in result['reference_groups']], [50000, 65000])
        self.assertTrue(all('delta_pct' not in group['summary'] for group in result['reference_groups']))

    def test_changing_prices_never_changes_reference_order_or_feature_flags(self):
        rows = sample() + [listing(i, building_floors=5) for i in range(4, 7)]
        rows += [listing(i, building_floors=12, structure='src') for i in range(7, 10)]
        rows += [listing(10, structure='steel', building_floors=9)]
        before = self.compare(rows)
        changed = deepcopy(rows)
        for index, row in enumerate(changed[3:]):
            row['rent_yen'] = 1000 if index % 2 else 9000000
            row['mgmt_fee_yen'] = 500000 if index % 2 else 0
        after = self.compare(changed)
        def ordering(result):
            return [(group['id'], group['same_building_features'], [row['id'] for row in group['comparables']])
                    for group in result['reference_groups']]
        self.assertEqual(ordering(before), ordering(after))
        self.assertEqual(before['related_reference_count'], after['related_reference_count'])
        self.assertEqual(before['summary'], after['summary'])


if __name__ == '__main__':
    unittest.main()
