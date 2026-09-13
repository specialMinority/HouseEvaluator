"""Synthetic detail HTML and local HTTP only; never load real advertisements."""
import json
import time

import pytest

from backend.tests.test_v2_personal_http import request, server_factory
from backend.v2 import listing_import
from backend.v2.chintai_detail import parse_detail
from backend.v2.public_fetch import PublicFetchError, PublicResponse
from backend.v2.public_search import _REGIONS, _SEARCH_SLOT

URL = 'https://www.chintai.net/detail/bk-C000000000000000000000000001/'
STAMP = '2026-01-01T00:00:00Z'
SECRET = 'private-secret-cookie-internal-url'


def detail(*, fee='なし', plan='1K', address='東京都新宿区合成1', canonical=URL,
           extra='', features=None, built='2010年04月(築15年)', title=None):
    title = title or f'合成インポート館 3階／{address}の賃貸物件詳細'
    features = features if features is not None else '<span class="js_help">エレベーター</span><span class="js_help">バス・トイレ別</span>'
    return f'''<head><link rel="canonical" href="{canonical}"></head><form name="detailForm">
    <input id="bkapi" value="C000000000000000000000000001">
    <div class="mod_h2Box"><h2>{title}</h2></div><div class="detail_basicInfo"><table>
    <tr><th>家賃</th><td><span class="rent"><span>9.5</span>万円</span><table><tr><th>管理費等</th><td>{fee}</td></tr></table></td></tr>
    <tr><th>住所</th><td>{address}<span class="mapTextLink">地図で確認</span></td></tr>
    <tr><th>交通</th><td>合成線/新宿駅 徒歩 15 分　別線/大久保駅 徒歩 7 分</td></tr>
    <tr><th>間取り</th><td><span class="bold">{plan}</span> (洋室8)</td><th>専有面積</th><td>25.5m&#178;</td></tr>
    <tr><th>築年</th><td>{built}</td><th>方位</th><td>南</td></tr>
    <tr><th>建物種別</th><td>マンション</td><th>構造</th><td>鉄筋コンクリート造</td></tr>
    <tr><th>物件階層</th><td>3階/8階建</td></tr>{extra}</table></div>
    <div class="detail_specTable"><table><tr><th>その他</th><td>{features}</td></tr>
    <tr><th>契約期間</th><td>普通借家 2年</td></tr></table></div></form>'''


def parsed(html=None):
    return parse_detail(detail() if html is None else html, source_url=URL, fetched_at=STAMP, regions=_REGIONS)


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr(listing_import.time, 'sleep', lambda seconds: None)


def fake_fetcher(html=None, *, robots='User-agent: *\nDisallow:\n', status=200, error=None):
    calls = []
    def fetch(url, *, deadline):
        calls.append((url, deadline))
        if url.endswith('/robots.txt'):
            return PublicResponse(200, robots.encode(), 'text/plain')
        if error:
            raise error
        return PublicResponse(status, (html if html is not None else detail()).encode())
    return fetch, calls


def test_detail_reads_confirmed_facts_and_selects_nearest_observed_station():
    row = parsed()
    assert row['rent_yen'] == 95000 and row['mgmt_fee_yen'] == 0
    assert row['station_name'] == '大久保' and row['walk_min'] == 7
    assert row['city'] == 'tokyo' and row['municipality'] == '新宿区'
    assert row['layout'] == '1K' and row['area_sqm'] == 25.5
    assert row['structure'] == 'rc' and row['built_year'] == 2010
    assert row['building_type'] == 'mansion' and row['property_type'] == 'apartment'
    assert row['floor'] == 3 and row['building_floors'] == 8
    assert row['elevator'] is True and row['bathroom_separate'] is True
    assert row['furnished'] is None and row['orientation'] == 'S'
    assert row['contract_type'] == 'standard'
    assert row['building_name'] == '合成インポート館' and row['address'] == '東京都新宿区合成1'
    assert row['source_id'] == 'chintai' and row['source_url'] == URL


def test_missing_values_and_marketing_help_are_not_guessed():
    html = detail(fee='--', built='築8年', features='<span class="js_help">収納<div class="js_help_baloon">エレベーター バス・トイレ別</div></span>')
    html = html.replace('<td>南</td>', '<td>--</td>').replace('普通借家 2年', '一般契約：2年間')
    row = parsed(html)
    assert all(row[k] is None for k in ('mgmt_fee_yen', 'built_year', 'orientation', 'elevator', 'bathroom_separate', 'contract_type', 'furnished'))


