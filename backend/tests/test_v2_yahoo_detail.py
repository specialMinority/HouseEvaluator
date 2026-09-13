"""Invented page fixtures only; no captured advertisements or external requests."""
import json

import pytest

from backend.v2 import listing_import
from backend.v2.public_fetch import PublicFetchError, PublicResponse
from backend.v2.public_search import _REGIONS, _SEARCH_SLOT
from backend.v2.yahoo_detail import detail_url, parse_detail

IDENTITY = '_' + 'a' * 44
URL = 'https://realestate.yahoo.co.jp/rent/detail/' + IDENTITY + '/'
STAMP = '2026-01-01T00:00:00Z'
SECRET = 'private-crumb-cookie-company-message'


def fixture_page():
    return {
        'property': {
            'PropertyId': IDENTITY, 'Price': 95000, 'PriceLabel': '9.5万円',
            'MonthlyManagementCost': 5000, 'MonthlyManagementCostLabel': '5,000円',
            'MonopolyAreaLabel': '30.50m<sup>2</sup>', 'MonopolyArea': 3050,
            'BuiltOn': '2012-04', 'YearsOld': 99, 'KindName': 'マンション',
            'StructureName': '鉄筋コンクリート', 'CanDisplayBuildingName': True,
            'StructureView': {'BuildingName': '合成テスト館', 'TotalFloorNum': 8,
                              'FloorNum': '3', 'FloorNameLabel': '地上8階建て/3階部分'},
            'DetailsView': {'RoomLayoutBreakdown': '1DK (洋室7)', 'ContractPeriod': '24ヶ月', 'Remark': SECRET},
            'LocationView': {'AddressName': '東京都新宿区合成1'},
            'Transports': [
                {'StationName': '新宿', 'LineName': '合成線', 'MinutesFromStation': 12,
                 'Label': '新宿駅/合成線 徒歩12分'},
                {'StationName': '大久保', 'LineName': '別線', 'MinutesFromStation': 7,
                 'Label': '大久保駅/別線 徒歩7分'},
                {'StationName': '遠方', 'LineName': '合成線', 'MinutesFromStation': 1,
                 'Label': '遠方駅/合成線 徒歩1分', 'BusMinutes': 20},
            ],
        },
        'popularFacilities': [{'label': 'バス・トイレ独立', 'disabled': False}],
        'otherFacilities': [{'facilityGroupLabel': '室内設備', 'facilityLabels': ['エレベーター']}],
        'recommend': [{'PropertyId': 'other', 'Price': 1, 'CompanyName': SECRET}],
    }


def detail(page=None, *, canonical=URL, rent='9.5万円', fee='5,000円', plan='1DK', sqm='30.50m2',
           floor='地上8階建て/3階部分', built='築99年（2012年04月）', extra='', address='東京都新宿区合成1'):
    page = fixture_page() if page is None else page
    state = json.dumps(page, ensure_ascii=False)
    return f'''<head><link rel="canonical" href="{canonical}"></head>
    <h1 class="DetailHeadingLarge__title">合成テスト館<span class="DetailHeadingLarge__note">（3階/{plan}/{sqm}）</span></h1>
    <div class="DetailSummary"><dd class="DetailSummary__price__rent">{rent}</dd></div>
    <table class="DetailSummaryTable">
    <tr><th>賃料/管理費・共益費等</th><td>{rent} / {fee}</td></tr>
    <tr><th>間取り</th><td>{plan}</td><th>専有面積</th><td>{sqm}</td></tr>
    <tr><th>階建/階</th><td>{floor}</td><th>築年数</th><td>{built}</td></tr>
    <tr><th>所在地</th><td>{address}<a class="DetailSummaryTable__textLink">地図を見る</a></td></tr>
    <tr><th>構造</th><td>鉄筋コンクリート</td><th>方位</th><td>南</td></tr>
    <tr><th>建物名</th><td>合成テスト館</td></tr></table>{extra}
    <script>window.__SERVER_SIDE_CONTEXT__ = {{common:{{"crumb":"{SECRET}"}},page:{state},mode:"pc"}};</script>'''


def parsed(html=None):
    return parse_detail(detail() if html is None else html, source_url=URL, fetched_at=STAMP, regions=_REGIONS)


