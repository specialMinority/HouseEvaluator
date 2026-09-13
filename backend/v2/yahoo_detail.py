"""Identity-bound Yahoo! Real Estate detail facts; never execute page scripts.

Only the current property's state and visible summary are read. Recommendations,
opaque facility codes, age-derived dates and absent amenities are not facts.
"""
import re

from .chintai_html import MAX_HTML_CHARACTERS, _cells, _money, _text
from .personal import ENUMS, validate_listing
from .public_fetch import PublicFetchError
from .public_html import Node, Tree, address_region, area, building_type, clean, elevator, layout, orientation, select_access, structure
from .yahoo_discovery import checked_url, page_context


def detail_url(value):
    try:
        parts = checked_url(value)
    except PublicFetchError:
        return None
    return value if re.fullmatch(r'/rent/detail/_?[0-9a-f]{44}/', parts.path) else None


def _compact(value):
    return re.sub(r'\s+', '', clean(value))


def _summary_fields(root):
    tables, stack = [], [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, Node):
            continue
        classes = node.attrs.get('class', '').split()
        if 'DetailRecommend' in classes:
            continue
        if node.tag == 'table' and 'DetailSummaryTable' in classes:
            tables.append(node)
        stack.extend(reversed(node.children))
    result = {}
    for table in tables:
        for row in table.all('tr'):
            cells = _cells(row)
            for label, value in zip(cells, cells[1:]):
                if label.tag != 'th' or value.tag != 'td':
                    continue
                key, observed = _compact(label.text()), _text(value, skip=('DetailSummaryTable__textLink',))
                if key in result and _compact(result[key]) != _compact(observed):
                    raise PublicFetchError('ambiguous_listing')
                result[key] = observed
    return result


def _agree(left, right):
    if left is not None and right is not None and left != right:
        raise PublicFetchError('ambiguous_listing')


def _plain_area(value):
    # The source's human-readable area label has this single observed markup.
    return area(re.sub(r'<sup>2</sup>', '2', clean(value)))


def _levels(value):
    match = re.fullmatch(r'(?:地上)?([0-9]{1,3})階建て/((?:地下|B)?[0-9]{1,3})階部分', _compact(value))
    if not match:
        return None, None
    number = match[2]
    return int(match[1]), -int(re.sub(r'^(?:地下|B)', '', number)) if number.startswith(('地下', 'B')) else int(number)


def _exact_year(value, fetched_at):
    match = re.fullmatch(r'([0-9]{4})-([0-9]{2})', clean(value))
    return int(match[1]) if match and 1800 <= int(match[1]) <= int(fetched_at[:4]) and 1 <= int(match[2]) <= 12 else None


def _features(page):
    result = []
    for group in page.get('otherFacilities', []) if isinstance(page.get('otherFacilities'), list) else []:
        if isinstance(group, dict) and isinstance(group.get('facilityLabels'), list):
            result.extend(label for label in group['facilityLabels'] if isinstance(label, str))
    for value in page.get('popularFacilities', []) if isinstance(page.get('popularFacilities'), list) else []:
        if isinstance(value, dict) and value.get('disabled') is False and isinstance(value.get('label'), str):
            result.append(value['label'])
    return {_compact(value) for value in result}


