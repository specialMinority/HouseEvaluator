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


def test_build_homes_theme_list_url_uses_area_slug_from_benchmark_index():
    from backend.src import homes_scraper as h

    idx = {
        "by_pref_muni_layout": {
            "tokyo|江戸川区|1DK": {
                "sources": [
                    {"source_url": "https://www.homes.co.jp/chintai/tokyo/edogawa-city/price/"},
                ]
            }
        }
    }

    url = h.build_homes_theme_list_url(
        prefecture="tokyo",
        municipality="江戸川区",
        layout_type="1DK",
        benchmark_index=idx,
        page=2,
    )
    assert url == "https://www.homes.co.jp/chintai/theme/14127/tokyo/edogawa-city/list/?page=2"


def test_fetch_homes_listings_parses_core_fields(monkeypatch):
    from backend.src import homes_scraper as h

    html = """
    <html><body>
      <div>所在地 東京都江戸川区南小岩５</div>
      <div>交通 ＪＲ総武線/小岩駅 徒歩12分</div>
      <div>築年数/階数 新築 / 3階建</div>
      <ul>
        <li>3階</li>
        <li>8.7 万円/5,000円</li>
        <li>1ヶ月/1ヶ月/-/-1DK</li>
        <li>25.21m²</li>
        <li>バス・トイレ別</li>
        <li>主要採光面 南西</li>
        <li>ＲＣ造</li>
      </ul>
    </body></html>
    """.strip()

    monkeypatch.setattr(h.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = h.fetch_homes_listings("https://example.com/", timeout=12, retries=0)
    assert len(out) == 1
    lst = out[0]
    assert lst.rent_yen == 87000
    assert lst.admin_fee_yen == 5000
    assert lst.monthly_total_yen == 92000
    assert lst.layout == "1DK"
    assert lst.area_sqm == pytest.approx(25.21)
    assert lst.walk_min == 12
    assert lst.building_age_years == 0
    assert lst.orientation == "SW"
    assert lst.building_structure == "rc"
    assert lst.bathroom_toilet_separate is True
    assert lst.station_names == ["小岩"]

