"""Navigation restricted to Yahoo! Real Estate's observed public rental URLs.

The saved page exposes station links and pagination. Layout/area option names
exist in client state, but their URL serialization was not observed; this module
does not invent query filters or claim a station page applied those conditions.
"""
import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from .public_html import Tree, clean, station_name


ORIGIN = 'https://realestate.yahoo.co.jp'
_CITIES = {'tokyo': ('03', '13'), 'osaka': ('06', '27'), 'fukuoka': ('09', '40')}
_WARD_CODES = {
    'tokyo': {str(value) for value in range(13101, 13124)},
    'osaka': {str(27000 + value) for value in (102, 103, 104, 106, 107, 108, 109,
              111, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128)},
    'fukuoka': {str(value) for value in range(40131, 40138)},
}
_WARD = re.compile(r'/rent/search/([0-9]{2})/([0-9]{2})/([0-9]{5})/')
_STATION = re.compile(r'/rent/search/station/(?:[1-9][0-9]{3}|[1-9][0-9]{6})/')
_MAX_HTML = 3 * 1024 * 1024
_THEMES = {'1R': ('152', {'ワンルーム', '1R(ワンルーム)'}),
           '1K': ('156', {'1K、1DK'}), '1DK': ('156', {'1K、1DK'}),
           '1LDK': ('153', {'1LDK'})}


def _without_theme(path):
    return re.sub(r'theme/(?:152|153|156)/$', '', path)


def checked_url(url):
    """Validate the source-specific path before DNS or any network request."""
    from .public_fetch import PublicFetchError
    try:
        if (not isinstance(url, str) or len(url) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in url)
                or any(c in url for c in ('\\', '#', '%'))):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.netloc != 'realestate.yahoo.co.jp'
                or parts.username is not None or parts.password is not None):
            raise ValueError()
        if parts.path == '/robots.txt' or re.fullmatch(r'/rent/detail/_?[0-9a-f]{44}/', parts.path):
            if '?' in url:
                raise ValueError()
            return parts
        ward = _WARD.fullmatch(_without_theme(parts.path))
        station = _STATION.fullmatch(_without_theme(parts.path))
        city = next((city for city, codes in _CITIES.items() if ward and codes == ward.groups()[:2]), None)
        if not station and not (city and ward[3] in _WARD_CODES[city]):
            raise ValueError()
        if '?' in url:
            pairs = parse_qsl(parts.query, strict_parsing=True, keep_blank_values=True)
            if len(pairs) != 1 or pairs[0][0] != 'page' or pairs[0][1] not in ('1', '2', '3'):
                raise ValueError()
        return parts
    except (ValueError, TypeError, UnicodeError):
        raise PublicFetchError('endpoint_denied') from None


def municipality_url(subject, *, regions):
    from .public_fetch import PublicFetchError
    city = subject.get('city')
    region = next((region for region in regions if region.get('id') == city), None)
    if city not in _CITIES or region is None:
        raise PublicFetchError('unsupported_city')
    municipality = next((item for item in region.get('municipalities', [])
                         if item.get('name') == subject.get('municipality')), None)
    if municipality is None or municipality.get('code') not in _WARD_CODES[city]:
        raise PublicFetchError('unsupported_municipality')
    lc, prefecture = _CITIES[city]
    return f"{ORIGIN}/rent/search/{lc}/{prefecture}/{municipality['code']}/"


def build_search_url(subject, *, regions, station_url=None):
    """Choose the ward or an already-observed station; no speculative filters."""
    from .public_fetch import PublicFetchError
    initial = municipality_url(subject, regions=regions)
    if station_url is None:
        return initial
    parts = checked_url(station_url)
    if not _STATION.fullmatch(parts.path) or parts.query:
        raise PublicFetchError('invalid_station')
    return station_url


