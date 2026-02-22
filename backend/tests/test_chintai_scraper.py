import urllib.parse

import pytest


class _FakeHeaders:
    def get_content_charset(self):  # noqa: ANN201
        return "utf-8"


class _FakeResponse:
    def __init__(self, html: str) -> None:
        self._data = html.encode("utf-8")
        self.headers = _FakeHeaders()

    def read(self) -> bytes:  # noqa: D401
        return self._data

    def __enter__(self):  # noqa: ANN201
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN201
        return False


def test_build_chintai_list_url_includes_bucketed_filters():
    from backend.src import chintai_scraper as c

    idx = {
        "by_pref_muni_layout": {
            "tokyo|江戸川区|1DK": {
                "sources": [
                    {"source_name": "CHINTAI", "source_url": "https://www.chintai.net/tokyo/area/13123/list/page3/?m=2"},
                ]
            }
        }
    }

    url = c.build_chintai_list_url(
        prefecture="tokyo",
        municipality="江戸川区",
        layout_type="1DK",
        benchmark_index=idx,
        page=2,
        area_min_sqm=25,
        area_max_sqm=30,
        walk_max_min=15,
        age_max_years=20,
        building_structure="rc",
    )
    assert url is not None
    parsed = urllib.parse.urlparse(url)
    assert parsed.path.endswith("/tokyo/area/13123/list/page2/")
    q = urllib.parse.parse_qs(parsed.query)
    assert q.get("m") == ["2"]
    assert q.get("sf") == ["25"]
    assert q.get("st") == ["30"]
    assert q.get("j") == ["6"]  # <=15 min
    assert q.get("h") == ["6"]  # <=20 years
    assert q.get("kz") == ["1"]  # RC/SRC group
    assert q.get("rt") == ["50"]
    assert q.get("k") == ["1"]


