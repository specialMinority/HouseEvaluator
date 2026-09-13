"""Offline orchestration checks; navigation and parsed rows are test doubles.

Discovery URL validation and HTML parsing have independent fixtures. These tests
exercise the real robots policy, budgets, row validation, ranking, and routing;
no test may call a live rental portal.
"""
from copy import deepcopy
from importlib import import_module
import threading
from unittest.mock import Mock

import pytest

from backend.v2 import public_search
from backend.v2.public_fetch import PublicFetchError, PublicResponse


STAMP = "2026-01-01T00:00:00Z"
ROBOTS = "https://www.chintai.net/robots.txt"
MUNICIPALITY = "https://www.chintai.net/tokyo/area/13104/list/"
PAGE2 = MUNICIPALITY + "page2/"
PAGE3 = MUNICIPALITY + "page3/"
PAGE4 = MUNICIPALITY + "page4/"
STATION = "https://www.chintai.net/tokyo/ensen/000000069/list/"
FILTER = "https://www.chintai.net/list/?prefkey=tokyo&ue=000000069&rt=51&o=10&m=1&sf=20&st=30&cf=0&ct=0"
# The original next link starts with ue; changing its query order to make it
# pass robots would be a different request, not permissible pagination.
BLOCKED_NEXT = "https://www.chintai.net/list/?ue=000000069&prefkey=tokyo&rt=51&sf=20&m=1&st=30&cf=0&urlType=dynamic&ct=0&i=2&o=10"
SUBJECT = {
    "city": "tokyo", "municipality": "新宿区", "station_name": "新宿",
    "layout": "1K", "area_sqm": 25, "rent_yen": 90000,
    "building_type": "mansion", "building_floors": 8, "floor": 3,
}


def row(index=0, *, building=None, **changes):
    building = index if building is None else building
    value = dict(
        SUBJECT, source_id="chintai", rent_yen=80000, mgmt_fee_yen=None,
        source_url=f"https://www.chintai.net/detail/bk-TEST{index:010d}/",
        building_name=f"合成建物{building}", address=f"東京都新宿区合成町{building}",
        title=f"合成建物{building}", fetched_at=STAMP, structure=None,
        built_year=None, walk_min=10, orientation=None, elevator=None,
        missing_fields=["mgmt_fee_yen", "built_year", "structure", "elevator"],
    )
    value.update(changes)
    return value


@pytest.fixture
def source(monkeypatch):
    module = import_module("backend.v2.chintai_search")
    monkeypatch.setattr(module, "_SEARCH_SLOT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    # Security validators are not disabled in production; separate discovery
    # tests cover their contract. Here each fetched URL must be in our fixture.
    monkeypatch.setattr(module, "checked_url", lambda value: value)
    monkeypatch.setattr(module, "municipality_url", lambda subject, regions: MUNICIPALITY, raising=False)
    monkeypatch.setattr(module, "build_search_url", lambda subject, regions, station_url=None, broad=False: FILTER if station_url else MUNICIPALITY)
    monkeypatch.setattr(module, "resolve_station", lambda html, subject, base_url: None)
    monkeypatch.setattr(module, "_timestamp", lambda: STAMP)
    return module


class Pages:
    def __init__(self, source, monkeypatch, pages, *, robots="User-agent: *\nDisallow:\n", statuses=None, resolved=None, cards=1, empty=True, advance=None):
        self.source = source
        self.calls = []
        self.deadlines = []
        self.parser_calls = []
        self.robots = robots
        self.statuses = statuses or {}
        self.advance = advance
        self.urls = list(pages)
        self.tokens = {url: f"<html><body>synthetic-page-{index}</body></html>" for index, url in enumerate(self.urls)}
        self.snapshots = {self.tokens[url]: values for url, values in pages.items()}
        following = {self.tokens[url]: self.urls[index + 1] if index + 1 < len(self.urls) else None for index, url in enumerate(self.urls)}

        def parse(html, *, fetched_at, regions, target_station, conflicts_out=None):
            self.parser_calls.append((html, fetched_at, target_station))
            return deepcopy(self.snapshots[html]), cards, empty

        monkeypatch.setattr(source, "parse_search", parse)
        monkeypatch.setattr(source, "next_page", lambda html, current_url, max_page: following[html])
        if resolved:
            monkeypatch.setattr(source, "resolve_station", lambda html, subject, base_url: resolved.get(base_url))

    def __call__(self, url, *, deadline):
        assert url not in self.calls, f"Unexpected retry: {url}"
        self.calls.append(url)
        self.deadlines.append(deadline)
        if self.advance:
            self.advance(url)
        status = self.statuses.get(url, 200)
        if isinstance(status, Exception):
            raise status
        if status != 200:
            return PublicResponse(status, b"Service temporarily unavailable", diagnostics={"classification": "service_unavailable", "retry_after_seconds": 30})
        if url == ROBOTS:
            return PublicResponse(200, self.robots.encode(), "text/plain")
        assert url in self.tokens, f"Unexpected external or detail request: {url}"
        return PublicResponse(200, self.tokens[url].encode())

    def run(self, subject=None):
        return self.source.search(deepcopy(subject or SUBJECT), fetcher=self)


def report(result):
    return result["source_reports"][0]


def test_robots_denial_performs_only_the_robots_request(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()]}, robots="User-agent: *\nDisallow: /tokyo/")
    result = pages.run()
    assert pages.calls == [ROBOTS]
    assert pages.parser_calls == [] and result["listings"] == []
    assert report(result)["status"] == "blocked"
    assert report(result)["error_code"] == "robots_blocked"


