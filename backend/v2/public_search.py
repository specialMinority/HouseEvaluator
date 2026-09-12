"""On-demand public listing comparison input, separate from licensed snapshots.

At most three search pages and 24 detail pages; no inventory persistence, no
vacancy guarantee, and no invented source rights or canonical room identities.
"""

from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import math
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .public_fetch import PublicFetchError, PublicResponse, USER_AGENT, fetch_public
from .public_html import clean, enrich_detail, parse_search, station_name
from .personal import validate_listing
from .public_discovery import line_choices, station_choice, next_page


SEARCH_SECONDS = 45
MAX_DETAILS = 24
MAX_DETAILS_PER_BUILDING = 2
MAX_SEARCH_PAGES = 3
STATION_CACHE_SECONDS = 6 * 60 * 60
_STATION_CACHE = {}
_SEARCH_SLOT = threading.BoundedSemaphore(1)
_TOKYO = "千代田区 中央区 港区 新宿区 文京区 台東区 墨田区 江東区 品川区 目黒区 大田区 世田谷区 渋谷区 中野区 杉並区 豊島区 北区 荒川区 板橋区 練馬区 足立区 葛飾区 江戸川区".split()
_OSAKA = [("都島区", 102), ("福島区", 103), ("此花区", 104), ("西区", 106), ("港区", 107), ("大正区", 108), ("天王寺区", 109),
          ("浪速区", 111), ("西淀川区", 113), ("東淀川区", 114), ("東成区", 115), ("生野区", 116), ("旭区", 117), ("城東区", 118),
          ("阿倍野区", 119), ("住吉区", 120), ("東住吉区", 121), ("西成区", 122), ("淀川区", 123), ("鶴見区", 124), ("住之江区", 125),
          ("平野区", 126), ("北区", 127), ("中央区", 128)]
_REGIONS = [
    {"id": "tokyo", "name": "도쿄", "prefecture": "東京都", "ar": "030", "ta": "13",
     "municipalities": [{"name": name, "code": str(13101 + index)} for index, name in enumerate(_TOKYO)]},
    {"id": "osaka", "name": "오사카", "prefecture": "大阪府", "ar": "060", "ta": "27",
     "municipalities": [{"name": "大阪市" + name, "code": str(27000 + code)} for name, code in _OSAKA]},
    {"id": "fukuoka", "name": "후쿠오카", "prefecture": "福岡県", "ar": "090", "ta": "40",
     "municipalities": [{"name": "福岡市" + name, "code": str(40131 + index)} for index, name in enumerate("東区 博多区 中央区 南区 西区 城南区 早良区".split())]},
]
_LAYOUTS = {"1R": "01", "1K": "02", "1DK": "03", "1LDK": "04"}


def options():
    return {"cities": [{"id": city["id"], "name": city["name"], "municipalities": deepcopy(city["municipalities"])} for city in _REGIONS],
            "sources": [{"id": "suumo", "name": "SUUMO 공개 검색", "kind": "public_page", "automatic": True}],
            "layouts": list(_LAYOUTS), "max_search_seconds": SEARCH_SECONDS,
            "notice": "공개 검색 결과 일부를 비교합니다. 모집 중 여부와 전체 시장을 보장하지 않습니다."}


