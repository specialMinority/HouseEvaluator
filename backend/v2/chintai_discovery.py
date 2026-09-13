"""CHINTAI navigation from observed public forms, never guessed station codes.

Observed 2026-09-13: municipality /area/{JIS}/list/, station /ensen/{9 digits}/
list/, and GET /list/ with prefkey + g/ue, rt, m, sf/st, cf/ct, o. The returned
pagination link uses i and can add urlType=dynamic. Robots remains a separate
check: never reorder a returned link to evade a path-specific robots rule.
"""
import math
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from .public_html import Tree, clean, station_name


ORIGIN = 'https://www.chintai.net'
_AREA_CODES = {
    'tokyo': {str(value) for value in range(13101, 13124)},
    'osaka': {str(27000 + value) for value in (102, 103, 104, 106, 107, 108, 109,
              111, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128)},
    'fukuoka': {str(value) for value in range(40131, 40138)},
}
_LAYOUTS = {'1R': '0', '1K': '1', '1DK': '2', '1LDK': '3', '2K': '4',
            '2DK': '5', '2LDK': '6', '3K': '7', '3DK': '8', '3LDK': '9',
            '4K': 'A', '4DK': 'B', '4LDK': 'C', '5K': 'D', '5DK': 'D'}
_AREA_CHOICES = (0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
                 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200)
_FIELDS = frozenset(('prefkey', 'g', 'ue', 'rt', 'o', 'm', 'sf', 'st', 'cf', 'ct', 'i', 'urlType'))
_CANONICAL = re.compile(r'/(tokyo|osaka|fukuoka)/(area|ensen)/([0-9]+)/list/(?:page([23])/)?')


def _error(code):
    # Public transport imports this validator lazily; keep module import cycles
    # out of the navigation/parser layer.
    from .public_fetch import PublicFetchError
    return PublicFetchError(code)


def _pairs(parts):
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError('duplicate query field')
    return values


def checked_url(url):
    """Allow only this source's bounded, observed rental endpoint contract."""
    try:
        if (not isinstance(url, str) or len(url) > 2048 or '\\' in url or '%' in url or '#' in url
                or any(ord(char) < 33 or ord(char) > 126 for char in url)):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.netloc != 'www.chintai.net'
                or parts.hostname != 'www.chintai.net' or parts.username is not None
                or parts.password is not None or parts.fragment):
            raise ValueError()
        if parts.path == '/robots.txt' or re.fullmatch(r'/detail/bk-[A-Z0-9]{20,40}/', parts.path):
            if '?' in url:
                raise ValueError()
            return parts
        canonical = _CANONICAL.fullmatch(parts.path)
        if canonical:
            city, kind, code, _ = canonical.groups()
            if '?' in url or (kind == 'area' and code not in _AREA_CODES[city]):
                raise ValueError()
            if kind == 'ensen' and (not re.fullmatch(r'[0-9]{9}', code) or int(code) == 0):
                raise ValueError()
            return parts
        if parts.path != '/list/' or not parts.query:
            raise ValueError()
        values = _pairs(parts)
        if set(values) - _FIELDS or not {'prefkey', 'rt', 'o', 'm', 'sf', 'st', 'cf', 'ct'} <= values.keys():
            raise ValueError()
        city = values['prefkey']
        if city not in _AREA_CODES or ('g' in values) == ('ue' in values):
            raise ValueError()
        if 'g' in values and (values['g'] not in _AREA_CODES[city] or values['rt'] != '50'):
            raise ValueError()
        if 'ue' in values and (not re.fullmatch(r'[0-9]{9}', values['ue'])
                               or int(values['ue']) == 0 or values['rt'] != '51'):
            raise ValueError()
        if values['o'] != '10' or values['m'] not in set(_LAYOUTS.values()):
            raise ValueError()
        if values['cf'] != '0' or values['ct'] != '0':
            raise ValueError()
        allowed_area = {str(value) for value in _AREA_CHOICES}
        if values['sf'] not in allowed_area or values['st'] not in allowed_area:
            raise ValueError()
        if values['st'] != '0' and int(values['sf']) > int(values['st']):
            raise ValueError()
        if values.get('i', '1') not in ('1', '2', '3') or values.get('urlType', 'dynamic') != 'dynamic':
            raise ValueError()
        return parts
    except (ValueError, TypeError, UnicodeError):
        raise _error('endpoint_denied') from None


