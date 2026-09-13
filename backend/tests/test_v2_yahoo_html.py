"""Synthetic Yahoo building/room markup; no portal requests or inventory files."""
from copy import deepcopy
import json

import pytest

from backend.v2.public_fetch import PublicFetchError
from backend.v2.public_search import _REGIONS
from backend.v2.yahoo_html import detail_url, parse_search


STAMP = "2026-09-13T00:00:00Z"


def identity(index=0, building=0, prefix=False):
    return ("_" if prefix else "") + f"{building:012d}{index:032x}"


def room(index=0, *, building=0, rent="8万円", fee="5,000円", plan="1K",
         space="25m<sup>2</sup>", level="3階", room_id=None, amenities="NEW"):
    room_id = room_id or identity(index, building)
    return f'''<li class="ListCassetteRoom__item roomListItem">
      <input class="_propertyCheckbox" type="checkbox" value="{room_id}">
      <div class="ListCassetteRoom__block--floor">{level}</div>
      <div class="ListCassetteRoom__dtl__price">{rent}<span class="ListCassetteRoom__dtl__price__txtS">管理費等 {fee}</span></div>
      <div class="ListCassetteRoom__dtl__layout">{plan}</div>
      <div class="ListCassetteRoom__dtl__layout">{space}</div>
      <div class="ListCassetteRoom__tagLink"><span>{amenities}</span>
        <a class="ListCassetteRoom__textLink" href="/rent/detail/{room_id}/">詳細を見る</a></div>
      <a href="/rent/inquiry/room/input/?property_id={room_id}">問い合わせ</a>
    </li>'''


def card(index=0, *, rooms=None, name=None, address=None, kind="マンション",
         spec="築7年/地上8階建て/鉄筋コンクリート", building_id=None, routes=None):
    building_id = building_id or f"{index:012d}"
    routes = routes if routes is not None else ["西新宿駅/東京メトロ丸ノ内線 徒歩3分", "新宿駅/山手線 徒歩12分"]
    spans = ''.join(f'<span class="ListCassette__txt">{route}</span>' for route in routes)
    return f'''<li class="ListBukken__item" data-structureid="{building_id}">
      <div class="ListCassette"><h2 class="ListCassette__ttl__txt">{name or '合成建物' + str(index)}</h2>
        <div class="ListCassette__ttl__tag">{kind}</div>
        <ul class="ListCassette__list"><li class="ListCassette__item">{spans}</li>
          <li class="ListCassette__item">{address or '東京都新宿区合成町' + str(index)}</li>
          <li class="ListCassette__item">{spec}</li></ul></div>
      <ul class="ListCassetteRoom__list">{rooms if rooms is not None else room(building=index)}</ul>
    </li>'''


def metadata(index=0, *, room_ids=None):
    return {
        "StructureId": f"{index:012d}", "BuiltOn": "2011-04", "YearsOld": 7,
        "KindName": "マンション", "TotalFloorNum": 8,
        "BuildingName": "合成建物" + str(index), "CanDisplayBuildingName": True,
        "LocationView": {"AddressName": "東京都新宿区合成町" + str(index)},
        "GroupProperties": [
            {"PropertyId": room_id, "StructureId": f"{index:012d}",
             "BuiltOn": "2011-04", "FloorNum": "3", "PriceLabel": "8万円",
             "MonthlyManagementCostLabel": "5,000円", "MonopolyAreaLabel": "25m<sup>2</sup>"}
            for room_id in (room_ids or [identity(0, index)])
        ],
    }


def state(*buildings):
    page = json.dumps({"properties": list(buildings)}, ensure_ascii=False)
    return '<script>window.__SERVER_SIDE_CONTEXT__ = {common: {}, page: ' + page + ', mode: "pc"};</script>'


def parse(text=None, *, conflicts_out=None, station="新宿"):
    return parse_search(text if text is not None else card(), fetched_at=STAMP, regions=_REGIONS,
                        target_station=station, conflicts_out=conflicts_out)


