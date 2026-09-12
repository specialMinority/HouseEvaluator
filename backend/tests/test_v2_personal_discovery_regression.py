"""A poor discovery sample must not become a false market-price comparison.

The geometry and missingness reproduce the reported 60-card distribution.
All names, addresses, URLs and building identities are explicitly synthetic.
The pasted cards did not include room identifiers, addresses or source URLs,
so their actual duplicate relationships cannot be reconstructed from that text.
No network access, original names or personal contact information is used.
"""

from collections import Counter
from copy import deepcopy
import unittest

from backend.v2.personal import compare
from backend.tests.test_v2_personal import NOW, subject


def target():
    return subject(
        municipality='江戸川区', station_name='小岩駅', layout='1DK',
        area_sqm=33.58, structure='rc', built_year=2008, walk_min=12,
        floor=3, building_floors=8, building_type='mansion', elevator=True,
        rent_yen=100000, mgmt_fee_yen=15000,
        title='Synthetic target', building_name='Synthetic target building',
        address='Synthetic target address', source_url='https://example.invalid/target',
    )


def synthetic_listing(number, **changes):
    row = target()
    row.update(title=f'Synthetic advertisement {number}',
               building_name=f'Synthetic building {number}',
               address=f'Synthetic address {number}',
               source_url=f'https://example.invalid/listing/{number}',
               station_name='小岩')
    row.update(changes)
    return row


def sixty_card_pattern():
    rows = []

    def add(area, height, floor, walk, rent, fee, *, station='小岩',
            structure=None, year=None, elevator=None, kind='mansion'):
        rows.append(synthetic_listing(
            len(rows) + 1, area_sqm=area, building_floors=height, floor=floor,
            walk_min=walk, rent_yen=rent * 1000,
            mgmt_fee_yen=None if fee is None else fee * 1000,
            station_name=station, structure=structure, built_year=year,
            elevator=elevator, building_type=kind,
        ))

    # Seven nearby three-storey mansion advertisements.
    add(34.83, 3, 1, 20, 90, 5)
    add(34.43, 3, 1, 10, 80, None, structure='steel', year=1989)
    add(30.91, 3, 1, 7, 91, 4, structure='steel', year=2012)
    add(27, 3, 2, 6, 80, 3, structure='steel', year=1995)
    add(27, 3, 2, 5, 80, 3, structure='steel', year=1995)
    add(27, 3, 2, 6, 86, 3)
    add(27, 3, 2, 6, 86, 3)

    # Twenty cards with a nine-storey profile, all outside the area limit.
    # Seven have confirmed RC/year/elevator metadata in the reported cards.
    nine_storey = [
        (26.2, 2, 8, 119, True), (25.92, 2, 9, 119, True),
        (26.2, 3, 8, 121, False), (25.92, 3, 9, 121, True),
        (26.2, 4, 8, 123, False), (25.92, 4, 8, 123, False),
        (26.2, 5, 8, 125, False), (25.92, 5, 9, 125, True),
        (26.2, 6, 8, 125, False), (25.92, 6, 9, 125, True),
        (26.2, 8, 8, 126, False), (25.92, 7, 9, 126, True),
        (26.2, 7, 8, 126, False), (25.92, 2, 8, 119, False),
        (25.92, 3, 8, 121, False), (25.92, 4, 8, 123, False),
        (25.92, 5, 8, 125, True), (25.92, 6, 8, 125, False),
        (25.92, 8, 8, 126, False), (25.92, 7, 8, 126, False),
    ]
    for area, floor, walk, rent, confirmed in nine_storey:
        add(area, 9, floor, walk, rent, 10,
            structure='rc' if confirmed else None,
            year=2026 if confirmed else None,
            elevator=True if confirmed else None)

    add(25, 4, 1, 4, 73, None)
    for area, walk, rent, fee in ((30.91, 19, 98, 7), (30.91, 19, 102, 6),
                                  (30.62, 19, 103, 7), (29.34, 11, 92, 3),
                                  (29.34, 12, 92, 3)):
        add(area, 3, 2, walk, rent, fee, kind='apartment')

    # Twenty-seven other-station advertisements must remain outside both sets.
    for area, floor, rent in ((32.82, 1, 111), (32.82, 2, 112), (32.82, 3, 113),
                              (32.8, 1, 111), (32.8, 2, 112)):
        add(area, 3, floor, 16, rent, 5, station='平井')
    add(28.65, 7, 2, 6, 133, 10, station='西葛西')
    add(30.15, 5, 4, 4, 99, 8, station='瑞江')
    add(29.31, 11, 8, 4, 129, 15, station='平井')
    add(26.88, 5, 5, 15, 72, 3, station='新小岩')
    add(26.88, 5, 5, 15, 80, None, station='新小岩')
    add(30, 3, 3, 8, 68, 2, station='瑞江')
    add(41.93, 7, 7, 3, 119, 10, station='瑞江')
    add(41.93, 7, 7, 2, 119, 10, station='瑞江')
    add(25.54, 9, 8, 8, 130, 10, station='葛西')
    for floor, rent in [(2, 113)] * 4 + [(3, 116)] * 4 + [(6, 119)] * 4 + [(7, 120)]:
        add(25.5, 10, floor, 2, rent, 10, station='一之江')
    return rows


