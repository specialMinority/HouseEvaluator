"""Synthetic fixtures for observed Yahoo navigation and strict JSON extraction."""
import html
import json

import pytest

from backend.v2.yahoo_discovery import (ORIGIN, build_search_url, checked_url, municipality_url,
                                       next_page, page_context, resolve_layout_theme, resolve_station)
from backend.v2.public_fetch import PublicFetchError
from backend.v2.public_search import _REGIONS

SUBJECT = {'city': 'tokyo', 'municipality': '新宿区', 'station_name': '新宿駅', 'layout': '1K', 'area_sqm': 25}
WARD = ORIGIN + '/rent/search/03/13/13104/'
STATION = ORIGIN + '/rent/search/station/2167/'
DETAIL = ORIGIN + '/rent/detail/_0000051128845b96f83d36aeab33165a97bd60863e9d/'


def link(url, label='次へ'):
    return '<a href="' + html.escape(url, quote=True) + '">' + label + '</a>'


def context(page=None, common=None):
    return ('<script>/*<![CDATA[*/ window.__SERVER_SIDE_CONTEXT__ = {common: ' +
            json.dumps(common or {'synthetic_crumb': 'do-not-return'}) + ', page: ' +
            json.dumps(page or {'properties': []}) + ', mode: "pc"}; /*]]>*/</script>')


@pytest.mark.parametrize('url', [ORIGIN+'/robots.txt', WARD, WARD+'?page=2', STATION, STATION+'?page=3',
                                DETAIL, DETAIL.replace('/_', '/'), ORIGIN+'/rent/search/06/27/27127/',
                                ORIGIN+'/rent/search/09/40/40132/'])
def test_observed_host_paths_and_detail_identity_length(url):
    assert checked_url(url).hostname == 'realestate.yahoo.co.jp'


@pytest.mark.parametrize('url', [
    WARD.replace('https:', 'http:'), WARD.replace('realestate.yahoo.co.jp', 'realestate.yahoo.co.jp.evil.test'),
    WARD.replace('realestate.yahoo.co.jp', 'user@realestate.yahoo.co.jp'),
    WARD.replace('realestate.yahoo.co.jp', 'realestate.yahoo.co.jp:443'),
    ORIGIN+'/api/', WARD+'#', WARD+'#x', WARD+'?', WARD+'?page=0', WARD+'?page=4',
    WARD+'?page=2&page=2', WARD+'?sort=price', WARD+'?rent_from=0', WARD+'?ma_from=20',
    WARD+'?rl=2', WARD+'?page=%32', WARD.replace('13104', '40132'), WARD.replace('/03/13/', '/06/13/'),
    STATION.replace('2167', '0000'), STATION.replace('2167', '12345'),
    ORIGIN+'/rent/detail/'+'a'*40+'/', ORIGIN+'/rent/detail/'+'A'*44+'/',
    ORIGIN+'/rent/detail/'+'a'*43+'/', ORIGIN+'/rent/detail/'+'a'*45+'/',
    DETAIL+'printing/', DETAIL+'?id=1', DETAIL+'#', DETAIL+'\n', None,
])
def test_unobserved_filters_and_unsafe_or_unbounded_urls_are_rejected(url):
    with pytest.raises(PublicFetchError, match='endpoint_denied'):
        checked_url(url)


def test_three_city_ward_navigation_never_invents_server_condition_filters():
    for city, municipality, path in [('tokyo','新宿区','03/13/13104'), ('osaka','大阪市北区','06/27/27127'),
                                     ('fukuoka','福岡市博多区','09/40/40132')]:
        subject = dict(SUBJECT, city=city, municipality=municipality)
        assert municipality_url(subject, regions=_REGIONS) == ORIGIN+'/rent/search/'+path+'/'
        assert build_search_url(subject, regions=_REGIONS) == municipality_url(subject, regions=_REGIONS)
    assert build_search_url(SUBJECT, regions=_REGIONS, station_url=STATION) == STATION
    assert build_search_url(dict(SUBJECT, rent_yen=1, area_sqm=999), regions=_REGIONS, station_url=STATION) == STATION
    for bad in (WARD, STATION+'?page=2', DETAIL):
        with pytest.raises(PublicFetchError):
            build_search_url(SUBJECT, regions=_REGIONS, station_url=bad)


