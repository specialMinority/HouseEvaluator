"""Browser regressions for URL import, using synthetic responses only.

All browser traffic is fulfilled from repository assets or rejected. Deferred
mock fetch deliberately ignores AbortSignal to prove stale response protection.
No running API, rental portal, credentials, or browser profile is used.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://houseevaluator.test"
URL_A = "https://www.chintai.net/detail/bk-SYNTHETIC000001/"
URL_B = "https://www.chintai.net/detail/bk-SYNTHETIC000002/"
OPTIONS = {
    "enabled": True, "search_enabled": True, "import_enabled": True,
    "sources": [{"id": "chintai", "name": "CHINTAI 공개 검색", "automatic": True}],
    "import_sources": [{"id": "chintai", "name": "CHINTAI"}],
    "cities": [
        {"id": "tokyo", "municipalities": [{"name": "新宿区"}]},
        {"id": "osaka", "municipalities": [{"name": "大阪市北区"}]},
    ],
}


def imported(url=URL_A, rent=81000):
    return {
        "status": "partial",
        "listing": {"city": "osaka", "municipality": "大阪市北区", "station_name": "大阪",
                    "layout": "1K", "area_sqm": 26, "rent_yen": rent, "mgmt_fee_yen": None,
                    "building_name": "合成サンプル", "source_url": url},
        "missing_fields": ["mgmt_fee_yen", "building_floors", "elevator", "structure"],
        "source": {"id": "chintai", "url": url, "fetched_at": "2026-01-01T00:00:00Z"},
        "warnings": ["原文の条件を確認するための合成テストです。"],
    }


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runtime:
        try:
            instance = runtime.chromium.launch(headless=True)
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium to run offline UI regressions")
            raise
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    contexts = []
    errors = []

    def create(*, viewport=None, options=None):
        context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900}, locale="ko-KR")
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def local_assets(route):
            parsed = urlsplit(route.request.url)
            filenames = {"personal.html", "personal.js", "personal.css", "styles.css"}
            name = parsed.path.rsplit("/", 1)[-1]
            if parsed.netloc == "houseevaluator.test" and name in filenames:
                content_type = "text/html" if name.endswith(".html") else "text/javascript" if name.endswith(".js") else "text/css"
                route.fulfill(body=(ROOT / "frontend" / "v2" / name).read_bytes(), content_type=content_type)
            else:
                errors.append("Unexpected browser request: " + route.request.url)
                route.abort()

        page.route("**/*", local_assets)
        script = """
          window.__pendingImports = [];
          window.__apiRequests = [];
          const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
          window.fetch = (path, options = {}) => {
            window.__apiRequests.push({ path, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
            if (path === '/api/v2/personal/options') return Promise.resolve(jsonResponse(OPTIONS));
            if (path === '/api/v2/personal/import') return new Promise((resolve) => window.__pendingImports.push({ resolve, signal: options.signal }));
            if (path === '/api/v2/personal/search') return Promise.resolve(jsonResponse({ job_id: 'synthetic-job', status: 'complete', result: {
              listings: [{ city: 'tokyo', municipality: '新宿区', station_name: '新宿', layout: '1K', area_sqm: 25, rent_yen: 80000, source_id: 'chintai', source_url: 'https://www.chintai.net/detail/bk-OLDTEST000001/' }],
              source_reports: [{ source_id: 'chintai', status: 'ok', listing_count: 1 }]
            }}));
            return Promise.reject(new Error('Unexpected API request: ' + path));
          };
          window.__resolveImport = (index, body, status = 200) => window.__pendingImports[index].resolve(jsonResponse(body, status));
        """.replace("OPTIONS", json.dumps(options or OPTIONS, ensure_ascii=False))
        page.add_init_script(script)
        page.goto(ORIGIN + "/frontend/v2/personal.html")
        page.wait_for_selector("#subject-city")
        page.wait_for_function("window.__apiRequests.some(request => request.path.endsWith('/options'))")
        return page

    yield create
    for context in contexts:
        context.close()
    assert errors == []


def begin(page, url=URL_A, count=1):
    page.locator("#personal-import-url").fill(url)
    page.locator("#personal-import-submit").click()
    page.wait_for_function("count => window.__pendingImports.length === count", arg=count)


def finish(page, *, index=0, body=None, status=200):
    page.evaluate("args => window.__resolveImport(args.index, args.body, args.status)", {"index": index, "body": body or imported(), "status": status})


def subject_values(page):
    return page.locator("#personal-subject-form").evaluate("form => Object.fromEntries(Array.from(form.elements).filter(input => input.name).map(input => [input.name, input.value]))")


def test_success_replaces_every_subject_field_clears_old_candidates_and_does_not_search(ui):
    page = ui()
    page.locator("#personal-example").click()
    page.locator("#subject-building_floors").fill("8")
    page.locator("#subject-elevator").select_option("true")
    page.locator("#personal-search").click()
    playwright.expect(page.locator(".personal-listing")).to_have_count(1)
    begin(page)
    playwright.expect(page.locator("#personal-search")).to_be_disabled()
    playwright.expect(page.locator("#personal-example")).to_be_disabled()
    playwright.expect(page.locator("#personal-add")).to_be_disabled()
    finish(page)
    playwright.expect(page.locator("#personal-import-review")).to_be_visible()
    values = subject_values(page)
    assert values["city"] == "osaka" and values["municipality"] == "大阪市北区"
    assert values["rent_yen"] == "81000" and values["source_url"] == URL_A
    for key in ("mgmt_fee_yen", "structure", "built_year", "walk_min", "floor", "building_floors", "elevator", "orientation", "bathroom_separate", "furnished", "contract_type"):
        assert values[key] == "", key
    assert page.locator("#subject-municipalities option").all_text_contents() == [""]
    assert page.locator("#subject-municipalities option").first.get_attribute("value") == "大阪市北区"
    playwright.expect(page.locator(".personal-listing")).to_have_count(0)
    playwright.expect(page.locator("#personal-source-reports")).to_be_empty()
    playwright.expect(page.locator("#personal-result")).to_be_hidden()
    playwright.expect(page.locator("#personal-import-review")).to_contain_text("CHINTAI")
    playwright.expect(page.locator("#personal-import-review summary")).to_contain_text("미상 항목")
    assert page.locator("#personal-import-review>a").get_attribute("href") == URL_A
    requests = page.evaluate("window.__apiRequests")
    assert len([item for item in requests if item["path"] == "/api/v2/personal/search"]) == 1
    assert [item for item in requests if item["path"].endswith("/import")][0]["body"] == {"url": URL_A}


def test_failure_keeps_all_existing_inputs_and_allows_manual_work(ui):
    page = ui()
    page.locator("#personal-example").click()
    before = subject_values(page)
    begin(page)
    finish(page, body={"error": "source_unavailable", "message": "원문 응답을 확인하지 못했습니다."}, status=503)
    playwright.expect(page.locator("#personal-import-error")).to_contain_text("원문 응답")
    assert subject_values(page) == before
    playwright.expect(page.locator("#personal-import-submit")).to_be_enabled()
    playwright.expect(page.locator("#personal-search")).to_be_enabled()
    playwright.expect(page.locator("#personal-import-review")).to_be_hidden()


@pytest.mark.parametrize("action", ["manual_input", "select_change", "url_change", "cancel"])
def test_cancelled_or_edited_import_cannot_overwrite_subject_even_if_fetch_returns(ui, action):
    page = ui()
    page.locator("#personal-example").click()
    begin(page)
    if action == "manual_input":
        page.locator("#subject-rent_yen").fill("77777")
    elif action == "select_change":
        page.locator("#subject-structure").select_option("wood")
    elif action == "url_change":
        page.locator("#personal-import-url").fill(URL_B)
    else:
        page.locator("#personal-import-cancel").click()
    expected = subject_values(page)
    assert page.evaluate("window.__pendingImports[0].signal.aborted") is True
    finish(page)
    page.wait_for_timeout(30)
    assert subject_values(page) == expected
    playwright.expect(page.locator("#personal-import-review")).to_be_hidden()
    playwright.expect(page.locator("#personal-import-submit")).to_be_enabled()


def test_late_old_response_cannot_replace_a_newer_success(ui):
    page = ui()
    begin(page)
    begin(page, URL_B, count=2)
    finish(page, index=1, body=imported(URL_B, 82000))
    playwright.expect(page.locator("#subject-rent_yen")).to_have_value("82000")
    finish(page, index=0, body=imported(URL_A, 81000))
    page.wait_for_timeout(30)
    playwright.expect(page.locator("#subject-rent_yen")).to_have_value("82000")
    playwright.expect(page.locator("#subject-source_url")).to_have_value(URL_B)
    assert page.locator("#personal-import-review>a").get_attribute("href") == URL_B


def test_logout_discards_pending_import_and_clears_its_url(ui):
    page = ui()
    # A synthetic login exercises the real auth-change and logout handlers.
    page.evaluate("() => { document.getElementById('personal-access-code').value = 'x'.repeat(32); document.getElementById('personal-access-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); }")
    playwright.expect(page.locator("#personal-logout")).to_be_visible()
    page.locator("#personal-example").click()
    begin(page)
    page.locator("#personal-logout").click()
    finish(page)
    page.wait_for_timeout(30)
    assert all(value == "" for value in subject_values(page).values())
    playwright.expect(page.locator("#personal-import-url")).to_have_value("")
    playwright.expect(page.locator("#personal-import-review")).to_be_hidden()
    playwright.expect(page.locator("#personal-access-panel")).to_be_visible()


def test_required_missing_fields_are_visible_and_mobile_controls_fit(ui, tmp_path):
    page = ui(viewport={"width": 390, "height": 844})
    body = imported()
    body["listing"]["station_name"] = None
    body["missing_fields"].append("station_name")
    begin(page)
    finish(page, body=body)
    playwright.expect(page.locator(".personal-import-required")).to_contain_text("필수 항목: 가장 가까운 역 원문")
    assert page.locator("label[for=personal-import-url]").inner_text() == "매물 상세 URL"
    assert page.locator("#personal-import-status").get_attribute("role") == "status"
    assert page.locator("#personal-import-error").get_attribute("role") == "alert"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in ("#personal-import-url", "#personal-import-submit"):
        box = page.locator(selector).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert box["height"] >= 44
    screenshot = tmp_path / "personal-import-mobile.png"
    page.locator("#personal-import-form").screenshot(path=str(screenshot))
    print(f"Mobile import screenshot: {screenshot}")


def test_disabled_import_is_explained_without_blocking_manual_fields(ui):
    page = ui(options=dict(OPTIONS, import_enabled=False))
    playwright.expect(page.locator("#personal-import-submit")).to_be_disabled()
    playwright.expect(page.locator("#personal-import-help")).to_contain_text("불러오기가 꺼져")
    page.locator("#subject-rent_yen").fill("80000")
    playwright.expect(page.locator("#subject-rent_yen")).to_have_value("80000")
    assert page.evaluate("window.__pendingImports.length") == 0
