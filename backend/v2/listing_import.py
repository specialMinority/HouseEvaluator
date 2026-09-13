"""One user-selected detail read, with robots and the public-search concurrency slot."""
import os
import time

from .chintai_detail import parse_detail as parse_chintai_detail
from .chintai_discovery import checked_url as checked_chintai_url
from .chintai_html import detail_url as chintai_detail_url
from .public_fetch import PublicFetchError, fetch_public
from .public_search import RobotsPolicy, _REGIONS, _SEARCH_SLOT, _blocked, _decode, _timestamp
from .yahoo_detail import detail_url as yahoo_detail_url, parse_detail as parse_yahoo_detail

IMPORT_SECONDS = 8.5
IMPORT_SOURCES = ({'id': 'chintai', 'name': 'CHINTAI'}, {'id': 'yahoo_realestate', 'name': 'Yahoo! 부동산'})
_ERRORS = {
    'unsupported_url': (400, '지원되는 출처의 개별 매물 상세 링크를 입력해 주세요. 검색 목록·추적 정보가 포함된 링크는 지원하지 않습니다.'),
    'source_disabled': (403, '현재 이 출처의 자동 입력은 운영 설정에서 꺼져 있습니다. 직접 입력해 주세요.'),
    'invalid_payload': (400, '자동 입력할 url 하나만 전달해 주세요.'),
    'import_busy': (429, '다른 공개 조회를 처리 중입니다. 잠시 후 다시 시도하거나 직접 입력해 주세요.'),
    'import_timeout': (503, '매물 정보를 읽는 시간이 초과되었습니다. 원문을 확인하고 직접 입력해 주세요.'),
    'source_blocked': (503, '원문 사이트의 접근 제한으로 자동 입력을 중단했습니다. 직접 입력해 주세요.'),
    'source_unavailable': (503, '원문 페이지를 읽지 못했습니다. 원문을 확인하고 직접 입력해 주세요.'),
    'unsupported_layout': (422, '현재 자동 입력은 1R·1K·1DK·1LDK 매물을 지원합니다.'),
    'unsupported_region': (422, '현재 지원하는 도쿄 23구·오사카시·후쿠오카시의 매물인지 확인해 주세요.'),
    'ambiguous_listing': (422, '링크와 페이지의 매물 정보가 일치하는지 확인할 수 없어 자동 입력하지 않았습니다.'),
    'parse_changed': (422, '이 페이지에서 매물 조건을 확실히 읽지 못했습니다. 직접 입력해 주세요.'),
}


class ListingImportError(Exception):
    def __init__(self, code):
        self.code = code if code in _ERRORS else 'source_unavailable'
        self.status, self.message = _ERRORS[self.code]
        super().__init__(self.code)


def import_sources():
    """Explicit operator selection only; no automatic fallback after blocking.

    An empty value disables all import providers. Invalid configuration fails
    closed with fixed text, so a mistaken environment value cannot be disclosed.
    """
    raw = os.getenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    if not isinstance(raw, str) or len(raw) > 256:
        raise ValueError('매물 자동 입력 출처 설정이 올바르지 않습니다.')
    selected = [part.strip() for part in raw.split(',')] if raw.strip() else []
    allowed = {source['id'] for source in IMPORT_SOURCES}
    if len(selected) != len(set(selected)) or any(value not in allowed for value in selected):
        raise ValueError('매물 자동 입력 출처 설정이 올바르지 않습니다.')
    return [dict(source) for source in IMPORT_SOURCES if source['id'] in selected]


def _provider(url):
    if isinstance(url, str) and chintai_detail_url(url) == url:
        checked_chintai_url(url)
        return 'chintai', 'https://www.chintai.net/robots.txt', parse_chintai_detail
    if isinstance(url, str) and yahoo_detail_url(url) == url:
        return 'yahoo_realestate', 'https://realestate.yahoo.co.jp/robots.txt', parse_yahoo_detail
    raise ListingImportError('unsupported_url')


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {'url'}:
        raise ListingImportError('invalid_payload')
    url = payload['url']
    try:
        source_id, _, _ = _provider(url)
    except PublicFetchError:
        raise ListingImportError('unsupported_url') from None
    if source_id not in {source['id'] for source in import_sources()}:
        raise ListingImportError('source_disabled')
    return url


def import_listing(payload, *, fetcher=None, deadline=None):
    url = validate_payload(payload)
    source_id, robots_url, parse_detail = _provider(url)
    fetcher = fetcher or fetch_public
    deadline = min(deadline, time.monotonic() + IMPORT_SECONDS) if deadline is not None else time.monotonic() + IMPORT_SECONDS
    if not _SEARCH_SLOT.acquire(blocking=False):
        raise ListingImportError('import_busy')

    def check_time():
        if time.monotonic() >= deadline:
            raise ListingImportError('import_timeout')

    def read(page_url):
        check_time()
        response = fetcher(page_url, deadline=deadline)
        check_time()
        if response.status in (401, 403, 429):
            raise ListingImportError('source_blocked')
        if response.status != 200:
            raise ListingImportError('source_unavailable')
        return _decode(response)

    try:
        robots = read(robots_url)
        policy = RobotsPolicy(robots)
        if not policy.allows(url):
            raise ListingImportError('source_blocked')
        delay = max(.15, policy.delay)
        if time.monotonic() + delay + .2 >= deadline:
            raise ListingImportError('import_timeout')
        time.sleep(delay)
        text = read(url)
        if _blocked(text):
            raise ListingImportError('source_blocked')
        fetched_at = _timestamp()
        listing = parse_detail(text, source_url=url, fetched_at=fetched_at, regions=_REGIONS)
        check_time()
        missing = [key for key, value in listing.items() if value is None and key not in ('title', 'source_id', 'source_url', 'fetched_at')]
        warnings = ['원문에서 읽은 조건입니다. 모집 여부와 금액을 원문에서 다시 확인해 주세요.',
                    '여러 역이 있으면 확인된 도보 시간이 가장 짧은 역을 입력합니다. 비교할 역이 다르면 바꿔 주세요.']
        if missing:
            warnings.append('확인되지 않은 조건은 미상으로 남겼습니다. 필요한 항목을 직접 확인해 입력해 주세요.')
        return {'status': 'partial' if missing else 'ok', 'listing': listing, 'missing_fields': missing,
                'source': {'id': source_id, 'url': url, 'fetched_at': fetched_at}, 'warnings': warnings}
    except ListingImportError:
        raise
    except PublicFetchError as error:
        code = ('import_timeout' if error.code == 'timeout' else error.code if error.code in
                ('unsupported_layout', 'unsupported_region', 'ambiguous_listing', 'parse_changed') else
                'parse_changed' if error.code in ('invalid_listing_fields', 'html_too_complex') else 'source_unavailable')
        raise ListingImportError(code) from None
    except Exception:
        # Never put arbitrary parser, socket, URL or page text into API/logs.
        raise ListingImportError('source_unavailable') from None
    finally:
        _SEARCH_SLOT.release()
