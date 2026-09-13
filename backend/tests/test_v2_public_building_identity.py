"""Synthetic advertisements must not invent independent building identities."""
import pytest

from backend.tests.test_v2_public_search import card, detail, enrich, parsed
from backend.tests.test_v2_personal import NOW, listing, sample, subject
from backend.v2.personal import compare


@pytest.mark.parametrize('title', [
    '合成線 合成駅 14階建 築7年',
    '合成地下鉄 合成駅 13階建 築12年',
    '合成線 合成駅 徒歩3分 14階建 築7年',
    '合成線 合成駅 １４階建 築７年',
    '合成線 合成駅 8階建(地下1階) 新築',
    '合成線 合成駅 8階建',
    '合成線 合成駅 築7年',
])
def test_description_is_retained_as_title_without_becoming_building_name(title):
    item = parsed(card(name=title))[0]
    assert item['title'] == title
    assert item['building_name'] is None
    assert item['source_url'] and item['rent_yen'] == 80000


@pytest.mark.parametrize('title', [
    '合成レジデンス', '合成駅前マンション', '合成駅 レジデンス14',
    '合成ビル14', '合成駅前 8階建レジデンス',
])
def test_building_names_with_station_or_number_words_are_not_erased(title):
    item = parsed(card(name=title))[0]
    assert item['title'] == item['building_name'] == title


def test_detail_enrichment_does_not_promote_description_back_to_building_name():
    title = '合成線 合成駅 8階建 築6年'
    item = enrich(detail(name=title), parsed(card(name=title))[0])
    assert item is not None and item['structure'] == 'rc' and item['built_year'] == 2020
    assert item['title'] == title and item['building_name'] is None


def test_description_cannot_supply_third_known_building_for_median():
    description = parsed(card(name='合成線 合成駅 8階建 築6年'))[0]
    rows = sample()
    rows[2].update(title=description['title'], building_name=description['building_name'])
    result = compare({'subject': subject(), 'listings': rows}, now=NOW)
    assert result['matched_count'] == 3
    assert result['summary'] is None
    assert any('독립된 건물' in warning for warning in result['warnings'])


def test_existing_browser_description_names_are_not_independent_buildings():
    rows = sample()
    rows[1]['building_name'] = '合成線 合成駅 8階建 築6年'
    rows[2]['building_name'] = '別合成線 別合成駅 8階建 築6年'
    result = compare({'subject': subject(), 'listings': rows}, now=NOW)
    assert result['matched_count'] == 3
    assert result['summary'] is None
    assert result['visual_comparison']['displayed_count'] == 2  # One known + one unknown identity.


def test_generic_subject_name_does_not_claim_cross_source_self_detection():
    target = subject(building_name='合成線 合成駅 8階建 築6年')
    result = compare({'subject': target, 'listings': sample()}, now=NOW)
    assert any('다른 사이트' in warning and '자기 매물' in warning for warning in result['warnings'])


@pytest.mark.parametrize('missing', [{'building_name': None}, {'address': None}, {'building_name': None, 'address': None}])
def test_other_source_subject_url_does_not_imply_cross_source_self_exclusion(missing):
    target = subject(source_url='https://realestate.yahoo.co.jp/rent/detail/' + '1' * 44 + '/', **missing)
    result = compare({'subject': target, 'listings': sample()}, now=NOW)
    assert any('다른 사이트' in warning and '자기 매물' in warning for warning in result['warnings'])
    assert result['matched_count'] == 3  # No new fuzzy removal by price/area.


def test_missing_building_warning_does_not_disable_exact_subject_url_exclusion():
    target = subject(building_name=None)
    result = compare({'subject': target, 'listings': [listing(1, source_url=target['source_url'])]}, now=NOW)
    assert result['matched_count'] == 0
    assert result['excluded'][0]['reason'] == '비교 대상과 같은 URL'


def test_existing_known_subject_building_does_not_get_missing_identity_warning():
    result = compare({'subject': subject(), 'listings': sample()}, now=NOW)
    assert not any('다른 사이트' in warning for warning in result['warnings'])