def build_search_url(subject, *, station=None, broad=False):
    city = next((city for city in _REGIONS if city["id"] == subject.get("city")), None)
    municipality = next((item for item in city["municipalities"] if item["name"] == subject.get("municipality")), None) if city else None
    code = _LAYOUTS.get(subject.get("layout"))
    area = subject.get("area_sqm")
    if not city or not municipality or not code or type(area) not in (int, float) or not math.isfinite(area) or not .1 <= area <= 1000:
        raise PublicFetchError("unsupported_subject")
    if not isinstance(subject.get("station_name"), str) or not 1 <= len(subject["station_name"].strip()) <= 100:
        raise PublicFetchError("unsupported_subject")
    # Start near the target instead of filling the page with smaller studios.
    # A second, explicitly reported search can cover the wider 20% range.
    bounds = [0, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 9999999]
    tolerance = .20 if broad else .05
    lower = max(value for value in bounds[:-1] if value <= area * (1 - tolerance))
    upper = min(value for value in bounds[1:] if value >= area * (1 + tolerance))
    values = {"ar": city["ar"], "bs": "040", "ta": city["ta"], "sc": municipality["code"], "md": code,
              "mb": lower, "mt": upper, "pc": 30, "cb": "0.0", "ct": "9999999", "et": "9999999", "cn": "9999999"}
    if station:
        if (station_name(station.get('station_name')) != station_name(subject['station_name'])
                or station.get('ar') != city['ar'] or station.get('bs') != '040'
                or not re.fullmatch(r'[0-9]{4}', station.get('rn', ''))
                or not re.fullmatch(r'[0-9]{9}', station.get('ek', ''))
                or not re.fullmatch(r'[0-9]{3}', station.get('ra', ''))
                or not station['ek'].startswith(station['rn'])):
            raise PublicFetchError('invalid_station')
        values.pop('sc')
        values.pop('ta')
        values.update(rn=station['rn'], ek=station['ek'], ra=station['ra'])
    kind = {'mansion': '1', 'apartment': '2'}.get(subject.get('building_type'))
    if kind:
        values['ts'] = kind
    return "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?" + urlencode(values)


def _wildcard_match(pattern, path):
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    chunks = pattern.split("*")
    if not path.startswith(chunks[0]):
        return False
    position = len(chunks[0])
    if len(chunks) == 1:
        return not anchored or position == len(path)
    for chunk in chunks[1:-1]:
        if chunk:
            found = path.find(chunk, position)
            if found < 0:
                return False
            position = found + len(chunk)
    tail = chunks[-1]
    if anchored:
        return path.endswith(tail) and len(path) - len(tail) >= position
    return not tail or path.find(tail, position) >= 0


