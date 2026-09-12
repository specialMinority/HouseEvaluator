"""Synthetic markup follows the public classes inspected on 2026-09-13.

No real rental advertisements are embedded in the repository fixtures.
"""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from backend.v2.personal import validate_listing
from backend.v2.public_fetch import PublicFetchError, PublicResponse
from backend.v2.public_html import building_floors, building_type, elevator, enrich_detail, floor, parse_search, structure
from backend.v2.public_search import MAX_DETAILS, RobotsPolicy, _REGIONS, _rank_candidates, build_search_url, options, search
from backend.v2 import public_search
from backend.v2.public_discovery import line_choices, station_choice, next_page


STAMP = "2026-01-01T00:00:00Z"
SUBJECT = {"city": "tokyo", "municipality": "新宿区", "station_name": "新宿", "layout": "1K", "area_sqm": 25, "rent_yen": 90000}


def card(index=0, *, rent="8万円", fee="5000円", plan="1K", space="25m<sup>2</sup>", level="2階", name=None, address=None):
    return f'''<div class="cassetteitem"><div class="cassetteitem_content-title">{name or '合成建物' + str(index)}</div>
    <div class="cassetteitem_content-label">賃貸マンション</div>
    <li class="cassetteitem_detail-col1">{address or '東京都新宿区合成町' + str(index)}</li>
    <li class="cassetteitem_detail-col2"><div>東京メトロ丸ノ内線/西新宿駅 歩3分</div><div>ＪＲ山手線/新宿駅 歩12分</div></li>
    <li class="cassetteitem_detail-col3">築6年 8階建</li><table><tbody><tr class="js-cassette_link">
    <td><input name="bc" value="100000000{index:03d}"></td><td></td><td>{level}</td>
    <td><span class="cassetteitem_price--rent">{rent}</span><span class="cassetteitem_price--administration">{fee}</span></td>
    <td><span class="cassetteitem_madori">{plan}</span><span class="cassetteitem_menseki">{space}</span></td>
    <td><a class="js-cassette_link_href" href="/chintai/jnc_000000000{index:03d}/?bc=100000000{index:03d}">詳細</a></td>
    </tr></tbody></table></div>'''


def detail(index=0, *, extra="", amenities="バストイレ別", rent="8万円", fee="5000円", address=None, name=None):
    return f'''<div id="wrapper"><div class="section_h1"><h1>{name or '合成建物' + str(index)}</h1>
    <div class="property_view_note"><span class="property_view_note-emphasis">{rent}</span><span>管理費・共益費: {fee}</span></div>
    <table class="property_view_table"><tr><th>所在地</th><td>{address or '東京都新宿区合成町' + str(index)}</td></tr>
    <tr><th>間取り</th><td>1K</td><th>専有面積</th><td>25m<sup>2</sup></td></tr>
    <tr><th>駅徒歩</th><td>東京メトロ丸ノ内線/西新宿駅 歩3分 ＪＲ山手線/新宿駅 歩12分</td></tr>
    <tr><th>向き</th><td>南</td><th>建物種別</th><td>マンション</td></tr></table></div>
    <div id="contents"><div id="bkdt-option"><ul><li>{amenities}</li></ul></div>
    <table class="table_gaiyou"><tr><th>構造</th><td>鉄筋コン</td><th>築年月</th><td>2020年3月</td></tr>
    <tr><th>階建</th><td>2階/8階建</td><th>契約期間</th><td>普通借家 2年</td></tr>
    <tr><th>SUUMO 物件コード</th><td>100000000{index:03d}</td><th>情報更新日</th><td>2025/12/31</td></tr>{extra}</table></div></div>'''


def parsed(html=None):
    return parse_search(html or card(), fetched_at=STAMP, regions=_REGIONS, target_station="新宿")[0]


