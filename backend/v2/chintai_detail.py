"""Partial detail-page facts from CHINTAI's observed, identity-bound main form."""
import re

from .chintai_html import MAX_HTML_CHARACTERS, _building_name, _cells, _money, _text, detail_url
from .personal import ENUMS, validate_listing
from .public_fetch import PublicFetchError
from .public_html import (Tree, accesses, address_region, area, building_floors, building_type,
                          clean, elevator, floor, layout, orientation, select_access, structure)


def _fields(container):
    result = {}
    for row in container.all('tr'):
        cells = _cells(row)
        for label, value in zip(cells, cells[1:]):
            if label.tag != 'th' or value.tag != 'td':
                continue
            key = re.sub(r'\s+', '', clean(label.text()))
            text = _text(value, skip=('mapTextLink', 'js_help_baloon'))
            if key in result and text != result[key][0]:
                raise PublicFetchError('ambiguous_listing')
            result[key] = (text, value)
    return result


def parse_detail(text, *, source_url, fetched_at, regions):
    if not isinstance(text, str) or len(text) > MAX_HTML_CHARACTERS:
        raise PublicFetchError('html_too_complex')
    if detail_url(source_url) != source_url:
        raise PublicFetchError('endpoint_denied')
    root = Tree(text).root
    canonical = [n.attrs.get('href') for n in root.all('link') if 'canonical' in n.attrs.get('rel', '').split()]
    if len(canonical) != 1 or detail_url(canonical[0]) != source_url:
        raise PublicFetchError('ambiguous_listing')
    forms = [n for n in root.all('form') if n.attrs.get('name') == 'detailForm']
    if len(forms) != 1:
        raise PublicFetchError('parse_changed')
    form = forms[0]
    identities = [n.attrs.get('value') for n in form.all('input') if n.attrs.get('id') == 'bkapi']
    expected_identity = source_url.rsplit('bk-', 1)[1].rstrip('/')
    if len(identities) != 1 or identities[0] != expected_identity:
        raise PublicFetchError('ambiguous_listing')
    basic = list(form.all(cls='detail_basicInfo'))
    if len(basic) != 1:
        raise PublicFetchError('parse_changed')
    fields = _fields(basic[0])
    if not {'住所', '間取り', '専有面積'}.issubset(fields):
        raise PublicFetchError('parse_changed')
    value = lambda key: fields.get(key, ('', None))[0]
    address = value('住所')
    city, municipality = address_region(address, regions)
    if address and city is None:
        raise PublicFetchError('unsupported_region')
    layout_node = fields['間取り'][1].first(cls='bold')
    plan_text = clean(layout_node.text()) if layout_node else re.split(r'\s*\(', value('間取り'), 1)[0]
    plan = layout(plan_text)
    if plan is not None and plan not in ENUMS['layout']:
        raise PublicFetchError('unsupported_layout')
    rents = list(basic[0].all(cls='rent'))
    if len(rents) > 1:
        raise PublicFetchError('ambiguous_listing')
    rent = _money(rents[0].text()) if rents else None
    date = re.fullmatch(r'([0-9]{4})年([0-9]{1,2})月(?:\([^)]*\))?', re.sub(r'\s+', '', value('築年')))
    year = int(date[1]) if date and 1800 <= int(date[1]) <= int(fetched_at[:4]) and 1 <= int(date[2]) <= 12 else None
    label = value('建物種別')
    level = floor(value('物件階層'))
    titles = [node for box in form.all(cls='mod_h2Box') for node in box.all('h2')]
    if len(titles) > 1:
        raise PublicFetchError('ambiguous_listing')
    title_node = titles[0] if titles else None
    title = clean(title_node.text()) if title_node else None
    name = None
    if title and '/' in title:
        left, right = title.split('/', 1)
        heading_match = re.fullmatch(r'(.*?)\s+((?:地下|B)?[0-9]+階)', left.strip())
        if heading_match:
            if level is not None and floor(heading_match[2]) != level:
                raise PublicFetchError('ambiguous_listing')
            name = _building_name(heading_match[1].strip())
        heading_address = right.removesuffix('の賃貸物件詳細').strip()
        if address and re.sub(r'\s+', '', heading_address) != re.sub(r'\s+', '', address):
            raise PublicFetchError('ambiguous_listing')
    spec_boxes = list(form.all(cls='detail_specTable'))
    spec = {}
    features = []
    for box in spec_boxes:
        for key, observed in _fields(box).items():
            if key in spec and spec[key][0] != observed[0]:
                raise PublicFetchError('ambiguous_listing')
            spec[key] = observed
        features.extend(_text(n, skip=('js_help_baloon',)) for n in box.all('span', 'js_help'))
    contract = spec.get('契約期間', ('', None))[0]
    item = {
        'city': city, 'municipality': municipality, 'title': title, 'building_name': name,
        'address': address or None, 'layout': plan, 'area_sqm': area(value('専有面積')),
        'rent_yen': rent, 'mgmt_fee_yen': _money(value('管理費等')),
        'structure': structure(value('構造')), 'built_year': year,
        'building_type': building_type(label), 'building_floors': building_floors(value('物件階層')),
        'floor': level, 'orientation': orientation(value('方位')),
        'property_type': 'apartment' if label in ('マンション', 'アパート') else 'house' if label in ('一戸建て', '貸家') else None,
        'elevator': elevator('、'.join(features)),
        'bathroom_separate': True if any(re.sub(r'\s+', '', f) in ('バス・トイレ別', 'バストイレ別') for f in features) else None,
        'furnished': True if any(re.sub(r'\s+', '', f) in ('家具付き', '家具付', '家具・家電付き') for f in features) else None,
        'contract_type': 'standard' if contract.startswith('普通借家') else 'fixed_term' if contract.startswith('定期借家') else None,
        'source_id': 'chintai', 'source_url': source_url, 'fetched_at': fetched_at,
        'accesses': accesses(re.sub(r'([0-9])\s+分', r'\1分', value('交通'))),
    }
    select_access(item, None)
    try:
        normalized = validate_listing(item)
    except ValueError:
        raise PublicFetchError('invalid_listing_fields') from None
    if sum(normalized[key] is not None for key in ('city', 'layout', 'area_sqm', 'rent_yen')) < 2:
        raise PublicFetchError('parse_changed')
    return normalized
