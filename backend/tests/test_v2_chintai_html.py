"""Tiny invented building/room tables; no real advertisement fixtures."""
import pytest

from backend.v2.chintai_html import detail_url, parse_search
from backend.v2.personal import validate_listing
from backend.v2.public_fetch import PublicFetchError
from backend.v2.public_search import _REGIONS

STAMP = '2026-01-01T00:00:00Z'


def room(index=1, *, rent='9.5万円', fee='5,000円', plan='1K', space='25m&#178;', level='3階', url=None):
    identity = f'C000000000000000000000{index:06d}'
    url = url or f'/detail/bk-{identity}/'
    return f'''<tbody class="js-detailLinkUrl" data-detailurl="{url}" data-bkkey="{identity}" data-cn-bkkey="{identity}">
    <tr class="detail-inner"><td class="check"></td><td class="madori"></td>
    <td class="floar"><ul><li>{level}</li><li>即入居可</li></ul></td>
    <td class="price"><span>{rent}</span><br>{fee}</td><td class="other_price">なし<br>なし</td>
    <td>{plan}<br>{space}</td><td class="inquiry">問い合わせ</td>
    <td class="detail"><a href="{url}" data-detailurl="{url}">詳細</a></td></tr>
    <tr><td colspan="8" class="supplement_box">広告補足 1万円 99m²</td></tr></tbody>'''


def building(rooms=None, *, name='合成テスト建物', address='東京都新宿区合成番地1', label='賃貸マンション',
             built='2008年04月（築17年）', floors='8階建', material='鉄筋コンクリート造',
             routes='合成線/新宿駅 徒歩12分</li><li>別路線/新新宿駅 徒歩1分'):
    return f'''<section class="cassette_item build"><div class="cassette_ttl"><h2><span class="icn_typeB">{label}</span>{name}</h2></div>
    <div class="bukken_information"><table class="l-table"><tbody>
    <tr><th>住所</th><td>{address}<p class="map"><a>周辺地図</a></p></td><th>交通</th><td><ul><li>{routes}</li></ul></td></tr>
    <tr><th>築年</th><td>{built}</td></tr><tr><th>階建</th><td>{floors}</td><th>構造</th><td>{material}</td></tr>
    </tbody></table></div><div class="cassette_detail"><table><thead><tr>
    <th class="check"></th><th class="madori">間取図</th><th class="floar">階</th><th class="price">家賃<br>管理費</th>
    <th class="other_price">敷金<br>礼金</th><th class="layout">間取り<br>専有面積</th><th class="inquiry"></th><th class="detail"></th>
    </tr></thead>{rooms if rooms is not None else room()}</table></div></section>'''


def parse(html, target='新宿'):
    return parse_search(html, fetched_at=STAMP, regions=_REGIONS, target_station=target)


def test_room_facts_stay_attached_to_their_building_and_exact_station():
    rows, cards, empty = parse(building(room(1) + room(2, rent='10万円', fee='なし', level='7階')))
    assert cards == 1 and not empty and len(rows) == 2
    assert [r['rent_yen'] for r in rows] == [95000, 100000]
    assert [r['mgmt_fee_yen'] for r in rows] == [5000, 0]
    assert [r['floor'] for r in rows] == [3, 7]
    for row in rows:
        assert row['station_name'] == '新宿' and row['walk_min'] == 12
        assert row['built_year'] == 2008 and row['built_month'] == 4
        assert row['building_floors'] == 8 and row['structure'] == 'rc'
        assert row['building_type'] == 'mansion' and row['property_type'] == 'apartment'
        assert row['building_name'] == '合成テスト建物' and row['address'] == '東京都新宿区合成番地1'
        assert row['source_id'] == 'chintai' and row['source_url'].startswith('https://www.chintai.net/detail/bk-')
        assert all(row[key] is None for key in ('elevator', 'orientation', 'bathroom_separate', 'furnished', 'contract_type'))
        validate_listing(row)
    rows[0]['accesses'][0]['station_name'] = 'changed'
    assert rows[1]['accesses'][0]['station_name'] == '新宿'


def test_missing_target_station_keeps_observed_station_instead_of_inventing_match():
    rows, _, _ = parse(building(), '存在しない駅')
    assert rows[0]['station_name'] == '新新宿' and rows[0]['walk_min'] == 1
    rows, _, _ = parse(building(routes='合成線/新宿駅 バス10分 徒歩2分'), '新宿')
    assert rows[0]['station_name'] is None and rows[0]['walk_min'] is None


