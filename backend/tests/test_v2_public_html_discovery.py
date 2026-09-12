"""A large building's advertisements must not hide the next building card."""
from backend.tests.test_v2_public_search import card, parsed


def rows(*indexes, **kwargs):
    return ''.join(card(index, **kwargs).split('<tbody>')[1].split('</tbody>')[0] for index in indexes)


def building(index, inner):
    header, tail = card(index).split('<tbody>', 1)
    return header + '<tbody>' + inner + '</tbody>' + tail.split('</tbody>', 1)[1]


def test_first_building_with_150_ads_does_not_hide_later_candidates():
    first = building(0, rows(*range(150)))
    items = parsed(first + ''.join(card(index) for index in (200, 201, 202)))
    assert len(items) == 11
    assert sum(item['building_name'] == '合成建物0' for item in items) == 8
    assert {'合成建物200', '合成建物201', '合成建物202'} <= {item['building_name'] for item in items}


def test_page_output_is_bounded_but_distributed_across_fifty_cards():
    html = ''.join(building(index, rows(*range(index * 20, index * 20 + 20))) for index in range(51))
    items = parsed(html)
    assert len(items) == 400
    assert len({item['building_name'] for item in items}) == 50
    assert '合成建物50' not in {item['building_name'] for item in items}


def test_conflicting_url_after_card_cap_still_removes_earlier_advertisement():
    html = building(0, rows(*range(8)) + rows(0, rent='9万円')) + card(200)
    items = parsed(html)
    assert len(items) == 8
    assert not any('jnc_000000000000/' in item['source_url'] for item in items)
    assert items[-1]['building_name'] == '合成建物200'


def test_malformed_rows_do_not_spend_valid_advertisement_slots():
    html = building(0, rows(*range(8), rent='確認中') + rows(*range(10, 18)))
    items = parsed(html)
    assert len(items) == 8
    assert all(item['rent_yen'] == 80000 for item in items)
