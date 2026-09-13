"""Offline browser flows for asynchronous import and query-service availability.

All assets and API responses are synthetic/local. Deferred responses deliberately
ignore AbortSignal to test cancellation, auth changes and out-of-order delivery.
"""
import json

import pytest

from backend.tests.test_v2_personal_import_ui import (
    OPTIONS, URL_A, URL_B, browser, ui, imported, playwright, subject_values,
)


def remote(page, *, online=True):
    options = dict(OPTIONS, execution_mode='worker', worker={'online': online, 'last_seen_seconds': 1 if online else None},
                   search_available=online, import_available=online)
    page.evaluate("""options => {
      window.__remote = {options, polls: [], registrations: [], holdRegistration: false,
        registrationStatus: 202, registrationBody: null, holdOptions: false, pendingOptions: [], nextId: 1};
      const previousFetch = window.fetch;
      const reply = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
      const originalTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, ms, ...args) => originalTimeout(callback, ms === 2000 ? 10 : ms, ...args);
      const originalNow = Date.now;
      window.__remote.clockOffset = 0;
      Date.now = () => originalNow() + window.__remote.clockOffset;
      window.fetch = (path, settings = {}) => {
        const state = window.__remote;
        if (path === '/api/v2/personal/search') return previousFetch(path, settings);
        window.__apiRequests.push({path, method: settings.method || 'GET', body: settings.body ? JSON.parse(settings.body) : null});
        if (path === '/api/v2/personal/options') {
          if (state.holdOptions) return new Promise(resolve => state.pendingOptions.push({resolve}));
          return Promise.resolve(reply(state.options));
        }
        if (path === '/api/v2/personal/import') {
          const body = state.registrationBody || {job_id: 'remote-' + state.nextId++, status: 'pending'};
          if (state.holdRegistration) return new Promise(resolve => state.registrations.push({resolve, body}));
          return Promise.resolve(reply(body, state.registrationStatus));
        }
        if (/^\\/api\\/v2\\/personal\\/import\\/[^/]+\\/cancel$/.test(path)) return Promise.resolve(reply({cancelled: true}));
        if (/^\\/api\\/v2\\/personal\\/import\\/[^/]+$/.test(path)) return new Promise(resolve => state.polls.push({resolve, path, signal: settings.signal}));
        return Promise.reject(new Error('Unexpected synthetic API call: ' + path));
      };
      window.__remoteResolve = (kind, index, body, status = 200) => stateResolve(kind, index, body, status);
      function stateResolve(kind, index, body, status) { window.__remote[kind][index].resolve(reply(body, status)); }
    }""", options)
    return options


def make_remote(ui, *, online=True, viewport=None):
    options = dict(OPTIONS, execution_mode='worker', worker={'online': online, 'last_seen_seconds': 0 if online else None},
                   search_available=online, import_available=online)
    page = ui(options=options, viewport=viewport)
    remote(page, online=online)
    return page


def begin(page, url=URL_A, polls=1):
    page.locator('#personal-import-url').fill(url)
    page.locator('#personal-import-submit').click()
    page.wait_for_function('count => window.__remote.polls.length === count', arg=polls)


def resolve(page, kind, index, body, status=200):
    page.evaluate('args => window.__remoteResolve(...args)', [kind, index, body, status])


def complete(page, index=0, job_id='remote-1', url=URL_A, rent=81000):
    resolve(page, 'polls', index, {'job_id': job_id, 'status': 'complete', 'result': imported(url, rent)})


def requests(page, suffix):
    return page.evaluate('suffix => window.__apiRequests.filter(item => item.path.endsWith(suffix))', suffix)


def test_remote_import_pending_running_complete_updates_once_without_extra_search(ui):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    before = subject_values(page)
    begin(page)
    assert subject_values(page) == before
    playwright.expect(page.locator('#personal-import-submit')).to_be_disabled()
    playwright.expect(page.locator('#personal-reconnect')).to_be_disabled()
    resolve(page, 'polls', 0, {'job_id': 'remote-1', 'status': 'running'})
    page.wait_for_function('window.__remote.polls.length === 2')
    complete(page, index=1)
    playwright.expect(page.locator('#subject-rent_yen')).to_have_value('81000')
    playwright.expect(page.locator('#personal-import-review')).to_be_visible()
    assert page.locator('#personal-import-review>a').get_attribute('href') == URL_A
    assert len(requests(page, '/personal/import')) == 1
    assert not requests(page, '/personal/search')