def enrich(html=None, item=None):
    return enrich_detail(item or parsed()[0], html or detail(), fetched_at=STAMP, regions=_REGIONS, target_station="新宿")


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr("backend.v2.public_search.time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def clean_station_cache():
    public_search._STATION_CACHE.clear()
    yield
    public_search._STATION_CACHE.clear()


def mocked_search(html=None, *, robots="User-agent: *\nDisallow:\n", detail_status=200, detail_html=None):
    calls = []

    def fetch(url, *, deadline):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return PublicResponse(200, robots.encode(), "text/plain")
        if "/ichiran/" in url:
            return PublicResponse(200, (html or card()).encode())
        return PublicResponse(detail_status, (detail_html or detail()).encode())

    with patch('backend.v2.public_search._resolve_station', return_value=None):
        return search(SUBJECT, fetcher=fetch), calls


def test_options_use_all_three_cities_and_personal_layouts():
    result = options()
    assert [len(city["municipalities"]) for city in result["cities"]] == [23, 24, 7]
    assert result["layouts"] == ["1R", "1K", "1DK", "1LDK"]
    result["cities"][0]["municipalities"][0]["name"] = "changed"
    assert options()["cities"][0]["municipalities"][0]["name"] == "千代田区"


def test_price_never_changes_search_filters():
    a = build_search_url(SUBJECT)
    b = build_search_url(dict(SUBJECT, rent_yen=900000, mgmt_fee_yen=80000))
    assert a == b
    query = parse_qs(urlsplit(a).query)
    assert query["cb"] == ["0.0"] and query["ct"] == ["9999999"]
    assert query["md"] == ["02"] and query["mb"] == ["20"] and query["mt"] == ["30"]


@pytest.mark.parametrize("changes", [{"city": "kyoto"}, {"municipality": "京都市"}, {"layout": "2LDK"}, {"area_sqm": float("nan")}, {"area_sqm": True}, {"area_sqm": 1001}, {"station_name": ""}])
def test_unsupported_subject_never_fetches(changes):
    result = search(dict(SUBJECT, **changes), fetcher=lambda *a, **k: pytest.fail("unexpected network"))
    assert result["source_reports"][0]["status"] == "unsupported"


@pytest.mark.parametrize("rules,allowed", [
    ("Disallow: /jj/", False), ("Disallow: /*?*sc=", False), ("Disallow: /*?*sort=", True),
    ("Disallow: /jj/\nAllow: /jj/chintai/", True),
    ("Disallow: /jj/chintai/ichiran/FR301FC001/$", True),
    ("Disallow: /jj/chintai/ichiran/%46R301FC001/", False),
    ("Disallow: /jj/\nAllow: /jj/", True),
])
def test_robots_wildcards_anchor_specificity_percent_encoding(rules, allowed):
    assert RobotsPolicy("User-agent: *\n" + rules).allows(build_search_url(SUBJECT)) is allowed


def test_robots_product_group_overrides_wildcard_and_combines_groups():
    policy = RobotsPolicy("User-agent: *\nDisallow: /\nUser-agent: HouseEvaluator\nAllow: /jj/\nUser-agent: HouseEvaluator\nDisallow: /chintai/")
    assert policy.allows(build_search_url(SUBJECT))
    assert not policy.allows("https://suumo.jp/chintai/bc_100000000000/")


def test_robots_truncated_or_unrecognized_policy_cannot_allow():
    with pytest.raises(PublicFetchError):
        RobotsPolicy("User-agent: *\n" + "# comment\n" * 4999 + "Disallow: /\n")
    assert not RobotsPolicy("<html>server error</html>").allows(build_search_url(SUBJECT))


def test_station_exact_match_and_unknown_values_remain_unknown():
    row = parsed(card(fee="-"))[0]
    assert row["station_name"] == "新宿" and row["walk_min"] == 12
    assert row["mgmt_fee_yen"] is None and row["built_year"] is None
    assert "mgmt_fee_yen" in row["missing_fields"]
    assert not any(field in row for field in ("rights", "unit_id", "building_id", "status_verified_at", "status"))


def test_bad_numbers_and_foreign_urls_are_not_candidates():
    assert not parsed(card(rent="相談"))
    assert not parsed(card().replace("/chintai/jnc_", "https://evil.example/chintai/jnc_"))
    assert not parsed(card(space="25坪"))


def test_duplicate_url_price_conflicts_remove_both_rows():
    assert len(parsed(card() + card())) == 1
    assert parsed(card() + card(rent="9万円")) == []


def test_detail_enrichment_has_exact_year_and_separate_listing_date():
    row = enrich()
    assert row["structure"] == "rc" and row["built_year"] == 2020
    assert row["bathroom_separate"] is True and row["furnished"] is None
    assert row["contract_type"] == "standard"
    assert row["listing_updated_date"] == "2025/12/31"
    assert row["details_fetched_at"] == STAMP and "status_verified_at" not in row


@pytest.mark.parametrize("html", [
    detail(extra="<tr><th>構造</th><td>木造</td></tr>"), detail(address="東京都新宿区別の町0"),
    detail(name="別の建物"), detail(rent="9万円"), detail(fee="-"),
    detail().replace("100000000000", "100000000001"),
    detail().replace("2階/8階建", "3階/8階建"), detail().replace("25m", "26m"),
])
def test_detail_conflicts_never_mix_with_search_row(html):
    assert enrich(html) is None


def test_recommendation_amenities_and_tables_do_not_leak_into_listing():
    row = enrich(detail(amenities="エアコン") + "<section><li>家具付、バストイレ別</li><table><tr><th>構造</th><td>木造</td></tr></table></section>")
    assert row["bathroom_separate"] is None and row["furnished"] is None and row["structure"] == "rc"


def test_robots_deny_stops_before_search(no_delay):
    result, calls = mocked_search(robots="User-agent: *\nDisallow: /jj/")
    assert len(calls) == 1 and result["source_reports"][0]["status"] == "blocked"


def test_long_crawl_delay_does_not_fetch_search(no_delay):
    result, calls = mocked_search(robots="User-agent: *\nCrawl-delay: 60")
    assert len(calls) == 1 and result["source_reports"][0]["status"] == "unavailable"


def test_crawl_delay_applies_before_search_and_detail(monkeypatch):
    delays = []
    monkeypatch.setattr("backend.v2.public_search.time.sleep", delays.append)
    result, calls = mocked_search(robots="User-agent: *\nCrawl-delay: 2")
    assert delays == [2, 2] and len(calls) == 3
    assert result["source_reports"][0]["detail_count"] == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_detail_restriction_stops_all_remaining_requests(no_delay, status):
    result, calls = mocked_search(card() + card(1), detail_status=status)
    assert len(calls) == 3
    assert result["source_reports"][0]["status"] == "blocked"
    assert len(result["listings"]) == 2


def test_captcha_stops_and_returns_no_mock_data(no_delay):
    result, calls = mocked_search("<title>Access Denied</title>")
    assert result["source_reports"][0]["status"] == "blocked" and result["listings"] == [] and len(calls) == 2


def test_zero_results_are_distinguished_from_parser_change(no_delay):
    empty, _ = mocked_search("<div>該当する物件はありません</div>")
    changed, _ = mocked_search("<title>SUUMO</title><div>new layout</div>")
    assert empty["source_reports"][0]["status"] == "ok"
    assert changed["source_reports"][0]["status"] == "parse_changed"


def test_output_limit_and_invalid_rows_are_isolated(no_delay):
    # Two rows per building creates more than sixty valid rooms on one page.
    html = "".join(card(i) for i in range(70))
    # Parser itself only reads the first 50 buildings: use additional rows in
    # each card, all with independent detail URLs, to exercise response capping.
    html = card(999, level="101階") + "".join(card(i).replace("</tbody>", card(i + 100).split("<tbody>")[1].split("</tbody>")[0] + "</tbody>") for i in range(40))
    result, calls = mocked_search(html)
    report = result["source_reports"][0]
    assert report["rejected_count"] == 1 and report["partial"]
    assert len(result["listings"]) == 60 and len(calls) <= MAX_DETAILS + 2
    for row in result["listings"]:
        validate_listing(row)


def test_huge_robots_policy_fails_closed_at_orchestrator(no_delay):
    result, calls = mocked_search(robots="User-agent: *\n" + "# padding\n" * 5001 + "Disallow: /\n")
    assert len(calls) == 1 and result["source_reports"][0]["status"] == "unavailable"


def test_explicit_advertised_age_is_ranking_only_and_not_construction_year():
    row = parsed(card().replace("築6年", "築24年"))[0]
    assert row["building_age_years"] == 24
    assert row["built_year"] is None and "built_year" in row["missing_fields"]
    assert parsed(card().replace("築6年", "新築"))[0]["building_age_years"] is None


def test_matching_station_second_room_precedes_other_station_first_building():
    first = parsed()[0]
    second = dict(first, source_url=first["source_url"] + "-second", area_sqm=25.1)
    different = dict(first, building_name="another", address="another", station_name="西新宿")
    ranked = _rank_candidates([first, different, second], SUBJECT, current_year=2026)
    assert ranked == [0, 2, 1]


def test_similar_area_precedes_age_and_age_ranking_never_uses_rent():
    subject = dict(SUBJECT, built_year=2002)
    first = dict(parsed()[0], building_age_years=24, area_sqm=30)
    second = dict(parsed()[0], building_age_years=6, area_sqm=25.2, building_name="B", address="B")
    third = dict(parsed()[0], building_age_years=23, area_sqm=25.5, building_name="C", address="C")
    rows = [first, second, third]
    assert _rank_candidates(rows, subject, current_year=2026) == [2, 1, 0]
    expensive = [dict(row, rent_yen=1000000 - index * 100000, mgmt_fee_yen=index * 30000) for index, row in enumerate(rows)]
    assert _rank_candidates(expensive, subject, current_year=2026) == [2, 1, 0]


def test_diversity_is_applied_within_matching_station_and_area_band():
    first = parsed()[0]
    second = dict(first, area_sqm=25.1)
    third = dict(first, area_sqm=25.2, building_name="B", address="B")
    assert _rank_candidates([first, second, third], SUBJECT, current_year=2026) == [0, 2, 1]


@pytest.mark.parametrize("label,expected", [("鉄骨鉄筋コン", "src"), ("ＳＲＣ", "src"), ("鉄筋コンクリート造", "rc"),
                                             ("気泡コン", None), ("その他", None), ("プレコン", None)])
def test_structure_aliases_require_unambiguous_structure(label, expected):
    assert structure(label) == expected


def test_up_to_24_details_are_requested_without_an_extra_search_page(no_delay):
    result, calls = mocked_search("".join(card(index) for index in range(30)))
    assert len(calls) == 26
    assert result["source_reports"][0]["detail_attempt_count"] == 24
    assert result["source_reports"][0]["detail_limit"] == 24
    assert sum("/ichiran/" in url for url in calls) == 1


def test_longer_detail_budget_still_stops_before_45_seconds(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("backend.v2.public_search.time.monotonic", lambda: elapsed[0])
    monkeypatch.setattr("backend.v2.public_search.time.sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))
    result, calls = mocked_search("".join(card(index) for index in range(30)), robots="User-agent: *\nCrawl-delay: 2")
    assert elapsed[0] <= 45
    assert result["source_reports"][0]["detail_attempt_count"] < 24
    assert result["source_reports"][0]["partial"] is True


def test_search_distinguishes_advertised_building_type_and_above_ground_total():
    mansion = parsed()[0]
    apartment = parsed(card(1).replace("賃貸マンション", "賃貸アパート").replace("8階建", "3階建"))[0]
    assert mansion["property_type"] == apartment["property_type"] == "apartment"
    assert (mansion["building_type"], mansion["building_floors"], mansion["floor"]) == ("mansion", 8, 2)
    assert (apartment["building_type"], apartment["building_floors"], apartment["floor"]) == ("apartment", 3, 2)
    assert mansion["elevator"] is None and "elevator" in mansion["missing_fields"]


@pytest.mark.parametrize("text,expected", [
    ("築6年 8階建", 8), ("新築 地下1地上13階建", 13), ("3階/8階建", 8),
    ("地下1階/地下2地上8階建", 8), ("地下1階地上8階建", 8), ("地上8階建", 8),
    ("８階建（地下２階）", 8), ("100階建", 100),
    ("地下1階建", None), ("3階", None), ("0階建", None), ("101階建", None),
    ("3階建・8階建", None), ("8階建以上", None), ("2-3階/8階建", None),
    ("地下0地上8階建", None), ("2階/8階建/地下1階", None),
])
def test_total_floors_do_not_include_basements_or_guess_composite_values(text, expected):
    assert building_floors(text) == expected


@pytest.mark.parametrize("text,expected", [("3階/8階建", 3), ("地下1階/地下2地上8階建", -1),
                                           ("B2階", -2), ("8階建", None), ("1-2階", None), ("地下1地上8階建", None)])
def test_occupied_floor_is_never_the_building_total(text, expected):
    assert floor(text) == expected


@pytest.mark.parametrize("text", ["RC", "鉄筋コン", "8階建", "賃貸マンション・アパート", "その他"])
def test_building_type_requires_an_explicit_unambiguous_kind(text):
    assert building_type(text) is None


def test_missing_building_label_is_not_inferred_from_name_height_or_structure():
    original = parsed(card(name="合成マンション").replace("賃貸マンション", "その他"))[0]
    row = enrich(detail(name="合成マンション").replace("<th>建物種別</th><td>マンション</td>", ""), original)
    assert row["building_type"] is None and row["structure"] == "rc" and row["building_floors"] == 8
    assert "building_type" in row["missing_fields"]


def test_detail_backfills_only_explicit_building_profile_and_preserves_false():
    original = parsed(card().replace("賃貸マンション", "その他").replace("築6年 8階建", "築6年"))[0]
    row = enrich(detail(amenities="バストイレ別、エレベーターなし"), original)
    assert row["building_type"] == "mansion" and row["building_floors"] == 8
    assert row["floor"] == 2 and row["elevator"] is False
    assert not set(("building_type", "building_floors", "elevator")) & set(row["missing_fields"])


@pytest.mark.parametrize("html", [
    detail().replace("<td>マンション</td>", "<td>アパート</td>"),
    detail().replace("2階/8階建", "2階/3階建"),
    detail(extra="<tr><th>建物種類</th><td>アパート</td></tr>"),
    detail(extra="<tr><th>階</th><td>3階</td></tr>"),
    detail(extra="<tr><th>階建</th><td>2階/3階建</td></tr>"),
])
def test_profile_conflicts_cannot_be_mixed_into_search_row(html):
    assert enrich(html) is None


def test_occupied_floor_above_building_total_is_rejected(no_delay):
    result, _ = mocked_search(card(level="9階"))
    assert result["source_reports"][0]["rejected_count"] == 1 and result["listings"] == []
    row = parsed(card(level="9階"))[0]
    assert enrich(detail().replace("2階/8階建", "9階/8階建"), row) is None


@pytest.mark.parametrize("features,field,expected", [
    ("エレベーター", None, True), ("バストイレ別、エレベーター2基", None, True),
    ("エレベータ有り", None, True), ("エレベーターなし", None, False),
    ("エアコン", "なし", False), ("", "あり", True), ("", "2基", True),
    ("エアコン", None, None), ("エレベーター停止中", None, None),
    ("エレベーター要確認", None, None), ("エレベーター、エレベーターなし", None, None),
    ("エレベーター", "無し", None), ("", ["有", "無"], None),
])
def test_elevator_only_uses_explicit_nonconflicting_evidence(features, field, expected):
    assert elevator(features, field) is expected


def test_elevator_amenity_scope_and_list_boundaries():
    html = detail(amenities="エレベーター</li><li>エアコン")
    assert enrich(html)["elevator"] is True
    assert enrich(detail(amenities="エアコン") + "<aside><li>エレベーター</li></aside>")["elevator"] is None
    extra = "<tr><th>エレベーター</th><td>有</td><th>エレベータ</th><td>無</td></tr>"
    assert enrich(detail(extra=extra))["elevator"] is None


def test_search_returns_new_profile_fields_from_both_pages(no_delay):
    result, _ = mocked_search(detail_html=detail(amenities="エレベーター"))
    row = result["listings"][0]
    assert (row["building_type"], row["building_floors"], row["floor"], row["elevator"]) == ("mansion", 8, 2, True)
    assert result["source_reports"][0]["detail_count"] == 1


def test_detail_priority_matches_explicit_kind_and_height_without_price_filtering():
    base = parsed()[0]
    subject = dict(SUBJECT, building_type="mansion", building_floors=8)
    rows = [dict(base, building_type="apartment", building_floors=3),
            dict(base, building_type="mansion", building_floors=3),
            dict(base, building_type=None, building_floors=8),
            dict(base, building_type="mansion", building_floors=8)]
    assert _rank_candidates(rows, subject, current_year=2026) == [3, 1, 2, 0]
    rows[3]["rent_yen"] = 9999999
    assert _rank_candidates(rows, subject, current_year=2026) == [3, 1, 2, 0]
    assert parse_qs(urlsplit(build_search_url(subject)).query)['ts'] == ['1']
    assert build_search_url(subject) == build_search_url(dict(subject, rent_yen=9999999))


LINE_FORM = '<form id="js-gotoEkiForm"><ul><li><input name="rn" value="0005"><a href="/chintai/tokyo/en_yamanotesen/">ＪＲ山手線</a></li></ul></form>'
STATION_FORM = '<form id="js-showEkiForm"><input name="ar" value="030"><input name="bs" value="040"><input name="ra" value="013"><input name="rn" value="0005"></form><form id="js-areaSelectForm"><input name="rn" value="0005"><ul><li><input name="ek" value="000519670">新宿 (1,000)</li><li><input name="ek" value="000591670">西新宿 (100)</li></ul></form>'


def station_fetcher(*, conflict=False, restriction=False):
    calls = []

    def fetch(url, *, deadline):
        calls.append(url)
        if url.endswith('/robots.txt'):
            return PublicResponse(200, b'User-agent: *\nDisallow:\n')
        if url.endswith('/ensen/'):
            return PublicResponse(403 if restriction else 200, LINE_FORM.encode())
        if '/en_yamanotesen/' in url:
            return PublicResponse(200, STATION_FORM.encode())
        if '/ichiran/' in url:
            query = parse_qs(urlsplit(url).query)
            if 'sc' in query:
                html = card(0)
            else:
                page = int(query.get('page', ['1'])[0])
                index = 0 if conflict and page == 2 else page
                html = card(index, rent='9万円' if conflict and page == 2 else '8万円')
                if page < 3:
                    base = url.split('&page=', 1)[0]
                    html += f'<a href="{base}&page={page + 1}">次へ</a>'
            return PublicResponse(200, html.encode())
        index = int(parse_qs(urlsplit(url).query)['bc'][0][-3:])
        return PublicResponse(200, detail(index).encode())

    return fetch, calls


def test_official_forms_resolve_exact_route_and_station_not_substrings():
    choices = line_choices(LINE_FORM, 'tokyo', ['JR山手線'])
    assert len(choices) == 1 and choices[0]['rn'] == '0005'
    found = station_choice(STATION_FORM, choices[0], '新宿駅')
    assert found['ek'] == '000519670' and found['source_url'].endswith('/en_yamanotesen/')
    query = parse_qs(urlsplit(build_search_url(SUBJECT, station=found)).query)
    assert query['ra'] == ['013'] and 'ta' not in query and 'sc' not in query
    assert station_choice(STATION_FORM, choices[0], '宿') is None
    assert line_choices(LINE_FORM.replace('tokyo', 'osaka'), 'tokyo', ['JR山手線']) == []


def test_ambiguous_station_or_wrong_line_never_generates_a_code():
    line = line_choices(LINE_FORM, 'tokyo', ['JR山手線'])[0]
    duplicate = STATION_FORM.replace('</ul>', '<li><input name="ek" value="000519671">新宿 (200)</li></ul>')
    assert station_choice(duplicate, line, '新宿') is None
    assert station_choice(STATION_FORM.replace('name="rn" value="0005"', 'name="rn" value="0573"'), line, '新宿') is None


def test_station_search_is_bounded_and_metadata_cache_is_reused(no_delay):
    fetch, calls = station_fetcher()
    result = search(SUBJECT, fetcher=fetch)
    report = result['source_reports'][0]
    assert report['search_page_count'] == 3 and report['metadata_page_count'] == 2
    assert report['station_page_count'] == 2 and report['search_scope'] == 'station'
    assert report['station']['ek'] == '000519670' and report['station_resolution'] == 'public_form'
    assert report['listing_count'] == report['station_count'] == report['distinct_building_count'] == 3
    assert report['stop_reason'] == 'page_limit'
    assert len(report['search_urls']) == 3
    second, calls2 = station_fetcher()
    again = search(SUBJECT, fetcher=second)['source_reports'][0]
    assert again['station_resolution'] == 'cached_public_form'
    assert again['station_page_count'] == 3 and again['metadata_page_count'] == 0
    assert not any('/ensen/' in url or '/en_' in url for url in calls2)


def test_station_cache_expires_and_is_isolated_by_municipality(no_delay, monkeypatch):
    fetch, _ = station_fetcher()
    search(SUBJECT, fetcher=fetch)
    assert public_search._cached_station(dict(SUBJECT, municipality='江戸川区')) is None
    record = next(iter(public_search._STATION_CACHE.values()))
    monkeypatch.setattr(public_search.time, 'monotonic', lambda: record['cached_at'] + public_search.STATION_CACHE_SECONDS + 1)
    assert public_search._cached_station(SUBJECT) is None


def test_metadata_restriction_stops_before_station_pages_and_details(no_delay):
    fetch, calls = station_fetcher(restriction=True)
    result = search(SUBJECT, fetcher=fetch)
    report = result['source_reports'][0]
    assert report['status'] == 'blocked' and report['stop_reason'] == 'blocked'
    assert report['search_page_count'] == 1 and report['detail_attempt_count'] == 0
    assert len(calls) == 3 and len(result['listings']) == 1


def test_cross_page_conflicting_url_removes_both_and_no_detail_repeats(no_delay):
    fetch, calls = station_fetcher(conflict=True)
    result = search(SUBJECT, fetcher=fetch)
    assert result['source_reports'][0]['rejected_count'] == 2
    assert all(row['source_listing_id'] != '100000000000' for row in result['listings'])
    detail_urls = [url for url in calls if '/jnc_' in url]
    assert len(detail_urls) == len(set(detail_urls))


def test_pagination_follows_only_observed_identical_filters():
    url = build_search_url(SUBJECT)
    page2 = url + '&page=2'
    assert next_page(f'<a href="{page2}">次へ</a>', url) == page2
    assert next_page(f'<a href="{page2.replace("md=02", "md=03")}">次へ</a>', url) is None
    assert next_page(f'<a href="{page2}&page=2">次へ</a>', url) is None
    assert next_page(f'<a href="{page2.replace("suumo.jp", "evil.example")}">次へ</a>', url) is None
    assert next_page(f'<a href="{url}&page=4">次へ</a>', url + '&page=3') is None


def test_near_area_query_precedes_broad_search_and_remains_price_independent():
    subject = dict(SUBJECT, area_sqm=33.58, building_type='mansion')
    narrow = parse_qs(urlsplit(build_search_url(subject)).query)
    broad = parse_qs(urlsplit(build_search_url(subject, broad=True)).query)
    assert (narrow['mb'], narrow['mt']) == (['30'], ['40'])
    assert (broad['mb'], broad['mt']) == (['25'], ['45'])
    assert narrow['ts'] == ['1'] and narrow['cb'] == ['0.0'] and narrow['ct'] == ['9999999']


def test_candidate_ranking_spreads_buildings_before_area_subbands_and_rejects_outside_window():
    base = parsed()[0]
    target = dict(SUBJECT, area_sqm=33.58, building_type='mansion', building_floors=8)
    rows = [dict(base, area_sqm=33.5), dict(base, area_sqm=33.6),
            dict(base, area_sqm=31.0, building_name='B', address='B'),
            dict(base, area_sqm=25.92, building_name='C', address='C'),
            dict(base, area_sqm=33.0, building_type='apartment', building_floors=3, building_name='D', address='D')]
    ranked = _rank_candidates(rows, target, current_year=2026)
    assert ranked == [1, 2, 0, 4, 3]


def test_resolved_code_without_a_read_station_page_cannot_claim_station_search(no_delay):
    fetch, _ = station_fetcher()

    def fail_focused(url, *, deadline):
        if '/ichiran/' in url and 'ek=' in url:
            return PublicResponse(403)
        return fetch(url, deadline=deadline)

    report = search(SUBJECT, fetcher=fail_focused)['source_reports'][0]
    assert report['station_resolution'] == 'public_form'
    assert report['search_scope'] == 'municipality_fallback' and report['station_page_count'] == 0
    assert '중심' not in report['search_scope_label']


def test_wrong_station_and_outside_comparison_area_never_consume_details(no_delay):
    html = card(space='19m2') + card(1).replace('ＪＲ山手線/新宿駅', 'ＪＲ山手線/別駅')
    result, calls = mocked_search(html)
    assert result['listings'] == []
    assert result['source_reports'][0]['out_of_scope_count'] == 2
    assert result['source_reports'][0]['detail_attempt_count'] == 0


def test_same_building_middle_floor_precedes_ground_and_top_for_detail_budget():
    base = dict(parsed()[0], building_floors=8, building_type='mansion')
    subject = dict(SUBJECT, building_floors=8, building_type='mansion', floor=3)
    rows = [dict(base, floor=1), dict(base, floor=8), dict(base, floor=3)]
    assert _rank_candidates(rows, subject, current_year=2026)[0] == 2


def test_details_visit_each_known_building_before_a_second_room_and_stop_at_two():
    base = parsed()[0]
    rows = [dict(base, floor=index + 1) for index in range(8)] + [dict(base, building_name='B', address='B'), dict(base, building_name='C', address='C')]
    assert public_search._detail_candidates(rows) == [0, 8, 9, 1]


@pytest.mark.parametrize('status', [404, 410])
def test_removed_detail_does_not_prevent_checking_other_buildings(no_delay, status):
    result, calls = mocked_search(card() + card(1), detail_status=status)
    report = result['source_reports'][0]
    assert len(calls) == 4 and report['detail_attempt_count'] == 2
    assert report['detail_unavailable_count'] == 2 and report['partial']
    assert report['status'] == 'ok'