@pytest.mark.parametrize("status", [403, 429, 503])
def test_first_search_error_is_not_retried_or_replaced_by_another_source(source, monkeypatch, status):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()]}, statuses={MUNICIPALITY: status})
    result = pages.run()
    assert pages.calls == [ROBOTS, MUNICIPALITY]
    assert pages.parser_calls == [] and result["listings"] == []
    assert report(result)["http_status"] == status
    assert report(result)["search_page_count"] == 0
    assert report(result)["status"] == ("unavailable" if status == 503 else "blocked")


@pytest.mark.parametrize("status", [403, 429, 503])
def test_later_error_preserves_previous_page_without_retry(source, monkeypatch, status):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(1)], PAGE3: [row(2)]}, statuses={PAGE2: status})
    result = pages.run()
    assert pages.calls == [ROBOTS, MUNICIPALITY, PAGE2]
    assert [item["source_url"] for item in result["listings"]] == [row()["source_url"]]
    assert report(result)["partial"] is True
    assert report(result)["search_page_count"] == 1
    assert report(result)["detail_attempt_count"] == report(result)["detail_count"] == 0
    assert report(result)["http_status"] == status
    if status == 503:
        assert report(result)["diagnostics"] == {"classification": "service_unavailable", "retry_after_seconds": 30}


def test_three_page_limit_includes_initial_municipality_page(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(1)], PAGE3: [row(2)], PAGE4: [row(3)]})
    result = pages.run()
    assert pages.calls == [ROBOTS, MUNICIPALITY, PAGE2, PAGE3]
    assert report(result)["search_page_count"] == 3 and len(result["listings"]) == 3
    assert report(result)["stop_reason"] == "page_limit" and report(result)["partial"]
    assert report(result)["detail_limit"] == report(result)["detail_attempt_count"] == 0
    assert len(set(pages.deadlines)) == 1


def test_shared_45_second_deadline_prevents_another_page_and_keeps_rows(source, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(source.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def advance(url):
        if url == MUNICIPALITY:
            clock[0] = 144.9

    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(1)]}, advance=advance)
    result = pages.run()
    assert pages.calls == [ROBOTS, MUNICIPALITY]
    assert pages.deadlines == [145.0, 145.0]
    assert len(result["listings"]) == 1
    assert report(result)["stop_reason"] == "time_limit"


def test_robots_crawl_delay_larger_than_deadline_stops_before_search(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()]}, robots="User-agent: *\nCrawl-delay: 60")
    result = pages.run()
    assert pages.calls == [ROBOTS]
    assert report(result)["stop_reason"] == "time_limit"


def test_exact_geography_station_layout_and_inclusive_twenty_percent_area(source, monkeypatch):
    values = [
        row(0, area_sqm=20), row(1, area_sqm=30), row(2),
        row(3, station_name="西新宿"), row(4, station_name="新宿三丁目"),
        row(5, layout="1DK"), row(6, area_sqm=19.99), row(7, area_sqm=30.01),
        row(8, municipality="渋谷区"), row(9, city="osaka"),
    ]
    pages = Pages(source, monkeypatch, {MUNICIPALITY: values})
    result = pages.run(dict(SUBJECT, station_name="新宿駅"))
    assert {item["source_url"] for item in result["listings"]} == {row(i)["source_url"] for i in range(3)}
    assert report(result)["out_of_scope_count"] == 7
    assert report(result)["listing_count"] == report(result)["station_count"] == 3


