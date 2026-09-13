"""Small synthetic navigation fixtures; no external calls or real ads."""
import html
import unittest
from urllib.parse import parse_qs

from backend.v2.chintai_discovery import (ORIGIN, area_bounds, build_search_url,
                                         checked_url, municipality_url, next_page, resolve_station)
from backend.v2.public_fetch import PublicFetchError


REGIONS = [
    {'id': 'tokyo', 'municipalities': [{'name': '新宿区', 'code': '13104'}]},
    {'id': 'osaka', 'municipalities': [{'name': '大阪市北区', 'code': '27127'}]},
    {'id': 'fukuoka', 'municipalities': [{'name': '福岡市博多区', 'code': '40132'}]},
]
SUBJECT = {'city': 'tokyo', 'municipality': '新宿区', 'station_name': '新宿駅', 'area_sqm': 25, 'layout': '1K'}
AREA = ORIGIN + '/tokyo/area/13104/list/'
STATION = ORIGIN + '/tokyo/ensen/000000069/list/'
FILTER = ORIGIN + '/list/?prefkey=tokyo&ue=000000069&rt=51&o=10&m=1&sf=20&st=30&cf=0&ct=0'
NEXT = ORIGIN + '/list/?ue=000000069&prefkey=tokyo&rt=51&sf=20&m=1&st=30&cf=0&urlType=dynamic&ct=0&i=2&o=10'


def link(url, text='次へ'):
    return '<a href="' + html.escape(url, quote=True) + '">' + text + '</a>'


