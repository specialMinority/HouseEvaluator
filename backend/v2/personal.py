"""Ephemeral, user-reviewable sample comparison; never a market valuation.

Public advertisements are not promoted to licensed observations or verified units.
The policy is deliberately explicit and uncalibrated; no price adjustment is made.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

LABELS = {
    'city': '도시', 'municipality': '행정구역', 'station_name': '역명',
    'layout': '평면', 'area_sqm': '면적', 'structure': '구조',
    'built_year': '준공 연도', 'walk_min': '역 도보', 'floor': '거주 층',
    'rent_yen': '월세', 'mgmt_fee_yen': '관리비', 'orientation': '방향',
    'bathroom_separate': '욕실·화장실 분리', 'furnished': '가구 포함',
    'contract_type': '계약 종류', 'property_type': '주택 유형',
    'building_type': '광고 건물 종류', 'building_floors': '건물 총층수', 'elevator': '엘리베이터',
}
CORE = ('city', 'municipality', 'station_name', 'layout', 'area_sqm',
        'structure', 'built_year', 'walk_min', 'floor', 'rent_yen', 'mgmt_fee_yen')
EXTRA = ('orientation', 'bathroom_separate', 'furnished', 'contract_type', 'property_type',
         'building_type', 'building_floors', 'elevator')
STRUCTURE_FAMILIES = {'rc': 'concrete', 'src': 'concrete', 'steel': 'steel', 'light_steel': 'steel', 'wood': 'wood'}
STRUCTURE_NAMES = {'rc': 'RC', 'src': 'SRC', 'steel': '철골', 'light_steel': '경량철골', 'wood': '목조'}
BUILDING_TYPES = {'mansion': '맨션', 'apartment': '아파트'}
ENUMS = {
    'city': ('tokyo', 'osaka', 'fukuoka'),
    'layout': ('1R', '1K', '1DK', '1LDK'),
    'structure': ('wood', 'light_steel', 'steel', 'rc', 'src'),
    'orientation': ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'),
    'contract_type': ('standard', 'fixed_term'),
    'property_type': ('apartment', 'house', 'shared'),
    'building_type': ('mansion', 'apartment'),
}
STEPS = (
    ('exact', '기본 조건: 면적 ±5% · 준공 ±2년 · 도보 ±2분', .05, 2, 2, True),
    ('orientation', '방향 조건 해제', .05, 2, 2, False),
    ('area', '면적 차이 ±10%까지', .10, 2, 2, False),
    ('year', '준공 연도 차이 ±5년까지', .10, 5, 2, False),
    ('walk', '역 도보 차이 ±5분까지', .10, 5, 5, False),
)


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).casefold()


def station_key(value):
    return normalized(value).removesuffix('駅')


def safe_url(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) > 2048 or '\\' in value or any(ord(c) < 33 for c in value):
        raise ValueError('출처 URL 형식을 확인해 주세요.')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError
        parts.port
    except ValueError as error:
        raise ValueError('출처는 http 또는 https URL이어야 합니다.') from error
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ''))


def _number(value, field, low, high, integer=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{LABELS[field]}: 올바른 숫자를 입력해 주세요.')
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f'{LABELS[field]}: 허용 범위 {low}~{high}를 확인해 주세요.')
    return int(value) if integer else float(value)


def validate_listing(payload, *, subject=False, now=None):
    if not isinstance(payload, dict):
        raise ValueError('매물은 JSON 객체여야 합니다.')
    now = now or datetime.now(timezone.utc)
    result = {}
    for field in ('municipality', 'station_name', 'title', 'building_name', 'address', 'source_id'):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 240):
            raise ValueError(f'{LABELS.get(field, field)}: 240자 이하 텍스트가 필요합니다.')
        result[field] = value.strip() or None if value else None
    for field, allowed in ENUMS.items():
        value = payload.get(field)
        if value == '':
            value = None
        if value is not None and (not isinstance(value, str) or value not in allowed):
            raise ValueError(f'{LABELS[field]}: 지원되지 않는 값입니다.')
        result[field] = value
    ranges = {
        'rent_yen': (1, 10000000, True), 'mgmt_fee_yen': (0, 1000000, True),
        'area_sqm': (.1, 1000, False), 'built_year': (1800, now.year, True),
        'walk_min': (0, 180, False), 'floor': (-10, 100, True),
        'building_floors': (1, 100, True),
    }
    for field, (low, high, integer) in ranges.items():
        result[field] = _number(payload.get(field), field, low, high, integer)
    if result['building_floors'] is not None and result['floor'] is not None and result['floor'] > result['building_floors']:
        raise ValueError('거주 층은 건물의 지상 총층수보다 높을 수 없습니다.')
    for field in ('bathroom_separate', 'furnished', 'elevator'):
        value = payload.get(field)
        if value is not None and type(value) is not bool:
            raise ValueError(f'{LABELS[field]}: 있음·없음·미상 중 선택해 주세요.')
        result[field] = value
    result['source_url'] = safe_url(payload.get('source_url'))
    fetched = payload.get('fetched_at')
    if fetched is not None:
        try:
            stamp = datetime.fromisoformat(fetched.replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp > now:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError('조회 시각은 시간대를 포함한 과거 시각이어야 합니다.') from error
    result['fetched_at'] = fetched
    result['missing_fields'] = [f for f in CORE + EXTRA if result[f] is None]
    if subject:
        missing = [f for f in ('city', 'municipality', 'station_name', 'layout', 'area_sqm', 'rent_yen') if result[f] is None]
        if missing:
            raise ValueError('필수 입력: ' + ', '.join(LABELS[f] for f in missing))
    return result


def _url_identity(value):
    if not value:
        return None
    parts = urlsplit(value)
    # Only SUUMO's known detail paths encode the advertised listing identity
    # in the path. Other sites may use ?id=123 to identify separate rooms.
    query = parts.query
    if parts.hostname == 'suumo.jp' and re.fullmatch(r'/chintai/(?:bc|jnc)_\d+/?', parts.path):
        query = ''
    return parts.hostname, parts.port, parts.path.rstrip('/'), query


def _building_key(row):
    address, name = normalized(row['address']), normalized(row['building_name'])
    if address and name:
        return row['city'], address, name
    return None


def _floor_band(value):
    return 'basement' if value < 0 else 'ground' if value <= 1 else 'upper'


def _height_band(value):
    if value is None:
        return None
    return next(label for limit, label in ((3, '1~3층'), (5, '4~5층'), (10, '6~10층'), (19, '11~19층'), (100, '20층 이상')) if value <= limit)


def _floor_position(row):
    floor, height = row['floor'], row['building_floors']
    if floor is None:
        return None
    if floor < 0:
        return '지하'
    if floor <= 1:
        return '1층·지상 접지층'
    if height is None:
        return None
    return '최상층' if floor == height else '중간층'


def _profile(row):
    return (row['property_type'], row['building_type'], _height_band(row['building_floors']),
            STRUCTURE_FAMILIES.get(row['structure']), row['elevator'], _floor_position(row))


def _feature_grades(subject, profile):
    def grade(target, value):
        if target is None or value is None:
            return 1
        return 0 if target == value else 2

    _, building_type, height, family, elevator, position = profile
    target_height = subject['building_floors']
    return (grade(subject['building_type'], building_type),
            grade(STRUCTURE_FAMILIES.get(subject['structure']), family),
            grade(None if target_height is None else target_height <= 3,
                  None if height is None else height == '1~3층'),
            grade(subject['elevator'], elevator), grade(_floor_position(subject), position))


def _profile_reasons(subject, row):
    """These boundaries cannot be removed just to obtain enough comparables."""
    reasons = []
    for field, transform in (('building_type', lambda value: value),
                             ('building_floors', _height_band),
                             ('structure', lambda value: STRUCTURE_FAMILIES.get(value))):
        target, observed = subject[field], row[field]
        if target is None or observed is None:
            reasons.append(LABELS[field] + ' 미확인')
        elif transform(target) != transform(observed):
            reasons.append(LABELS[field] + (' 구간 차이' if field == 'building_floors' else ' 차이'))
    if subject['building_floors'] is not None and row['building_floors'] is not None and abs(subject['building_floors'] - row['building_floors']) > 2:
        reasons.append('건물 총층수 차이 2층 초과')
    target_position, row_position = _floor_position(subject), _floor_position(row)
    if target_position is None or row_position is None:
        reasons.append('거주 층 위치 미확인')
    elif target_position != row_position:
        reasons.append('거주 층 위치 차이: ' + target_position + ' / ' + row_position)
    if subject['floor'] is not None and row['floor'] is not None and abs(subject['floor'] - row['floor']) > 4:
        reasons.append('거주 층 차이 4층 초과')
    if subject['elevator'] != row['elevator']:
        reasons.append('엘리베이터 미확인' if subject['elevator'] is None or row['elevator'] is None else '엘리베이터 유무 차이')
    return reasons


def _policies():
    """Each transition changes one declared matching rule, never a price rule."""
    policy = dict(area=.05, year=2, walk=2, orientation=True, unknown=False,
                  floor=True, bathroom=True, structure='exact')
    yield dict(policy, id='exact', label=STEPS[0][1], field=None, level='exact')
    changes = (
        ('orientation', '방향 조건 해제', 'orientation', False, 'relaxed'),
        ('area', '면적 차이 ±10%까지', 'area', .10, 'relaxed'),
        ('year', '준공 연도 차이 ±5년까지', 'year', 5, 'relaxed'),
        ('walk', '역 도보 차이 ±5분까지', 'walk', 5, 'relaxed'),
        ('unverified', '구조·준공·도보·층 미확인 매물도 참고에 포함', 'unknown', True, 'partial'),
        ('floor', '같은 거주 위치 안에서 층 차이 ±4층까지', 'floor', False, 'broad'),
        ('bathroom', '욕실·화장실 분리 조건 해제', 'bathroom', False, 'broad'),
        ('year_wide', '준공 연도 차이 ±10년까지', 'year', 10, 'broad'),
        ('walk_wide', '역 도보 차이 ±10분까지', 'walk', 10, 'broad'),
        ('area_wide', '면적 차이 ±20%까지', 'area', .20, 'broad'),
        ('structure_family', '같은 구조군까지 · RC/SRC, 철골/경량철골', 'structure', 'family', 'broad'),
        ('year_any', '준공 연도 조건 해제 · 신축 포함 주변 참고', 'year', None, 'broad'),
    )
    for identity, label, field, value, level in changes:
        policy[field] = value
        yield dict(policy, id=identity, label=label, field=field, level=level)


def _mismatch(subject, row, policy, now):
    for field in ('city', 'municipality', 'station_name', 'layout', 'area_sqm', 'rent_yen'):
        if row[field] is None:
            return '기본 정보 미확인: ' + LABELS[field]
    for field in ('city', 'municipality', 'layout'):
        if normalized(subject[field]) != normalized(row[field]):
            return LABELS[field] + ' 불일치'
    if station_key(subject['station_name']) != station_key(row['station_name']):
        return '역명 불일치: ' + row['station_name']
    for field in ('furnished', 'contract_type', 'property_type'):
        if subject[field] is not None and row[field] is not None and subject[field] != row[field]:
            return LABELS[field] + ' 불일치'
    if abs(subject['area_sqm'] - row['area_sqm']) > subject['area_sqm'] * policy['area'] + .00001:
        return f"면적 차이 ±{policy['area'] * 100:g}% 초과"
    for field in ('structure', 'built_year', 'walk_min', 'floor'):
        if not policy['unknown'] and (subject[field] is None or row[field] is None):
            return '핵심 조건 미확인: ' + LABELS[field]
    if policy['orientation'] and subject['orientation'] and row['orientation'] and subject['orientation'] != row['orientation']:
        return '방향 불일치'
    if policy['bathroom'] and subject['bathroom_separate'] is not None and row['bathroom_separate'] is not None and subject['bathroom_separate'] != row['bathroom_separate']:
        return '욕실·화장실 분리 조건 불일치'
    if subject['floor'] is not None and row['floor'] is not None:
        if (subject['floor'] < 0) != (row['floor'] < 0):
            return '지하·지상 구분 불일치'
        if policy['floor'] and _floor_band(subject['floor']) != _floor_band(row['floor']):
            return '층 구간 불일치'
        if policy['floor'] and abs(subject['floor'] - row['floor']) > 2:
            return '거주 층 차이 2층 초과'
    if policy['year'] is not None and subject['built_year'] is not None and row['built_year'] is not None:
        if (now.year - subject['built_year'] <= 1) != (now.year - row['built_year'] <= 1):
            return '신축·기존 건물 구분 불일치'
        if abs(subject['built_year'] - row['built_year']) > policy['year']:
            return f"준공 연도 차이 ±{policy['year']}년 초과"
    if subject['walk_min'] is not None and row['walk_min'] is not None and abs(subject['walk_min'] - row['walk_min']) > policy['walk']:
        return f"역 도보 차이 ±{policy['walk']}분 초과"
    if subject['structure'] and row['structure'] and policy['structure'] != 'any':
        same = subject['structure'] == row['structure'] if policy['structure'] == 'exact' else STRUCTURE_FAMILIES[subject['structure']] == STRUCTURE_FAMILIES[row['structure']]
        if not same:
            return '건물 구조 불일치'
    return None


def _quantile(rows, value):
    groups = Counter(row['_group'] for row in rows)
    ordered = sorted((row['comparison_price_yen'], 1 / groups[row['_group']]) for row in rows)
    target, cumulative = len(groups) * value, 0
    for i, (price, weight) in enumerate(ordered):
        cumulative += weight
        if math.isclose(cumulative, target, abs_tol=1e-9) and i + 1 < len(ordered):
            return (price + ordered[i + 1][0]) / 2
        if cumulative >= target:
            return price
    return ordered[-1][0]


def _decorate(subject, rows, basis):
    orientation_names = dict(zip(('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'), ('북', '북동', '동', '남동', '남', '남서', '서', '북서')))

    def display(field, value):
        if field == 'structure':
            return STRUCTURE_NAMES[value]
        if field == 'building_type':
            return BUILDING_TYPES[value]
        if field == 'orientation':
            return orientation_names[value]
        if type(value) is bool:
            return '있음' if value else '없음'
        return str(value)

    for row in rows:
        row['monthly_total_yen'] = None if row['mgmt_fee_yen'] is None else row['rent_yen'] + row['mgmt_fee_yen']
        row['comparison_price_yen'] = row['monthly_total_yen'] if basis == 'total' else row['rent_yen']
        row['_group'] = _building_key(row) or ('unknown',)
        row['differences'] = []
        for field in ('structure', 'area_sqm', 'built_year', 'walk_min', 'floor') + EXTRA:
            if subject[field] is None or row[field] is None:
                row['differences'].append(LABELS[field] + ' 미확인')
            elif subject[field] != row[field]:
                row['differences'].append(f'{LABELS[field]}: 대상 {display(field, subject[field])} / 비교 {display(field, row[field])}')
    # No listing price or target rent enters candidate selection or ordering.
    rows.sort(key=lambda row: (
        sum(row[f] is None for f in ('building_type', 'building_floors', 'structure', 'built_year', 'walk_min', 'floor')),
        row['structure'] != subject['structure'], abs(row['area_sqm'] - subject['area_sqm']),
        abs(row['built_year'] - subject['built_year']) if row['built_year'] is not None and subject['built_year'] is not None else 999,
        row['id'],
    ))


def _condition_notes(subject, rows):
    if not rows:
        return []
    notes = []
    for field, names in (('building_type', BUILDING_TYPES), ('structure', STRUCTURE_NAMES)):
        counts = Counter(names.get(row[field], '미상') for row in rows)
        notes.append(LABELS[field] + ': 대상 ' + names.get(subject[field], '미상') + ' / 비교 ' +
                     ' · '.join(f'{name} {count}개' for name, count in counts.items()))
    for field, unit in (('building_floors', '층'), ('built_year', '년'), ('walk_min', '분'), ('floor', '층')):
        known = [row[field] for row in rows if row[field] is not None]
        observed = f'{min(known):g}~{max(known):g}{unit}' if known else '미상'
        if known and len(known) != len(rows):
            observed += f' · 미상 {len(rows) - len(known)}개'
        target = f'{subject[field]:g}{unit}' if subject[field] is not None else '미상'
        notes.append(f'{LABELS[field]}: 대상 {target} / 비교 {observed}')
    elevators = Counter('미상' if row['elevator'] is None else '있음' if row['elevator'] else '없음' for row in rows)
    target_elevator = '미상' if subject['elevator'] is None else '있음' if subject['elevator'] else '없음'
    notes.append('엘리베이터: 대상 ' + target_elevator + ' / 비교 ' + ' · '.join(f'{name} {count}개' for name, count in elevators.items()))
    return notes


def _range(rows):
    if not rows:
        return None
    prices = [row['comparison_price_yen'] for row in rows]
    return {'min_yen': min(prices), 'max_yen': max(prices), 'sample_count': len(rows)}


def _reference_groups(subject, rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[_profile(row)].append(row)
    result = []
    family_names = {'concrete': 'RC·SRC', 'steel': '철골·경량철골', 'wood': '목조'}
    property_names = {'apartment': '공동주택', 'house': '단독주택', 'shared': '셰어하우스'}

    def feature_grades(profile):
        return _feature_grades(subject, profile)

    def reference_priority(profile):
        items = grouped[profile]
        height_distance = min((abs(row['building_floors'] - subject['building_floors'])
                               for row in items if row['building_floors'] is not None and subject['building_floors'] is not None),
                              default=999)
        return (feature_grades(profile), height_distance,
                min(abs(row['area_sqm'] - subject['area_sqm']) for row in items),
                tuple('' if value is None else str(value) for value in profile))

    # Rank by observed characteristics, never by advertised price. These
    # references still fail the primary policy; a positive feature flag does
    # not imply matching height, age, size, or a usable target price delta.
    for profile in sorted(grouped, key=reference_priority):
        items = grouped[profile]
        # A reference group reports its own prices, never a percentage against
        # the target. Do not pool known and unknown fee totals within the group.
        basis = 'total' if all(row['mgmt_fee_yen'] is not None for row in items) else 'rent'
        _decorate(subject, items, basis)
        groups = {row['_group'] for row in items if row['_group'] != ('unknown',)}
        property_type, building_type, height, family, elevator, position = profile
        label = ' · '.join((property_names.get(property_type, '주택 유형 미상'), BUILDING_TYPES.get(building_type, '광고 종류 미상'),
                            height or '총층수 미상', family_names.get(family, '구조 미상'),
                            '승강기 미상' if elevator is None else '승강기 있음' if elevator else '승강기 없음', position or '거주 위치 미상'))
        reasons = sorted({reason for row in items for reason in _profile_reasons(subject, row)})
        summary = None
        if len(items) >= 3 and len(groups) >= 3:
            summary = {'median_yen': _quantile(items, .5), 'q1_yen': _quantile(items, .25), 'q3_yen': _quantile(items, .75),
                       'sample_count': len(items), 'building_groups': len(groups)}
        warnings = []
        if any(row['fetched_at'] is None for row in items):
            warnings.append('조회 시각이 없는 직접 입력 매물의 최신성은 확인되지 않았습니다.')
        if any(row['_group'] == ('unknown',) for row in items):
            warnings.append('주소·건물명이 미상인 광고는 독립된 건물로 확인되지 않아 하나의 그룹으로 묶었습니다.')
        result.append({'id': f'reference-{len(result) + 1}', 'label': label,
                       'same_building_features': all(grade == 0 for grade in feature_grades(profile)),
                       'relation': 'unverified' if any('미확인' in reason for reason in reasons) else 'different',
                       'reasons': reasons, 'warnings': warnings, 'sample_count': len(items), 'building_groups': len(groups),
                       'price_basis': basis, 'price_basis_label': '월세 + 관리비' if basis == 'total' else '월세만 · 관리비 제외',
                       'price_range': _range(items), 'summary': summary, 'comparables': items,
                       'condition_notes': _condition_notes(subject, items),
                       'unverified_fields': [field for field in CORE + EXTRA if subject[field] is None or any(row[field] is None for row in items)]})
        for row in items:
            row.pop('_group')
    return result


def _visual_comparison(subject, primary, reference_groups):
    """Observed prices of the closest available ads, not an adjusted valuation.

    Pick by characteristics before looking at rent or fee completeness. Keep
    this small display sample independent of the primary/cohort statistics.
    """
    maximum = 5
    group_labels = {row['id']: group['label'] for group in reference_groups for row in group['comparables']}
    pool = primary or [row for group in reference_groups for row in group['comparables']]
    mode = 'primary' if primary else 'nearest_reference' if pool else 'empty'
    if not primary and pool:
        closest_tier = min(_feature_grades(subject, _profile(row)) for row in pool)
        pool = [row for row in pool if _feature_grades(subject, _profile(row)) == closest_tier]

    def distance(row, field):
        if subject[field] is None or row[field] is None:
            return float('inf')
        return abs(subject[field] - row[field])

    ordered = sorted(pool, key=lambda row: (
        _feature_grades(subject, _profile(row)), distance(row, 'building_floors'),
        distance(row, 'area_sqm'), distance(row, 'built_year'), distance(row, 'walk_min'),
        distance(row, 'floor'), row['id']))
    chosen, seen = [], set()
    for row in ordered:
        # Without both address and building name, independence is unknown.
        # One ad per known building and one unknown ad avoid inflated ranks.
        key = _building_key(row) or ('unknown',)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(deepcopy(row))
        if len(chosen) == maximum:
            break
    basis = 'total' if subject['mgmt_fee_yen'] is not None and all(row['mgmt_fee_yen'] is not None for row in chosen) else 'rent'
    price = subject['rent_yen'] + (subject['mgmt_fee_yen'] if basis == 'total' else 0)
    selection_ids = [row['id'] for row in chosen]
    _decorate(subject, chosen, basis)
    chosen.sort(key=lambda row: selection_ids.index(row['id']))
    for index, row in enumerate(chosen):
        row.pop('_group')
        matches = ['같은 지역·역·평면']
        if subject['building_type'] is not None and subject['building_type'] == row['building_type']:
            matches.append('같은 광고 건물 종류')
        if subject['structure'] is not None and STRUCTURE_FAMILIES[subject['structure']] == STRUCTURE_FAMILIES.get(row['structure']):
            matches.append('같은 구조군')
        if subject['elevator'] is not None and subject['elevator'] == row['elevator']:
            matches.append('승강기 조건 일치')
        if _floor_position(subject) is not None and _floor_position(subject) == _floor_position(row):
            matches.append('같은 거주 층 위치')
        row.update(price_yen=row['comparison_price_yen'], delta_yen=price - row['comparison_price_yen'],
                   similarity_order=index + 1, reference_group_label=group_labels.get(row['id']), match_labels=matches)
    lower = sum(row['price_yen'] < price for row in chosen)
    equal = sum(row['price_yen'] == price for row in chosen)
    rank = {'low_to_high': lower + 1, 'tied_to': lower + equal + 1, 'total': len(chosen) + 1,
            'lower_count': lower, 'equal_count': equal, 'higher_count': len(chosen) - lower - equal} if chosen else None
    warnings = []
    if basis == 'rent':
        warnings.append('관리비가 미상인 항목이 있어 내 집과 그래프의 모든 매물에서 관리비를 제외했습니다.')
    if any(_building_key(row) is None for row in chosen):
        warnings.append('건물 식별 정보가 부족한 광고는 그래프에 최대 1개만 포함했습니다.')
    if any(row['fetched_at'] is None for row in chosen):
        warnings.append('조회 시각이 없는 매물은 원문에서 최신 가격을 확인해 주세요.')
    if mode == 'nearest_reference':
        warnings.append('총층수·연식 등 조건이 다른 참고 매물이 포함됩니다. 아래에서 차이를 확인하세요.')
    selection_label = ('주 비교 매물에서 조건이 가까운 순으로 건물당 1개, 최대 5개를 골랐습니다.' if primary else
                       '참고 매물 중 건물 조건이 가장 가까운 단계에서 건물당 1개, 최대 5개를 골랐습니다.' if chosen else
                       '가격을 나란히 볼 비교 매물이 없습니다. 원문을 확인해 비교 매물을 추가해 주세요.')
    return {'version': 'observed-price-1', 'mode': mode, 'title': '가까운 매물과 내 집 가격',
            'selection_label': selection_label,
            'scope_label': '표시한 광고와 내 집의 가격 순서입니다. 조건 차이를 보정한 시세나 시장 전체 순위가 아닙니다.',
            'pool_count': len(pool), 'displayed_count': len(chosen), 'max_count': maximum,
            'price_basis': basis, 'price_basis_label': '월세 + 관리비' if basis == 'total' else '월세만 · 관리비 제외',
            'subject': dict(subject, id='subject', price_yen=price), 'comparables': chosen, 'rank': rank, 'warnings': warnings}


def compare(payload, *, now=None):
    if not isinstance(payload, dict) or set(payload) - {'subject', 'listings'}:
        raise ValueError('subject와 listings만 전달해 주세요.')
    now = now or datetime.now(timezone.utc)
    subject = validate_listing(payload.get('subject'), subject=True, now=now)
    raw = payload.get('listings')
    if not isinstance(raw, list) or len(raw) > 60:
        raise ValueError('비교 매물은 최대 60개입니다.')
    rows = [dict(validate_listing(item, now=now), id=f'listing-{i + 1}') for i, item in enumerate(raw)]
    missing = [f for f in CORE if subject[f] is None]
    total = None if subject['mgmt_fee_yen'] is None else subject['rent_yen'] + subject['mgmt_fee_yen']
    response = {
        'mode': 'personal', 'policy_version': 'personal-3', 'status': 'insufficient',
        'judgment': None, 'subject_total_yen': total, 'summary': None,
        'matched_count': 0, 'input_count': len(rows), 'missing_subject_fields': missing,
        'comparables': [], 'excluded': [], 'steps': [],
        'price_basis': 'total', 'price_basis_label': '월세 + 관리비',
        'subject_comparison_yen': total, 'comparison_level': 'exact',
        'comparison_label': '가까운 조건 비교', 'relaxed_fields': [], 'unverified_fields': [],
        'price_range': None, 'fee_coverage': {'known_count': 0, 'missing_count': 0}, 'strict_count': 0,
        'condition_notes': [], 'reference_groups': [], 'reference_count': 0, 'related_reference_count': 0,
        'scope': '검색하거나 직접 입력한 일부 광고의 모집가격 비교입니다. 시장 전체의 시세나 현재 공실을 확인한 결과가 아닙니다.',
        'warnings': ['이 비교 조건과 완화 순서는 실제 시장 자료로 검증되지 않았습니다.',
                     '조회 시각은 모집 상태 확인 시각이 아닙니다. 계약 전 원문과 중개사에게 확인하세요.'],
    }
    profile_missing = [field for field in ('building_type', 'building_floors', 'structure', 'floor') if subject[field] is None]
    response['missing_subject_fields'] = list(dict.fromkeys(missing + profile_missing))
    if profile_missing:
        response['warnings'].append('같은 건물군을 비교하려면 대상의 ' + ' · '.join(LABELS[field] for field in profile_missing) + ' 정보를 보완해 주세요. 현재 매물은 건물군별 별도 참고가격으로 제공합니다.')

    # Collapse likely duplicate ads conservatively; never call these verified units.
    by_url, by_room = defaultdict(list), defaultdict(list)
    for row in rows:
        if row['source_url']:
            by_url[_url_identity(row['source_url'])].append(row)
        building = _building_key(row)
        if building and all(row[f] is not None for f in ('floor', 'area_sqm', 'layout')):
            by_room[(building, row['floor'], row['area_sqm'], row['layout'], row['orientation'])].append(row)
    rejected = {}
    comparison_fields = CORE + EXTRA
    parents = {row['id']: row['id'] for row in rows}

    def root(identity):
        while parents[identity] != identity:
            parents[identity] = parents[parents[identity]]
            identity = parents[identity]
        return identity

    # A shared URL may connect two advertisements while a room fingerprint
    # connects one of them to a third. Conflicts apply to the whole connected
    # group, otherwise a conflicting advertisement can survive through its copy.
    for group in list(by_url.values()) + list(by_room.values()):
        for item in group[1:]:
            parents[root(item['id'])] = root(group[0]['id'])
    connected = defaultdict(list)
    for row in rows:
        connected[root(row['id'])].append(row)
    for group in connected.values():
        if len(group) < 2:
            continue
        conflicts = any(len({item[f] for item in group if item[f] is not None}) > 1 for f in comparison_fields)
        if conflicts:
            for item in group:
                rejected[item['id']] = '동일 매물로 의심되는 광고의 가격·조건 충돌'
        else:
            best = min(group, key=lambda item: sum(item[f] is None for f in comparison_fields))
            for item in group:
                if item is best:
                    continue
                rejected.setdefault(item['id'], '중복 또는 같은 호실로 의심되어 제외')
    subject_building = _building_key(subject)
    subject_identity = _url_identity(subject['source_url'])
    self_groups = {}
    for row in rows:
        identity = _url_identity(row['source_url'])
        if identity and identity == subject_identity:
            self_groups[root(row['id'])] = '비교 대상과 같은 URL'
        if subject_building and _building_key(row) == subject_building:
            self_groups[root(row['id'])] = '비교 대상과 같은 건물로 의심되어 제외'
    candidates = []
    for row in rows:
        if root(row['id']) in self_groups:
            rejected[row['id']] = self_groups[root(row['id'])]
        if row['fetched_at']:
            stamp = datetime.fromisoformat(row['fetched_at'].replace('Z', '+00:00'))
            if (now - stamp).total_seconds() > 86400:
                rejected[row['id']] = '페이지 조회 후 24시간 경과: 다시 확인 필요'
        if row['id'] not in rejected:
            candidates.append(row)
    primary_candidates = [row for row in candidates if not _profile_reasons(subject, row)]
    reference_policy = dict(list(_policies())[-1], structure='any', floor=False)
    references = [row for row in candidates if _profile_reasons(subject, row) and _mismatch(subject, row, reference_policy, now) is None]
    response['reference_groups'] = _reference_groups(subject, references)
    response['reference_count'] = len(references)
    response['related_reference_count'] = sum(group['sample_count'] for group in response['reference_groups']
                                              if group['same_building_features'])
    if references:
        response['warnings'].append('건물 종류·총층수·구조군·거주 위치·승강기 조건이 다르거나 미확인인 매물은 별도 참고군으로 분리했습니다. 주 비교군의 중앙값과 가격 차이에 합산하지 않습니다.')

    def sufficient(items):
        return len(items) >= 3 and len({key for row in items if (key := _building_key(row)) is not None}) >= 3

    selected, matching, basis = [], [], 'total'
    best_partial = None
    selected_step = None
    relaxed = []
    field_names = {'area': 'area_sqm', 'year': 'built_year', 'walk': 'walk_min', 'bathroom': 'bathroom_separate'}
    for policy in _policies():
        if policy['field'] and policy['field'] != 'unknown':
            field = field_names.get(policy['field'], policy['field'])
            if field not in relaxed:
                relaxed.append(field)
        matching = [row for row in primary_candidates if _mismatch(subject, row, policy, now) is None]
        totals = [row for row in matching if row['mgmt_fee_yen'] is not None] if total is not None else []
        if policy['id'] == 'exact':
            response['strict_count'] = len(totals)
        response['steps'].append({'id': policy['id'], 'label': policy['label'], 'count': len(totals),
                                  'rent_count': len(matching), 'price_basis': 'total', 'selected': False})
        if matching:
            coverage = (len({key for row in matching if (key := _building_key(row)) is not None}), len(matching))
            if best_partial is None or coverage > best_partial[0]:
                best_partial = (coverage, policy, matching, list(relaxed), len(response['steps']) - 1)
        if sufficient(totals):
            selected, basis = totals, 'total'
            break
        # Prefer matching characteristics with a consistent rent-only basis to
        # widening characteristics solely because a management fee is missing.
        if sufficient(matching):
            selected, basis = matching, 'rent'
            response['steps'].append({'id': policy['id'] + '_rent', 'label': '관리비 미상: 모든 표본을 월세만으로 비교',
                                      'count': len(matching), 'rent_count': len(matching), 'price_basis': 'rent', 'selected': False})
            break
    else:
        # Even one or two nearby ads convey useful observed prices. Show their
        # actual range and differences without manufacturing a median estimate.
        if best_partial is not None:
            _, policy, matching, relaxed, selected_step = best_partial
        selected = matching
        basis = 'total' if total is not None and all(row['mgmt_fee_yen'] is not None for row in selected) else 'rent'
        if basis == 'rent':
            response['steps'].append({'id': 'individual_rent', 'label': '관리비 미상: 개별 표본의 월세만 확인',
                                      'count': len(selected), 'rent_count': len(selected), 'price_basis': 'rent', 'selected': False})
            selected_step = None
    if selected:
        response['steps'][selected_step if selected_step is not None else -1]['selected'] = True
    else:
        relaxed = []
    response['price_basis'] = basis
    response['price_basis_label'] = '월세 + 관리비' if basis == 'total' else '월세만 · 관리비 제외'
    response['subject_comparison_yen'] = total if basis == 'total' else subject['rent_yen']
    response['relaxed_fields'] = relaxed
    response['unverified_fields'] = [field for field in CORE + EXTRA
                                     if subject[field] is None or any(row[field] is None for row in selected)]
    level = policy['level']
    if level != 'broad' and response['unverified_fields']:
        level = 'partial'
    response['comparison_level'] = level
    response['comparison_label'] = {
        'exact': '가까운 조건의 매물 비교', 'relaxed': '조건을 일부 완화한 매물 비교',
        'partial': '일부 조건이 미확인된 참고 비교', 'broad': '같은 건물군에서 조건 범위를 넓힌 참고가격',
    }[level]
    if not selected:
        response['comparison_label'] = '조회한 매물에서 주 비교군을 확인하지 못함'
    selected_ids = {row['id'] for row in selected}
    reference_ids = {row['id'] for row in references}
    for row in rows:
        if row['id'] not in selected_ids and row['id'] not in reference_ids:
            applicable_policy = reference_policy if _profile_reasons(subject, row) else policy
            reason = rejected.get(row['id']) or _mismatch(subject, row, applicable_policy, now) or '관리비 미확인: 총액 비교군에서 제외'
            response['excluded'].append({'id': row['id'], 'title': row['title'], 'reason': reason})
    _decorate(subject, selected, basis)
    response['matched_count'] = len(selected)
    response['fee_coverage'] = {'known_count': sum(row['mgmt_fee_yen'] is not None for row in selected),
                                'missing_count': sum(row['mgmt_fee_yen'] is None for row in selected)}
    response['price_range'] = _range(selected)
    response['condition_notes'] = _condition_notes(subject, selected)
    groups = {row['_group'] for row in selected if row['_group'] != ('unknown',)}
    if len(selected) >= 3 and len(groups) >= 3:
        median = _quantile(selected, .5)
        response['status'] = 'reference'
        response['summary'] = {
            'median_yen': median, 'q1_yen': _quantile(selected, .25), 'q3_yen': _quantile(selected, .75),
            'delta_pct': round((response['subject_comparison_yen'] / median - 1) * 100, 1),
            'sample_count': len(selected), 'building_groups': len(groups),
        }
    else:
        response['warnings'].append('중앙값에는 매물 3개와 구분 가능한 건물 그룹 3개가 필요합니다. 현재 확인된 개별 가격과 범위를 참고하세요.')
    if selected and basis == 'rent':
        response['warnings'].append('관리비 미상으로 대상과 모든 비교 매물에서 관리비를 제외했습니다. 표시한 차이는 월세만의 차이이며 총 주거비 차이가 아닙니다.')
    if selected and level == 'broad':
        response['warnings'].append('같은 건물군 안에서 거주 층·연식·면적 등의 범위를 넓힌 참고가격입니다. 동일 조건의 적정가격으로 해석하지 마세요.')
    if selected and any(field in response['unverified_fields'] for field in ('structure', 'built_year', 'walk_min', 'floor')):
        response['warnings'].append('구조·준공·도보·층 중 미확인 정보는 추정하지 않았습니다. 아래 표에서 각 매물의 미상 항목을 확인하세요.')
    if not subject['source_url'] and not subject_building:
        response['warnings'].append('대상의 원문 URL과 건물 정보가 없어 자기 매물 포함 여부를 확인할 수 없습니다.')
    if any(row['_group'] == ('unknown',) for row in selected):
        response['warnings'].append('주소·건물명이 미상인 광고는 독립된 건물로 확인되지 않아 하나의 그룹으로 묶었습니다.')
    if selected and any(subject[f] is None or any(row[f] is None for row in selected) for f in EXTRA):
        response['warnings'].append('방향·계약·가구 등 일부 추가 조건이 미확인입니다. 완전히 같은 조건의 비교로 해석하지 마세요.')
    if relaxed:
        response['warnings'].append('일부 조건을 완화했습니다. 단계별 기록과 각 매물의 차이를 확인하세요.')
    if any(row['fetched_at'] is None for row in selected):
        response['warnings'].append('조회 시각이 없는 직접 입력 매물의 최신성은 확인되지 않았습니다.')
    response['warnings'].append('주소·건물명·층·면적에 따른 중복 추정은 실제 호실 식별을 보증하지 않습니다.')
    for row in selected:
        row.pop('_group')
    response['comparables'] = selected
    response['visual_comparison'] = _visual_comparison(subject, selected, response['reference_groups'])
    return response
