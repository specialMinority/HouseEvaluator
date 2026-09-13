"""Bounded reads of CHINTAI's public listing pages, with observed navigation.

This source is selected explicitly by deployment configuration. A failure never
changes source, identity, network route, or access policy automatically.
"""

import time

from .chintai_discovery import build_search_url, checked_url, municipality_url, next_page, resolve_station
from .chintai_html import parse_search
from .personal import validate_listing
from .public_fetch import PublicFetchError, fetch_public, safe_diagnostics
from .public_html import clean, station_name
from .public_search import (MAX_SEARCH_PAGES, SEARCH_SECONDS, RobotsPolicy, _REGIONS,
                            _SEARCH_SLOT, _blocked, _decode, _rank_candidates, _timestamp)


def search(subject, *, fetcher=None):
    fetcher = fetcher or fetch_public
    started = _timestamp()
    report = {
        'source_id': 'chintai', 'status': 'unavailable',
        'message': 'CHINTAI 공개 검색에 연결하지 못했습니다.',
        'search_url': None, 'search_urls': [], 'fetched_at': None,
        'listing_count': 0, 'search_page_count': 0, 'station_page_count': 0,
        'metadata_page_count': 0, 'discovered_count': 0, 'rejected_count': 0,
        'duplicate_count': 0, 'conflicting_url_count': 0, 'out_of_scope_count': 0, 'station_count': 0,
        'distinct_building_count': 0, 'detail_count': 0, 'detail_attempt_count': 0,
        'detail_unavailable_count': 0, 'detail_limit': 0, 'detail_limit_per_building': 0,
        'partial': False, 'search_scope': 'municipality_fallback',
        'station_resolution': 'unresolved', 'stop_reason': 'unavailable',
        'area_searches': [], 'parser_limits': {'building_cards_per_page': 50, 'listings_per_card': 8},
    }
    result = {
        'listings': [], 'source_reports': [report], 'searched_at': started,
        'scope': 'public_search_sample', 'price_filter_applied': False,
        'notice': 'CHINTAI 공개 목록 최대 3페이지의 일부입니다. 모집 상태와 조건은 각 매물 원문에서 확인하세요.',
    }
    try:
        subject = validate_listing(subject, subject=True)
        url = municipality_url(subject, regions=_REGIONS)
        # Validate the complete subject before any external request.
        build_search_url(subject, regions=_REGIONS)
        checked_url(url)
        report['search_url'] = url
    except (PublicFetchError, TypeError, AttributeError, ValueError):
        report.update(status='unsupported', stop_reason='unsupported',
                      message='지원하는 도시·행정구·평면·면적·역 이름을 입력해 주세요.')
        return result
    if not _SEARCH_SLOT.acquire(blocking=False):
        report.update(stop_reason='busy', message='다른 공개 검색을 처리 중입니다. 잠시 후 다시 시도해 주세요.')
        return result

    deadline = time.monotonic() + SEARCH_SECONDS
    context = {'failure_stage': 'robots', 'http_status': None}
    found, observed, conflicts, attempted = {}, {}, set(), set()
    station_url = None
    broad = False

    def read(page_url, *, robots=False):
        checked_url(page_url)
        context.clear()
        context.update(failure_stage='robots' if robots else 'search', http_status=None)
        if not robots:
            if not policy.allows(page_url):
                raise PublicFetchError('robots_blocked')
            if time.monotonic() + delay + .1 >= deadline:
                raise PublicFetchError('timeout')
            time.sleep(delay)
            report['search_urls'].append(page_url)
            report['search_url'] = page_url
        response = fetcher(page_url, deadline=deadline)
        if type(response.status) is not int or not 100 <= response.status <= 599:
            raise PublicFetchError('invalid_response')
        context['http_status'] = response.status
        diagnostics = safe_diagnostics(response.diagnostics)
        if diagnostics is not None and 500 <= response.status <= 599:
            context['diagnostics'] = diagnostics
        if response.status in (401, 403, 429):
            raise PublicFetchError('source_blocked')
        if response.status != 200:
            raise PublicFetchError('http_' + str(response.status))
        text = _decode(response)
        if not robots and _blocked(text):
            raise PublicFetchError('source_blocked')
        return text

    def ingest(text):
        stamp = _timestamp()
        page_conflicts = set()
        rows, cards, empty = parse_search(text, fetched_at=stamp, regions=_REGIONS,
                                          target_station=subject['station_name'], conflicts_out=page_conflicts)
        if not rows and not empty and not cards:
            raise PublicFetchError('parse_changed')
        report['search_page_count'] += 1
        report['discovered_count'] += len(rows)
        report['fetched_at'] = stamp
        for identity in page_conflicts:
            conflicts.add(identity)
            if found.pop(identity, None) is not None:
                report['rejected_count'] += 1
        if cards >= 50:
            report['partial'] = True
        for row in rows:
            try:
                validate_listing(row)
            except ValueError:
                report['rejected_count'] += 1
                continue
            identity = row['source_url']
            if not identity or type(row.get('rent_yen')) is not int or row['rent_yen'] <= 0:
                report['rejected_count'] += 1
                continue
            fingerprint = {key: value for key, value in row.items()
                           if key not in ('fetched_at', 'details_fetched_at', 'missing_fields')}
            if identity in conflicts:
                report['duplicate_count'] += 1
                continue
            if identity in observed:
                report['duplicate_count'] += 1
                if observed[identity] != fingerprint:
                    conflicts.add(identity)
                    found.pop(identity, None)
                    report['rejected_count'] += 2
                continue
            observed[identity] = fingerprint
            area = row.get('area_sqm')
            if (row.get('city') != subject['city'] or row.get('municipality') != subject['municipality']
                    or row.get('station_name') != station_name(subject['station_name'])
                    or row.get('layout') != subject['layout'] or type(area) not in (float, int)
                    or abs(area - subject['area_sqm']) > subject['area_sqm'] * .20 + .00001):
                report['out_of_scope_count'] += 1
                continue
            found[identity] = row

    try:
        robots = read('https://www.chintai.net/robots.txt', robots=True)
        policy = RobotsPolicy(robots)
        delay = max(.15, policy.delay)
        while url and report['search_page_count'] < MAX_SEARCH_PAGES:
            if url in attempted:
                break
            attempted.add(url)
            text = read(url)
            ingest(text)
            if station_url:
                report['station_page_count'] += 1
                report['search_scope'] = 'station'
            report.update(status='ok', message='CHINTAI 공개 목록에서 비교 후보를 읽었습니다. 조건과 모집 상태를 원문에서 확인하세요.')

            if station_url is None:
                resolved = resolve_station(text, subject, base_url=url)
                if resolved:
                    checked_url(resolved)
                    station_url = resolved
                    report['station_resolution'] = 'observed_public_link'
                    destination = build_search_url(subject, regions=_REGIONS, station_url=station_url)
                    if destination not in attempted:
                        url = destination
                        report['stop_reason'] = 'page_limit'
                        continue
            filtered = build_search_url(subject, regions=_REGIONS, station_url=station_url)
            if filtered not in attempted:
                url = filtered
            elif not broad and len(found) < 5:
                broad = True
                wider = build_search_url(subject, regions=_REGIONS, station_url=station_url, broad=True)
                url = wider if wider not in attempted else next_page(text, url, max_page=MAX_SEARCH_PAGES)
            else:
                url = next_page(text, url, max_page=MAX_SEARCH_PAGES)
            report['stop_reason'] = 'page_limit' if url else 'results_exhausted'
        if url and report['search_page_count'] >= MAX_SEARCH_PAGES:
            report['partial'] = True
            report['stop_reason'] = 'page_limit'
    except PublicFetchError as error:
        report.update(context, error_code=error.code, partial=True)
        if error.code in ('source_blocked', 'robots_blocked'):
            report.update(status='blocked', stop_reason='blocked',
                          message='CHINTAI의 접근 제한을 확인해 추가 조회를 중단했습니다.')
        elif error.code == 'parse_changed':
            report.update(status='parse_changed', stop_reason='parse_changed',
                          message='CHINTAI 목록 형식을 확인하지 못해 추가 조회를 중단했습니다.')
        else:
            report.update(status='unavailable', stop_reason='time_limit' if error.code == 'timeout' else 'unavailable',
                          message='CHINTAI 사이트 응답 또는 연결 문제로 추가 조회를 중단했습니다.')
    finally:
        try:
            rows = list(found.values())
            order = _rank_candidates(rows, subject, current_year=int(started[:4]))
            result['listings'] = [rows[index] for index in order[:60]]
            report['partial'] = report['partial'] or len(rows) > 60
            report['listing_count'] = len(result['listings'])
            report['conflicting_url_count'] = len(conflicts)
            report['station_count'] = report['listing_count']
            report['discovered_station_count'] = len(rows)
            report['distinct_building_count'] = len({(clean(row.get('address')), clean(row.get('building_name')))
                                                    for row in result['listings'] if row.get('address') and row.get('building_name')})
            scope = ('공개 링크로 확인한 ' + subject['station_name'] + ' 중심' if report['station_page_count']
                     else subject['municipality'] + ' 검색 · 역 집중 목록 미확인')
            report['search_scope_label'] = scope + ' · 목록 ' + str(report['search_page_count']) + '페이지'
            report['area_scope_label'] = '반환 후보는 같은 역·평면, 대상 전용면적 ±20%'
            report['stop_reason_label'] = {
                'page_limit': '목록 3페이지 한도에서 검색을 마쳤습니다.',
                'results_exhausted': '조회한 조건에서 다음 페이지 링크가 없어 검색을 마쳤습니다.',
                'blocked': '사이트 접근 제한을 확인해 추가 조회를 중단했습니다.',
                'time_limit': '전체 45초 처리 한도에서 추가 조회를 중단했습니다.',
                'parse_changed': '목록 형식을 확인하지 못해 추가 조회를 중단했습니다.',
                'unavailable': '사이트 응답 또는 연결 문제로 추가 조회를 중단했습니다.',
            }.get(report['stop_reason'], '공개 목록 일부를 확인했습니다.')
            report['result_order'] = '같은 역·평면·면적 범위의 후보를 건물 조건과 다양성으로 정렬 (가격 무관)'
        finally:
            _SEARCH_SLOT.release()
    return result