def _city_and_area(subject, regions):
    region = next((item for item in regions if item.get('id') == subject.get('city')), None)
    if region is None or region['id'] not in _AREA_CODES:
        raise _error('unsupported_city')
    municipality = next((item for item in region.get('municipalities', [])
                         if item.get('name') == subject.get('municipality')), None)
    if municipality is None or municipality.get('code') not in _AREA_CODES[region['id']]:
        raise _error('unsupported_municipality')
    return region['id'], municipality['code']


def municipality_url(subject, *, regions):
    city, code = _city_and_area(subject, regions)
    return f'{ORIGIN}/{city}/area/{code}/list/'


def area_bounds(area_sqm, *, broad=False):
    """Round outward to real form options; zero is the unbounded sentinel."""
    if type(area_sqm) not in (int, float) or not math.isfinite(area_sqm) or area_sqm <= 0:
        raise _error('invalid_area')
    margin = .25 if broad else .10
    low, high = area_sqm * (1 - margin), area_sqm * (1 + margin)
    lower = max((value for value in _AREA_CHOICES if value <= low), default=0)
    upper = min((value for value in _AREA_CHOICES if value >= high and value != 0), default=0)
    return lower, upper


def build_search_url(subject, *, regions, station_url=None, broad=False):
    city, code = _city_and_area(subject, regions)
    if subject.get('layout') not in _LAYOUTS:
        raise _error('unsupported_layout')
    sf, st = area_bounds(subject.get('area_sqm'), broad=broad)
    values = [('prefkey', city)]
    if station_url is not None:
        parts = checked_url(station_url)
        match = _CANONICAL.fullmatch(parts.path)
        if not match or match[1] != city or match[2] != 'ensen' or match[4] is not None:
            raise _error('invalid_station')
        values.extend((('ue', match[3]), ('rt', '51')))
    else:
        values.extend((('g', code), ('rt', '50')))
    values.extend((('o', '10'), ('m', _LAYOUTS[subject['layout']]), ('sf', str(sf)),
                   ('st', str(st)), ('cf', '0'), ('ct', '0')))
    url = ORIGIN + '/list/?' + urlencode(values)
    checked_url(url)
    return url


def resolve_station(text, subject, *, base_url):
    """Resolve an exact visible station name only when its source code is unique."""
    try:
        base = checked_url(base_url)
    except ValueError:
        return None
    city, wanted = subject.get('city'), station_name(subject.get('station_name', ''))
    if city not in _AREA_CODES or not wanted:
        return None
    base_match = _CANONICAL.fullmatch(base.path)
    base_city = base_match[1] if base_match else _pairs(base).get('prefkey') if base.query else None
    if base_city != city:
        return None
    matches = set()
    for link in Tree(text).root.all('a'):
        if station_name(clean(link.text())) != wanted:
            continue
        target = urljoin(base_url, link.attrs.get('href', ''))
        try:
            parts = checked_url(target)
        except ValueError:
            continue
        match = _CANONICAL.fullmatch(parts.path)
        if match and match[1] == city and match[2] == 'ensen' and match[4] is None:
            matches.add(target)
    return next(iter(matches)) if len(matches) == 1 else None


def next_page(text, current_url, max_page=3):
    """Return an observed next link unchanged; callers must check robots again."""
    if type(max_page) is not int or not 1 <= max_page <= 3:
        return None
    try:
        current = checked_url(current_url)
        canonical = _CANONICAL.fullmatch(current.path)
        values = _pairs(current) if current.query else {}
    except ValueError:
        return None
    number = int(values.get('i', '1')) if current.query else int(canonical[4] or '1') if canonical else 0
    if number < 1 or number >= max_page:
        return None
    # urlType is a presentation marker inserted by the returned /list/ links,
    # not a location, price or condition filter.
    baseline = {key: value for key, value in values.items() if key not in ('i', 'urlType')}
    for link in Tree(text).root.all('a'):
        target = urljoin(current_url, link.attrs.get('href', ''))
        try:
            candidate = checked_url(target)
        except ValueError:
            continue
        if current.query:
            if candidate.path != current.path or not candidate.query:
                continue
            other = _pairs(candidate)
            if other.pop('i', None) != str(number + 1):
                continue
            other.pop('urlType', None)
            if other == baseline:
                return target
        elif canonical:
            match = _CANONICAL.fullmatch(candidate.path)
            if match and match.groups()[:3] == canonical.groups()[:3] and match[4] == str(number + 1):
                return target
    return None
