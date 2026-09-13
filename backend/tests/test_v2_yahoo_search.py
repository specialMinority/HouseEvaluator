"""Offline Yahoo orchestration; real discovery/robots/ranking, synthetic rows."""
from copy import deepcopy
import html
import threading
from unittest.mock import Mock

import pytest

from backend.v2 import public_search, yahoo_search
from backend.v2.public_fetch import PublicFetchError, PublicResponse
from backend.v2.yahoo_discovery import ORIGIN

SUBJECT = {'city':'tokyo','municipality':'新宿区','station_name':'新宿駅','layout':'1K',
           'area_sqm':25,'rent_yen':100000,'floor':3,'building_floors':8}
ROBOTS = ORIGIN+'/robots.txt'
WARD = ORIGIN+'/rent/search/03/13/13104/'
STATION = ORIGIN+'/rent/search/station/2167/'
STAMP = '2026-01-01T00:00:00Z'


def row(index=0, **changes):
    value = dict(SUBJECT, station_name='新宿', source_id='yahoo_realestate', source_url=ORIGIN+f'/rent/detail/{index:044x}/',
                 rent_yen=90000, mgmt_fee_yen=None, structure=None, elevator=None, built_year=None,
                 building_type='mansion', address=f'東京都新宿区合成{index}', building_name=f'合成建物{index}', fetched_at=STAMP)
    value.update(changes)
    return value


def body(name, *, station=False, next_url=None):
    return ('<html><title>'+name+'</title>'+('<div class="ListInfo"><a href="'+STATION+'">新宿</a></div>' if station else '')+
            ('<a href="'+html.escape(next_url)+'">次へ</a>' if next_url else '')+'</html>')


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(yahoo_search, '_SEARCH_SLOT', threading.BoundedSemaphore(1))
    monkeypatch.setattr(yahoo_search.time, 'sleep', lambda _: None)
    monkeypatch.setattr(yahoo_search, '_timestamp', lambda: STAMP)
    return yahoo_search


def run_pages(source, monkeypatch, pages, *, robots='User-agent: *\nDisallow:\n', statuses=None, subject=None):
    statuses = statuses or {}
    calls = []
    decoded = {page[0]: page[1:] for page in pages.values()}

    def parse(text, *, fetched_at, regions, target_station, conflicts_out=None):
        values, *conflicts = decoded[text]
        if conflicts:
            conflicts_out.update(conflicts[0])
        return deepcopy(values), 1, False

    monkeypatch.setattr(source, 'parse_search', parse)

    def fetcher(url, *, deadline):
        calls.append(url)
        assert calls.count(url) == 1, 'No retries'
        if url == ROBOTS:
            return PublicResponse(200, robots.encode(), 'text/plain')
        assert url in pages, 'No guessed filter/detail/foreign URL request'
        status = statuses.get(url, 200)
        if isinstance(status, Exception):
            raise status
        return PublicResponse(status, pages[url][0].encode())

    return source.search(deepcopy(subject or SUBJECT), fetcher=fetcher), calls


def test_robots_denial_stops_before_listing(source, monkeypatch):
    result, calls = run_pages(source, monkeypatch, {WARD:(body('first'),[row()])}, robots='User-agent: *\nDisallow: /rent/\n')
    assert calls == [ROBOTS] and result['listings'] == []
    assert result['source_reports'][0]['status'] == 'blocked'


@pytest.mark.parametrize('status', [403, 429, 503])
def test_source_error_is_not_retried_or_switched(source, monkeypatch, status):
    result,calls=run_pages(source,monkeypatch,{WARD:(body('first'),[row()])},statuses={WARD:status})
    assert calls == [ROBOTS,WARD] and result['listings'] == []
    assert result['source_reports'][0]['source_id'] == 'yahoo_realestate'
    assert result['source_reports'][0]['http_status'] == status


def test_observed_station_then_next_only_with_total_three_page_budget(source, monkeypatch):
    pages={WARD:(body('ward',station=True),[row(0)]), STATION:(body('station',next_url=STATION+'?page=2'),[row(1)]),
           STATION+'?page=2':(body('station2',next_url=STATION+'?page=3'),[row(2)])}
    result,calls=run_pages(source,monkeypatch,pages)
    report=result['source_reports'][0]
    assert calls == [ROBOTS,WARD,STATION,STATION+'?page=2']
    assert report['search_page_count']==3 and report['station_page_count']==2
    assert report['stop_reason']=='page_limit' and report['partial']
    assert len(result['listings'])==3 and report['detail_attempt_count']==0
    assert result['server_condition_filters_applied'] is False
    assert '미적용' in report['area_scope_label']


def test_unresolved_station_keeps_observed_ward_pages_without_guessed_filters(source,monkeypatch):
    pages={WARD:(body('ward',next_url=WARD+'?page=2'),[row()]), WARD+'?page=2':(body('ward2'),[row(1)])}
    result,calls=run_pages(source,monkeypatch,pages)
    assert calls==[ROBOTS,WARD,WARD+'?page=2']
    assert result['source_reports'][0]['search_scope']=='municipality_fallback'
    assert result['source_reports'][0]['station_page_count']==0


