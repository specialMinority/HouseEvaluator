"""Personal mode is usable without supplier records, including fetch failure."""
import json
import threading
import time
from http.client import HTTPConnection

import pytest

from backend.src.server import create_server


SUBJECT = dict(city='tokyo', municipality='新宿区', station_name='高田馬場',
               layout='1K', area_sqm=25, rent_yen=90000, mgmt_fee_yen=5000,
               structure='rc', built_year=2010, walk_min=5, floor=3,
               building_type='mansion', building_floors=8, elevator=True)


@pytest.fixture
def server_factory(tmp_path):
    opened = []
    def start(**kwargs):
        server = create_server(port=0, db_path=tmp_path / f'{len(opened)}.sqlite3', **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opened.append((server, thread))
        return server
    yield start
    for server, thread in opened:
        server.shutdown()
        server.server_close()
        thread.join(3)


def request(server, method, path, payload=None, token=None):
    connection = HTTPConnection(*server.server_address, timeout=5)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body) if 'application/json' in response.getheader('Content-Type', '') else body
    finally:
        connection.close()


def complete(server, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status, body = request(server, 'GET', '/api/v2/personal/search/' + job_id)
        assert status == 200
        if body['status'] in ('complete', 'failed'):
            return body
        time.sleep(.01)
    raise AssertionError('Search did not finish')


def test_default_personal_and_optional_licensed_routes(server_factory):
    server = server_factory()
    assert b'personal.js' in request(server, 'GET', '/frontend/v2/')[1]
    assert b'./app.js' in request(server, 'GET', '/frontend/v2/licensed.html')[1]
    for path in ('personal.html', 'personal.js', 'personal.css'):
        assert request(server, 'GET', '/frontend/v2/' + path)[0] == 200
    status, options = request(server, 'GET', '/api/v2/personal/options')
    assert status == 200 and options['enabled']
    assert {c['id'] for c in options['cities']} == {'tokyo', 'osaka', 'fukuoka'}
    assert not request(server, 'GET', '/api/v2/capabilities')[1]['market_data_available']


def test_no_supplier_search_and_manual_comparison(server_factory):
    listings = [dict(SUBJECT, rent_yen=70000 + i * 10000,
                     address=f'新宿区テスト{i}', building_name=f'手入力{i}') for i in range(3)]
    seen = []
    def search(subject):
        seen.append(subject)
        return {'listings': listings, 'source_reports': [{'source_id': 'suumo', 'status': 'ok'}]}
    server = server_factory(search_fn=search)
    status, job = request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})
    assert status == 202
    found = complete(server, job['job_id'])
    assert found['status'] == 'complete'
    assert seen[0]['station_name'] == '高田馬場'
    status, result = request(server, 'POST', '/api/v2/personal/compare', {'subject': SUBJECT, 'listings': found['result']['listings']})
    assert status == 200 and result['status'] == 'reference'
    assert result['summary']['median_yen'] == 85000
    assert result['judgment'] is None


def test_source_exception_does_not_break_manual_comparison(server_factory):
    def fail(subject):
        raise RuntimeError('secret provider URL should never leave the process')
    server = server_factory(search_fn=fail)
    job = request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})[1]
    outcome = complete(server, job['job_id'])
    assert outcome['status'] == 'failed'
    assert 'secret' not in json.dumps(outcome)
    assert request(server, 'POST', '/api/v2/personal/compare', {'subject': SUBJECT, 'listings': []})[0] == 200
    assert request(server, 'GET', '/healthz')[0] == 200


def test_busy_cancellation_retains_actual_worker_slots(server_factory):
    release = threading.Event()
    server = server_factory(search_fn=lambda subject: release.wait(3) and {'listings': []})
    try:
        jobs = [request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})[1] for _ in range(2)]
        assert request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})[0] == 429
        assert request(server, 'POST', '/api/v2/personal/search/' + jobs[0]['job_id'] + '/cancel', {}) == (200, {'cancelled': True})
        assert request(server, 'GET', '/api/v2/personal/search/' + jobs[0]['job_id'])[1]['status'] == 'cancelled'
        assert request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})[0] == 429
    finally:
        release.set()


def test_personal_api_uses_auth_for_all_operations(server_factory):
    token = 'private-personal-test-code-000000000'
    server = server_factory(access_token=token)
    for method, path, payload in (
        ('GET', '/api/v2/personal/options', None),
        ('POST', '/api/v2/personal/search', {'subject': SUBJECT}),
        ('GET', '/api/v2/personal/search/missing', None),
        ('POST', '/api/v2/personal/search/missing/cancel', {}),
        ('POST', '/api/v2/personal/compare', {'subject': SUBJECT, 'listings': []}),
    ):
        assert request(server, method, path, payload)[0] == 401
    assert request(server, 'GET', '/api/v2/personal/options', token=token)[0] == 200


def test_personal_cannot_bypass_licensed_pilot_gate(server_factory, tmp_path):
    config = tmp_path / 'suppliers.json'
    config.write_text('{"schema_version":"2.0","suppliers":[]}', encoding='utf-8')
    token = 'private-personal-test-code-000000000'
    server = server_factory(pilot_mode=True, suppliers_path=config, access_token=token)
    assert request(server, 'GET', '/api/v2/personal/options', token=token)[1]['enabled'] is False
    assert request(server, 'POST', '/api/v2/personal/compare', {'subject': SUBJECT, 'listings': []}, token)[0] == 403
    assert request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT}, token)[0] == 403


@pytest.mark.parametrize('payload', [{}, {'subject': []}, {'subject': SUBJECT, 'url': 'https://example.invalid'}, {'subject': dict(SUBJECT, rent_yen=True)}])
def test_invalid_search_never_schedules_fetch(server_factory, payload):
    seen = []
    server = server_factory(search_fn=lambda subject: seen.append(subject))
    assert request(server, 'POST', '/api/v2/personal/search', payload)[0] == 400
    assert seen == []


def test_expired_or_unknown_job_and_missing_subject_fields(server_factory):
    server = server_factory()
    assert request(server, 'GET', '/api/v2/personal/search/not-found')[0] == 404
    status, result = request(server, 'POST', '/api/v2/personal/compare', {'subject': dict(SUBJECT, mgmt_fee_yen=None), 'listings': []})
    assert status == 200 and result['summary'] is None
    assert 'mgmt_fee_yen' in result['missing_subject_fields']


def test_full_completed_result_cache_accepts_new_search_and_evicted_job_returns_404(server_factory):
    server = server_factory(search_fn=lambda subject: {'listings': [], 'source_reports': [{'status': 'unavailable'}]})
    identities = []
    for _ in range(33):
        status, created = request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})
        assert status == 202
        identities.append(created['job_id'])
        assert complete(server, created['job_id'])['status'] == 'complete'
    assert len(server.search_jobs._jobs) == 32
    status, missing = request(server, 'GET', '/api/v2/personal/search/' + identities[0])
    assert status == 404 and missing['error'] == 'search_expired'
    assert request(server, 'GET', '/api/v2/personal/search/' + identities[-1])[1]['status'] == 'complete'


def test_public_source_can_be_disabled_while_manual_still_works(server_factory):
    server = server_factory(public_search_enabled=False)
    options = request(server, 'GET', '/api/v2/personal/options')[1]
    assert options['enabled'] is True and options['search_enabled'] is False
    assert request(server, 'POST', '/api/v2/personal/search', {'subject': SUBJECT})[0] == 503
    assert request(server, 'POST', '/api/v2/personal/compare', {'subject': SUBJECT, 'listings': []})[0] == 200
