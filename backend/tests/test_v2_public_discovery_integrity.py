"""Discovery must discard contradictory or newly out-of-scope advertisements.

All responses are synthetic HTML. The fetch callback never opens a connection.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.tests.test_v2_public_search import SUBJECT, card, detail
from backend.v2.public_fetch import PublicResponse
from backend.v2.public_search import search


def synthetic_search(pages, detail_html):
    calls = []

    def fetch(url, *, deadline):
        calls.append(url)
        if url.endswith('/robots.txt'):
            return PublicResponse(200, b'User-agent: *\nDisallow:\n', 'text/plain')
        if '/ichiran/' in url:
            page = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
            assert 1 <= page <= len(pages), 'Unexpected search-page request'
            html = pages[page - 1]
            if page < len(pages):
                base = url.split('&page=', 1)[0]
                html += f'<a href="{base}&page={page + 1}">次へ</a>'
            return PublicResponse(200, html.encode('utf-8'))
        assert '/chintai/jnc_' in url, 'Unexpected metadata or external request'
        return PublicResponse(200, detail_html.encode('utf-8'))

    # Isolate navigation metadata; these cases test page/detail integrity, not
    # station-code resolution. The 25sqm target uses identical narrow/broad bins.
    with patch('backend.v2.public_search.time.sleep', lambda _: None), \
            patch('backend.v2.public_search._cached_station', return_value=None), \
            patch('backend.v2.public_search._resolve_station', return_value=None):
        result = search(dict(SUBJECT), fetcher=fetch)
    return result, calls


@pytest.mark.parametrize('areas', [(25, 50), (50, 25)])
def test_same_url_scope_change_rejects_both_versions_before_candidate_filtering(areas):
    # Both pages explicitly identify the same advertisement, but disagree about
    # a known area. Even an out-of-scope first version must invalidate the later
    # in-scope version; scope filtering cannot erase conflicting observations.
    result, calls = synthetic_search([card(space=f'{area}m2') for area in areas], detail())
    report = result['source_reports'][0]
    assert report['search_page_count'] == 2
    assert report['discovered_count'] == 2
    assert report['duplicate_count'] == 1
    assert report['rejected_count'] == 2
    assert report['listing_count'] == report['station_count'] == 0
    assert report['distinct_building_count'] == 0
    assert report['detail_attempt_count'] == report['detail_count'] == 0
    assert report['partial'] is True
    assert result['listings'] == []
    assert len(calls) == 3  # robots + the two observed search pages
    assert not any('/chintai/jnc_' in url for url in calls)


def test_detail_station_change_is_removed_from_final_results_and_reported_as_out_of_scope():
    # The list includes 新宿; the detailed access list no longer does. It retains
    # 西新宿, which must not silently replace the user's exact station in output.
    changed_detail = detail().replace('ＪＲ山手線/新宿駅 歩12分', 'ＪＲ山手線/別駅 歩12分')
    result, calls = synthetic_search([card()], changed_detail)
    report = result['source_reports'][0]
    assert report['search_page_count'] == 1
    assert report['discovered_count'] == 1
    assert report['detail_attempt_count'] == 1
    assert report['out_of_scope_count'] == 1
    assert report['listing_count'] == report['station_count'] == 0
    assert report['distinct_building_count'] == 0
    assert report['partial'] is True
    assert result['listings'] == []
    assert len(calls) == 3  # robots + list + exactly one detail
    assert sum('/chintai/jnc_' in url for url in calls) == 1
