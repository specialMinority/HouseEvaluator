"""Read the observed Yahoo rental building/room cards and matching JSON facts.

The visible advertisement stays authoritative. Embedded construction dates are
used only after matching the exact DOM building and room IDs plus visible facts.
No scripts run, no hidden inventory is emitted, and age never becomes a year.
"""
import re
from urllib.parse import urljoin, urlsplit

from .chintai_html import _money
from .personal import validate_listing
from .public_fetch import PublicFetchError
from .public_html import (Node, Tree, address_region, area, building_floors,
                          building_type, clean, elevator, floor, layout, missing,
                          select_access, station_name, structure)

MAX_BUILDINGS = 50
MAX_ROOMS_PER_BUILDING = 8
MAX_HTML_CHARACTERS = 3 * 1024 * 1024
ORIGIN = 'https://realestate.yahoo.co.jp'
_DETAIL = re.compile(r'/rent/detail/(_?[0-9a-f]{44})/')


def detail_url(value):
    if (not isinstance(value, str) or len(value) > 256 or '\\' in value
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
            or not (value.startswith('/rent/detail/') or value.startswith(ORIGIN + '/'))):
        return None
    parts = urlsplit(urljoin(ORIGIN, value))
    if (parts.scheme != 'https' or parts.netloc != 'realestate.yahoo.co.jp'
            or parts.query or parts.fragment or not _DETAIL.fullmatch(parts.path)):
        return None
    return ORIGIN + parts.path


def _one(node, cls):
    matches = list(node.all(cls=cls))
    return matches[0] if len(matches) == 1 else None


