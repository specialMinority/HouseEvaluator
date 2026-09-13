"""Read CHINTAI's observed building/room tables without inferring missing facts.

Only ordinary building cards are supported. PR cards, interactive additional
rooms and unrelated page fragments are not an inventory or detail-page source.
"""
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urljoin, urlsplit

from .personal import validate_listing
from .public_fetch import PublicFetchError
from .public_html import (Node, Tree, accesses, address_region, area, building_floors,
                          building_type, clean, floor, layout, missing, select_access, structure)

MAX_BUILDINGS = 50
MAX_ROOMS_PER_BUILDING = 8
MAX_HTML_CHARACTERS = 3 * 1024 * 1024
# Same observed detail-ID contract as chintai_discovery.checked_url. Keep this
# local to avoid the public_html -> public_fetch -> discovery import cycle.
_DETAIL = re.compile(r'/detail/bk-([A-Z0-9]{20,40})/')


def detail_url(value):
    if (not isinstance(value, str) or len(value) > 256 or '\\' in value
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        return None
    if not (value.startswith('/detail/') or value.startswith('https://www.chintai.net/')):
        return None
    parsed = urlsplit(urljoin('https://www.chintai.net', value))
    if (parsed.scheme != 'https' or parsed.netloc != 'www.chintai.net'
            or parsed.query or parsed.fragment or not _DETAIL.fullmatch(parsed.path)):
        return None
    return 'https://www.chintai.net' + parsed.path


def _text(node, *, skip=(), breaks=False):
    parts, stack = [], [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Node) and not set(skip).intersection(item.attrs.get('class', '').split()):
            if breaks and item.tag == 'br':
                parts.append('\n')
            else:
                stack.extend(reversed(item.children))
    return clean(''.join(parts))


def _cells(row, tags=('th', 'td')):
    return [node for node in row.children if isinstance(node, Node) and node.tag in tags] if row else []


def _money(value):
    value = re.sub(r'\s+', '', clean(value)).replace(',', '')
    if value in ('なし', '無し', '不要', '無料'):
        return 0
    matched = re.fullmatch(r'([0-9]{1,8}(?:\.[0-9]{1,4})?)(万)?円', value)
    if not matched:
        return None
    try:
        amount = Decimal(matched[1]) * (10000 if matched[2] else 1)
        return int(amount) if amount == amount.to_integral_value() else None
    except InvalidOperation:
        return None


def _building_fields(card):
    information = list(card.all(cls='bukken_information'))
    if len(information) != 1:
        return None
    fields = {}
    for row in information[0].all('tr'):
        cells = _cells(row)
        for label, value in zip(cells, cells[1:]):
            if label.tag != 'th' or value.tag != 'td':
                continue
            key = clean(label.text()).replace(' ', '')
            observed = _text(value, skip=('map',)) if key == '住所' else clean(value.text())
            if key in fields and fields[key] != observed:
                return None
            fields[key] = observed
    return fields


def _room_columns(table):
    head = table.first('thead')
    headers = _cells(head.first('tr') if head else None, ('th',))
    columns = {}
    for key in ('floar', 'price', 'layout'):
        found = [index for index, node in enumerate(headers) if key in node.attrs.get('class', '').split()]
        if len(found) != 1:
            return None
        columns[key] = found[0]
    return columns, len(headers)


def _room_floor(cell):
    labels = list(cell.all('li'))
    if not labels:
        return floor(cell.text())
    observed = {floor(label.text()) for label in labels} - {None}
    return next(iter(observed)) if len(observed) == 1 else None


def _building_name(title):
    # Station/height/age descriptions are advertisement titles, not verified
    # building names. Do not merge them with a nearby named advertisement.
    description = re.fullmatch(r'.+駅\s+(.+)', title)
    if description:
        facts = re.sub(r'\s+', '', description[1])
        if re.fullmatch(r'(?:徒歩[0-9]+分)?(?:[0-9]+階建(?:\(地下[0-9]+階\))?)?(?:築[0-9]+年|新築)?', facts) and facts:
            return None
    return title or None


def parse_search(text, *, fetched_at, regions, target_station=None, conflicts_out=None):
    if not isinstance(text, str) or len(text) > MAX_HTML_CHARACTERS:
        raise PublicFetchError('html_too_complex')
    tree = Tree(text).root
    cards = [node for node in tree.all('section', 'cassette_item') if 'build' in node.attrs.get('class', '').split()]
    listings, seen, conflicts = [], {}, set()
    for card in cards[:MAX_BUILDINGS]:
        # Nested cards are ambiguous: never combine another building's facts.
        if any(node is not card for node in card.all('section', 'cassette_item')):
            continue
        fields = _building_fields(card)
        heading_box = card.first(cls='cassette_ttl')
        heading = heading_box.first('h2') if heading_box else None
        detail_box = card.first(cls='cassette_detail')
        table = detail_box.first('table') if detail_box else None
        columns = _room_columns(table) if table else None
        if fields is None or heading is None or columns is None:
            continue
        indices, expected_cells = columns
        label = clean(heading.first(cls='icn_typeB').text()) if heading.first(cls='icn_typeB') else ''
        name = _text(heading, skip=('icn_typeB', 'icn_recommend'))
        address = fields.get('住所', '')
        if len(name) > 200 or len(address) > 240:
            continue
        city, municipality = address_region(address, regions)
        routes = accesses(fields.get('交通', ''))
        date = re.fullmatch(r'([0-9]{4})年([0-9]{1,2})月(?:\([^)]*\))?', fields.get('築年', ''))
        built_year = int(date[1]) if date and 1800 <= int(date[1]) <= int(fetched_at[:4]) and 1 <= int(date[2]) <= 12 else None
        built_month = int(date[2]) if built_year is not None else None
        accepted = 0
        # All rows are inspected for conflicting IDs, even past the output cap.
        for room in table.all('tbody', 'js-detailLinkUrl'):
            url = detail_url(room.attrs.get('data-detailurl'))
            if url is None or url in conflicts:
                continue
            identity = _DETAIL.fullmatch(urlsplit(url).path)[1]
            if any(room.attrs.get(key) not in (None, identity) for key in ('data-bkkey', 'data-cn-bkkey')):
                continue
            advertised = [node for node in room.all('a') if node.attrs.get('data-detailurl')]
            if any(detail_url(node.attrs['data-detailurl']) != url for node in advertised):
                continue
            link_cell = room.first('td', 'detail')
            link = link_cell.first('a') if link_cell else None
            if link is not None and detail_url(link.attrs.get('href')) != url:
                continue
            rows = list(room.all('tr', 'detail-inner'))
            if len(rows) != 1:
                continue
            cells = _cells(rows[0], ('td',))
            if len(cells) != expected_cells:
                continue
            prices = [line.strip() for line in _text(cells[indices['price']], breaks=True).split('\n')]
            plans = [line.strip() for line in _text(cells[indices['layout']], breaks=True).split('\n')]
            if not 1 <= len(prices) <= 2 or len(plans) != 2:
                continue
            item = {
                'source_id': 'chintai', 'source_url': url, 'source_listing_id': identity,
                'title': name or None, 'building_name': _building_name(name), 'address': address or None,
                'city': city, 'municipality': municipality,
                'rent_yen': _money(prices[0]), 'mgmt_fee_yen': _money(prices[1]) if len(prices) == 2 else None,
                'layout': layout(plans[0]), 'area_sqm': area(plans[1]),
                'floor': _room_floor(cells[indices['floar']]),
                'building_type': building_type(label), 'building_floors': building_floors(fields.get('階建', '')),
                'structure': structure(fields.get('構造', '')), 'built_year': built_year, 'built_month': built_month,
                'property_type': 'apartment' if label in ('賃貸マンション', '賃貸アパート') else 'house' if label == '賃貸一戸建て' else None,
                'orientation': None, 'elevator': None, 'bathroom_separate': None, 'furnished': None,
                'contract_type': None, 'accesses': [dict(route) for route in routes], 'fetched_at': fetched_at,
                'details_fetched_at': None, 'listing_updated_date': None,
            }
            select_access(item, target_station)
            if url in seen:
                if item != seen[url]:
                    conflicts.add(url)
                    listings = [row for row in listings if row['source_url'] != url]
                continue
            seen[url] = dict(item)
            if item['rent_yen'] is None or item['area_sqm'] is None or item['layout'] is None:
                continue
            try:
                validate_listing(item)
            except ValueError:
                continue
            if accepted < MAX_ROOMS_PER_BUILDING:
                listings.append(missing(item))
                accepted += 1
    # A page-local conflict must also invalidate any earlier page's copy of the
    # same URL. Keep the public three-value return contract for other callers.
    if conflicts_out is not None:
        conflicts_out.update(conflicts)
    empty = not cards and bool(re.search(r'(?:該当する物件(?:は|が)ありません|条件に一致する物件が見つかりません|(?<![0-9])0\s*件の賃貸物件情報)', clean(tree.text())))
    return listings, len(cards), empty