def resolve_station(text, subject, *, base_url):
    """Resolve a unique exact name from the ward page's related-station links."""
    try:
        base = checked_url(base_url)
    except ValueError:
        return None
    ward = _WARD.fullmatch(base.path)
    city = subject.get('city')
    wanted = station_name(subject.get('station_name', ''))
    if not ward or _CITIES.get(city) != ward.groups()[:2] or not wanted:
        return None
    matches = set()
    # ListInfo is the observed related-location navigation. Ranking and ad
    # fragments elsewhere in the page must not resolve ambiguous station names.
    for container in Tree(text).root.all(cls='ListInfo'):
        for link in container.all('a'):
            if station_name(clean(link.text())) != wanted:
                continue
            target = urljoin(base_url, link.attrs.get('href', ''))
            try:
                parts = checked_url(target)
            except ValueError:
                continue
            if _STATION.fullmatch(parts.path) and not parts.query:
                matches.add(target)
    return next(iter(matches)) if len(matches) == 1 else None


def resolve_layout_theme(text, subject, *, base_url):
    """Use an observed layout navigation link, never construct a theme URL."""
    try:
        base = checked_url(base_url)
    except ValueError:
        return None
    theme = _THEMES.get(subject.get('layout'))
    if theme is None or _without_theme(base.path) != base.path or not (_WARD.fullmatch(base.path) or _STATION.fullmatch(base.path)):
        return None
    code, names = theme
    expected_path = base.path + 'theme/' + code + '/'
    matches = set()
    for link in Tree(text).root.all('a'):
        if clean(link.text()) not in names:
            continue
        target = urljoin(base_url, link.attrs.get('href', ''))
        try:
            parts = checked_url(target)
        except ValueError:
            continue
        if parts.path == expected_path and not parts.query:
            matches.add(target)
    return next(iter(matches)) if len(matches) == 1 else None


def next_page(text, current_url, max_page=3):
    """Follow the exact observed next link without changing query order."""
    if type(max_page) is not int or not 1 <= max_page <= 3:
        return None
    try:
        current = checked_url(current_url)
    except ValueError:
        return None
    if not (_WARD.fullmatch(_without_theme(current.path)) or _STATION.fullmatch(_without_theme(current.path))):
        return None
    number = int(dict(parse_qsl(current.query)).get('page', '1'))
    if number >= max_page:
        return None
    for link in Tree(text).root.all('a'):
        target = urljoin(current_url, link.attrs.get('href', ''))
        try:
            parts = checked_url(target)
        except ValueError:
            continue
        if parts.path == current.path and dict(parse_qsl(parts.query)) == {'page': str(number + 1)}:
            return target
    return None


def page_context(text, *, require_properties=True):
    """Read only the page JSON value from the observed JS wrapper, never eval.

    Anonymous request crumbs live in the sibling `common` value. They are never
    returned, logged or used in outgoing requests. Ambiguous state fails closed.
    """
    if not isinstance(text, str) or len(text) > _MAX_HTML:
        return None
    scripts = re.findall(r'<script\b[^>]*>(.*?)</script\s*>', text, re.I | re.S)
    state_scripts = [value for value in scripts if 'window.__SERVER_SIDE_CONTEXT__' in value]
    if len(state_scripts) != 1:
        return None
    script = state_scripts[0]
    prefix = re.match(r'\s*(?:/\*<!\[CDATA\[\*/\s*)?window\.__SERVER_SIDE_CONTEXT__\s*=\s*\{', script)
    if prefix is None:
        return None

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def no_constant(value):
        raise ValueError('non-finite JSON constant')

    decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=no_constant)
    position, names, page = prefix.end(), set(), None
    try:
        while True:
            key = re.match(r'\s*([a-zA-Z_]+)\s*:\s*', script[position:])
            if key is None or key[1] not in ('common', 'page', 'mode') or key[1] in names:
                return None
            names.add(key[1])
            position += key.end()
            value, length = decoder.raw_decode(script[position:])
            position += length
            if key[1] == 'page':
                page = value
            elif key[1] == 'mode' and value != 'pc':
                return None
            separator = re.match(r'\s*([,}])', script[position:])
            if separator is None:
                return None
            position += separator.end()
            if separator[1] == '}':
                break
        if not re.fullmatch(r'\s*;?\s*(?:/\*\]\]>\*/\s*)?', script[position:]):
            return None
        if names != {'common', 'page', 'mode'} or not isinstance(page, dict):
            return None
        if require_properties and not isinstance(page.get('properties'), list):
            return None
        return page
    except (ValueError, TypeError, RecursionError, OverflowError):
        return None