def test_fetch_chintai_listings_parses_build_table_rows(monkeypatch):
    from backend.src import chintai_scraper as c

    html = """
    <html><body>
      <section class="cassette_item build">
        <div class="bukken_information">
          <table class="l-table">
            <tr><th>築年</th><td>2004年03月（築21年）</td></tr>
            <tr><th>構造</th><td>ＲＣ造</td></tr>
          </table>
        </div>
        <div class="cassette_detail">
          <table>
            <tbody class="js-detailLinkUrl" data-detailurl="/detail/bk-TEST/">
              <tr class="detail-inner">
                <td class="price"><span class="num">8.7</span>万円<br>5,000円</td>
                <td class="floar"><ul><li>3階</li></ul></td>
                <td class="layout">1DK<br>25.21m&#178;</td>
              </tr>
              <input type="hidden" value="87000" class="chinRyo">
              <input type="hidden" value="1DK" class="madori">
              <input type="hidden" value="25.21" class="senMenseki">
              <input type="hidden" value="小岩駅" class="ekiName">
              <input type="hidden" value="12" class="ekiToho">
              <input type="hidden" value="ダミー" class="bkName">
            </tbody>
          </table>
        </div>
      </section>
    </body></html>
    """.strip()

    monkeypatch.setattr(c.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = c.fetch_chintai_listings("https://example.com/", timeout=12)
    assert len(out) == 1
    lst = out[0]
    assert lst.rent_yen == 87000
    assert lst.admin_fee_yen == 5000
    assert lst.monthly_total_yen == 92000
    assert lst.layout == "1DK"
    assert lst.area_sqm == pytest.approx(25.21)
    assert lst.walk_min == 12
    assert lst.building_age_years == 21
    assert lst.building_structure == "rc"
    assert lst.station_names == ["小岩"]
    assert lst.detail_url == "https://www.chintai.net/detail/bk-TEST/"


def test_fetch_chintai_detail_fields_parses_orientation_and_bath_sep(monkeypatch):
    from backend.src import chintai_scraper as c

    html = """
    <html><body>
      <table>
        <tr><th>方位</th><td>南西</td></tr>
        <tr><th>構造</th><td>鉄骨鉄筋コンクリート造</td></tr>
      </table>
      <div>設備：バス・トイレ別、オートロック</div>
    </body></html>
    """.strip()

    monkeypatch.setattr(c.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    det = c.fetch_chintai_detail_fields("https://www.chintai.net/detail/bk-TEST/", timeout=12)
    assert det.orientation == "SW"
    assert det.bath_sep is True
    assert det.structure == "src"


def test_fetch_chintai_listings_parses_multiple_stations_and_prefers_target_walk(monkeypatch):
    from backend.src import chintai_scraper as c

    html = """
    <html><body>
      <section class="cassette_item build">
        <div class="bukken_information">
          <div class="traffic">
            <div>大阪メトロ御堂筋線/なんば駅&nbsp;徒歩10分</div>
            <div>南海本線/難波駅&nbsp;徒歩13分</div>
          </div>
        </div>
        <div class="cassette_detail">
          <table>
            <tbody class="js-detailLinkUrl" data-detailurl="/detail/bk-TEST/">
              <tr class="detail-inner">
                <td class="price"><span class="num">8.7</span>万円<br>5,000円</td>
                <td class="layout">1DK<br>25.21m&#178;</td>
              </tr>
              <input type="hidden" value="87000" class="chinRyo">
              <input type="hidden" value="1DK" class="madori">
              <input type="hidden" value="25.21" class="senMenseki">
              <input type="hidden" value="日本橋駅" class="ekiName">
              <input type="hidden" value="3" class="ekiToho">
            </tbody>
          </table>
        </div>
      </section>
    </body></html>
    """.strip()

    monkeypatch.setattr(c.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = c.fetch_chintai_listings("https://example.com/", timeout=12, target_station="なんば")
    assert len(out) == 1
    lst = out[0]
    assert "なんば" in lst.station_names
    assert "難波" in lst.station_names
    assert lst.walk_min == 10  # prefers target station walk over primary ekiToho=3


def test_search_comparable_listings_enriches_missing_orientation_from_detail(monkeypatch):
    from backend.src import chintai_scraper as c

    idx = {
        "by_pref_muni_layout": {
            "tokyo|江戸川区|1DK": {
                "sources": [
                    {"source_name": "CHINTAI", "source_url": "https://www.chintai.net/tokyo/area/13123/list/?m=2"},
                ]
            }
        }
    }

    list_html = """
    <html><body>
      <section class="cassette_item build">
        <div class="bukken_information">
          <table class="l-table">
            <tr><th>築年</th><td>2004年3月</td></tr>
            <tr><th>構造</th><td>RC</td></tr>
          </table>
        </div>
        <div class="cassette_detail">
          <table>
            <tbody class="js-detailLinkUrl" data-detailurl="/detail/bk-TEST/">
              <tr class="detail-inner">
                <td class="price"><span class="num">8.7</span>万円<br>5,000円</td>
                <td class="floar"><ul><li>3階</li></ul></td>
                <td class="layout">1DK<br>25.21m&#178;</td>
              </tr>
              <input type="hidden" value="87000" class="chinRyo">
              <input type="hidden" value="1DK" class="madori">
              <input type="hidden" value="25.21" class="senMenseki">
              <input type="hidden" value="日本橋駅" class="ekiName">
              <input type="hidden" value="3" class="ekiToho">
            </tbody>
          </table>
        </div>
      </section>
    </body></html>
    """.strip()

    detail_html = """
    <html><body>
      <table>
        <tr><th>方位</th><td>南西</td></tr>
        <tr><th>構造</th><td>RC</td></tr>
      </table>
    </body></html>
    """.strip()

    def fake_urlopen(req, timeout=12):  # noqa: ANN001, D401
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
        if "/detail/" in str(url):
            return _FakeResponse(detail_html)
        return _FakeResponse(list_html)

    monkeypatch.setattr(c.urllib.request, "urlopen", fake_urlopen)

    res = c.search_comparable_listings(
        prefecture="tokyo",
        municipality="江戸川区",
        layout_type="1DK",
        benchmark_index=idx,
        orientation="SW",
        min_listings=1,
        max_relaxation_steps=0,
        max_pages=1,
        request_delay_s=0,
        fetch_timeout=12,
    )
    assert res.benchmark_n_sources == 1
    assert res.benchmark_confidence in ("mid", "high")
    assert res.matched_level == "chintai_live"


def test_chintai_bucket_area_range_expands_by_step():
    from backend.src import chintai_scraper as c

    assert c._bucket_area_range(25.2, 0) == (25, 30)
    assert c._bucket_area_range(25.2, 2) == (15, 40)


def test_chintai_bucket_walk_max_moves_to_next_bucket_by_step():
    from backend.src import chintai_scraper as c

    assert c._bucket_walk_max(7, 0) == 7
    assert c._bucket_walk_max(7, 1) == 10


def test_chintai_bucket_age_max_moves_to_next_bucket_by_step():
    from backend.src import chintai_scraper as c

    assert c._bucket_age_max(8, 0) == 10
    assert c._bucket_age_max(8, 2) == 20


def test_build_chintai_list_url_normalizes_municipality_address():
    from backend.src import chintai_scraper as c

    tokyo = "\u6771\u4eac\u90fd"
    edogawa = "\u6c5f\u6238\u5ddd\u533a"
    minami_koiwa_5 = "\u5357\u5c0f\u5ca9\uff15"
    full_addr = tokyo + edogawa + minami_koiwa_5

    idx = {
        "by_pref_muni_layout": {
            f"tokyo|{edogawa}|1DK": {
                "sources": [
                    {"source_name": "CHINTAI", "source_url": "https://www.chintai.net/tokyo/area/13123/list/page3/?m=2"},
                ]
            }
        }
    }

    url = c.build_chintai_list_url(
        prefecture="tokyo",
        municipality=full_addr,
        layout_type="1DK",
        benchmark_index=idx,
        page=1,
    )
    assert url is not None
    parsed = urllib.parse.urlparse(url)
    assert parsed.path.endswith("/tokyo/area/13123/list/")


def test_build_chintai_list_url_falls_back_to_suumo_sc_code():
    from backend.src import chintai_scraper as c

    edogawa = "\u6c5f\u6238\u5ddd\u533a"
    idx = {
        "by_pref_muni_layout": {
            f"tokyo|{edogawa}|1DK": {
                "sources": [
                    {
                        "source_name": "SUUMO",
                        "source_url": "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13&md=03&sc=13123",
                    }
                ]
            }
        }
    }

    url = c.build_chintai_list_url(
        prefecture="tokyo",
        municipality=edogawa,
        layout_type="1DK",
        benchmark_index=idx,
        page=2,
    )
    assert url is not None
    parsed = urllib.parse.urlparse(url)
    assert parsed.path.endswith("/tokyo/area/13123/list/page2/")
    q = urllib.parse.parse_qs(parsed.query)
    assert q.get("m") == ["2"]


def test_build_chintai_list_url_converts_rent_url_to_list_url():
    from backend.src import chintai_scraper as c

    edogawa = "\u6c5f\u6238\u5ddd\u533a"
    idx = {
        "by_pref_muni_layout": {
            f"tokyo|{edogawa}|1K": {
                "sources": [
                    {"source_name": "CHINTAI", "source_url": "https://www.chintai.net/tokyo/area/13123/rent/1k/"},
                ]
            }
        }
    }

    url = c.build_chintai_list_url(
        prefecture="tokyo",
        municipality=edogawa,
        layout_type="1K",
        benchmark_index=idx,
        page=1,
    )
    assert url is not None
    parsed = urllib.parse.urlparse(url)
    assert parsed.path.endswith("/tokyo/area/13123/list/")
    q = urllib.parse.parse_qs(parsed.query)
    assert q.get("m") == ["1"]