def test_current_visible_property_and_exact_date_win_over_age_and_recommendations():
    row = parsed()
    assert row['rent_yen'] == 95000 and row['mgmt_fee_yen'] == 5000
    assert row['layout'] == '1DK' and row['area_sqm'] == 30.5
    assert row['built_year'] == 2012 and row['floor'] == 3 and row['building_floors'] == 8
    assert row['station_name'] == '大久保' and row['walk_min'] == 7
    assert row['city'] == 'tokyo' and row['municipality'] == '新宿区'
    assert row['structure'] == 'rc' and row['building_type'] == 'mansion'
    assert row['building_name'] == '合成テスト館' and row['elevator'] is True
    assert row['bathroom_separate'] is True and row['contract_type'] is None
    assert row['source_id'] == 'yahoo_realestate' and row['source_url'] == URL
    assert SECRET not in json.dumps(row)


def test_missing_or_disabled_features_and_age_are_not_invented():
    page = fixture_page()
    prop = page['property']
    prop.update({'BuiltOn': None, 'MonthlyManagementCostLabel': '-', 'MonthlyManagementCost': 0,
                 'CanDisplayBuildingName': False})
    page['popularFacilities'][0]['disabled'] = True
    page['otherFacilities'] = []
    row = parsed(detail(page, fee='-', built='築99年').replace('<td>南</td>', '<td>-</td>'))
    assert all(row[field] is None for field in ('mgmt_fee_yen', 'built_year', 'elevator',
                                               'bathroom_separate', 'furnished', 'orientation', 'building_name'))


def test_explicit_no_management_fee_is_zero():
    page = fixture_page()
    page['property'].update({'MonthlyManagementCostLabel': 'なし', 'MonthlyManagementCost': 0})
    assert parsed(detail(page, fee='なし'))['mgmt_fee_yen'] == 0


def test_recommendation_summary_table_cannot_override_current_property():
    extra = '<section class="DetailRecommend"><table class="DetailSummaryTable"><tr><th>間取り</th><td>4LDK</td><th>方位</th><td>北</td></tr></table></section>'
    row = parsed(detail(extra=extra))
    assert row['layout'] == '1DK' and row['orientation'] == 'S'


@pytest.mark.parametrize('change', ['canonical', 'identity', 'two_states', 'missing_state', 'duplicate_key', 'extra_js', 'two_headings'])
def test_ambiguous_or_executable_identity_is_refused(change):
    page = fixture_page()
    html = detail(page)
    if change == 'canonical':
        html = detail(canonical=URL.replace('aaaa/', 'bbbb/'))
    elif change == 'identity':
        page['property']['PropertyId'] = 'b' * 44
        html = detail(page)
    elif change == 'two_states':
        html += html[html.index('<script>'):]
    elif change == 'missing_state':
        html = html[:html.index('<script>')]
    elif change == 'duplicate_key':
        html = html.replace('"Price": 95000', '"Price": 95000, "Price": 1')
    elif change == 'extra_js':
        html = html.replace(';</script>', '; fetch("https://private.invalid");</script>')
    else:
        html += '<h1 class="DetailHeadingLarge__title">Other</h1>'
    with pytest.raises(PublicFetchError):
        parsed(html)


@pytest.mark.parametrize('change', ['rent', 'fee', 'area', 'layout', 'floor', 'year', 'address', 'structure', 'name', 'heading_note'])
def test_state_and_visible_main_property_conflicts_are_refused(change):
    page = fixture_page()
    prop = page['property']
    if change == 'rent': prop['Price'] = 95001
    elif change == 'fee': prop['MonthlyManagementCost'] = 6000
    elif change == 'area': prop['MonopolyAreaLabel'] = '40m<sup>2</sup>'
    elif change == 'layout': prop['DetailsView']['RoomLayoutBreakdown'] = '1LDK (洋室7)'
    elif change == 'floor': prop['StructureView']['FloorNum'] = '4'
    elif change == 'year': prop['BuiltOn'] = '2013-04'
    elif change == 'address': prop['LocationView']['AddressName'] = '東京都新宿区別1'
    elif change == 'structure': prop['StructureName'] = '木造'
    elif change == 'name': prop['StructureView']['BuildingName'] = '別の建物'
    html = detail(page)
    if change == 'heading_note': html = html.replace('（3階/1DK/', '（4階/1DK/')
    with pytest.raises(PublicFetchError, match='ambiguous_listing'):
        parsed(html)