def test_actual_station_theme_link_takes_priority_over_next_page_with_same_budget(source,monkeypatch):
    theme=STATION+'theme/156/'
    station_html=body('station',next_url=STATION+'?page=2')+'<a href="'+theme+'">1K、1DK</a>'
    pages={WARD:(body('ward',station=True),[row(0)]),STATION:(station_html,[row(1)]),
           theme:(body('theme',next_url=theme+'?page=2'),[row(2)])}
    result,calls=run_pages(source,monkeypatch,pages)
    assert calls==[ROBOTS,WARD,STATION,theme]
    report=result['source_reports'][0]
    assert report['layout_theme_page_count']==1 and report['search_page_count']==3
    assert '1K·1DK 함께' in report['area_scope_label']
    assert result['server_condition_filters_applied'] is True


def test_robots_denied_theme_is_not_rewritten_or_replaced_with_dynamic_form(source,monkeypatch):
    theme=WARD+'theme/156/'
    pages={WARD:(body('ward')+'<a href="'+theme+'">1K、1DK</a>',[row()])}
    result,calls=run_pages(source,monkeypatch,pages,robots='User-agent: *\nDisallow: /rent/search/03/13/13104/theme/\n')
    assert calls==[ROBOTS,WARD]
    assert result['source_reports'][0]['status']=='blocked'
    assert result['source_reports'][0]['layout_theme_page_count']==0


def test_local_scope_filter_is_explicit_and_missing_facts_are_not_inferred(source,monkeypatch):
    values=[row(0,area_sqm=20),row(1,area_sqm=30),row(2,area_sqm=19.99),row(3,layout='1DK'),
            row(4,station_name='新宿三丁目'),row(5,municipality='渋谷区'),row(6,rent_yen=True)]
    result,_=run_pages(source,monkeypatch,{WARD:(body('ward'),values)})
    assert len(result['listings'])==2
    assert all(item['mgmt_fee_yen'] is None and item['structure'] is None and item['elevator'] is None for item in result['listings'])
    assert result['price_filter_applied'] is False


def test_page_internal_conflict_removes_prior_and_future_copies(source,monkeypatch):
    conflict=row(0)['source_url']
    pages={WARD:(body('ward',next_url=WARD+'?page=2'),[row(0),row(1)]),
           WARD+'?page=2':(body('ward2',next_url=WARD+'?page=3'),[],{conflict}),
           WARD+'?page=3':(body('ward3'),[row(0),row(2)])}
    result,_=run_pages(source,monkeypatch,pages)
    assert {r['source_url'] for r in result['listings']}=={row(1)['source_url'],row(2)['source_url']}
    assert result['source_reports'][0]['conflicting_url_count']==1


def test_cross_page_price_conflict_invalidates_both_observations(source,monkeypatch):
    pages={WARD:(body('ward',next_url=WARD+'?page=2'),[row()]), WARD+'?page=2':(body('ward2'),[row(rent_yen=90001)])}
    result,_=run_pages(source,monkeypatch,pages)
    assert result['listings']==[] and result['source_reports'][0]['conflicting_url_count']==1


def test_later_station_failure_preserves_rows_and_does_not_claim_station_success(source,monkeypatch):
    pages={WARD:(body('ward',station=True),[row()]),STATION:(body('station'),[row(1)])}
    result,calls=run_pages(source,monkeypatch,pages,statuses={STATION:503})
    assert calls==[ROBOTS,WARD,STATION] and len(result['listings'])==1
    assert result['source_reports'][0]['station_resolution']=='observed_public_link'
    assert result['source_reports'][0]['station_page_count']==0


def test_invalid_subject_does_not_consume_external_request_or_slot(source):
    fetcher=Mock(side_effect=AssertionError('no external request'))
    result=source.search(dict(SUBJECT,station_name=None),fetcher=fetcher)
    fetcher.assert_not_called()
    assert result['source_reports'][0]['status']=='unsupported'
    assert source._SEARCH_SLOT.acquire(blocking=False)
    source._SEARCH_SLOT.release()


def test_rank_exception_releases_global_slot(source,monkeypatch):
    monkeypatch.setattr(source,'_rank_candidates',Mock(side_effect=RuntimeError('injected rank failure')))
    with pytest.raises(RuntimeError,match='injected rank failure'):
        source.search(SUBJECT,fetcher=Mock(return_value=PublicResponse(403)))
    assert source._SEARCH_SLOT.acquire(blocking=False)
    source._SEARCH_SLOT.release()


def test_crawl_delay_cannot_exceed_total_budget(source,monkeypatch):
    result,calls=run_pages(source,monkeypatch,{WARD:(body('ward'),[row()])},robots='User-agent: *\nCrawl-delay: 60\n')
    assert calls==[ROBOTS]
    assert result['source_reports'][0]['stop_reason']=='time_limit'


def test_explicit_source_config_routes_only_to_yahoo(source,monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE','yahoo_realestate')
    expected={'listings':[],'source_reports':[{'source_id':'yahoo_realestate'}]}
    handler=Mock(return_value=expected)
    monkeypatch.setattr(source,'search',handler)
    fetcher=Mock(side_effect=AssertionError('router must not fetch directly'))
    assert public_search.search(SUBJECT,fetcher=fetcher) is expected
    handler.assert_called_once_with(SUBJECT,fetcher=fetcher)
    assert public_search.options()['sources']==[{'id':'yahoo_realestate','name':'Yahoo! 부동산 공개 검색','kind':'public_page','automatic':True}]