@pytest.mark.parametrize('action', ['cancel', 'url_change', 'manual_input', 'select_change'])
def test_remote_cancel_and_edits_cancel_known_job_and_ignore_late_completion(ui, action):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    begin(page)
    if action == 'cancel':
        page.locator('#personal-import-cancel').click()
    elif action == 'url_change':
        page.locator('#personal-import-url').fill(URL_B)
    elif action == 'manual_input':
        page.locator('#subject-rent_yen').fill('77777')
    else:
        page.locator('#subject-structure').select_option('wood')
    before = subject_values(page)
    complete(page)
    page.wait_for_timeout(30)
    assert subject_values(page) == before
    assert len(requests(page, '/import/remote-1/cancel')) == 1
    playwright.expect(page.locator('#personal-import-review')).to_be_hidden()
    playwright.expect(page.locator('#personal-import-submit')).to_be_enabled()


def test_cancel_before_registration_returns_cancels_late_job_without_polling_it(ui):
    page = make_remote(ui)
    page.evaluate('window.__remote.holdRegistration = true')
    page.locator('#personal-import-url').fill(URL_A)
    page.locator('#personal-import-submit').click()
    page.wait_for_function('window.__remote.registrations.length === 1')
    page.locator('#personal-import-cancel').click()
    resolve(page, 'registrations', 0, {'job_id': 'remote-1', 'status': 'pending'}, 202)
    page.wait_for_function("window.__apiRequests.some(item => item.path.endsWith('/remote-1/cancel'))")
    assert page.evaluate('window.__remote.polls.length') == 0
    playwright.expect(page.locator('#personal-import-review')).to_be_hidden()


def test_older_remote_job_cannot_overwrite_newer_success(ui):
    page = make_remote(ui)
    begin(page)
    begin(page, URL_B, polls=2)
    complete(page, index=1, job_id='remote-2', url=URL_B, rent=82000)
    playwright.expect(page.locator('#subject-rent_yen')).to_have_value('82000')
    complete(page)
    page.wait_for_timeout(30)
    playwright.expect(page.locator('#subject-rent_yen')).to_have_value('82000')
    playwright.expect(page.locator('#subject-source_url')).to_have_value(URL_B)
    assert len(requests(page, '/import/remote-1/cancel')) == 1


@pytest.mark.parametrize('body', [None, {'job_id': 'different-job', 'status': 'complete', 'result': imported()}])
def test_invalid_poll_response_preserves_inputs_and_cancels_only_original_job(ui, body):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    before = subject_values(page)
    begin(page)
    resolve(page, 'polls', 0, body)
    playwright.expect(page.locator('#personal-import-error')).to_contain_text('작업 응답이 일치하지 않습니다')
    assert subject_values(page) == before
    assert len(requests(page, '/import/remote-1/cancel')) == 1
    assert not requests(page, '/import/different-job/cancel')


@pytest.mark.parametrize('status,code', [('failed', 'source_blocked'), ('failed', 'worker_timeout'), ('cancelled', None)])
def test_terminal_failure_preserves_inputs_and_never_retries_or_changes_provider(ui, status, code):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    before = subject_values(page)
    begin(page)
    body = {'job_id': 'remote-1', 'status': status}
    if code:
        body['error'] = {'code': code, 'message': '합성 검사: 조회를 중단했습니다.'}
    resolve(page, 'polls', 0, body)
    playwright.expect(page.locator('#personal-import-error')).to_be_visible()
    assert subject_values(page) == before
    assert len(requests(page, '/personal/import')) == 1
    assert not requests(page, '/personal/search')
    playwright.expect(page.locator('#personal-import-submit')).to_be_enabled()