def test_visible_room_facts_are_kept_without_an_embedded_construction_date():
    rows, count, empty = parse()
    assert count == 1 and not empty and len(rows) == 1
    item = rows[0]
    assert item["source_id"] == "yahoo_realestate"
    assert item["rent_yen"] == 80000 and item["mgmt_fee_yen"] == 5000
    assert item["layout"] == "1K" and item["area_sqm"] == 25
    assert item["floor"] == 3 and item["building_floors"] == 8
    assert item["structure"] == "rc" and item["building_type"] == "mansion"
    assert item["built_year"] is item["built_month"] is item["elevator"] is None
    assert item["building_age_years"] == 7
    assert item["fetched_at"] == STAMP and item["details_fetched_at"] is None
    assert not any(key in item for key in ("rights", "unit_id", "building_id", "status", "status_verified_at"))


def test_exact_station_access_is_selected_without_substring_match():
    item = parse(station="新宿駅")[0][0]
    assert item["station_name"] == "新宿" and item["walk_min"] == 12
    assert len(item["accesses"]) == 2
    other = parse(card(routes=["西新宿駅/東京メトロ丸ノ内線 徒歩3分"]), station="新宿")[0][0]
    assert other["station_name"] == "西新宿"


def test_bus_or_unparseable_walk_is_not_fabricated():
    item = parse(card(routes=["新宿駅/山手線 バス10分 徒歩2分", "西新宿駅/丸ノ内線 徒歩不明"] ))[0][0]
    assert item["walk_min"] is item["station_name"] is None
    assert item["accesses"] == []


@pytest.mark.parametrize("fee,expected", [("なし", 0), ("0円", 0), ("-", None), ("相談", None)])
def test_unknown_management_fee_is_not_zero(fee, expected):
    item = parse(card(rooms=room(fee=fee)))[0][0]
    assert item["mgmt_fee_yen"] == expected


@pytest.mark.parametrize("label,expected", [("NEW", None), ("エレベーターあり", True), ("エレベーターなし", False)])
def test_only_explicit_room_amenity_can_set_elevator(label, expected):
    rows, _, _ = parse(card(rooms=room(amenities=label)) + '<aside><span>エレベーターあり</span></aside>')
    assert rows[0]["elevator"] is expected
    assert rows[0]["bathroom_separate"] is rows[0]["furnished"] is None


@pytest.mark.parametrize("spec,expected", [
    ("築7年/地上8階建て/鉄筋コンクリート", 8),
    ("築7年/地下1階地上8階建て/鉄筋コンクリート", 8),
    ("築7年/地上8階建て(地下1階)/鉄筋コンクリート", 8),
    ("築7年/地上8階建て/地上9階建て/鉄筋コンクリート", None),
    ("築7年/地上8・9階建て/鉄筋コンクリート", None),
])
def test_above_ground_height_is_distinct_from_room_and_basement(spec, expected):
    item = parse(card(spec=spec))[0][0]
    assert item["building_floors"] == expected and item["floor"] == 3


def test_exact_embedded_date_requires_matching_dom_room_and_building_facts():
    rows, _, _ = parse(card() + state(metadata()))
    assert rows[0]["built_year"] == 2011 and rows[0]["built_month"] == 4
    assert rows[0]["building_age_years"] == 7  # 2026 - 7 is never used as a year.
    assert rows[0]["building_name"] == "合成建物0"


@pytest.mark.parametrize("changes", [
    {"BuiltOn": "2011-00"}, {"BuiltOn": "2011-13"}, {"BuiltOn": "2030-04"}, {"BuiltOn": "築7年"},
])
def test_invalid_or_future_embedded_date_does_not_replace_visible_fields(changes):
    building = metadata()
    building.update(changes)
    building["GroupProperties"][0].update(changes)
    item = parse(card() + state(building))[0][0]
    assert item["built_year"] is item["built_month"] is None
    assert item["rent_yen"] == 80000


