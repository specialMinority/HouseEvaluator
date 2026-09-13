"""Independent offline review of source boundaries and complete search cleanup."""
import io
import threading
import time
from unittest.mock import Mock, patch

import pytest

from backend.v2 import chintai_search, public_fetch
from backend.v2.chintai_discovery import build_search_url, municipality_url
from backend.v2.public_fetch import PublicFetchError, PublicResponse
from backend.v2.public_search import _REGIONS
from backend.tests.test_v2_chintai_html import building, room


SUBJECT = {'city': 'tokyo', 'municipality': '新宿区', 'station_name': '新宿駅',
           'layout': '1K', 'area_sqm': 25, 'rent_yen': 100000,
           'building_floors': 8, 'floor': 3}
ROBOTS = 'https://www.chintai.net/robots.txt'


@pytest.fixture
def isolated_search(monkeypatch):
    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(chintai_search, '_SEARCH_SLOT', slot)
    monkeypatch.setattr(chintai_search.time, 'sleep', lambda delay: None)
    monkeypatch.setattr(chintai_search, '_timestamp', lambda: '2026-01-01T00:00:00Z')
    return slot


@pytest.mark.parametrize('changes', [
    {'station_name': None}, {'station_name': ''}, {'station_name': True},
    {'station_name': '駅' * 241}, {'rent_yen': True}, {'floor': 9},
    {'elevator': 'true'}, {'structure': 'anything'},
])
def test_full_subject_validation_precedes_any_external_request(isolated_search, changes):
    fetcher = Mock(side_effect=AssertionError('Invalid subject must not cause any external request'))
    result = chintai_search.search(dict(SUBJECT, **changes), fetcher=fetcher)
    fetcher.assert_not_called()
    assert result['source_reports'][0]['status'] == 'unsupported'
    assert isolated_search.acquire(blocking=False)
    isolated_search.release()


def test_output_ranking_exception_cannot_permanently_lock_all_future_searches(isolated_search, monkeypatch):
    monkeypatch.setattr(chintai_search, '_rank_candidates', Mock(side_effect=RuntimeError('injected rank failure')))
    fetcher = Mock(return_value=PublicResponse(200, b'User-agent: *\nDisallow: /\n', 'text/plain'))
    with pytest.raises(RuntimeError, match='injected rank failure'):
        chintai_search.search(SUBJECT, fetcher=fetcher)
    assert fetcher.call_count == 1
    assert isolated_search.acquire(blocking=False), 'The source lock leaked after result formatting failed'
    isolated_search.release()


def test_later_page_internal_conflict_invalidates_previously_seen_same_url(isolated_search):
    # The first page's row is otherwise valid. On a later page the source itself
    # advertises this URL with conflicting prices; keeping the earlier value
    # would hide an observed ambiguity from the comparison.
    initial = municipality_url(SUBJECT, regions=_REGIONS)
    filtered = build_search_url(SUBJECT, regions=_REGIONS)
    broad = build_search_url(SUBJECT, regions=_REGIONS, broad=True)
    first = building(''.join(room(index) for index in range(1, 6)))
    second = building(room(1) + room(1, rent='10万円'))
    pages = {
        ROBOTS: PublicResponse(200, b'User-agent: *\nDisallow:\n', 'text/plain'),
        initial: PublicResponse(200, first.encode()),
        filtered: PublicResponse(200, second.encode()),
        # A later reappearance cannot restore an already conflicting URL.
        broad: PublicResponse(200, building(room(1)).encode()),
    }
    calls = []

    def fetcher(url, *, deadline):
        calls.append(url)
        assert url in pages and calls.count(url) == 1
        return pages[url]

    result = chintai_search.search(SUBJECT, fetcher=fetcher)
    assert calls == [ROBOTS, initial, filtered, broad]
    assert len(result['listings']) == 4
    assert not any(item['source_listing_id'].endswith('000001') for item in result['listings'])
    assert result['source_reports'][0]['conflicting_url_count'] == 1


class Response:
    def __init__(self, status=200, body=b'<html>synthetic</html>', headers=None):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = {'Content-Type': 'text/html; charset=utf-8', **(headers or {})}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, limit):
        return self.body.read(limit)


class Connection:
    sock = None

    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, path, *, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.mark.parametrize('url,hostname,path', [
    ('https://www.chintai.net/tokyo/area/13104/list/', 'www.chintai.net', '/tokyo/area/13104/list/'),
    ('https://www.chintai.net/robots.txt', 'www.chintai.net', '/robots.txt'),
    ('https://suumo.jp/chintai/bc_10000001/', 'suumo.jp', '/chintai/bc_10000001/'),
])
def test_selected_host_is_used_consistently_for_dns_and_verified_tls_connection(url, hostname, path):
    connection = Connection(Response())
    with patch.object(public_fetch, '_bounded_dns', return_value=['203.0.113.15']) as dns, \
            patch.object(public_fetch, '_PinnedHTTPSConnection', return_value=connection) as factory:
        assert public_fetch.fetch_public(url, deadline=time.monotonic()+2).status == 200
    assert dns.call_args.args[:2] == (hostname, 443)
    assert factory.call_args.args == (hostname, '203.0.113.15')
    assert connection.requests == [('GET', path, {
        'User-Agent': public_fetch.USER_AGENT, 'Accept': 'text/html,text/plain;q=0.9',
        'Accept-Encoding': 'identity',
    })]
    assert connection.closed


@pytest.mark.parametrize('location', ['https://suumo.jp/robots.txt', 'http://127.0.0.1/', '/tokyo/area/13104/list/'])
def test_source_redirects_never_follow_even_to_another_allowed_host(location):
    connection = Connection(Response(302, headers={'Location': location}))
    with patch.object(public_fetch, '_bounded_dns', return_value=['203.0.113.15']) as dns, \
            patch.object(public_fetch, '_PinnedHTTPSConnection', return_value=connection) as factory:
        with pytest.raises(PublicFetchError, match='redirect_denied'):
            public_fetch.fetch_public(ROBOTS, deadline=time.monotonic()+2)
    assert dns.call_count == factory.call_count == len(connection.requests) == 1
    assert connection.closed


@pytest.mark.parametrize('url', [
    'https://www.chintai.net.evil.invalid/robots.txt',
    'https://www.chintai.net@127.0.0.1/robots.txt',
    'https://127.0.0.1/robots.txt', 'https://www.chintai.net:444/robots.txt',
    'https://www.chintai.net/api/', 'https://www.chintai.net/robots.txt#',
    'https://www.chintai.net/detail/bk-C00000000000000000001/?redirect=http://127.0.0.1/',
])
def test_new_host_branch_rejects_bad_endpoints_before_dns(url):
    with patch.object(public_fetch, '_bounded_dns', side_effect=AssertionError('must not resolve')) as dns:
        with pytest.raises(PublicFetchError, match='endpoint_denied'):
            public_fetch.fetch_public(url, deadline=time.monotonic()+2)
    dns.assert_not_called()
