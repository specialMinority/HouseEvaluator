import urllib.parse
import uuid
from pathlib import Path


def test_parse_chintai_pref_area_index_extracts_codes():
    from backend.src.url_resolver import parse_chintai_pref_area_index

    html = """
    <html><body>
      <a href="/tokyo/area/13123/list/">江戸川区</a>
      <a href="https://www.chintai.net/tokyo/area/13101/list/">千代田区</a>
      <a href="/tokyo/area/13123/rent/1k/">江戸川区(家賃相場)</a>
    </body></html>
    """.strip()

    mapping = parse_chintai_pref_area_index(html)
    assert mapping.get("江戸川区") == "13123"
    assert mapping.get("千代田区") == "13101"


def test_resolve_chintai_area_code_uses_cache(monkeypatch):
    import backend.src.url_resolver as ur

    ur._CACHE = None
    # Avoid pytest tmp_path on environments where OS temp is restricted.
    cache_path = Path(".cache") / f"url_resolver_test_cache_{uuid.uuid4().hex}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("URL_RESOLVER_CACHE_PATH", str(cache_path))

    html = "<a href='/osaka/area/27128/list/'>大阪市中央区</a>"
    called = {"n": 0}

    def fake_fetch(url: str, *, timeout: int = 12) -> str:  # noqa: ARG001
        called["n"] += 1
        return html

    monkeypatch.setattr(ur, "_fetch_html", fake_fetch)

    try:
        code1 = ur.resolve_chintai_area_code("osaka", ["大阪市中央区"])
        assert code1 == "27128"
        assert called["n"] == 1

        # Second call should hit in-memory cache and not re-fetch.
        code2 = ur.resolve_chintai_area_code("osaka", ["中央区", "大阪市中央区"])
        assert code2 == "27128"
        assert called["n"] == 1
    finally:
        cache_path.unlink(missing_ok=True)


def test_build_chintai_list_url_falls_back_to_dynamic_resolver(monkeypatch):
    from backend.src import chintai_scraper as c

    monkeypatch.setattr(c, "resolve_chintai_area_code", lambda pref, cands: "13123")  # noqa: ARG005

    url = c.build_chintai_list_url(
        prefecture="tokyo",
        municipality="東京都江戸川区南小岩５",
        layout_type="1DK",
        benchmark_index=None,
        page=2,
    )
    assert url is not None
    parsed = urllib.parse.urlparse(url)
    assert parsed.path.endswith("/tokyo/area/13123/list/page2/")