@pytest.mark.parametrize(('floor_text', 'total', 'expected'), [
    ('地下1階', '8階建(地下1階)', -1), ('8階', '8階建', 8),
    ('3階', '地下2地上12階建', 3), ('1-2階', '8階建', None),
    ('3階</li><li>4階', '8階建', None),
])
def test_occupied_floor_is_separate_from_above_ground_total(floor_text, total, expected):
    rows, _, _ = parse(building(room(level=floor_text), floors=total))
    assert rows[0]['floor'] == expected
    assert rows[0]['building_floors'] == (12 if '地上12' in total else 8)


@pytest.mark.parametrize('built', ['築8年', '新築', '2008年13月', '2008年00月', '2027年01月', '2008年04月 2015年01月'])
def test_only_exact_valid_advertised_year_month_populates_construction_year(built):
    rows, _, _ = parse(building(built=built))
    assert rows[0]['built_year'] is None and rows[0]['built_month'] is None


@pytest.mark.parametrize('fee', ['--', '', '不明', '相談'])
def test_unknown_management_fee_is_never_zero(fee):
    rows, _, _ = parse(building(room(fee=fee)))
    assert rows[0]['mgmt_fee_yen'] is None


def test_building_kind_and_structure_are_not_inferred_from_each_other_or_height():
    rows, _, _ = parse(building(label='賃貸住宅', material='不明', floors='32階建'))
    assert rows[0]['building_type'] is None and rows[0]['property_type'] is None
    assert rows[0]['structure'] is None and rows[0]['building_floors'] == 32


def test_same_name_different_address_and_construction_keep_separate_facts():
    rows, cards, _ = parse(building(room(1), address='東京都新宿区合成番地1', built='2008年01月') +
                          building(room(2), address='東京都新宿区合成番地2', built='2018年02月', material='木造', label='賃貸アパート'))
    assert cards == 2 and len(rows) == 2
    assert rows[0]['building_name'] == rows[1]['building_name']
    assert rows[0]['address'] != rows[1]['address']
    assert [r['built_year'] for r in rows] == [2008, 2018]
    assert [r['structure'] for r in rows] == ['rc', 'wood']


@pytest.mark.parametrize('name', ['合成線 新宿駅 12階建 築25年', '合成線 新宿駅 8階建 新築',
                                  '合成線 新宿駅 徒歩5分 8階建', '合成線 新宿駅 8階建(地下1階) 築20年'])
def test_station_height_age_description_is_a_title_not_a_known_building_name(name):
    rows, _, _ = parse(building(room(1), name=name) + building(room(2), name='合成西新宿レジデンス'))
    assert len(rows) == 2
    assert rows[0]['title'] == name and rows[0]['building_name'] is None
    assert rows[1]['building_name'] == '合成西新宿レジデンス'
    assert rows[0]['source_url'] != rows[1]['source_url']
    assert rows[0]['address'] == rows[1]['address']


def test_station_word_in_actual_building_name_is_preserved():
    rows, _, _ = parse(building(name='合成新宿駅前レジデンス'))
    assert rows[0]['building_name'] == '合成新宿駅前レジデンス'


@pytest.mark.parametrize('bad_room', [
    room(rent='0円'), room(rent='無料'), room(rent='0.00001万円'), room(rent='1.5円'), room(rent='10000001円'),
    room(rent='相談'), room(fee='1000001円'), room(plan='2LDK'), room(space='1001m²'),
    room(space='NaNm²'), room(level='101階'), room(level='9階'),
])
def test_malformed_or_unsupported_rows_do_not_poison_valid_comparison_input(bad_room):
    rows, _, _ = parse(building(bad_room + room(2)))
    assert len(rows) == 1 and rows[0]['source_listing_id'].endswith('000002')
    validate_listing(rows[0])