@pytest.mark.parametrize(('change', 'code'), [('unsupported_layout', 'unsupported_layout'), ('region', 'unsupported_region'),
                                            ('bool_price', 'invalid_listing_fields'), ('too_high_floor', 'invalid_listing_fields'),
                                            ('nan', 'parse_changed'), ('oversize', 'html_too_complex')])
def test_bad_values_and_resource_bounds_fail_safely(change, code):
    page = fixture_page()
    if change == 'unsupported_layout':
        page['property']['DetailsView']['RoomLayoutBreakdown'] = '2LDK'
        html = detail(page, plan='2LDK')
    elif change == 'region':
        page['property']['LocationView']['AddressName'] = '神奈川県横浜市合成1'
        html = detail(page, address='神奈川県横浜市合成1')
    elif change == 'bool_price':
        page['property']['Price'] = True
        html = detail(page)
    elif change == 'too_high_floor':
        page['property']['StructureView'].update({'FloorNum': '103', 'TotalFloorNum': 108,
                                                 'FloorNameLabel': '地上108階建て/103階部分'})
        html = detail(page, floor='地上108階建て/103階部分').replace('（3階/', '（103階/')
    elif change == 'nan':
        page['property']['Price'] = float('nan')
        html = detail(page)
    else:
        html = 'x' * (3 * 1024 * 1024 + 1)
    with pytest.raises(PublicFetchError, match=code):
        parsed(html)


@pytest.mark.parametrize('url', [URL.replace('https:', 'http:'), URL + '?x=1', URL + '#x',
    URL.replace('realestate.yahoo.co.jp', '127.0.0.1'), URL.replace('realestate.yahoo.co.jp', 'realestate.yahoo.co.jp.evil.invalid'),
    URL.replace('realestate.yahoo.co.jp', 'user:secret@realestate.yahoo.co.jp'), URL.replace('aaaa/', '/'),
    URL.upper(), 'https://realestate.yahoo.co.jp/robots.txt', 'https://realestate.yahoo.co.jp/rent/search/station/2172/'])
def test_only_exact_observed_detail_urls_are_accepted_before_network(url):
    assert detail_url(url) is None
    calls = []
    with pytest.raises(listing_import.ListingImportError, match='unsupported_url'):
        listing_import.import_listing({'url': url}, fetcher=lambda *a, **kw: calls.append(a))
    assert calls == []


def test_yahoo_import_reads_only_its_robots_then_detail_and_preserves_source(monkeypatch):
    monkeypatch.setattr(listing_import.time, 'sleep', lambda seconds: None)
    calls = []
    def fetch(url, *, deadline):
        calls.append((url, deadline))
        return PublicResponse(200, ('User-agent: *\nDisallow:\n' if url.endswith('/robots.txt') else detail()).encode())
    result = listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert [url for url, _ in calls] == ['https://realestate.yahoo.co.jp/robots.txt', URL]
    assert calls[0][1] == calls[1][1]
    assert result['source']['id'] == result['listing']['source_id'] == 'yahoo_realestate'
    assert result['listing']['station_name'] == '大久保'
    assert result['status'] == 'partial' and 'contract_type' in result['missing_fields']
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('status', [301, 401, 403, 429, 503])
def test_yahoo_failure_never_fetches_an_alternate_source_or_leaks_body(status, monkeypatch):
    monkeypatch.setattr(listing_import.time, 'sleep', lambda seconds: None)
    calls = []
    def fetch(url, *, deadline):
        calls.append(url)
        return PublicResponse(status, SECRET.encode())
    with pytest.raises(listing_import.ListingImportError) as error:
        listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert calls == ['https://realestate.yahoo.co.jp/robots.txt']
    assert SECRET not in error.value.message
    assert _SEARCH_SLOT.acquire(blocking=False)
    _SEARCH_SLOT.release()