@pytest.mark.parametrize("missing_mapping", ["building_id", "room_id", "duplicate_building_id", "duplicate_room_id", "missing_visible_height"])
def test_missing_or_ambiguous_mapping_keeps_construction_year_unknown(missing_mapping):
    building = metadata()
    markup = card()
    if missing_mapping == "building_id":
        building["StructureId"] = "different"
    elif missing_mapping == "room_id":
        building["GroupProperties"][0]["PropertyId"] = identity(99)
    elif missing_mapping == "duplicate_room_id":
        building["GroupProperties"].append(deepcopy(building["GroupProperties"][0]))
    elif missing_mapping == "missing_visible_height":
        markup = card(spec="築7年/階建不明/鉄筋コンクリート")
    embedded = state(building, deepcopy(building)) if missing_mapping == "duplicate_building_id" else state(building)
    item = parse(markup + embedded)[0][0]
    assert item["built_year"] is item["built_month"] is None


@pytest.mark.parametrize("scope,key,value", [
    ("building", "BuildingName", "別の建物"),
    ("building", "LocationView", {"AddressName": "東京都新宿区別の町"}),
    ("building", "TotalFloorNum", 9), ("building", "KindName", "アパート"),
    ("room", "StructureId", "different"), ("room", "PriceLabel", "9万円"),
    ("room", "MonthlyManagementCostLabel", "6,000円"),
    ("room", "MonopolyAreaLabel", "26m<sup>2</sup>"), ("room", "FloorNum", "4"),
    ("room", "BuiltOn", "2011-05"),
])
def test_known_dom_json_conflicts_reject_advertisement_and_report_its_url(scope, key, value):
    building = metadata()
    target = building if scope == "building" else building["GroupProperties"][0]
    target[key] = value
    conflicts = set()
    assert parse(card() + state(building), conflicts_out=conflicts)[0] == []
    assert conflicts == {detail_url('/rent/detail/' + identity() + '/')}


def test_hidden_rooms_in_embedded_state_are_not_returned():
    building = metadata(room_ids=[identity(), identity(1), identity(2)])
    rows, _, _ = parse(card() + state(building))
    assert len(rows) == 1 and rows[0]["source_listing_id"] == identity()


@pytest.mark.parametrize("scope,key,value", [
    ("building", "BuildingName", 42), ("building", "KindName", []),
    ("building", "TotalFloorNum", True), ("building", "TotalFloorNum", {}),
    ("building", "LocationView", 42), ("building", "LocationView", {"AddressName": []}),
    ("building", "BuiltOn", 2011), ("building", "CanDisplayBuildingName", "yes"),
    ("room", "PriceLabel", None), ("room", "PriceLabel", 80000),
    ("room", "MonthlyManagementCostLabel", {}), ("room", "MonopolyAreaLabel", {}),
    ("room", "FloorNum", True), ("room", "FloorNum", []), ("room", "BuiltOn", []),
])
def test_wrong_metadata_types_preserve_dom_and_other_buildings_without_invented_year(scope, key, value):
    building = metadata()
    target = building if scope == "building" else building["GroupProperties"][0]
    target[key] = value
    rows, _, _ = parse(card() + card(1) + state(building, metadata(1)))
    assert len(rows) == 2
    assert rows[0]["rent_yen"] == 80000 and rows[0]["built_year"] is None
    assert rows[1]["built_year"] == 2011 and rows[1]["built_month"] == 4


@pytest.mark.parametrize("script", [
    '<script>window.__SERVER_SIDE_CONTEXT__ = {common: {}, page: {"properties": invalid()}, mode: "pc"};</script>',
    '<script>window.__SERVER_SIDE_CONTEXT__ = {common: {}, page: {"properties": [], "properties": []}, mode: "pc"};</script>',
])
def test_unreadable_embedded_script_does_not_prevent_visible_facts(script):
    item = parse(card() + script)[0][0]
    assert item["rent_yen"] == 80000 and item["built_year"] is None