@pytest.mark.parametrize('url', ['http://www.chintai.net/detail/bk-C00000000000000000001/',
    'https://www.chintai.net.evil.invalid/detail/bk-C00000000000000000001/', '//evil.invalid/detail/bk-C00000000000000000001/',
    'https://user:secret@www.chintai.net/detail/bk-C00000000000000000001/',
    'https://www.chintai.net:443/detail/bk-C00000000000000000001/', '/detail/bk-C00000000000000000001/?vm=1',
    '/detail/bk-C00000000000000000001/#token', '/detail/bk-../', '/detail/bk-C00000000000000000001/inquiry/',
    '/detail/bk-C00000000000000000001/../', 'javascript:alert(1)', '/detail/bk-C00000000000000000001/\n'])
def test_source_url_is_exact_and_has_no_tracking_query_or_credentials(url):
    assert detail_url(url) is None
    assert parse(building(room(url=url)))[0] == []


@pytest.mark.parametrize('identity', ['A' * 19, 'A' * 41, 'a' * 26, 'C00000000000000000000000000_', 'C00000000000000000000000000-'])
def test_detail_identity_contract_rejects_short_long_or_non_uppercase_ids(identity):
    assert detail_url('/detail/bk-' + identity + '/') is None


def test_parser_detail_urls_also_pass_the_transport_validator():
    from backend.v2.chintai_discovery import checked_url
    for size in (20, 26, 27, 40):
        url = detail_url('/detail/bk-' + 'A' * size + '/')
        assert checked_url(url).path == '/detail/bk-' + 'A' * size + '/'
    rows, _, _ = parse(building(room(1) + room(2)))
    assert len(rows) == 2
    for row in rows:
        assert checked_url(row['source_url']).hostname == 'www.chintai.net'


def test_conflicting_url_or_identity_rejects_room_and_duplicate_prices_reject_both():
    good = room()
    assert len(parse(building(good + good))[0]) == 1
    assert parse(building(good + room(rent='10万円')))[0] == []
    assert parse(building(good.replace('data-bkkey="C000', 'data-bkkey="D000')))[0] == []
    assert parse(building(good.replace('href="/detail/bk-C000', 'href="/detail/bk-D000')))[0] == []


def test_unrelated_pr_script_and_supplement_rows_cannot_supply_facts():
    fake = building(room(9, rent='1万円'))
    html = f'<script>{fake}</script><template>{fake}</template>' + building(room())
    html += building(room(8)).replace('cassette_item build', 'cassette_item item_pr')
    rows, cards, _ = parse(html)
    assert cards == 1 and len(rows) == 1 and rows[0]['rent_yen'] == 95000
    assert rows[0]['area_sqm'] == 25


def test_malformed_headers_or_conflicting_building_metadata_fail_closed():
    assert parse(building().replace('class="layout"', 'class="unrelated"'))[0] == []
    duplicate = '<tr><th>構造</th><td>木造</td></tr>'
    assert parse(building().replace('</tbody></table></div><div', duplicate + '</tbody></table></div><div', 1))[0] == []
    assert parse(building(address='東京都新宿区' + 'a' * 250))[0] == []


def test_empty_page_requires_explicit_zero_results_not_an_unknown_html_shape():
    assert parse('<p>0件の賃貸物件情報が見つかりました</p>') == ([], 0, True)
    assert parse('<p>10件の賃貸物件情報</p>') == ([], 0, False)
    assert parse('<p>該当する物件がありません</p>') == ([], 0, True)
    assert parse('<script>0件の賃貸物件情報</script>') == ([], 0, False)
    assert parse('<title>ページが変わりました</title>') == ([], 0, False)
    assert parse(building() + '<p>0件の賃貸物件情報</p>')[2] is False


def test_per_building_budget_preserves_later_buildings_and_checks_late_conflicts():
    rows, cards, _ = parse(building(''.join(room(i) for i in range(1, 12))) + building(room(99), name='別の合成建物'))
    assert cards == 2 and len(rows) == 9
    assert rows[-1]['building_name'] == '別の合成建物'
    rows, _, _ = parse(building(''.join(room(i) for i in range(1, 12)) + room(1, rent='10万円')))
    assert len(rows) == 7 and not any(row['source_listing_id'].endswith('000001') for row in rows)
    rows, cards, _ = parse(''.join(building(room(i), name=f'合成{i}') for i in range(1, 52)))
    assert cards == 51 and len(rows) == 50


def test_html_size_and_nesting_have_bounded_failure():
    for html in ('x' * (3 * 1024 * 1024 + 1), '<div>' * 300):
        with pytest.raises(PublicFetchError, match='html_too_complex'):
            parse(html)