class PersonalDiscoveryRegressionTest(unittest.TestCase):
    def compare(self, rows):
        result = compare({'subject': target(), 'listings': rows}, now=NOW)
        self.assertEqual(result['input_count'], len(rows))
        self.assertEqual(len(rows), result['matched_count'] + result['reference_count'] + len(result['excluded']))
        self.assertEqual(result['reference_count'], sum(group['sample_count'] for group in result['reference_groups']))
        self.assertIsNone(result['judgment'])
        return result

    def test_reported_sixty_card_geometry_explains_zero_primary_without_widening_the_policy(self):
        rows = sixty_card_pattern()
        self.assertEqual(len(rows), 60)
        self.assertEqual(Counter(row['station_name'] for row in rows),
                         {'小岩': 33, '平井': 6, '西葛西': 1, '瑞江': 4, '新小岩': 2, '葛西': 1, '一之江': 13})
        self.assertEqual(sum(row['structure'] is None for row in rows), 49)
        self.assertEqual(sum(row['built_year'] is None for row in rows), 49)
        self.assertEqual(sum(row['elevator'] is None for row in rows), 53)
        self.assertEqual(sum(row['mgmt_fee_yen'] is None for row in rows), 3)
        same_station = [row for row in rows if row['station_name'] == '小岩']
        area_ok = [row for row in same_station if 26.864 <= row['area_sqm'] <= 40.296]
        self.assertEqual(len(area_ok), 12)
        self.assertTrue(all(row['building_floors'] == 3 for row in area_ok))
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 12)
        self.assertEqual(len(result['reference_groups']), 5)
        self.assertIsNone(result['summary'])
        self.assertIsNone(result['price_range'])
        self.assertEqual(sum('역명 불일치' in row['reason'] for row in result['excluded']), 27)
        self.assertEqual(sum('면적 차이' in row['reason'] for row in result['excluded']), 21)
        for group in result['reference_groups']:
            self.assertNotIn('delta_pct', group)
            if group['summary']:
                self.assertNotIn('delta_pct', group['summary'])

    def test_confirming_every_missing_field_cannot_fix_the_area_and_height_intersection(self):
        rows = sixty_card_pattern()
        for row in rows:
            # A hypothetical complete-information upper bound, not an assertion
            # about the actual advertisements' unknown building characteristics.
            for field, value in (('structure', 'rc'), ('built_year', 2008), ('elevator', True)):
                if row[field] is None:
                    row[field] = value
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 12)
        self.assertIsNone(result['summary'])

    def test_small_nine_storey_mansions_are_excluded_but_27_sqm_three_storey_rows_stay_visible(self):
        rows = [synthetic_listing(1, area_sqm=25.92, building_floors=9, built_year=2026),
                synthetic_listing(2, area_sqm=26.2, building_floors=9, built_year=2026),
                synthetic_listing(3, area_sqm=27, building_floors=3, floor=2)]
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 1)
        self.assertEqual({row['id'] for row in result['excluded']}, {'listing-1', 'listing-2'})
        self.assertTrue(all('면적 차이' in row['reason'] for row in result['excluded']))
        self.assertEqual(result['reference_groups'][0]['comparables'][0]['area_sqm'], 27)

    def test_twenty_percent_area_boundaries_accept_exact_values_without_integer_rounding(self):
        for area in (26.864, 40.296):
            with self.subTest(accepted_area=area):
                result = self.compare([synthetic_listing(i, area_sqm=area) for i in range(1, 4)])
                self.assertEqual(result['matched_count'], 3)
                self.assertEqual(result['reference_count'], 0)
                self.assertEqual(result['summary']['building_groups'], 3)
                self.assertEqual([step['id'] for step in result['steps'] if step['selected']], ['area_wide'])
        for area in (26.8639, 40.2961):
            with self.subTest(rejected_area=area):
                result = self.compare([synthetic_listing(i, area_sqm=area) for i in range(1, 4)])
                self.assertEqual(result['matched_count'], 0)
                self.assertEqual(result['reference_count'], 0)
                self.assertTrue(all('면적 차이' in row['reason'] for row in result['excluded']))

    def test_three_eligible_buildings_replace_other_station_slots_and_become_primary_within_sixty_rows(self):
        rows = sixty_card_pattern()
        rows[-3:] = [synthetic_listing(i, rent_yen=rent) for i, rent in ((58, 90000), (59, 100000), (60, 110000))]
        self.assertEqual(len(rows), 60)
        result = self.compare(rows)
        self.assertEqual(result['strict_count'], 3)
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['reference_count'], 12)
        self.assertEqual(len(result['excluded']), 45)
        self.assertEqual({row['id'] for row in result['comparables']}, {'listing-58', 'listing-59', 'listing-60'})
        self.assertEqual(result['summary']['median_yen'], 115000)
        self.assertEqual(result['summary']['delta_pct'], 0)
        self.assertEqual(result['summary']['building_groups'], 3)
        self.assertEqual([step['id'] for step in result['steps'] if step['selected']], ['exact'])

    def test_explicit_synthetic_url_conflict_explains_two_fewer_references_without_assuming_real_room_identity(self):
        rows = deepcopy(sixty_card_pattern())
        # Only this controlled case asserts the two prices belong to one URL.
        # The attachment cannot establish that fact for the real advertisements.
        rows[29]['source_url'] = rows[28]['source_url']
        result = self.compare(rows)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['reference_count'], 10)
        self.assertEqual(len(result['excluded']), 50)
        self.assertEqual({row['id'] for row in result['excluded'] if '충돌' in row['reason']},
                         {'listing-29', 'listing-30'})
        self.assertIsNone(result['summary'])


if __name__ == '__main__':
    unittest.main()
