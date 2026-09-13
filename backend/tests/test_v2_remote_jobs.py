"""Offline queue and HTTP role-isolation tests, with no source requests."""
from copy import deepcopy
import threading

import pytest

from backend.tests.test_v2_personal import subject as base_subject
from backend.tests.test_v2_listing_import import parsed as imported_row, URL
from backend.tests.test_v2_personal_http import request, server_factory
from backend.v2.remote_jobs import WorkerError, WorkerJobs

USER_TOKEN, WORKER_TOKEN = 'u' * 40, 'w' * 40
CAPS = {'worker_id': 'test_worker_identity_123', 'protocol': 1, 'search_source': 'suumo',
        'import_sources': ['chintai', 'yahoo_realestate']}


def subject(**kwargs):
    return base_subject(**dict({'fetched_at': '2026-01-01T00:00:00Z'}, **kwargs))


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    now = [0.0]
    q = WorkerJobs('suumo', CAPS['import_sources'], clock=lambda: now[0])
    q.claim(CAPS)
    return q, now


def search_result():
    row = subject(source_id='suumo', source_url='https://suumo.jp/chintai/jnc_12345678901/', fetched_at='2026-01-01T00:00:00Z')
    return {'listings': [row], 'source_reports': [{'source_id': 'suumo', 'status': 'ok'}]}


def import_result():
    row = imported_row()
    return {'status': 'partial', 'listing': row, 'source': {'id': 'chintai', 'url': URL, 'fetched_at': row['fetched_at']}, 'missing_fields': row['missing_fields'], 'warnings': []}


def claimed(q, kind='search'):
    created = q.start(kind, {'subject': subject()} if kind == 'search' else {'url': URL})
    lease = q.claim(CAPS)['job']
    assert lease['job_id'] == created['job_id']
    return created['job_id'], lease


def finished(lease, result=None, *, error=None):
    return {'worker_id': CAPS['worker_id'], 'job_id': lease['job_id'], 'lease_token': lease['lease_token'],
            'outcome': {'status': 'failed', 'error': {'code': error, 'message': 'untrusted-secret'}} if error else {'status': 'complete', 'result': result if result is not None else search_result()}}


def test_offline_blocks_new_jobs_without_source_fetch():
    q = WorkerJobs('suumo', CAPS['import_sources'])
    assert q.state()['online'] is False
    with pytest.raises(WorkerError, match='연결'):
        q.start('search', {'subject': subject()})


@pytest.mark.parametrize('kind', ['search', 'import'])
def test_job_claim_complete_and_idempotent_upload(queue, kind):
    q, _ = queue
    identity, lease = claimed(q, kind)
    result = import_result() if kind == 'import' else search_result()
    message = finished(lease, result)
    assert q.finish(message) == {'accepted': True, 'duplicate': False}
    assert q.finish(message) == {'accepted': True, 'duplicate': True}
    assert q.get(kind, identity)['result'] == result
    assert q.get('import' if kind == 'search' else 'search', identity) is None
    result['test_mutation'] = True
    assert 'test_mutation' not in q.get(kind, identity)['result']


