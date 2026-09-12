"""The five-row visual is an observed-price comparison, not a market rank."""

from copy import deepcopy
import unittest

from backend.tests.test_v2_personal import NOW, listing, sample, subject
from backend.v2.personal import compare


class PersonalVisualComparisonTest(unittest.TestCase):
    def compare(self, rows, target=None):
        payload = {'subject': target or subject(), 'listings': rows}
        before = deepcopy(payload)
        result = compare(payload, now=NOW)
        self.assertEqual(payload, before)
        visual = result['visual_comparison']
        self.assertEqual(visual['version'], 'observed-price-1')
        self.assertEqual(visual['max_count'], 5)
        self.assertEqual(visual['displayed_count'], len(visual['comparables']))
        self.assertLessEqual(visual['displayed_count'], 5)
        self.assertLessEqual(visual['displayed_count'], visual['pool_count'])
        self.assertEqual(visual['subject']['id'], 'subject')
        self.assertTrue(visual['scope_label'])
        self.assertIsNone(result['judgment'])
        if visual['rank'] is not None:
            rank = visual['rank']
            self.assertEqual(rank['total'], visual['displayed_count'] + 1)
            self.assertEqual(rank['lower_count'] + rank['equal_count'] + rank['higher_count'], visual['displayed_count'])
            self.assertEqual(rank['low_to_high'], rank['lower_count'] + 1)
            self.assertEqual(rank['tied_to'], rank['lower_count'] + rank['equal_count'] + 1)
        return result, visual

    def test_primary_rows_are_used_without_filling_with_reference_rows_even_when_only_one_exists(self):
        rows = [listing(1)] + [listing(i, building_floors=5) for i in range(2, 7)]
        result, visual = self.compare(rows)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['related_reference_count'], 5)
        self.assertEqual(visual['mode'], 'primary')
        self.assertEqual(visual['pool_count'], 1)
        self.assertEqual(visual['displayed_count'], 1)
        self.assertEqual(visual['comparables'][0]['id'], 'listing-1')
        self.assertIsNone(visual['comparables'][0]['reference_group_label'])
        self.assertEqual(visual['rank']['total'], 2)

    def test_closest_five_are_selected_without_using_advertised_price(self):
        rows = [listing(1, area_sqm=26.2, rent_yen=1000)] + [listing(i) for i in range(2, 7)]
        _, before = self.compare(rows)
        self.assertEqual(before['mode'], 'primary')
        self.assertEqual(before['pool_count'], 6)
        self.assertEqual({row['id'] for row in before['comparables']}, {f'listing-{i}' for i in range(2, 7)})
        changed = deepcopy(rows)
        for index, row in enumerate(changed):
            row['rent_yen'] = 9000000 if index % 2 else 1000
        _, after = self.compare(changed)
        self.assertEqual({row['id']: row['similarity_order'] for row in before['comparables']},
                         {row['id']: row['similarity_order'] for row in after['comparables']})
        self.assertEqual(sorted(row['similarity_order'] for row in after['comparables']), [1, 2, 3, 4, 5])

    def test_one_row_per_identified_building_and_one_unknown_identity_prevent_density_bias(self):
        rows = [listing(i, area_sqm=area, building_name='Synthetic dense building', address='Synthetic dense address')
                for i, area in ((1, 24.8), (2, 25), (3, 25.2))]
        rows += [listing(4), listing(5), listing(6, address=None, building_name=None),
                 listing(7, address=None, building_name=None)]
        _, visual = self.compare(rows)
        self.assertEqual(visual['pool_count'], 7)
        self.assertEqual(visual['displayed_count'], 4)
        self.assertEqual(sum(row['building_name'] == 'Synthetic dense building' for row in visual['comparables']), 1)
        self.assertEqual(sum(row['address'] is None and row['building_name'] is None for row in visual['comparables']), 1)
        self.assertIn('listing-2', {row['id'] for row in visual['comparables']})
        self.assertEqual(visual['rank']['total'], 5)

    def test_related_reference_tier_is_used_when_no_primary_row_exists(self):
        rows = [listing(i, building_floors=5) for i in range(1, 4)]
        rows += [listing(i, structure='src', building_floors=12) for i in range(4, 7)]
        rows += [listing(7, structure='steel', building_floors=9, rent_yen=1000)]
        result, visual = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(visual['mode'], 'nearest_reference')
        self.assertEqual(visual['pool_count'], 6)
        self.assertEqual(visual['displayed_count'], 5)
        self.assertTrue(all(row['structure'] in ('rc', 'src') and row['elevator'] is True for row in visual['comparables']))
        self.assertTrue(all(row['reference_group_label'] for row in visual['comparables']))
        self.assertIsNone(result['summary'])

    def test_fallback_uses_only_one_feature_tier_without_filling_from_known_contradictions(self):
        rows = [listing(i, structure=None, building_floors=9) for i in (1, 2)]
        rows += [listing(i, structure='steel', building_floors=9) for i in (3, 4, 5)]
        rows += [listing(6, building_type='apartment', building_floors=3, floor=2)]
        result, visual = self.compare(rows)
        self.assertEqual(result['related_reference_count'], 0)
        self.assertEqual(visual['mode'], 'nearest_reference')
        self.assertEqual(visual['pool_count'], 2)
        self.assertEqual(visual['displayed_count'], 2)
        self.assertTrue(all(row['structure'] is None for row in visual['comparables']))
        self.assertEqual(visual['rank']['total'], 3)

    def test_rank_includes_subject_and_represents_equal_prices_as_a_range(self):
        rows = [listing(i, rent_yen=rent) for i, rent in ((1, 75000), (2, 95000), (3, 95000), (4, 115000))]
        _, visual = self.compare(rows, subject(rent_yen=95000))
        self.assertEqual(visual['subject']['price_yen'], 100000)
        self.assertEqual(visual['rank'], {'low_to_high': 2, 'tied_to': 4, 'total': 5,
                                         'lower_count': 1, 'equal_count': 2, 'higher_count': 1})
        self.assertEqual({row['id']: row['delta_yen'] for row in visual['comparables']},
                         {'listing-1': 20000, 'listing-2': 0, 'listing-3': 0, 'listing-4': -20000})
        self.assertNotIn('median_yen', visual)
        self.assertNotIn('judgment', visual)

    def test_unknown_management_fee_uses_rent_for_subject_and_every_displayed_row(self):
        rows = [listing(1, rent_yen=80000, mgmt_fee_yen=None),
                listing(2, rent_yen=100000, mgmt_fee_yen=10000),
                listing(3, rent_yen=120000, mgmt_fee_yen=0)]
        _, visual = self.compare(rows, subject(rent_yen=100000, mgmt_fee_yen=20000))
        self.assertEqual(visual['price_basis'], 'rent')
        self.assertEqual(visual['subject']['price_yen'], 100000)
        self.assertTrue(all(row['price_yen'] == row['rent_yen'] for row in visual['comparables']))
        self.assertEqual({row['id']: row['delta_yen'] for row in visual['comparables']},
                         {'listing-1': 20000, 'listing-2': 0, 'listing-3': -20000})
        self.assertEqual(visual['rank'], {'low_to_high': 2, 'tied_to': 3, 'total': 4,
                                         'lower_count': 1, 'equal_count': 1, 'higher_count': 1})
        _, target_unknown = self.compare(sample(), subject(mgmt_fee_yen=None))
        self.assertEqual(target_unknown['price_basis'], 'rent')
        self.assertTrue(all(row['price_yen'] == row['rent_yen'] for row in target_unknown['comparables']))

    def test_visual_price_fields_do_not_mutate_reference_price_basis_or_caller_rows(self):
        rows = [listing(i, building_floors=5, rent_yen=89000 + i * 1000) for i in range(1, 6)]
        rows.append(listing(6, building_floors=5, area_sqm=29, rent_yen=200000, mgmt_fee_yen=None))
        result, visual = self.compare(rows)
        self.assertEqual(visual['pool_count'], 6)
        self.assertEqual(visual['displayed_count'], 5)
        self.assertEqual(visual['price_basis'], 'total')
        self.assertTrue(all(row['price_yen'] == row['rent_yen'] + row['mgmt_fee_yen'] for row in visual['comparables']))
        group = result['reference_groups'][0]
        self.assertEqual(group['price_basis'], 'rent')
        self.assertEqual(group['summary']['median_yen'], 92500)
        self.assertTrue(all(row['comparison_price_yen'] == row['rent_yen'] for row in group['comparables']))
        for row in group['comparables']:
            self.assertTrue({'price_yen', 'delta_yen', 'similarity_order', 'reference_group_label', 'match_labels'}.isdisjoint(row))
        self.assertNotIn('delta_pct', group['summary'])
        self.assertIsNone(result['summary'])

    def test_hard_exclusions_never_reappear_in_the_visual_or_change_its_price_basis(self):
        invalid = [listing(10, station_name='西新宿', mgmt_fee_yen=None),
                   listing(11, municipality='渋谷区'), listing(12, contract_type='fixed_term'),
                   listing(13, floor=-1), listing(14, area_sqm=30.1)]
        result, visual = self.compare(sample() + invalid)
        self.assertEqual(len(result['excluded']), 5)
        self.assertEqual(visual['mode'], 'primary')
        self.assertEqual(visual['displayed_count'], 3)
        self.assertEqual(visual['price_basis'], 'total')
        self.assertEqual({row['id'] for row in visual['comparables']}, {'listing-1', 'listing-2', 'listing-3'})
        self.assertEqual(result['summary']['median_yen'], 90000)
        self.assertTrue(all('price_yen' not in row and 'similarity_order' not in row for row in result['comparables']))

    def test_no_eligible_rows_means_no_visual_rank_or_invented_comparable(self):
        for rows in ([], [listing(1, station_name='西新宿')]):
            with self.subTest(size=len(rows)):
                _, visual = self.compare(rows)
                self.assertEqual(visual['mode'], 'empty')
                self.assertEqual(visual['pool_count'], 0)
                self.assertEqual(visual['displayed_count'], 0)
                self.assertEqual(visual['comparables'], [])
                self.assertIsNone(visual['rank'])


if __name__ == '__main__':
    unittest.main()