@pytest.mark.parametrize("areas", [(25, 50, 25), (50, 25, 25)])
def test_conflicting_same_url_is_rejected_before_scope_filter_and_cannot_return(source, monkeypatch, areas):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row(area_sqm=areas[0])], PAGE2: [row(area_sqm=areas[1])], PAGE3: [row(area_sqm=areas[2])]})
    result = pages.run()
    assert result["listings"] == []
    assert report(result)["duplicate_count"] == 2
    assert report(result)["rejected_count"] >= 2
    assert report(result)["discovered_count"] == 3
    assert pages.calls == [ROBOTS, MUNICIPALITY, PAGE2, PAGE3]


def test_same_url_price_conflict_also_invalidates_previous_version(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(rent_yen=80001)]})
    result = pages.run()
    assert result["listings"] == [] and report(result)["rejected_count"] == 2


def test_matching_duplicate_ignores_observation_time_and_does_not_count_twice(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(fetched_at="2026-01-02T00:00:00Z")]})
    result = pages.run()
    assert len(result["listings"]) == 1
    assert report(result)["discovered_count"] == 2 and report(result)["duplicate_count"] == 1
    assert report(result)["rejected_count"] == 0
    assert result["listings"][0]["fetched_at"] == STAMP


def test_invalid_rows_are_isolated_and_missing_fields_stay_unknown(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row(), row(1, rent_yen=True), row(2, floor=9), row(3, layout="2LDK")]})
    result = pages.run()
    assert len(result["listings"]) == 1 and report(result)["rejected_count"] == 3
    item = result["listings"][0]
    assert item["mgmt_fee_yen"] is item["structure"] is item["built_year"] is item["elevator"] is None
    assert not any(key in item for key in ("rights", "unit_id", "status_verified_at", "status"))


def test_candidates_are_capped_at_sixty_and_spread_buildings_independent_of_price(source, monkeypatch):
    # The source presents all three rooms of building zero before building one.
    values = [row(building * 3 + room, building=building) for building in range(30) for room in range(3)]
    pages = Pages(source, monkeypatch, {MUNICIPALITY: values})
    first = pages.run()
    first_order = [item["source_url"] for item in first["listings"]]
    assert len(first_order) == 60 and report(first)["partial"]
    assert len({item["building_name"] for item in first["listings"][:30]}) == 30
    assert report(first)["distinct_building_count"] == 30

    for index, item in enumerate(values):
        item["rent_yen"] = 9900000 - index * 10000
        item["mgmt_fee_yen"] = index * 100
    changed = Pages(source, monkeypatch, {MUNICIPALITY: values}).run(dict(SUBJECT, rent_yen=9000000, mgmt_fee_yen=1000000))
    assert [item["source_url"] for item in changed["listings"]] == first_order
    assert first["price_filter_applied"] is changed["price_filter_applied"] is False
    assert pages.calls == [ROBOTS, MUNICIPALITY]


def test_observed_station_followed_by_blocked_original_next_link_stops_without_reordering(source, monkeypatch):
    pages = Pages(source, monkeypatch,
                  {MUNICIPALITY: [row()], FILTER: [row(1)], BLOCKED_NEXT: [row(2)]},
                  robots="User-agent: *\nDisallow: /list/?ue=\n",
                  resolved={MUNICIPALITY: STATION})
    result = pages.run()
    assert pages.calls == [ROBOTS, MUNICIPALITY, FILTER]
    assert report(result)["error_code"] == "robots_blocked"
    assert report(result)["search_page_count"] == 2
    assert report(result)["station_page_count"] == 1
    assert report(result)["search_scope"] == "station"
    assert len(result["listings"]) == 2


def test_failed_station_page_does_not_claim_station_search_succeeded(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], FILTER: [row(1)]},
                  statuses={FILTER: 503}, resolved={MUNICIPALITY: STATION})
    result = pages.run()
    assert report(result)["station_resolution"] == "observed_public_link"
    assert report(result)["search_scope"] == "municipality_fallback"
    assert report(result)["station_page_count"] == 0
    assert report(result)["search_page_count"] == 1
    assert len(result["listings"]) == 1


@pytest.mark.parametrize("empty,expected", [(True, "ok"), (False, "parse_changed")])
def test_explicit_empty_result_and_unrecognized_markup_have_different_status(source, monkeypatch, empty, expected):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: []}, cards=0, empty=empty)
    result = pages.run()
    assert report(result)["status"] == expected
    assert result["listings"] == []