def _text(node, *, omit=()):
    if node is None:
        return ''
    pieces, stack = [], [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            pieces.append(current)
        elif isinstance(current, Node) and not set(omit).intersection(current.attrs.get('class', '').split()):
            stack.extend(reversed(current.children))
    return clean(' '.join(pieces))


def _routes(node):
    result = []
    for label in node.all('span', 'ListCassette__txt'):
        match = re.fullmatch(r'([^/]+)駅\s*/\s*(.+?)\s+徒歩\s*([0-9]{1,3})分', clean(label.text()))
        if not match or int(match[3]) > 180 or re.search(r'(?:バス|車)\s*[0-9]+分', match[2]):
            continue
        route = {'station_name': station_name(match[1]), 'line': clean(match[2]), 'walk_min': int(match[3])}
        if route not in result:
            result.append(route)
    return result


def _name(title):
    if re.fullmatch(r'.+駅\s+(?:(?:徒歩[0-9]+分|地上[0-9]+階建て?|地下[0-9]+階地上[0-9]+階建て?|築[0-9]+年|新築)\s*)+', title):
        return None
    return title or None


def _specs(value):
    parts = clean(value).split('/')
    ages = {int(match[1]) for part in parts if (match := re.fullmatch(r'築([0-9]{1,3})年', part.strip())) and int(match[1]) <= 300}
    levels = {building_floors(re.sub(r'階建て', '階建', part.strip())) for part in parts} - {None}
    materials = {structure(part) for part in parts} - {None}
    return {
        'building_age_years': next(iter(ages)) if len(ages) == 1 else None,
        'building_floors': next(iter(levels)) if len(levels) == 1 else None,
        'structure': next(iter(materials)) if len(materials) == 1 else None,
    }


def _context_buildings(text):
    from .yahoo_discovery import page_context
    page = page_context(text)
    if not isinstance(page, dict) or not isinstance(page.get('properties'), list) or len(page['properties']) > 500:
        return {}
    found, bad = {}, set()
    for building in page['properties']:
        if not isinstance(building, dict):
            continue
        identity = building.get('StructureId')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
            continue
        if identity in found:
            bad.add(identity)
        else:
            found[identity] = building
    return {key: value for key, value in found.items() if key not in bad}


def _embedded_facts(item, building, building_id, room_id, fetched_at):
    """Return aligned metadata or a conflict; missing mapping adds no facts."""
    if not building or building.get('StructureId') != building_id:
        return {}, False
    groups = building.get('GroupProperties')
    if not isinstance(groups, list) or len(groups) > 500:
        return {}, False
    candidates = [room for room in groups if isinstance(room, dict) and room.get('PropertyId') == room_id]
    if len(candidates) != 1:
        return {}, False
    room = candidates[0]
    if room.get('StructureId') != building_id:
        return {}, True
    location = building.get('LocationView')
    address = location.get('AddressName') if isinstance(location, dict) else None
    # Embedded JSON is untrusted and can drift independently of the DOM. A
    # malformed metadata value cannot make a visible advertisement disappear
    # or accidentally compare True as floor 1; use the DOM without enrichment.
    if (not all(isinstance(value, str) for value in (building.get('BuildingName'),
            address, building.get('KindName'), room.get('PriceLabel'), room.get('MonopolyAreaLabel')))
            or type(building.get('TotalFloorNum')) is not int
            or type(room.get('FloorNum')) not in (str, int)
            or (room.get('MonthlyManagementCostLabel') is not None and not isinstance(room['MonthlyManagementCostLabel'], str))
            or any(value is not None and not isinstance(value, str) for value in (building.get('BuiltOn'), room.get('BuiltOn')))
            or (building.get('CanDisplayBuildingName') is not None and type(building['CanDisplayBuildingName']) is not bool)):
        return {}, False
    visible = (
        (clean(building.get('BuildingName')), item['title']),
        (clean(address), item['address']),
        (building_type(building.get('KindName')), item['building_type']),
        (building.get('TotalFloorNum'), item['building_floors']),
        (_money(room.get('PriceLabel')), item['rent_yen']),
        (_money(room.get('MonthlyManagementCostLabel')), item['mgmt_fee_yen']),
        (area(Tree(room['MonopolyAreaLabel']).root.text()) if isinstance(room.get('MonopolyAreaLabel'), str) and len(room['MonopolyAreaLabel']) <= 100 else None, item['area_sqm']),
        (floor(str(room['FloorNum']) + '階') if isinstance(room.get('FloorNum'), (str, int)) else None, item['floor']),
    )
    if any(left is not None and left != '' and right is not None and left != right for left, right in visible):
        return {}, True
    # Every identifying visible fact must be independently present and equal.
    # Fee may explicitly be unknown in both versions; it is never filled here.
    if any(left in (None, '') or right in (None, '') or left != right for index, (left, right) in enumerate(visible) if index != 5):
        return {}, False
    result = {}
    if building.get('CanDisplayBuildingName') is False:
        result['building_name'] = None
    dates = (building.get('BuiltOn'), room.get('BuiltOn'))
    if dates[0] and dates[1] and dates[0] != dates[1]:
        return {}, True
    date = re.fullmatch(r'([0-9]{4})-([0-9]{2})', dates[0]) if isinstance(dates[0], str) and dates[0] == dates[1] else None
    if date and 1800 <= int(date[1]) <= int(fetched_at[:4]) and 1 <= int(date[2]) <= 12:
        result.update(built_year=int(date[1]), built_month=int(date[2]))
    return result, False


def parse_search(text, *, fetched_at, regions, target_station=None, conflicts_out=None):
    if not isinstance(text, str) or len(text) > MAX_HTML_CHARACTERS:
        raise PublicFetchError('html_too_complex')
    tree = Tree(text).root
    cards = list(tree.all('li', 'ListBukken__item'))
    embedded = _context_buildings(text)
    listings, seen, conflicts = [], {}, set()
    for card in cards[:MAX_BUILDINGS]:
        if any(node is not card for node in card.all('li', 'ListBukken__item')):
            continue
        header = _one(card, 'ListCassette__ttl__txt')
        tag = _one(card, 'ListCassette__ttl__tag')
        information = _one(card, 'ListCassette__list')
        if not header or not tag or not information:
            continue
        parts = [node for node in information.children if isinstance(node, Node) and node.tag == 'li' and 'ListCassette__item' in node.attrs.get('class', '').split()]
        if len(parts) != 3:
            continue
        title, label, address = clean(header.text()), clean(tag.text()), clean(parts[1].text())
        if not title or len(title) > 200 or not address or len(address) > 240:
            continue
        kind = building_type(label)
        if kind is None and label not in ('一戸建て', 'タウンハウス'):
            continue
        city, municipality = address_region(address, regions)
        routes = _routes(parts[0])
        specs = _specs(parts[2].text())
        building_id = card.attrs.get('data-structureid')
        context = embedded.get(building_id)
        name = _name(title)
        if context and clean(context.get('BuildingName')) == title and context.get('CanDisplayBuildingName') is False:
            name = None
        accepted = 0
        for room in card.all('li', 'ListCassetteRoom__item'):
            if 'roomListItem' not in room.attrs.get('class', '').split():
                continue
            links = list(room.all('a', 'ListCassetteRoom__textLink'))
            urls = {detail_url(link.attrs.get('href')) for link in links}
            if len(urls) != 1 or None in urls:
                continue
            url = next(iter(urls))
            if url in conflicts:
                continue
            identity = _DETAIL.fullmatch(urlsplit(url).path)[1]
            checkboxes = list(room.all('input', '_propertyCheckbox'))
            if len(checkboxes) != 1 or checkboxes[0].attrs.get('value') != identity:
                conflicts.add(url)
                continue
            price = _one(room, 'ListCassetteRoom__dtl__price')
            fees = _one(room, 'ListCassetteRoom__dtl__price__txtS')
            level = _one(room, 'ListCassetteRoom__block--floor')
            plans = list(room.all(cls='ListCassetteRoom__dtl__layout'))
            if not price or not level or len(plans) != 2:
                continue
            fee_text = re.sub(r'^管理費等\s*', '', clean(fees.text())) if fees else ''
            amenity_box = _one(room, 'ListCassetteRoom__tagLink')
            amenities = '\n'.join(node.text() for node in amenity_box.all('span')) if amenity_box else ''
            item = {
                'source_id': 'yahoo_realestate', 'source_url': url, 'source_listing_id': identity,
                'title': title, 'building_name': name, 'address': address,
                'city': city, 'municipality': municipality,
                'rent_yen': _money(_text(price, omit=('ListCassetteRoom__dtl__price__txtS',))),
                'mgmt_fee_yen': _money(fee_text), 'layout': layout(plans[0].text()),
                'area_sqm': area(plans[1].text()), 'floor': floor(level.text()),
                'building_type': kind, **specs,
                'built_year': None, 'built_month': None,
                'property_type': 'apartment' if kind else 'house' if label == '一戸建て' else None,
                'orientation': None, 'elevator': elevator(amenities), 'bathroom_separate': None,
                'furnished': None, 'contract_type': None, 'accesses': [dict(route) for route in routes],
                'fetched_at': fetched_at, 'details_fetched_at': None, 'listing_updated_date': None,
            }
            select_access(item, target_station)
            extra, mismatch = _embedded_facts(item, embedded.get(building_id), building_id, identity, fetched_at)
            if mismatch:
                conflicts.add(url)
                continue
            item.update(extra)
            if url in seen:
                if item != seen[url]:
                    conflicts.add(url)
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
    if conflicts_out is not None:
        conflicts_out.update(conflicts)
    listings = [item for item in listings if item['source_url'] not in conflicts]
    empty = not cards and bool(re.search(r'(?:該当する物件(?:は|が)ありません|条件に一致する物件が見つかりません|該当物件(?:数)?\s*0\s*件|(?<![0-9])0\s*件の賃貸物件)', clean(tree.text())))
    return listings, len(cards), empty