class ChintaiEndpointTest(unittest.TestCase):
    def test_observed_shapes_and_three_city_area_codes(self):
        for url in (ORIGIN+'/robots.txt', AREA, STATION, FILTER, NEXT,
                    AREA+'page2/', STATION+'page3/', ORIGIN+'/osaka/area/27127/list/',
                    ORIGIN+'/fukuoka/area/40132/list/',
                    ORIGIN+'/detail/bk-C010090150000012491582510001/'):
            with self.subTest(url=url):
                self.assertEqual(checked_url(url).netloc, 'www.chintai.net')

    def test_unrelated_hosts_paths_fragments_encoded_or_price_filters_rejected(self):
        urls = (
            FILTER.replace('https:', 'http:'), FILTER.replace('www.chintai.net', 'www.chintai.net.evil.test'),
            FILTER.replace('www.chintai.net', 'user@www.chintai.net'),
            FILTER.replace('www.chintai.net', 'www.chintai.net:443'),
            FILTER.replace('www.chintai.net', 'chintai.net'), FILTER+'#x', FILTER+'#',
            FILTER.replace('/list/', '/api/'), FILTER.replace('cf=0', 'cf=70'),
            FILTER.replace('ct=0', 'ct=100'), FILTER.replace('o=10', 'o=2'),
            FILTER.replace('ue=000000069', 'ue=0'), FILTER+'&ue=000000070',
            FILTER+'&callback=evil', FILTER+'&i=4', FILTER+'&i=0', FILTER+'&i=02',
            FILTER.replace('sf=20', 'sf=21'), FILTER.replace('st=30', 'st=10'),
            FILTER.replace('rt=51', 'rt=50'), FILTER+'&g=13104',
            FILTER.replace('prefkey=tokyo', 'prefkey=kyoto'), FILTER.replace('m=1', 'm=%31'),
            ORIGIN+'/tokyo/area/99999/list/', ORIGIN+'/tokyo/area/40132/list/',
            ORIGIN+'/tokyo/ensen/000000000/list/', AREA+'page4/', AREA+'?m=1',
            AREA+'?', ORIGIN+'/robots.txt?x=1', ORIGIN+'/robots.txt?',
            ORIGIN+'/detail/bk-C010090150000012491582510001/inquiry/',
            ORIGIN+'/detail/bk-C010090150000012491582510001/?vm=1',
            'https://www.chintai.net\\@evil.test/list/', FILTER+'\n', None,
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(PublicFetchError) as caught:
                checked_url(url)
            self.assertEqual(caught.exception.code, 'endpoint_denied')


class ChintaiNavigationTest(unittest.TestCase):
    def test_three_cities_use_correct_area_with_observed_form_fields(self):
        for city, municipality, code in [('tokyo', '新宿区', '13104'), ('osaka','大阪市北区','27127'),
                                         ('fukuoka','福岡市博多区','40132')]:
            subject = dict(SUBJECT, city=city, municipality=municipality)
            self.assertEqual(municipality_url(subject, regions=REGIONS), f'{ORIGIN}/{city}/area/{code}/list/')
            values = parse_qs(checked_url(build_search_url(subject, regions=REGIONS)).query)
            self.assertEqual(values['g'], [code])
            self.assertEqual(values['rt'], ['50'])
            self.assertEqual(values['m'], ['1'])
            self.assertEqual((values['cf'], values['ct'], values['o']), (['0'], ['0'], ['10']))

    def test_station_code_is_used_only_from_passed_valid_same_city_url(self):
        self.assertEqual(build_search_url(SUBJECT, regions=REGIONS, station_url=STATION), FILTER)
        self.assertNotIn('ue=', build_search_url(SUBJECT, regions=REGIONS))
        for bad in (AREA, STATION+'page2/', STATION.replace('tokyo', 'osaka'), STATION+'?ue=000000069'):
            with self.subTest(bad=bad), self.assertRaises(PublicFetchError):
                build_search_url(SUBJECT, regions=REGIONS, station_url=bad)

    def test_area_rounding_uses_existing_options_and_no_target_rent(self):
        self.assertEqual(area_bounds(33.8), (30, 40))
        self.assertEqual(area_bounds(33.8, broad=True), (25, 45))
        self.assertEqual(area_bounds(250), (200, 0))
        self.assertEqual(area_bounds(5), (0, 10))
        self.assertEqual(build_search_url(dict(SUBJECT, rent_yen=1), regions=REGIONS),
                         build_search_url(dict(SUBJECT, rent_yen=9999999), regions=REGIONS))
        for area in (True, None, '25', float('nan'), float('inf'), 0, -3):
            with self.subTest(area=area), self.assertRaises(PublicFetchError):
                area_bounds(area)

    def test_invalid_subject_is_not_silently_mapped(self):
        for change in ({'city':'kyoto'}, {'municipality':'江戸川区'}, {'layout':'unknown'}):
            with self.subTest(change=change), self.assertRaises(PublicFetchError):
                build_search_url(dict(SUBJECT, **change), regions=REGIONS)

    def test_exact_station_name_resolves_only_observed_unique_safe_same_city_link(self):
        page = link('/tokyo/ensen/000000069/list/', '新宿駅') + link('/tokyo/ensen/000000070/list/', '新大久保駅')
        self.assertEqual(resolve_station(page, SUBJECT, base_url=AREA), STATION)
        self.assertEqual(resolve_station(page, dict(SUBJECT, station_name='新宿'), base_url=AREA), STATION)
        self.assertIsNone(resolve_station(page, dict(SUBJECT, station_name='小岩駅'), base_url=AREA))
        self.assertIsNone(resolve_station(link('https://evil.test/tokyo/ensen/000000069/list/', '新宿駅'), SUBJECT, base_url=AREA))
        self.assertIsNone(resolve_station(link('/osaka/ensen/000000069/list/', '新宿駅'), SUBJECT, base_url=AREA))
        self.assertIsNone(resolve_station(page, SUBJECT, base_url=ORIGIN+'/osaka/area/27127/list/'))

    def test_ambiguous_station_codes_are_not_chosen_arbitrarily(self):
        page = link(STATION,'新宿駅') + link(STATION.replace('069','070'),'新宿駅')
        self.assertIsNone(resolve_station(page, SUBJECT, base_url=AREA))
        self.assertEqual(resolve_station(link(STATION,'新宿駅')*2, SUBJECT, base_url=AREA), STATION)

    def test_next_dynamic_page_preserves_actual_query_order_and_filters(self):
        self.assertEqual(next_page(link(NEXT), FILTER), NEXT)
        third = NEXT.replace('i=2', 'i=3')
        self.assertEqual(next_page(link(third), NEXT), third)
        self.assertIsNone(next_page(link(third), third))
        self.assertIsNone(next_page(link(NEXT), FILTER, max_page=1))
        self.assertIsNone(next_page(link(NEXT), FILTER, max_page=99))
        for bad in (NEXT.replace('m=1','m=2'), NEXT.replace('ue=000000069','ue=000000070'),
                    NEXT.replace('sf=20','sf=15'), NEXT.replace('i=2','i=3'),
                    NEXT.replace('www.chintai.net','evil.test'), NEXT+'&m=1', NEXT+'#x'):
            with self.subTest(bad=bad):
                self.assertIsNone(next_page(link(bad), FILTER))

    def test_next_canonical_page_never_constructs_unobserved_url(self):
        self.assertIsNone(next_page('<p>次へ</p>', AREA))
        self.assertEqual(next_page(link('/tokyo/area/13104/list/page2/'), AREA), AREA+'page2/')
        self.assertIsNone(next_page(link('/tokyo/area/13105/list/page2/'), AREA))
        self.assertIsNone(next_page(link('/tokyo/area/13104/list/page3/'), AREA))
        self.assertIsNone(next_page(link(AREA+'page4/'), AREA+'page3/'))

    def test_returned_next_link_is_not_reordered_to_avoid_robots(self):
        from backend.v2.public_search import RobotsPolicy
        policy = RobotsPolicy('User-agent: *\nDisallow: /list/?ue=\n')
        self.assertTrue(policy.allows(FILTER))
        observed = next_page(link(NEXT), FILTER)
        self.assertEqual(observed, NEXT)
        self.assertFalse(policy.allows(observed))


if __name__ == '__main__':
    unittest.main()
