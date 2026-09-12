"""Read station choices and pagination from SUUMO's public HTML forms.

These are navigation hints, not a station database or rental observations.
"""
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from .public_html import Tree, clean, station_name


def line_choices(text, city, observed_lines):
    tree = Tree(text).root
    form = next((node for node in tree.all('form') if node.attrs.get('id') == 'js-gotoEkiForm'), None)
    if form is None:
        return []
    wanted = {clean(name) for name in observed_lines}
    choices = {}
    for row in form.all('li'):
        controls = [node for node in row.all('input') if node.attrs.get('name') == 'rn']
        links = list(row.all('a'))
        if len(controls) != 1 or len(links) != 1 or clean(links[0].text()) not in wanted:
            continue
        code = controls[0].attrs.get('value', '')
        url = urljoin('https://suumo.jp', links[0].attrs.get('href', ''))
        parts = urlsplit(url)
        if (not re.fullmatch(r'[0-9]{4}', code) or parts.scheme != 'https' or parts.netloc != 'suumo.jp'
                or parts.query or parts.fragment or not re.fullmatch('/chintai/' + re.escape(city) + r'/en_[a-z0-9_]{1,80}/', parts.path)):
            continue
        choice = {'rn': code, 'line': clean(links[0].text()), 'source_url': url}
        choices[(code, url)] = choice
    return list(choices.values())


def station_choice(text, line, target):
    tree = Tree(text).root
    form = next((node for node in tree.all('form') if node.attrs.get('id') == 'js-areaSelectForm'), None)
    if form is None:
        return None
    routes = {node.attrs.get('value') for node in form.all('input') if node.attrs.get('name') == 'rn'}
    if routes != {line['rn']}:
        return None
    navigation = next((node for node in tree.all('form') if node.attrs.get('id') == 'js-showEkiForm'), None)
    if navigation is None:
        return None
    metadata = {}
    for node in navigation.all('input'):
        name, value = node.attrs.get('name'), node.attrs.get('value')
        if name in ('ar', 'bs', 'ra', 'rn'):
            if name in metadata and metadata[name] != value:
                return None
            metadata[name] = value
    if (metadata.get('rn') != line['rn'] or metadata.get('bs') != '040'
            or not re.fullmatch(r'[0-9]{3}', metadata.get('ar', ''))
            or not re.fullmatch(r'[0-9]{3}', metadata.get('ra', ''))):
        return None
    codes = set()
    for row in form.all('li'):
        controls = [node for node in row.all('input') if node.attrs.get('name') == 'ek']
        name = re.sub(r'\s*\([0-9,]+\)\s*$', '', clean(row.text())).strip()
        if station_name(name) != station_name(target) or len(controls) != 1:
            continue
        code = controls[0].attrs.get('value', '')
        if re.fullmatch(r'[0-9]{9}', code) and code.startswith(line['rn']):
            codes.add(code)
    if len(codes) != 1:
        return None
    return dict(line, ek=next(iter(codes)), ar=metadata['ar'], bs=metadata['bs'], ra=metadata['ra'], station_name=station_name(target))


def next_page(text, current_url, limit=3):
    """Follow only an observed next-page link with identical search filters."""
    current = urlsplit(current_url)
    pairs = parse_qsl(current.query, keep_blank_values=True)
    values = dict(pairs)
    if len(values) != len(pairs):
        return None
    number = int(values.pop('page', '1'))
    if number >= limit:
        return None
    for link in Tree(text).root.all('a'):
        target = urljoin(current_url, link.attrs.get('href', ''))
        parts = urlsplit(target)
        if parts.scheme != current.scheme or parts.netloc != current.netloc or parts.path != current.path or parts.fragment:
            continue
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        candidate = dict(pairs)
        if len(candidate) != len(pairs) or candidate.pop('page', '') != str(number + 1):
            continue
        if candidate == values:
            return target
    return None