def test_external_other_room_tables_and_scripts_cannot_pollute_main_facts():
    fake = '<table><tr><th>管理費等</th><td>999999円</td><th>構造</th><td>木造</td></tr></table>'
    html = detail() + '<aside class="detail_otherList">' + fake + '</aside><script>' + fake + '</script>'
    row = parsed(html)
    assert row['mgmt_fee_yen'] == 0 and row['structure'] == 'rc'


@pytest.mark.parametrize('html', [
    detail(canonical=URL.replace('000001/', '000002/')),
    detail().replace('<link rel="canonical"', '<link rel="unknown"'),
    detail().replace('id="bkapi" value="C000000000000000000000000001"', 'id="bkapi" value="C000000000000000000000000002"'),
    detail().replace('id="bkapi"', 'id="unrelated"'),
    detail(extra='<tr><th>管理費等</th><td>5000円</td></tr>'),
    detail(title='合成インポート館 4階／東京都新宿区合成1の賃貸物件詳細'),
    detail(title='合成インポート館 3階／東京都新宿区別の住所の賃貸物件詳細'),
])
def test_identity_or_field_conflicts_refuse_to_fill(html):
    with pytest.raises(PublicFetchError, match='ambiguous_listing'):
        parsed(html)


@pytest.mark.parametrize(('html', 'code'), [
    (detail(plan='2LDK'), 'unsupported_layout'),
    (detail(address='神奈川県横浜市合成1'), 'unsupported_region'),
    ('<html>source changed</html>', 'ambiguous_listing'),
    (detail().replace('detail_basicInfo', 'unrelated'), 'parse_changed'),
    (detail(extra='<tr><th>不明</th><td>値</td></tr>').replace('3階/8階建', '9階/8階建'), 'ambiguous_listing'),
])
def test_unsupported_or_malformed_pages_produce_fixed_errors(html, code):
    with pytest.raises(PublicFetchError, match=code):
        parsed(html)


def test_generic_description_stays_unverified_building_name():
    row = parsed(detail(title='合成線 新宿駅 8階建 築15年 3階／東京都新宿区合成1の賃貸物件詳細'))
    assert row['building_name'] is None


def test_empty_heading_box_does_not_hide_the_main_building_name():
    html = detail().replace('<div class="mod_h2Box">', '<div class="mod_h2Box"></div><div class="mod_h2Box">', 1)
    row = parsed(html)
    assert row['building_name'] == '合成インポート館' and row['title'].startswith('合成インポート館')


@pytest.mark.parametrize('payload', [None, [], {}, {'url': URL, 'source_id': 'suumo'}, {'url': None},
    {'url': 'https://www.chintai.net/robots.txt'}, {'url': 'https://www.chintai.net/tokyo/area/13104/list/'},
    {'url': 'http://www.chintai.net/detail/bk-C000000000000000000000000001/'},
    {'url': URL + '?vm=0'}, {'url': URL + '#private'}, {'url': URL.replace('www.chintai.net', '127.0.0.1')},
    {'url': URL.replace('www.chintai.net', 'www.chintai.net.evil.invalid')},
    {'url': URL.replace('www.chintai.net', 'user:secret@www.chintai.net')},
    {'url': 'https://suumo.jp/chintai/bc_100000000001/'}, {'url': URL + '\n'}, {'url': URL.lower()},
])
def test_import_rejects_non_detail_or_malformed_input_before_network(payload):
    calls = []
    with pytest.raises(listing_import.ListingImportError) as raised:
        listing_import.import_listing(payload, fetcher=lambda *a, **k: calls.append(a))
    assert raised.value.status == 400 and not calls


def test_import_contract_only_two_reads_partial_fields_and_same_deadline(no_delay):
    fetch, calls = fake_fetcher()
    result = listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert result['status'] == 'partial' and result['listing']['furnished'] is None
    assert 'furnished' in result['missing_fields']
    assert result['source'] == {'id': 'chintai', 'url': URL, 'fetched_at': result['listing']['fetched_at']}
    assert len(calls) == 2 and calls[0][0] == 'https://www.chintai.net/robots.txt' and calls[1][0] == URL
    assert calls[0][1] == calls[1][1] and calls[1][1] <= time.monotonic() + 8.5
    assert result['warnings'] and 'html' not in result


@pytest.mark.parametrize('robots', ['User-agent: *\nDisallow: /detail/', '', '<html>captcha</html>', 'User-agent: *\nCrawl-delay: 60'])
def test_robots_restriction_or_delay_stops_before_detail(robots, no_delay):
    fetch, calls = fake_fetcher(robots=robots)
    with pytest.raises(listing_import.ListingImportError):
        listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert len(calls) == 1