def test_station_resolution_requires_related_navigation_exact_name_unique_code():
    page = '<div class="ListInfo">'+link(STATION,'新宿')+link(STATION,'新宿駅')+'</div>'
    assert resolve_station(page, SUBJECT, base_url=WARD) == STATION
    assert resolve_station(page, dict(SUBJECT, station_name='新宿三丁目'), base_url=WARD) is None
    assert resolve_station(link(STATION,'新宿'), SUBJECT, base_url=WARD) is None
    assert resolve_station(page, dict(SUBJECT, city='osaka'), base_url=WARD) is None
    ambiguous = '<div class="ListInfo">'+link(STATION,'新宿')+link(STATION.replace('2167','2168'),'新宿')+'</div>'
    assert resolve_station(ambiguous, SUBJECT, base_url=WARD) is None
    hostile = '<div class="ListInfo">'+link(STATION.replace('realestate.yahoo.co.jp','evil.test'),'新宿')+'</div>'
    assert resolve_station(hostile, SUBJECT, base_url=WARD) is None


def test_pagination_preserves_path_and_follows_only_observed_sequential_links():
    assert next_page(link('/rent/search/03/13/13104/?page=2'), WARD) == WARD+'?page=2'
    assert next_page(link(STATION+'?page=3'), STATION+'?page=2') == STATION+'?page=3'
    for candidate in (WARD+'?page=3', WARD.replace('13104','13105')+'?page=2', STATION+'?page=2', WARD+'?page=2&sort=price'):
        assert next_page(link(candidate), WARD) is None
    assert next_page('', WARD) is None
    assert next_page(link(WARD+'?page=2'), WARD, max_page=1) is None
    assert next_page(link(WARD+'?page=4'), WARD+'?page=3') is None


@pytest.mark.parametrize('layout,code,label', [('1R','152','ワンルーム'), ('1K','156','1K、1DK'),
                                               ('1DK','156','1K、1DK'), ('1LDK','153','1LDK')])
def test_layout_theme_requires_exact_observed_link_and_matching_label(layout,code,label):
    target=STATION+'theme/'+code+'/'
    subject=dict(SUBJECT,layout=layout)
    assert checked_url(target).path.endswith('/theme/'+code+'/')
    assert resolve_layout_theme(link(target,label),subject,base_url=STATION)==target
    assert resolve_layout_theme('',subject,base_url=STATION) is None
    assert resolve_layout_theme(link(WARD+'theme/'+code+'/',label),subject,base_url=STATION) is None
    assert resolve_layout_theme(link(target,'安い順'),subject,base_url=STATION) is None
    assert resolve_layout_theme(link(target,label),subject,base_url=target) is None
    assert next_page(link(target+'?page=2'),target)==target+'?page=2'


def test_unrelated_themes_and_dynamic_filter_form_remain_denied():
    for url in (WARD+'theme/025/',STATION+'theme/02/',ORIGIN+'/rent/search/?geo=13104&pf=13&lc=03'):
        with pytest.raises(PublicFetchError):
            checked_url(url)


def test_page_context_returns_page_only_without_common_auth_or_request_values():
    page = {'properties': [{'BuiltOn': '2005-01', 'GroupProperties': [{'PropertyId': '0'*44}]}], 'formValues': {'geo':['13104']}}
    assert page_context(context(page)) == page
    result = page_context(context())
    assert 'common' not in result and 'synthetic_crumb' not in result
    detail=context({'property':{'PropertyId':'0'*44}})
    assert page_context(detail) is None
    assert page_context(detail,require_properties=False)=={'property':{'PropertyId':'0'*44}}


@pytest.mark.parametrize('mutate', [
    lambda value: value+value,
    lambda value: value.replace('mode: "pc"', 'mode: "pc", mode: "pc"'),
    lambda value: value.replace('"properties": []', '"properties": [], "properties": []'),
    lambda value: value.replace('"properties": []', '"properties": NaN'),
    lambda value: value.replace('"properties": []', '"properties": Infinity'),
    lambda value: value.replace('"properties": []', '"properties": getData()'),
    lambda value: value.replace('mode: "pc"', 'mode: "other"'),
    lambda value: value.replace('; /*]]>*/', '; doSomething(); /*]]>*/'),
    lambda value: value.replace('"properties": []', '"properties": {}'),
    lambda value: value.replace('"properties": []', '"properties": ['*1100 + '0' + ']'*1100),
])
def test_ambiguous_or_executable_state_is_not_evaluated(mutate):
    assert page_context(mutate(context())) is None


def test_page_context_bounds_and_unrecognized_markup():
    assert page_context('<html>no state</html>') is None
    assert page_context('x'*(3*1024*1024+1)) is None
    assert page_context(None) is None