def test_local_wait_limit_cancels_job_without_replacing_inputs(ui):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    before = subject_values(page)
    begin(page)
    page.evaluate('window.__remote.clockOffset = 100001')
    resolve(page, 'polls', 0, {'job_id': 'remote-1', 'status': 'running'})
    playwright.expect(page.locator('#personal-import-error')).to_contain_text('대기를 마쳤습니다')
    assert len(requests(page, '/import/remote-1/cancel')) == 1
    assert subject_values(page) == before


@pytest.mark.parametrize('late_status', [200, 401])
def test_logout_clears_values_and_late_poll_cannot_change_auth_panel(ui, late_status):
    page = make_remote(ui)
    page.evaluate("() => { document.getElementById('personal-access-code').value = 'x'.repeat(32); document.getElementById('personal-access-form').dispatchEvent(new Event('submit', {bubbles:true,cancelable:true})); }")
    playwright.expect(page.locator('#personal-logout')).to_be_visible()
    begin(page)
    page.locator('#personal-logout').click()
    resolve(page, 'polls', 0, {'job_id': 'remote-1', 'status': 'complete', 'result': imported()}, late_status)
    page.wait_for_timeout(30)
    assert all(value == '' for value in subject_values(page).values())
    playwright.expect(page.locator('#personal-access-panel')).to_be_visible()
    playwright.expect(page.locator('#personal-import-review')).to_be_hidden()


def test_offline_recheck_only_refreshes_options_and_preserves_subject_and_candidates(ui):
    page = make_remote(ui)
    page.locator('#personal-example').click()
    page.locator('#personal-search').click()
    playwright.expect(page.locator('.personal-listing')).to_have_count(1)
    before = subject_values(page)
    page.evaluate('window.__remote.options.worker.online = false; window.__remote.options.search_available = false; window.__remote.options.import_available = false')
    page.locator('#personal-reconnect').click()
    playwright.expect(page.locator('#personal-service-status')).to_contain_text('연결 대기')
    playwright.expect(page.locator('#personal-search')).to_be_disabled()
    playwright.expect(page.locator('#personal-import-submit')).to_be_disabled()
    playwright.expect(page.locator('#personal-compare-top')).to_be_enabled()
    page.evaluate('window.__remote.options.worker.online = true; window.__remote.options.search_available = true; window.__remote.options.import_available = true')
    page.locator('#personal-reconnect').click()
    playwright.expect(page.locator('#personal-search')).to_be_enabled()
    playwright.expect(page.locator('#personal-import-submit')).to_be_enabled()
    assert subject_values(page) == before
    playwright.expect(page.locator('.personal-listing')).to_have_count(1)
    assert len(requests(page, '/personal/search')) == 1
    assert not requests(page, '/personal/import')


def test_worker_offline_registration_disables_queries_until_manual_recheck(ui):
    page = make_remote(ui)
    page.evaluate("window.__remote.registrationStatus = 503; window.__remote.registrationBody = {error:'worker_offline',message:'조회 서비스 연결 대기'}")
    page.locator('#personal-import-url').fill(URL_A)
    page.locator('#personal-import-submit').click()
    playwright.expect(page.locator('#personal-service-status')).to_contain_text('연결이 끊겼습니다')
    playwright.expect(page.locator('#personal-search')).to_be_disabled()
    page.locator('#subject-rent_yen').fill('88888')
    page.locator('#personal-reconnect').click()
    playwright.expect(page.locator('#personal-import-submit')).to_be_enabled()
    playwright.expect(page.locator('#subject-rent_yen')).to_have_value('88888')
    assert len(requests(page, '/personal/import')) == 1


def test_mobile_offline_controls_fit_and_recheck_is_keyboard_accessible(ui):
    page = make_remote(ui, online=False, viewport={'width': 390, 'height': 844})
    playwright.expect(page.locator('#personal-service-status')).to_contain_text('연결 대기')
    playwright.expect(page.locator('#personal-search')).to_be_disabled()
    playwright.expect(page.locator('#personal-import-submit')).to_be_disabled()
    page.locator('#subject-rent_yen').fill('85000')
    button = page.locator('#personal-reconnect')
    button.focus()
    page.keyboard.press('Enter')
    assert len(requests(page, '/personal/options')) == 2
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    box = button.bounding_box()
    assert box['height'] >= 44 and box['x'] >= 0 and box['x'] + box['width'] <= 390