@pytest.mark.parametrize('status', [401, 403, 404, 429, 503])
def test_failed_http_never_retries_or_exposes_body(status, no_delay):
    fetch, calls = fake_fetcher(html=SECRET, status=status)
    with pytest.raises(listing_import.ListingImportError) as raised:
        listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert raised.value.status == 503 and SECRET not in str(raised.value)
    assert len(calls) == 2
    assert _SEARCH_SLOT.acquire(blocking=False)
    _SEARCH_SLOT.release()


@pytest.mark.parametrize('error', [PublicFetchError('timeout'), PublicFetchError('redirect_denied'), RuntimeError(SECRET)])
def test_exception_or_timeout_always_releases_slot_and_hides_details(error, no_delay):
    fetch, calls = fake_fetcher(error=error)
    with pytest.raises(listing_import.ListingImportError) as raised:
        listing_import.import_listing({'url': URL}, fetcher=fetch)
    assert SECRET not in raised.value.message and len(calls) == 2
    assert _SEARCH_SLOT.acquire(blocking=False)
    _SEARCH_SLOT.release()


def test_import_uses_same_slot_as_search_and_never_fetches_when_busy():
    assert _SEARCH_SLOT.acquire(blocking=False)
    try:
        fetch, calls = fake_fetcher()
        with pytest.raises(listing_import.ListingImportError, match='import_busy'):
            listing_import.import_listing({'url': URL}, fetcher=fetch)
        assert calls == []
    finally:
        _SEARCH_SLOT.release()


def test_expired_or_mid_request_deadline_never_starts_more_work(no_delay, monkeypatch):
    fetch, calls = fake_fetcher()
    with pytest.raises(listing_import.ListingImportError, match='import_timeout'):
        listing_import.import_listing({'url': URL}, fetcher=fetch, deadline=time.monotonic() - 1)
    assert calls == []
    clock = [100.0]
    monkeypatch.setattr(listing_import.time, 'monotonic', lambda: clock[0])
    def slow_robots(url, *, deadline):
        calls.append(url)
        clock[0] = deadline
        return PublicResponse(200, b'User-agent: *\nDisallow:\n')
    with pytest.raises(listing_import.ListingImportError, match='import_timeout'):
        listing_import.import_listing({'url': URL}, fetcher=slow_robots)
    assert len(calls) == 1


def test_http_import_auth_flags_validation_and_no_network_for_rejections(server_factory):
    seen = []
    def imported(payload, *, deadline):
        seen.append((payload, deadline))
        return {'status': 'partial', 'listing': {'rent_yen': 95000}, 'missing_fields': [], 'source': {'id': 'chintai', 'url': URL}, 'warnings': []}
    token = 'test-private-access-code-0000000000'
    server = server_factory(access_token=token, import_fn=imported)
    assert request(server, 'POST', '/api/v2/personal/import', {'url': URL})[0] == 401
    assert request(server, 'POST', '/api/v2/personal/import', {'url': 'https://127.0.0.1/'}, token)[0] == 400
    assert not seen
    status, result = request(server, 'POST', '/api/v2/personal/import', {'url': URL}, token)
    assert status == 200 and result['listing']['rent_yen'] == 95000 and len(seen) == 1
    options = request(server, 'GET', '/api/v2/personal/options', token=token)[1]
    assert options['import_enabled'] and options['import_sources'] == [
        {'id': 'chintai', 'name': 'CHINTAI'}, {'id': 'yahoo_realestate', 'name': 'Yahoo! 부동산'}]
    assert seen[0][1] <= time.monotonic() + 9.5
    for flags in ({'personal_enabled': False}, {'public_search_enabled': False}):
        disabled = server_factory(import_fn=imported, **flags)
        assert request(disabled, 'POST', '/api/v2/personal/import', {'url': URL})[0] == 403
        assert request(disabled, 'GET', '/api/v2/personal/options')[1]['import_enabled'] is False
    assert len(seen) == 1


def test_http_import_safe_source_error_and_rate_limit(server_factory):
    def unavailable(payload, **kwargs):
        raise listing_import.ListingImportError('source_unavailable')
    server = server_factory(import_fn=unavailable, requests_per_minute=1)
    status, result = request(server, 'POST', '/api/v2/personal/import', {'url': URL})
    assert status == 503 and result['error'] == 'source_unavailable'
    assert request(server, 'POST', '/api/v2/personal/import', {'url': URL})[0] == 429
