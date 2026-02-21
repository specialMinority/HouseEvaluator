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


def test_parse_suumo_listing_url_extracts_orientation_area_structure_and_built_year(monkeypatch):
    from backend.src import suumo_url_parser as p

    # Mimic a SUUMO detail page table and the common "m<sup>2</sup>" rendering.
    html = """
    <html><body>
      <table class="property_view_table">
        <tr><th>所在地</th><td>東京都江戸川区南小岩５</td></tr>
        <tr><th>駅徒歩</th><td>ＪＲ総武線/小岩駅 歩12分</td></tr>
        <tr><th>間取り</th><td>1DK</td></tr>
        <tr><th>専有面積</th><td>25.21m<sup>2</sup></td></tr>
        <tr><th>築年数</th><td>新築</td></tr>
        <tr><th>向き</th><td>南西</td></tr>
        <tr><th>築年月</th><td>2025年12月</td></tr>
        <tr><th>構造</th><td>ＲＣ造</td></tr>
        <tr><th>賃料</th><td>8.7万円</td></tr>
        <tr><th>管理費</th><td>5,000円</td></tr>
      </table>
    </body></html>
    """.strip()

    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = p.parse_suumo_listing_url("https://suumo.jp/chintai/jnc_000105006617/")

    assert out["prefecture"] == "tokyo"
    assert out["municipality"] == "江戸川区"
    assert out["nearest_station_name"] == "小岩"
    assert out["station_walk_min"] == 12
    assert out["layout_type"] == "1DK"
    assert out["orientation"] == "SW"
    assert out["building_structure"] == "rc"
    assert out["building_built_year"] == 2025
    assert out["building_age_years"] == 0
    assert out["rent_yen"] == 87000
    assert out["mgmt_fee_yen"] == 5000
    assert out["management_fee_yen"] == 5000
    assert out["area_sqm"] == pytest.approx(25.21)


def test_parse_suumo_listing_url_extracts_structure_even_when_value_is_split(monkeypatch):
    from backend.src import suumo_url_parser as p

    html = """
    <html><body>
      <table class="property_view_table">
        <tr><th>所在地</th><td>東京都江戸川区南小岩５</td></tr>
        <tr><th>構造</th><td><span>鉄筋</span><span>コンクリート</span>造</td></tr>
        <tr><th>賃料</th><td>8.7万円</td></tr>
      </table>
    </body></html>
    """.strip()

    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = p.parse_suumo_listing_url("https://suumo.jp/chintai/jnc_000105006617/")

    assert out["building_structure"] == "rc"


def test_parse_suumo_listing_url_skips_taishin_kozo_false_positive(monkeypatch):
    """'耐震構造' (seismic-resistant) must not be mistaken for the building structure label."""
    from backend.src import suumo_url_parser as p

    html = """
    <html><body>
      <div>オートロック、耐震構造、2駅利用可、南西向き</div>
      <table class="property_view_table">
        <tr><th>間取り詳細</th><td>洋3.4 DK6.1</td></tr>
        <tr><th>構造</th><td>鉄筋コン</td></tr>
        <tr><th>賃料</th><td>10.5万円</td></tr>
      </table>
    </body></html>
    """.strip()

    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = p.parse_suumo_listing_url("https://suumo.jp/chintai/jnc_000105006617/")

    assert out["building_structure"] == "rc"
    assert "_structure_debug" not in out


def test_parse_suumo_listing_url_detects_waf_challenge(monkeypatch):
    """WAF/JS challenge pages must return _error, not silently fail."""
    from backend.src import suumo_url_parser as p

    html = """
    <html><head>
    <script src="https://token.awswaf.com/challenge.js"></script>
    </head><body>
    <div id="challenge-container">Please wait...</div>
    <script>AwsWafIntegration.forceRefresh()</script>
    </body></html>
    """.strip()

    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = p.parse_suumo_listing_url("https://suumo.jp/chintai/jnc_000105006617/")

    assert "_error" in out
    assert "WAF" in out["_error"]


def test_parse_suumo_listing_url_adds_debug_when_structure_missing(monkeypatch):
    """When building_structure cannot be extracted, _structure_debug must be present."""
    from backend.src import suumo_url_parser as p

    html = """
    <html><body>
      <table class="property_view_table">
        <tr><th>所在地</th><td>東京都新宿区</td></tr>
        <tr><th>賃料</th><td>8.0万円</td></tr>
      </table>
    </body></html>
    """.strip()

    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=12: _FakeResponse(html))

    out = p.parse_suumo_listing_url("https://suumo.jp/chintai/jnc_000105006617/")

    assert "building_structure" not in out
    assert "_structure_debug" in out
    assert "not found" in out["_structure_debug"]