def parse_detail(text, *, source_url, fetched_at, regions):
    if not isinstance(text, str) or len(text) > MAX_HTML_CHARACTERS:
        raise PublicFetchError('html_too_complex')
    if detail_url(source_url) != source_url:
        raise PublicFetchError('endpoint_denied')
    root = Tree(text).root
    canonicals = [node.attrs.get('href') for node in root.all('link') if 'canonical' in node.attrs.get('rel', '').split()]
    if canonicals != [source_url]:
        raise PublicFetchError('ambiguous_listing')
    page = page_context(text, require_properties=False)
    prop = page.get('property') if isinstance(page, dict) else None
    if not isinstance(prop, dict):
        raise PublicFetchError('parse_changed')
    if prop.get('PropertyId') != source_url.rstrip('/').rsplit('/', 1)[1]:
        raise PublicFetchError('ambiguous_listing')
    headings = list(root.all('h1', 'DetailHeadingLarge__title'))
    summaries = list(root.all(cls='DetailSummary'))
    if len(headings) != 1 or len(summaries) != 1:
        raise PublicFetchError('parse_changed')
    fields = _summary_fields(root)
    if not {'賃料/管理費・共益費等', '間取り', '専有面積'}.issubset(fields):
        raise PublicFetchError('parse_changed')
    value = lambda key: fields.get(key, '')
    amounts = value('賃料/管理費・共益費等').split('/')
    if len(amounts) != 2:
        raise PublicFetchError('parse_changed')
    rent, fee = _money(amounts[0]), _money(amounts[1])
    displayed_rents = list(summaries[0].all(cls='DetailSummary__price__rent'))
    if len(displayed_rents) != 1 or rent is None:
        raise PublicFetchError('parse_changed')
    _agree(rent, _money(displayed_rents[0].text()))
    _agree(rent, _money(prop.get('PriceLabel')))
    if 'Price' in prop:
        if type(prop['Price']) is not int:
            raise PublicFetchError('invalid_listing_fields')
        _agree(rent, prop['Price'])
    _agree(fee, _money(prop.get('MonthlyManagementCostLabel')))
    if fee is not None and prop.get('MonthlyManagementCost') is not None:
        if type(prop['MonthlyManagementCost']) is not int:
            raise PublicFetchError('invalid_listing_fields')
        _agree(fee, prop['MonthlyManagementCost'])
    plan, sqm = layout(value('間取り')), area(value('専有面積'))
    if plan is not None and plan not in ENUMS['layout']:
        raise PublicFetchError('unsupported_layout')
    details = prop.get('DetailsView') if isinstance(prop.get('DetailsView'), dict) else {}
    _agree(plan, layout(re.split(r'\s*\(', clean(details.get('RoomLayoutBreakdown')), maxsplit=1)[0]))
    _agree(sqm, _plain_area(prop.get('MonopolyAreaLabel')))
    view = prop.get('StructureView') if isinstance(prop.get('StructureView'), dict) else {}
    total, level = _levels(value('階建/階'))
    state_total, state_level = _levels(view.get('FloorNameLabel'))
    _agree(total, state_total)
    _agree(level, state_level)
    for field, observed in (('TotalFloorNum', total), ('FloorNum', level)):
        raw = view.get(field)
        if raw is not None:
            if isinstance(raw, bool) or not re.fullmatch(r'-?[0-9]{1,3}', str(raw)):
                raise PublicFetchError('invalid_listing_fields')
            _agree(observed, int(raw))
    notes = list(headings[0].all(cls='DetailHeadingLarge__note'))
    if len(notes) != 1:
        raise PublicFetchError('parse_changed')
    note = re.fullmatch(r'\(((?:地下|B)?[0-9]+)階/([^/]+)/([^/]+)\)', _compact(notes[0].text()))
    if not note:
        raise PublicFetchError('parse_changed')
    note_level = -int(re.sub(r'^(地下|B)', '', note[1])) if note[1].startswith(('地下', 'B')) else int(note[1])
    _agree(level, note_level)
    _agree(plan, layout(note[2]))
    _agree(sqm, area(note[3]))
    year = _exact_year(prop.get('BuiltOn'), fetched_at)
    visible_date = re.search(r'([0-9]{4})年([0-9]{1,2})月', value('築年数'))
    if visible_date and year is not None:
        _agree(clean(prop.get('BuiltOn'))[:7], f'{int(visible_date[1]):04d}-{int(visible_date[2]):02d}')
    address = value('所在地') or None
    location = prop.get('LocationView') if isinstance(prop.get('LocationView'), dict) else {}
    if address and location.get('AddressName'):
        _agree(_compact(address), _compact(location['AddressName']))
    city, municipality = address_region(address or '', regions)
    if address and city is None:
        raise PublicFetchError('unsupported_region')
    material = structure(value('構造'))
    _agree(material, structure(prop.get('StructureName')))
    kind = building_type(prop.get('KindName'))
    name = clean(view.get('BuildingName')) if prop.get('CanDisplayBuildingName') is True else None
    if name and value('建物名'):
        _agree(_compact(name), _compact(value('建物名')))
    if name and re.search(r'駅.*(?:階建|築[0-9]+年)', name):
        name = None
    routes = []
    for route in prop.get('Transports', []) if isinstance(prop.get('Transports'), list) else []:
        if not isinstance(route, dict) or route.get('BusLineName') or route.get('BusMinutes') or route.get('OtherTransport'):
            continue
        walk, station = route.get('MinutesFromStation'), clean(route.get('StationName'))
        label = re.fullmatch(r'([^/]+)駅/[^/]+\s+徒歩([0-9]{1,3})分', clean(route.get('Label')))
        if type(walk) not in (int, float) or not 0 <= walk <= 180 or not station or not label:
            continue
        if label[1] == station and int(label[2]) == walk:
            routes.append({'station_name': station, 'walk_min': walk, 'line': clean(route.get('LineName'))})
    features = _features(page)
    contract = clean(details.get('ContractPeriod'))
    item = {
        'city': city, 'municipality': municipality, 'title': clean(headings[0].text()), 'address': address,
        'building_name': name, 'rent_yen': rent, 'mgmt_fee_yen': fee, 'layout': plan, 'area_sqm': sqm,
        'floor': level, 'building_floors': total, 'built_year': year, 'building_type': kind,
        'property_type': 'apartment' if kind else 'house' if prop.get('KindName') in ('一戸建て', '戸建て') else None,
        'structure': material, 'orientation': orientation(value('方位')),
        'elevator': elevator('、'.join(sorted(features))),
        'bathroom_separate': True if features.intersection(('バス・トイレ独立', 'バス・トイレ別', 'バストイレ別')) else None,
        'furnished': True if features.intersection(('家具付き', '家具付', '家具・家電付き')) else None,
        'contract_type': 'standard' if contract.startswith('普通借家') else 'fixed_term' if contract.startswith('定期借家') else None,
        'source_id': 'yahoo_realestate', 'source_url': source_url, 'fetched_at': fetched_at, 'accesses': routes,
    }
    select_access(item, None)
    try:
        return validate_listing(item)
    except ValueError:
        raise PublicFetchError('invalid_listing_fields') from None