def test_generic_station_title_is_not_a_building_name_with_or_without_json():
    title = "山手線 新宿駅 地上8階建て 築7年"
    assert parse(card(name=title))[0][0]["building_name"] is None
    building = metadata()
    building["BuildingName"] = title
    building["CanDisplayBuildingName"] = False
    item = parse(card(name=title) + state(building))[0][0]
    assert item["building_name"] is None and item["built_year"] == 2011


def test_explicit_hidden_name_flag_stays_unknown_even_without_complete_metadata():
    building = metadata()
    building["CanDisplayBuildingName"] = False
    building["GroupProperties"] = []
    item = parse(card() + state(building))[0][0]
    assert item["building_name"] is None


def test_duplicate_url_conflict_removes_every_version_and_cannot_restore_it():
    conflicts = set()
    html = card(rooms=room() + room(rent="9万円") + room())
    assert parse(html, conflicts_out=conflicts)[0] == []
    assert conflicts == {detail_url('/rent/detail/' + identity() + '/')}


def test_exact_duplicate_is_single_and_underscore_alias_is_not_invented_identity():
    assert len(parse(card(rooms=room() + room()))[0]) == 1
    rows = parse(card(rooms=room() + room(room_id=identity(prefix=True))))[0]
    assert len(rows) == 2 and len({item["source_url"] for item in rows}) == 2


def test_conflicts_after_eight_room_cap_are_still_detected_and_later_buildings_read():
    html = card(rooms=''.join(room(index) for index in range(10)) + room(0, rent="9万円")) + card(1)
    conflicts = set()
    rows, cards, _ = parse(html, conflicts_out=conflicts)
    assert cards == 2 and len(rows) == 8  # Seven retained first-building rooms + next building.
    assert any(item["building_name"] == "合成建物1" for item in rows)
    assert detail_url('/rent/detail/' + identity() + '/') in conflicts


def test_caps_fifty_building_cards_and_eight_advertisements_per_card():
    html = ''.join(card(index, rooms=''.join(room(room_index, building=index) for room_index in range(9))) for index in range(51))
    rows, cards, _ = parse(html)
    assert cards == 51 and len(rows) == 400
    assert not any(item["building_name"] == "合成建物50" for item in rows)


@pytest.mark.parametrize("changes", [{"rent": "相談"}, {"fee": "1000001円"}, {"space": "1001m2"}, {"level": "9階"}, {"plan": "2LDK"}])
def test_invalid_numeric_or_unsupported_personal_fields_are_isolated(changes):
    rows, _, _ = parse(card(rooms=room(**changes) + room(1)))
    assert len(rows) == 1 and rows[0]["source_listing_id"] == identity(1)


@pytest.mark.parametrize("value", [
    "https://evil.example/rent/detail/" + identity() + "/",
    "//realestate.yahoo.co.jp/rent/detail/" + identity() + "/",
    "/rent/detail/" + "a" * 40 + "/",
    "/rent/detail/" + "A" * 44 + "/",
    "/rent/detail/" + identity() + "/?id=1",
    "/rent/detail/" + identity() + "/subpage/",
])
def test_only_exact_observed_detail_path_and_id_are_accepted(value):
    assert detail_url(value) is None


def test_checkbox_room_id_must_match_its_detail_link():
    html = card(rooms=room().replace('value="' + identity() + '"', 'value="' + identity(1) + '"'))
    conflicts = set()
    assert parse(html, conflicts_out=conflicts)[0] == []
    assert len(conflicts) == 1


def test_advertising_only_card_and_unrecognized_markup_do_not_make_fake_rows():
    assert parse(card(kind="広告"))[0] == []
    assert parse('<div>new unsupported page structure</div>') == ([], 0, False)
    assert parse('<div>該当する物件はありません</div>') == ([], 0, True)


def test_html_size_limit_is_enforced():
    with pytest.raises(PublicFetchError, match="html_too_complex"):
        parse(" " * (3 * 1024 * 1024 + 1))