class RobotsPolicy:
    """Relevant RFC 9309 grouping, longest match, Allow ties, * and $ rules."""
    def __init__(self, text, user_agent=USER_AGENT):
        groups, agents, rules, delay, started = [], [], [], None, False
        lines = text.splitlines()
        if len(lines) > 5000:
            raise PublicFetchError("robots_too_large")
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (item.strip() for item in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if started:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, started = [], [], None, False
                agents.append(value.lower())
            elif agents and key in ("allow", "disallow"):
                started = True
                if len(value) > 2048:
                    raise PublicFetchError("robots_too_large")
                if value and value.startswith("/"):
                    # Percent-encoded unreserved octets equal their literal
                    # spelling; reserved octets keep their encoded semantics.
                    value = re.sub(r"%[0-9a-fA-F]{2}", lambda match: chr(int(match[0][1:], 16))
                        if chr(int(match[0][1:], 16)) in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
                        else match[0].upper(), value)
                    rules.append((key == "allow", value))
            elif agents and key == "crawl-delay":
                started = True
                try:
                    parsed = float(value)
                    if math.isfinite(parsed) and parsed >= 0:
                        delay = parsed
                except ValueError:
                    pass
        if agents:
            groups.append((agents, rules, delay))
        product = user_agent.lower().split("/", 1)[0]
        specific = [(max((len(agent) for agent in agents if agent != "*" and agent in product), default=0), rules, delay)
                    for agents, rules, delay in groups]
        longest = max((score for score, _, _ in specific), default=0)
        matched = [(rules, delay) for score, rules, delay in specific if longest and score == longest]
        if not longest:
            matched = [(rules, delay) for agents, rules, delay in groups if "*" in agents]
        self.recognized = bool(groups)
        self.rules = [rule for rules, _ in matched for rule in rules]
        self.delay = max((delay for _, delay in matched if delay is not None), default=0)

    def allows(self, url):
        if not self.recognized:
            return False
        parts = urlsplit(url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        matches = [(len(pattern.rstrip("$").replace("*", "").encode("utf8")), allow)
                   for allow, pattern in self.rules if _wildcard_match(pattern, path)]
        return max(matches)[1] if matches else True


def _decode(response):
    charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", response.content_type)
    encoding = charset[1].lower() if charset else "utf-8"
    if encoding not in ("utf-8", "utf8", "shift_jis", "shift-jis", "windows-31j", "cp932", "euc-jp", "us-ascii"):
        raise PublicFetchError("encoding_denied")
    try:
        return response.body.decode("cp932" if encoding == "windows-31j" else encoding, errors="strict")
    except (UnicodeError, LookupError):
        raise PublicFetchError("encoding_denied") from None


def _blocked(text):
    # Inspect title/visible block messages rather than incidental JS URLs.
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    message = clean(title[1]).lower() if title else ""
    return any(token in message for token in ("captcha", "access denied", "forbidden", "robot", "アクセス制限")) or any(
        token in text for token in ("ロボットではないことを確認", "アクセスが集中しています", "不正なアクセスを検知", "お客様のアクセスを制限"))


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rank_candidates(items, subject, *, current_year):
    """Preserve station/layout priority while spreading similar rooms by building.

    Advertised age is only a discovery hint. It is never used to populate the
    exact construction year required by the comparison engine.
    """
    target = station_name(subject["station_name"])
    built_year = subject.get("built_year")
    target_age = current_year - built_year if type(built_year) is int and 1800 <= built_year <= current_year else None
    target_type = subject.get("building_type")
    target_floors = subject.get("building_floors")
    remaining = set(range(len(items)))
    building_counts, ranked = {}, []

    def position(floor, total):
        if floor is None:
            return None
        if floor < 0:
            return 'basement'
        if floor <= 1:
            return 'ground'
        if total is None:
            return None
        return 'top' if floor == total else 'middle'

    target_position = position(subject.get('floor'), target_floors)

    def priority(index):
        item = items[index]
        relative_area = abs(item["area_sqm"] - subject["area_sqm"]) / subject["area_sqm"]
        area_band = next((index for index, bound in enumerate((.05, .10, .20)) if relative_area <= bound + 1e-9), 3)
        building = (clean(item.get("address")), clean(item.get("building_name")))
        # Unknown identities do not count as one shared building.
        times_seen = building_counts.get(building, 0) if all(building) else 0
        age = item.get("building_age_years")
        age_distance = abs(age - target_age) if target_age is not None and type(age) is int else 999
        kind = item.get("building_type")
        type_priority = 0 if target_type is None or kind == target_type else 1 if kind is None else 2
        total_floors = item.get("building_floors")
        height_distance = abs(total_floors - target_floors) if type(target_floors) is int and type(total_floors) is int else 999
        # Spread similarly sized buildings before taking many rooms from one
        # building whose height happens to equal the target exactly.
        height_priority = 0 if target_floors is None or height_distance <= 2 else 1 if total_floors is None else 2
        floor_position = position(item.get('floor'), total_floors)
        floor_distance = abs(item['floor'] - subject['floor']) if item.get('floor') is not None and subject.get('floor') is not None else 999
        floor_priority = (0 if target_position is None or (floor_position == target_position and floor_distance <= 4)
                          else 1 if floor_position is None else 2)
        return (item["station_name"] != target, item["layout"] != subject["layout"], area_band == 3,
                type_priority, height_priority, floor_priority, times_seen, area_band, age_distance, height_distance, floor_distance, relative_area, index)

    while remaining:
        selected = min(remaining, key=priority)
        remaining.remove(selected)
        ranked.append(selected)
        building = (clean(items[selected].get("address")), clean(items[selected].get("building_name")))
        if all(building):
            building_counts[building] = building_counts.get(building, 0) + 1
    return ranked


def _station_key(subject):
    return subject['city'], clean(subject['municipality']), station_name(subject['station_name'])


def _detail_candidates(items):
    """First room per known building, then at most one additional room.

    This is request-budget allocation, not a claim of canonical building IDs.
    """
    buckets = {}
    for index, item in enumerate(items):
        key = (clean(item.get('address')), clean(item.get('building_name')))
        if not all(key):
            key = ('unknown', index)
        buckets.setdefault(key, []).append(index)
    return [indices[round_] for round_ in range(MAX_DETAILS_PER_BUILDING)
            for indices in buckets.values() if len(indices) > round_][:MAX_DETAILS]


def _cached_station(subject):
    key = _station_key(subject)
    record = _STATION_CACHE.get(key)
    if record and time.monotonic() - record['cached_at'] < STATION_CACHE_SECONDS:
        return dict(record['station'])
    _STATION_CACHE.pop(key, None)
    return None


def _resolve_station(subject, items, read):
    """Resolve observed route+station names against public checkbox values.

    Different station identities with the same written name are ambiguous.
    Metadata caching never caches or fabricates rental inventory.
    """
    target = station_name(subject['station_name'])
    names = Counter(clean(route.get('line')) for item in items for route in item.get('accesses', [])
                    if station_name(route.get('station_name')) == target and route.get('line'))
    if not names or len(names) > 3:
        return None
    directory = 'https://suumo.jp/chintai/' + subject['city'] + '/ensen/'
    choices = line_choices(read(directory, 'metadata'), subject['city'], names)
    if {choice['line'] for choice in choices} != set(names) or len(choices) > 3:
        return None
    matches = []
    for line in choices:
        match = station_choice(read(line['source_url'], 'metadata'), line, target)
        if match is None:
            return None
        matches.append(match)
    if not matches or len({match['ek'][4:] for match in matches}) != 1:
        return None
    chosen = min(matches, key=lambda match: (-names[match['line']], match['rn']))
    chosen['verified_at'] = _timestamp()
    _STATION_CACHE[_station_key(subject)] = {'cached_at': time.monotonic(), 'station': dict(chosen)}
    # Bound the process-local navigation cache independently of request volume.
    if len(_STATION_CACHE) > 100:
        oldest = min(_STATION_CACHE, key=lambda key: _STATION_CACHE[key]['cached_at'])
        _STATION_CACHE.pop(oldest)
    return chosen


def search(subject, *, fetcher=None):
    fetcher = fetcher or fetch_public
    started = _timestamp()
    report = {"source_id": "suumo", "status": "unavailable", "message": "공개 검색에 연결하지 못했습니다.",
              "search_url": None, "fetched_at": None, "listing_count": 0, "detail_count": 0,
              "detail_attempt_count": 0, "partial": False, "search_page_count": 0,
              "detail_unavailable_count": 0,
              "station_page_count": 0,
              "metadata_page_count": 0, "discovered_count": 0, "rejected_count": 0,
              "duplicate_count": 0, "out_of_scope_count": 0, "station_count": 0,
              "distinct_building_count": 0, "search_urls": [], "area_searches": [],
              "search_scope": "municipality_fallback", "station_resolution": "unresolved",
              "stop_reason": "unavailable", "detail_limit": MAX_DETAILS,
              "detail_limit_per_building": MAX_DETAILS_PER_BUILDING,
              "parser_limits": {"building_cards_per_page": 50, "listings_per_card": 8}}
    result = {"listings": [], "source_reports": [report], "searched_at": started,
              "scope": "public_search_sample", "price_filter_applied": False,
              "notice": "최대 3개 목록 페이지의 일부입니다. 페이지당 최대 50개 건물 카드·카드당 8개 광고를 읽으며 현재 모집 여부·전체 시장·재사용 권리를 보장하지 않습니다."}
    try:
        report["search_url"] = build_search_url(subject)
    except (PublicFetchError, TypeError, AttributeError):
        report.update(status="unsupported", stop_reason="unsupported", message="지원하는 도시·행정구·방 구성·면적·역 이름을 입력해 주세요.")
        return result
    if not _SEARCH_SLOT.acquire(blocking=False):
        report.update(message="다른 공개 검색을 처리 중입니다. 잠시 후 다시 시도해 주세요.")
        return result
    deadline = time.monotonic() + SEARCH_SECONDS
    found, observed, conflicts = {}, {}, set()
    station = _cached_station(subject)
    if station:
        report['station_resolution'] = 'cached_public_form'
    halted = False

    def in_scope(item):
        return (item['city'] == subject['city'] and item['municipality'] == subject['municipality']
                and item['layout'] == subject['layout'] and item['station_name'] == station_name(subject['station_name'])
                and item['area_sqm'] is not None
                and abs(item['area_sqm'] - subject['area_sqm']) <= subject['area_sqm'] * .20 + .00001)

    def read(url, kind):
        if not policy.allows(url):
            raise PublicFetchError('robots_blocked')
        if time.monotonic() + delay + .1 >= deadline:
            raise PublicFetchError('timeout')
        time.sleep(delay)
        if kind == 'search':
            report['search_urls'].append(url)
        response = fetcher(url, deadline=deadline)
        if response.status in (401, 403, 429):
            raise PublicFetchError('source_blocked')
        if response.status != 200:
            raise PublicFetchError('http_' + str(response.status))
        text = _decode(response)
        if _blocked(text):
            raise PublicFetchError('source_blocked')
        if kind == 'metadata':
            report['metadata_page_count'] += 1
        return text

    def fail(exc):
        report['partial'] = True
        report['error_code'] = exc.code
        if exc.code in ('robots_blocked', 'source_blocked'):
            report.update(status='blocked', stop_reason='blocked', message='사이트의 자동 접근 제한을 확인해 추가 요청을 중단했습니다. 읽은 표본만 표시합니다.')
        elif exc.code == 'parse_changed':
            report.update(status='parse_changed', stop_reason='parse_changed', message='검색 페이지 형식을 해석하지 못해 추가 조회를 중단했습니다.')
        else:
            report.update(status='unavailable', stop_reason='time_limit' if exc.code == 'timeout' else 'unavailable', message='공개 검색의 시간 한도 또는 사이트 응답으로 추가 조회를 마쳤습니다. 읽은 표본만 표시합니다.')

    def ingest(text):
        stamp = _timestamp()
        items, cards, empty = parse_search(text, fetched_at=stamp, regions=_REGIONS, target_station=subject['station_name'])
        if not items and not empty:
            raise PublicFetchError('parse_changed')
        report['search_page_count'] += 1
        report['discovered_count'] += len(items)
        report['fetched_at'] = stamp
        if cards >= 50:
            report['partial'] = True
        for item in items:
            try:
                validate_listing(item)
            except ValueError:
                report['rejected_count'] += 1
                continue
            url = item['source_url']
            if url in conflicts:
                report['duplicate_count'] += 1
                continue
            ignored = {'fetched_at', 'details_fetched_at', 'missing_fields'}
            fingerprint = {k: v for k, v in item.items() if k not in ignored}
            if url in observed:
                report['duplicate_count'] += 1
                if fingerprint != observed[url]:
                    conflicts.add(url)
                    found.pop(url, None)
                    report['rejected_count'] += 2
                continue
            observed[url] = fingerprint
            if not in_scope(item):
                report['out_of_scope_count'] += 1
                continue
            found[url] = item
        return items

    try:
        robots = fetcher('https://suumo.jp/robots.txt', deadline=deadline)
        if robots.status != 200:
            raise PublicFetchError('source_blocked' if robots.status in (401, 403, 429) else 'robots_unavailable')
        policy = RobotsPolicy(_decode(robots))
        delay = max(.15, policy.delay)
        broad = False
        url = build_search_url(subject, station=station)
        attempted = set()
        try:
            while url and report['search_page_count'] < MAX_SEARCH_PAGES:
                if url in attempted:
                    break
                attempted.add(url)
                report['search_url'] = url
                text = read(url, 'search')
                page_items = ingest(text)
                report.update(status='ok', message='공개 검색 결과 일부를 읽었습니다. 표의 조건과 원문을 확인해 주세요.')
                query = parse_qs(urlsplit(url).query)
                if station:
                    report['station_page_count'] += 1
                bounds = {'min_sqm': int(query['mb'][0]), 'max_sqm': int(query['mt'][0]), 'scope': 'station' if station else 'municipality'}
                if bounds not in report['area_searches']:
                    report['area_searches'].append(bounds)
                observed_route = any(station_name(route.get('station_name')) == station_name(subject['station_name']) and route.get('line')
                                     for item in page_items for route in item.get('accesses', []))
                if (station is None and report['station_resolution'] == 'unresolved' and observed_route
                        and report['search_page_count'] < MAX_SEARCH_PAGES):
                    station = _resolve_station(subject, page_items, read)
                    report['station_resolution'] = 'public_form' if station else 'not_found_in_public_form'
                    if station:
                        broad = False
                        url = build_search_url(subject, station=station)
                        continue
                following = next_page(text, url, MAX_SEARCH_PAGES)
                if following:
                    url = following
                elif not broad and build_search_url(subject, station=station, broad=True) not in attempted:
                    broad = True
                    url = build_search_url(subject, station=station, broad=True)
                else:
                    url = None
                report['stop_reason'] = 'page_limit' if url else 'results_exhausted'
            if report['search_page_count'] >= MAX_SEARCH_PAGES and url:
                report['stop_reason'] = 'page_limit'
                report['partial'] = True
        except PublicFetchError as exc:
            halted = True
            fail(exc)
        if station:
            report['search_scope'] = 'station' if report['station_page_count'] else 'municipality_fallback'
            report['station'] = {key: station[key] for key in ('station_name', 'line', 'rn', 'ek', 'ra', 'source_url', 'verified_at')}
        items = list(found.values())
        report['discovered_station_count'] = sum(item['station_name'] == station_name(subject['station_name']) for item in items)
        candidates = _rank_candidates(items, subject, current_year=int(started[:4]))
        items = [items[index] for index in candidates[:60]]
        result['listings'] = items
        if not halted:
            for index in _detail_candidates(items):
                try:
                    if time.monotonic() + delay + .1 >= deadline:
                        raise PublicFetchError('timeout')
                    url = items[index]['source_url']
                    # An attempt counts an actual request, not a robots refusal.
                    if not policy.allows(url):
                        raise PublicFetchError('robots_blocked')
                    report['detail_attempt_count'] += 1
                    text = read(url, 'detail')
                    enriched = enrich_detail(items[index], text, fetched_at=_timestamp(), regions=_REGIONS, target_station=subject['station_name'])
                    if enriched:
                        try:
                            validate_listing(enriched)
                            items[index] = enriched
                            if in_scope(enriched):
                                report['detail_count'] += 1
                        except ValueError:
                            report['partial'] = True
                    else:
                        report['partial'] = True
                except PublicFetchError as exc:
                    if exc.code in ('http_404', 'http_410'):
                        report['partial'] = True
                        report['detail_unavailable_count'] += 1
                        continue
                    # Access restrictions, redirects and transport errors never
                    # trigger another route, proxy, or request retry.
                    fail(exc)
                    break
        retained = [item for item in items if in_scope(item)]
        report['out_of_scope_count'] += len(items) - len(retained)
        items = retained
        result['listings'] = items
        report['listing_count'] = len(items)
        report['station_count'] = sum(item['station_name'] == station_name(subject['station_name']) for item in items)
        report['distinct_building_count'] = len({(clean(item['address']), clean(item['building_name'])) for item in items if item.get('address') and item.get('building_name')})
        report['partial'] = bool(report['partial'] or report['rejected_count'] or report['detail_count'] < len(items) or len(found) > len(items))
        return result
    except PublicFetchError as exc:
        fail(exc)
        return result
    finally:
        scope = ('공식 역 선택폼을 확인한 ' + subject['station_name'] + ' 중심 ' + str(report['station_page_count']) + '페이지'
                 if report['station_page_count'] else subject['municipality'] + ' 검색 · 역 집중 페이지를 조회하지 못함')
        report['search_scope_label'] = scope + ' (목록 최대 3페이지)'
        report['area_scope_label'] = ' → '.join(str(bounds['min_sqm']) + '~' + str(bounds['max_sqm']) + 'm²' for bounds in report['area_searches'])
        labels = {'page_limit': '목록 3페이지 한도에서 검색을 마쳤습니다.', 'results_exhausted': '조회한 검색 조건에서 다음 페이지 링크가 없어 목록 검색을 마쳤습니다.',
                  'time_limit': '전체 45초 처리 한도에서 추가 조회를 중단했습니다.', 'blocked': '사이트 접근 제한을 확인해 추가 조회를 중단했습니다.',
                  'parse_changed': '페이지 형식을 확인하지 못해 추가 조회를 중단했습니다.', 'unavailable': '사이트 응답 또는 연결 문제로 추가 조회를 중단했습니다.'}
        report['stop_reason_label'] = labels.get(report['stop_reason'], '공개 검색 일부를 확인했습니다.')
        report['result_order'] = '가까운 면적부터 검색; 같은 역·평면·종류·유사 규모 안에서 건물별 순환 후 상세 조회 (가격 무관)'
        _SEARCH_SLOT.release()