def test_only_one_worker_can_claim_and_capacity_is_bounded(queue):
    q, _ = queue
    for _ in range(5):
        q.start('search', {'subject': subject()})
    with pytest.raises(WorkerError) as exc:
        q.start('search', {'subject': subject()})
    assert exc.value.code == 'worker_busy'
    claims = []
    threads = [threading.Thread(target=lambda: claims.append(q.claim(CAPS)['job'])) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([job for job in claims if job]) == 1


def test_expired_lease_never_redelivers_and_rejects_late_result(queue):
    q, now = queue
    identity, lease = claimed(q)
    now[0] = 76
    assert q.get('search', identity)['error']['code'] == 'worker_timeout'
    assert q.claim(CAPS)['job'] is None
    with pytest.raises(WorkerError) as exc:
        q.finish(finished(lease))
    assert exc.value.code == 'job_stale'


def test_cancel_rejects_result_and_does_not_release_active_source_lease_early(queue):
    q, _ = queue
    identity, lease = claimed(q)
    assert q.cancel('search', identity)
    q.start('search', {'subject': subject()})
    assert q.claim(CAPS)['job'] is None
    with pytest.raises(WorkerError):
        q.finish(finished(lease))
    assert q.claim(CAPS)['job'] is not None
    assert q.get('search', identity)['status'] == 'cancelled'


def test_pending_deadline_and_retention_are_bounded(queue):
    q, now = queue
    identity = q.start('import', {'url': URL})['job_id']
    now[0] = 451
    assert q.get('import', identity)['status'] == 'failed'
    now[0] = 900
    assert q.get('import', identity) is None


def test_deadline_failed_job_still_holds_its_active_source_lease(queue):
    q, now = queue
    identity = q.start('search', {'subject': subject()})['job_id']
    now[0] = 430
    lease = q.claim(CAPS)['job']
    now[0] = 451
    assert q.get('search', identity)['status'] == 'failed'
    q.start('search', {'subject': subject()})
    assert q.claim(CAPS)['job'] is None
    with pytest.raises(WorkerError):
        q.finish(finished(lease))
    assert q.claim(CAPS)['job'] is not None


def test_cancelled_lease_survives_terminal_record_eviction(queue):
    q, _ = queue
    identity, _ = claimed(q)
    q.cancel('search', identity)
    for _ in range(40):
        created = q.start('search', {'subject': subject()})['job_id']
        q.cancel('search', created)
    assert identity in q.jobs and len(q.jobs) == 32
    assert q.claim(CAPS)['job'] is None


@pytest.mark.parametrize('patch', [{'protocol': True}, {'protocol': 2}, {'worker_id': 'bad'}, {'search_source': 'yahoo_realestate'}, {'import_sources': ['chintai']}, {'headers': {'x': 'y'}}])
def test_claim_contract_and_source_config_are_fixed(queue, patch):
    q, _ = queue
    with pytest.raises(WorkerError):
        q.claim(dict(CAPS, **patch))


@pytest.mark.parametrize('field', ['worker_id', 'job_id', 'lease_token'])
def test_unrelated_worker_or_lease_cannot_publish(queue, field):
    q, _ = queue
    _, lease = claimed(q)
    message = finished(lease)
    message[field] = 'unrelated_identity_1234567890abcdef'
    with pytest.raises(WorkerError):
        q.finish(message)


@pytest.mark.parametrize('mutation', [
    lambda r: r['listings'][0].update(source_id='manual'),
    lambda r: r['listings'][0].update(source_url='http://127.0.0.1/private'),
    lambda r: r['listings'][0].update(source_url='https://example.com/listing'),
    lambda r: r['listings'][0].update(rent_yen=-1),
    lambda r: r['listings'][0].update(municipality='別区'),
    lambda r: r['listings'][0].update(station_name='別駅'),
    lambda r: r['listings'][0].update(area_sqm=999),
    lambda r: r['listings'][0].update(fetched_at=None),
    lambda r: r.update(listings=r['listings'] * 61),
    lambda r: r.update(source_reports=[{'source_id': 'chintai'}]),
    lambda r: r.update(html='source HTML must not transit the queue'),
    lambda r: r.update(warnings=['x' * 5000]),
])
def test_worker_result_is_validated_before_publication(queue, mutation):
    q, _ = queue
    identity, lease = claimed(q)
    result = search_result()
    mutation(result)
    with pytest.raises(WorkerError):
        q.finish(finished(lease, result))
    assert q.get('search', identity)['status'] == 'running'


def test_import_result_must_match_requested_detail_identity(queue):
    q, _ = queue
    _, lease = claimed(q, 'import')
    result = import_result()
    result['listing']['source_url'] = URL.replace('000001/', '000002/')
    with pytest.raises(WorkerError):
        q.finish(finished(lease, result))


def test_partial_import_keeps_missing_required_field_for_user_review(queue):
    q, _ = queue
    identity, lease = claimed(q, 'import')
    result = import_result()
    result['listing']['layout'] = None
    result['missing_fields'].append('layout')
    q.finish(finished(lease, result))
    assert q.get('import', identity)['result']['listing']['layout'] is None


def test_safe_error_messages_not_worker_supplied_text(queue):
    q, _ = queue
    identity, lease = claimed(q, 'import')
    q.finish(finished(lease, error='source_blocked'))
    response = q.get('import', identity)
    assert response['error']['code'] == 'source_blocked'
    assert 'untrusted-secret' not in str(response)
    assert q.claim(CAPS)['job'] is None


def test_record_eviction_keeps_only_bounded_completed_results(queue):
    q, _ = queue
    first = None
    for _ in range(40):
        identity, lease = claimed(q)
        first = first or identity
        q.finish(finished(lease))
    assert len(q.jobs) == 32 and q.get('search', first) is None
    q.close()
    assert not q.state()['online'] and not q.jobs


def test_http_roles_offline_options_and_async_import(server_factory, monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    server = server_factory(execution_mode='worker', access_token=USER_TOKEN, worker_token=WORKER_TOKEN)
    assert request(server, 'POST', '/api/v2/worker/claim', CAPS, token=USER_TOKEN)[0] == 401
    assert request(server, 'GET', '/api/v2/personal/options', token=WORKER_TOKEN)[0] == 401
    status, options = request(server, 'GET', '/api/v2/personal/options', token=USER_TOKEN)
    assert status == 200 and options['execution_mode'] == 'worker' and not options['import_available']
    status, error = request(server, 'POST', '/api/v2/personal/import', {'url': URL}, token=USER_TOKEN)
    assert status == 503 and error['error'] == 'worker_offline'
    assert request(server, 'POST', '/api/v2/worker/claim', CAPS, token=WORKER_TOKEN)[0] == 200
    status, created = request(server, 'POST', '/api/v2/personal/import', {'url': URL}, token=USER_TOKEN)
    assert status == 202
    lease = request(server, 'POST', '/api/v2/worker/claim', CAPS, token=WORKER_TOKEN)[1]['job']
    assert request(server, 'POST', '/api/v2/worker/result', finished(lease, import_result()), token=WORKER_TOKEN)[0] == 200
    status, result = request(server, 'GET', '/api/v2/personal/import/' + created['job_id'], token=USER_TOKEN)
    assert status == 200 and result['result']['listing']['source_url'] == URL


@pytest.mark.parametrize('args', [dict(worker_token=None), dict(worker_token=USER_TOKEN), dict(access_token=None), dict(personal_enabled=False), dict(pilot_mode=True), dict(execution_mode='other')])
def test_invalid_worker_configuration_fails_before_listening(server_factory, args):
    config = dict(execution_mode='worker', access_token=USER_TOKEN, worker_token=WORKER_TOKEN)
    config.update(args)
    with pytest.raises(ValueError):
        server_factory(**config)


def test_browser_server_and_worker_contract_end_to_end_without_source_calls(server_factory, monkeypatch):
    from backend.v2.remote_worker import RemoteWorker
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    server = server_factory(execution_mode='worker', access_token=USER_TOKEN, worker_token=WORKER_TOKEN)
    source_calls = []
    def transport(path, body):
        status, result = request(server, 'POST', path, body, token=WORKER_TOKEN)
        assert status == 200, result
        return result
    def search(data):
        source_calls.append('search')
        return search_result()
    def read(data):
        source_calls.append('import')
        return import_result()
    worker = RemoteWorker(transport, search_fn=search, import_fn=read)
    assert worker.step() == 10  # Establish availability without any source access.
    for kind, payload in [('import', {'url': URL}), ('search', {'subject': subject()})]:
        status, created = request(server, 'POST', '/api/v2/personal/' + kind, payload, token=USER_TOKEN)
        assert status == 202
        worker.step()
        status, result = request(server, 'GET', '/api/v2/personal/' + kind + '/' + created['job_id'], token=USER_TOKEN)
        assert status == 200 and result['status'] == 'complete', result
    assert source_calls == ['import', 'search']
    assert request(server, 'POST', '/api/v2/personal/compare', {'subject': subject(), 'listings': search_result()['listings']}, token=USER_TOKEN)[0] == 200


def test_only_worker_result_accepts_more_than_normal_body_limit(server_factory, monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    server = server_factory(execution_mode='worker', access_token=USER_TOKEN, worker_token=WORKER_TOKEN)
    request(server, 'POST', '/api/v2/worker/claim', CAPS, token=WORKER_TOKEN)
    request(server, 'POST', '/api/v2/personal/search', {'subject': subject()}, token=USER_TOKEN)
    lease = request(server, 'POST', '/api/v2/worker/claim', CAPS, token=WORKER_TOKEN)[1]['job']
    result = search_result()
    result['source_reports'][0]['warnings'] = ['bounded metadata ' * 100] * 55
    assert request(server, 'POST', '/api/v2/worker/result', finished(lease, result), token=WORKER_TOKEN)[0] == 200
    assert request(server, 'POST', '/api/v2/personal/compare', {'subject': subject(), 'listings': [], 'extra': 'x' * 66000}, token=USER_TOKEN)[0] == 413


def test_five_queued_searches_survive_slow_serial_processing(queue):
    q, now = queue
    ids = [q.start('search', {'subject': subject()})['job_id'] for _ in range(5)]
    assert [q.get('search', key)['queue_position'] for key in ids] == [1, 2, 3, 4, 5]
    now[0] = 10  # worker's idle polling delay
    for index, key in enumerate(ids):
        lease = q.claim(CAPS)['job']
        assert lease['job_id'] == key
        now[0] += 74  # near the execution lease limit, longer than a normal source query
        q.finish(finished(lease))
        assert q.get('search', key)['status'] == 'complete'
        for pending in ids[index + 1:]:
            status = q.get('search', pending)
            assert status['status'] == 'pending' and status['remaining_seconds'] > 0
        now[0] += 1
    assert now[0] == 385  # the fifth job used to expire at 100 seconds


def test_cancelled_active_job_remains_visible_in_wait_position(queue):
    q, _ = queue
    key, lease = claimed(q)
    waiting = q.start('import', {'url': URL})['job_id']
    q.cancel('search', key)
    assert q.get('import', waiting)['queue_position'] == 2
    with pytest.raises(WorkerError):
        q.finish(finished(lease))
    assert q.get('import', waiting)['queue_position'] == 1


def test_five_http_clients_search_poll_compare_under_shared_limit(server_factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from backend.v2.remote_worker import RemoteWorker
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    server = server_factory(execution_mode='worker', access_token=USER_TOKEN, worker_token=WORKER_TOKEN,
                            requests_per_minute=120)
    def transport(path, body):
        status, data = request(server, 'POST', path, body, token=WORKER_TOKEN)
        assert status == 200
        return data
    runner = RemoteWorker(transport, search_fn=lambda _: search_result())
    runner.step()
    barrier = threading.Barrier(5)
    def register(_):
        barrier.wait(timeout=5)
        return request(server, 'POST', '/api/v2/personal/search', {'subject': subject()}, token=USER_TOKEN)
    with ThreadPoolExecutor(max_workers=5) as pool:
        created = list(pool.map(register, range(5)))
        assert all(code == 202 for code, _ in created)
        ids = [value['job_id'] for _, value in created]
        assert sorted(value['queue_position'] for _, value in created) == [1, 2, 3, 4, 5]
        assert request(server, 'POST', '/api/v2/personal/search', {'subject': subject()}, token=USER_TOKEN)[0] == 429
        # Compress a whole minute of 5-second polling into one real rate-limit window.
        for _ in range(12):
            polled = list(pool.map(lambda key: request(server, 'GET', '/api/v2/personal/search/' + key, token=USER_TOKEN), ids))
            assert all(code == 200 for code, _ in polled)
        for _ in range(5):
            runner.step()
        def compare(key):
            code, data = request(server, 'GET', '/api/v2/personal/search/' + key, token=USER_TOKEN)
            assert code == 200 and data['status'] == 'complete'
            return request(server, 'POST', '/api/v2/personal/compare',
                           {'subject': subject(), 'listings': data['result']['listings']}, token=USER_TOKEN)[0]
        assert list(pool.map(compare, ids)) == [200] * 5