def test_real_navigation_and_robots_preserve_observed_next_query_order(source, monkeypatch):
    discovery = import_module("backend.v2.chintai_discovery")
    for name in ("municipality_url", "build_search_url", "checked_url", "resolve_station", "next_page"):
        monkeypatch.setattr(source, name, getattr(discovery, name))
    bootstrap = f'<html><a href="{STATION}">新宿駅</a></html>'
    filtered = f'<html><a href="{BLOCKED_NEXT}">次へ</a></html>'
    snapshots = {bootstrap: [row(index) for index in range(5)], filtered: [row(5)]}
    monkeypatch.setattr(source, "parse_search", lambda html, **kwargs: (deepcopy(snapshots[html]), 1, False))
    responses = {
        ROBOTS: PublicResponse(200, b"User-agent: *\nDisallow: /list/?ue=\n", "text/plain"),
        MUNICIPALITY: PublicResponse(200, bootstrap.encode()),
        FILTER: PublicResponse(200, filtered.encode()),
    }
    calls = []

    def fetcher(url, *, deadline):
        calls.append(url)
        assert url in responses, "Robots-disallowed or rewritten next link must not be fetched"
        assert calls.count(url) == 1
        return responses[url]

    result = source.search(SUBJECT, fetcher=fetcher)
    assert calls == [ROBOTS, MUNICIPALITY, FILTER]
    assert report(result)["status"] == "blocked" and report(result)["error_code"] == "robots_blocked"
    assert report(result)["search_page_count"] == 2 and report(result)["station_page_count"] == 1
    assert len(result["listings"]) == 6
    assert report(result)["detail_attempt_count"] == 0


def test_parser_card_limit_is_reported_as_a_partial_sample(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()]}, cards=50)
    result = pages.run()
    assert report(result)["status"] == "ok" and report(result)["partial"]
    assert report(result)["listing_count"] == 1


def test_transport_exception_keeps_completed_rows_and_releases_search_slot(source, monkeypatch):
    pages = Pages(source, monkeypatch, {MUNICIPALITY: [row()], PAGE2: [row(1)]}, statuses={PAGE2: PublicFetchError("timeout")})
    result = pages.run()
    assert len(result["listings"]) == 1 and report(result)["stop_reason"] == "time_limit"
    assert source._SEARCH_SLOT.acquire(blocking=False)
    source._SEARCH_SLOT.release()


def test_default_source_options_and_routing_remain_suumo(source, monkeypatch):
    monkeypatch.delenv("HOUSE_EVALUATOR_SEARCH_SOURCE", raising=False)
    monkeypatch.setattr(public_search, "_SEARCH_SLOT", threading.BoundedSemaphore(1))
    alternate = Mock(side_effect=AssertionError("unexpected source switch"))
    monkeypatch.setattr(source, "search", alternate)
    fetcher = Mock(return_value=PublicResponse(403))
    result = public_search.search(SUBJECT, fetcher=fetcher)
    assert public_search.options()["sources"] == [{"id": "suumo", "name": "SUUMO 공개 검색", "kind": "public_page", "automatic": True}]
    assert public_search.options()["max_search_seconds"] == 45
    assert report(result)["source_id"] == "suumo"
    assert fetcher.call_count == 1 and fetcher.call_args.args[0] == "https://suumo.jp/robots.txt"
    alternate.assert_not_called()


def test_explicit_chintai_options_and_search_route_preserve_injected_fetcher(source, monkeypatch):
    monkeypatch.setenv("HOUSE_EVALUATOR_SEARCH_SOURCE", "chintai")
    expected = {"listings": [], "source_reports": [{"source_id": "chintai", "status": "blocked"}]}
    delegated = Mock(return_value=expected)
    monkeypatch.setattr(source, "search", delegated)
    fetcher = Mock(side_effect=AssertionError("unexpected direct fetch"))
    assert public_search.search(SUBJECT, fetcher=fetcher) is expected
    delegated.assert_called_once_with(SUBJECT, fetcher=fetcher)
    fetcher.assert_not_called()
    assert public_search.options()["sources"] == [{"id": "chintai", "name": "CHINTAI 공개 검색", "kind": "public_page", "automatic": True}]
    assert public_search.options()["max_search_seconds"] == 45


def test_unknown_source_setting_does_not_silently_choose_a_provider(monkeypatch):
    monkeypatch.setenv("HOUSE_EVALUATOR_SEARCH_SOURCE", "other")
    fetcher = Mock(side_effect=AssertionError("unexpected network"))
    with pytest.raises(ValueError):
        public_search.options()
    with pytest.raises(ValueError):
        public_search.search(SUBJECT, fetcher=fetcher)
    fetcher.assert_not_called()
