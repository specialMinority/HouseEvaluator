"""Offline worker protocol and transport boundaries; no source or cloud calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import ssl

import pytest

from backend.tests.test_v2_personal_http import SUBJECT
from backend.v2 import remote_worker as module
from backend.v2.listing_import import ListingImportError
from backend.v2.personal_jobs import SearchBusy

TOKEN = 'x' * 43
JOB_ID = 'j' * 32
LEASE = 'l' * 43
WORKER = 'w' * 32
SECRET = 'private-cookie-url-socket-detail'
IMPORT_URL = 'https://www.chintai.net/detail/bk-C000000000000000000000000001/'


@pytest.fixture(autouse=True)
def source_configuration(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def advance(self, value): self.now += value


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
    def __call__(self, path, payload):
        self.calls.append((path, deepcopy(payload)))
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return deepcopy(response)


def job(kind='search', payload=None, **changes):
    value = {'job_id': JOB_ID, 'lease_token': LEASE, 'kind': kind,
             'payload': payload if payload is not None else {'subject': deepcopy(SUBJECT)}, 'lease_seconds': 75}
    value.update(changes)
    return {'job': value, 'poll_after_seconds': 1}


def worker(transport, **kwargs):
    return module.RemoteWorker(transport, worker_id=WORKER, **kwargs)


def outcome(transport):
    return transport.calls[-1][1]['outcome']


def test_idle_claim_advertises_fixed_process_capabilities_without_source_requests():
    transport = Transport({'job': None, 'poll_after_seconds': 1}, {'job': None, 'poll_after_seconds': 10})
    seen = []
    runner = worker(transport, search_fn=lambda p: seen.append(p), import_fn=lambda p: seen.append(p))
    assert runner.step() == runner.step() == 10 and not seen
    assert transport.calls[0][1] == {'worker_id': WORKER, 'protocol': 1, 'search_source': 'suumo',
                                    'import_sources': ['chintai', 'yahoo_realestate']}
    assert transport.calls[0] == transport.calls[1]


def test_search_subject_is_revalidated_and_result_keeps_source_failure_observation():
    observed = {'listings': [], 'source_reports': [{'source_id': 'suumo', 'status': 'blocked', 'http_status': 403}]}
    source_calls = []
    transport = Transport(job(), {})
    runner = worker(transport, search_fn=lambda subject: source_calls.append(subject) or observed)
    assert runner.step() == 1
    assert len(source_calls) == 1 and source_calls[0]['missing_fields']
    assert outcome(transport) == {'status': 'complete', 'result': observed}
    assert transport.calls[-1][1]['worker_id'] == WORKER
    assert transport.calls[-1][1]['job_id'] == JOB_ID and transport.calls[-1][1]['lease_token'] == LEASE


def test_import_uses_existing_validated_import_contract():
    seen = []
    transport = Transport(job('import', {'url': IMPORT_URL}), {})
    runner = worker(transport, import_fn=lambda payload: seen.append(payload) or {'status': 'partial', 'listing': {}})
    runner.step()
    assert seen == [{'url': IMPORT_URL}]
    assert outcome(transport)['result']['status'] == 'partial'


@pytest.mark.parametrize(('response', 'code'), [
    (job('shell', {'command': SECRET}), 'unsupported_job'),
    (job('search', {'subject': SUBJECT, 'url': IMPORT_URL}), 'invalid_job'),
    (job('search', {'subject': {}}), 'invalid_subject'),
    (job('search', {'subject': dict(SUBJECT, rent_yen=True)}), 'invalid_subject'),
    (job('import', {'url': 'https://127.0.0.1/private'}), 'unsupported_url'),
    (job('import', {'url': IMPORT_URL, 'token': SECRET}), 'invalid_payload'),
])
def test_untrusted_job_payload_is_rejected_without_any_source_call(response, code):
    seen = []
    transport = Transport(response, {})
    runner = worker(transport, search_fn=lambda p: seen.append(p), import_fn=lambda p: seen.append(p))
    runner.step()
    assert not seen and outcome(transport)['error']['code'] == code
    assert SECRET not in json.dumps(outcome(transport))


def test_worker_import_capability_restriction_precedes_source_call():
    seen = []
    transport = Transport(job('import', {'url': IMPORT_URL}), {})
    runner = worker(transport, import_source_ids=['yahoo_realestate'], import_fn=lambda p: seen.append(p))
    runner.step()
    assert not seen and outcome(transport)['error']['code'] == 'source_disabled'


@pytest.mark.parametrize('change', [{'job_id': '../bad'}, {'lease_token': 'bad token'}, {'lease_seconds': True}, {'lease_seconds': 999}, {'payload': SUBJECT, 'job_id': None}])
def test_invalid_job_envelope_is_not_executed_or_uploaded(change):
    transport = Transport(job(**change))
    with pytest.raises(module.WorkerProtocolError, match='invalid_job_envelope'):
        worker(transport, search_fn=lambda p: pytest.fail('source must not run')).step()
    assert len(transport.calls) == 1


@pytest.mark.parametrize(('error', 'code', 'delay'), [
    (RuntimeError(SECRET), 'worker_unavailable', 1),
    (SearchBusy(SECRET), 'worker_busy', 10),
    (ListingImportError('source_blocked'), 'source_blocked', 1),
    (ListingImportError('import_busy'), 'import_busy', 10),
])
def test_source_exception_is_fixed_safe_error_and_busy_pauses(error, code, delay):
    def source(_): raise error
    transport = Transport(job(), {})
    assert worker(transport, search_fn=source).step() == delay
    assert outcome(transport)['error']['code'] == code
    assert SECRET not in json.dumps(outcome(transport))


def test_failed_result_upload_retransmits_same_result_without_repeating_query():
    clock, calls = Clock(), []
    transport = Transport(job(), module.WorkerError('cloud_unavailable'), {})
    runner = worker(transport, clock=clock, search_fn=lambda p: calls.append(p) or {'listings': []})
    assert runner.step() == 30 and runner.last_event == 'upload_retry'
    original = deepcopy(transport.calls[-1])
    clock.advance(30)
    assert runner.step() == 1 and runner.last_event == 'result_sent'
    assert len(calls) == 1 and transport.calls[-1] == original
    assert [path for path, _ in transport.calls] == ['/api/v2/worker/claim', '/api/v2/worker/result', '/api/v2/worker/result']


def test_late_completion_and_expired_upload_lease_never_reexecute_source():
    clock, calls = Clock(), []
    def slow(_):
        calls.append(1)
        clock.advance(75)
        return {'listings': []}
    transport = Transport(job(), job(), {})
    runner = worker(transport, clock=clock, search_fn=slow)
    assert runner.step() == 10 and runner.last_event == 'result_expired'
    assert len(transport.calls) == 1
    runner.step()
    assert len(calls) == 1 and outcome(transport)['error']['code'] == 'already_processed'


def test_upload_retry_stops_at_lease_and_409_discards_pending():
    clock = Clock()
    transport = Transport(job(), module.WorkerError(), module.WorkerError(), {'job': None, 'poll_after_seconds': 10})
    runner = worker(transport, clock=clock, search_fn=lambda p: {'listings': []})
    assert runner.step() == 30
    clock.advance(30)
    assert runner.step() == 60 and runner.last_event == 'result_expired'
    clock.advance(60)
    assert runner.step() == 10 and runner.last_event == 'idle'
    transport = Transport(job(), module.WorkerLeaseExpired('lease_expired'))
    runner = worker(transport, search_fn=lambda p: {})
    assert runner.step() == 10 and runner.last_event == 'result_expired'


def test_retry_backoff_caps_at_300_and_auth_failure_is_not_retried():
    transport = Transport(*(module.WorkerError() for _ in range(6)), module.WorkerAuthError('worker_auth_rejected'))
    runner = worker(transport)
    assert [runner.step() for _ in range(6)] == [30, 60, 120, 240, 300, 300]
    with pytest.raises(module.WorkerAuthError): runner.step()
    assert len(transport.calls) == 7


def test_claim_busy_pauses_and_stop_does_not_claim():
    transport = Transport(module.WorkerBusy('worker_busy'))
    runner = worker(transport)
    assert runner.step() == 10
    runner.stop()
    assert runner.step() == 0 and len(transport.calls) == 1


@pytest.mark.parametrize('result', [{'body': SECRET * 100000}, {'number': float('nan')}, {'set': {1, 2}}])
def test_oversized_or_non_json_results_become_small_failure_without_content(result):
    transport = Transport(job(), {})
    worker(transport, search_fn=lambda p: result).step()
    assert outcome(transport)['error']['code'] == 'result_too_large'
    assert SECRET not in json.dumps(outcome(transport))
    assert len(module._json_bytes(transport.calls[-1][1])) < 1024


class Response:
    def __init__(self, status=200, body=b'{"job":null}', headers=None):
        self.status, self.body, self.reads = status, body, []
        self.headers = {'Content-Type': 'application/json', **(headers or {})}
    def getheader(self, name, default=None): return self.headers.get(name, default)
    def read(self, limit):
        self.reads.append(limit)
        result, self.body = self.body[:limit], self.body[limit:]
        return result


class Connection:
    def __init__(self, response):
        self.response, self.closed, self.calls, self.sock = response, False, [], None
    def request(self, *args, **kwargs): self.calls.append((args, kwargs))
    def getresponse(self): return self.response
    def close(self): self.closed = True


def transport_for(response, *, clock=None):
    connection, created = Connection(response), []
    def connect(*args, **kwargs):
        created.append((args, kwargs))
        return connection
    transport = module.HttpsTransport(module.ORIGIN, TOKEN, connection_factory=connect, **({'clock': clock} if clock else {}))
    return transport, connection, created


def test_transport_verifies_tls_uses_only_fixed_path_and_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://private.invalid:9999')
    response = Response()
    transport, connection, created = transport_for(response)
    assert transport('/api/v2/worker/claim', {'protocol': 1}) == {'job': None}
    args, kwargs = created[0]
    assert args == ('houseevaluator-personal.onrender.com', 443)
    assert kwargs['context'].verify_mode == ssl.CERT_REQUIRED and kwargs['context'].check_hostname
    assert kwargs['timeout'] == 10
    call_args, call_kwargs = connection.calls[0]
    assert call_args == ('POST', '/api/v2/worker/claim')
    assert call_kwargs['headers']['Authorization'] == 'Bearer ' + TOKEN
    assert call_kwargs['headers']['Accept-Encoding'] == 'identity' and connection.closed


@pytest.mark.parametrize(('status', 'kind'), [(301, module.WorkerProtocolError), (307, module.WorkerProtocolError),
    (401, module.WorkerAuthError), (403, module.WorkerAuthError), (429, module.WorkerBusy), (503, module.WorkerError)])
def test_denials_redirects_and_failures_never_read_response_body_or_follow_location(status, kind):
    response = Response(status, SECRET.encode(), {'Location': 'https://private.invalid/'})
    transport, connection, created = transport_for(response)
    with pytest.raises(kind) as error: transport('/api/v2/worker/claim', {})
    assert not response.reads and len(created) == 1 and connection.closed
    assert SECRET not in str(error.value)


@pytest.mark.parametrize('headers', [{'Content-Length': str(module.MAX_BYTES + 1)}, {'Content-Length': '1,1'},
    {'Content-Length': '-2'}, {'Content-Encoding': 'gzip'}, {'Content-Type': 'text/html'}])
def test_response_headers_are_checked_before_body_read(headers):
    response = Response(headers=headers)
    transport, connection, _ = transport_for(response)
    with pytest.raises(module.WorkerProtocolError): transport('/api/v2/worker/claim', {})
    assert not response.reads and connection.closed


@pytest.mark.parametrize('body', [b'x' * (module.MAX_BYTES + 100), b'{"job":null,"job":{}}',
                                b'{"value":NaN}', SECRET.encode(), b'[]'],
                         ids=['over-limit', 'duplicate-key', 'nonfinite', 'text', 'nonobject'])
def test_stream_limit_and_malformed_json_never_escape_as_response(body):
    response = Response(body=body)
    transport, connection, _ = transport_for(response)
    with pytest.raises(module.WorkerProtocolError) as error: transport('/api/v2/worker/claim', {})
    assert sum(response.reads) <= module.MAX_BYTES + 65536
    assert SECRET not in str(error.value) and connection.closed


def test_transmission_limit_wrong_endpoint_and_deadline_prevent_extra_io():
    response = Response()
    clock = Clock()
    transport, connection, created = transport_for(response, clock=clock)
    with pytest.raises(module.WorkerProtocolError): transport('/api/v2/worker/claim', {'huge': 'x' * module.MAX_BYTES})
    with pytest.raises(module.WorkerProtocolError): transport('https://private.invalid/', {})
    assert created == []
    def expired():
        clock.advance(11)
        return response
    connection.getresponse = expired
    with pytest.raises(module.WorkerError, match='cloud_timeout'): transport('/api/v2/worker/claim', {})
    assert not response.reads and connection.closed


@pytest.mark.parametrize('origin', ['http://houseevaluator-personal.onrender.com', module.ORIGIN + '/',
    module.ORIGIN + '/private', module.ORIGIN + '@private.invalid', 'https://127.0.0.1', module.ORIGIN + '?token=x'])
def test_origin_is_exact_and_cannot_leak_worker_token(origin):
    with pytest.raises(module.WorkerProtocolError, match='invalid_origin'):
        module.HttpsTransport(origin, TOKEN)


@pytest.mark.parametrize('raw', [b'too-short', b'x' * 1025, b'x' * 40 + b'\nsecret', b'\xff' * 43],
                         ids=['short', 'over-limit', 'embedded-line', 'nonascii'])
def test_token_files_are_bounded_and_invalid_values_are_not_disclosed(tmp_path, raw):
    path = tmp_path / 'worker-token.txt'
    path.write_bytes(raw)
    with pytest.raises(module.WorkerProtocolError, match='invalid_token_file'):
        module.read_token(path)


def test_token_file_newline_and_link_refusal(tmp_path, monkeypatch):
    path = tmp_path / 'worker-token.txt'
    path.write_text(TOKEN + '\n', encoding='ascii')
    assert module.read_token(path) == TOKEN
    original = module.Path.is_symlink
    monkeypatch.setattr(module.Path, 'is_symlink', lambda self: self == path or original(self))
    with pytest.raises(module.WorkerProtocolError): module.read_token(path)


def test_main_once_idle_and_auth_failure_only_log_fixed_message(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'worker-token.txt'
    path.write_text(TOKEN, encoding='ascii')
    monkeypatch.setenv('HOUSE_EVALUATOR_WORKER_TOKEN_FILE', str(path))
    monkeypatch.setenv('HOUSE_EVALUATOR_WORKER_ORIGIN', module.ORIGIN)
    transport = Transport({'job': None, 'poll_after_seconds': 10})
    monkeypatch.setattr(module, 'HttpsTransport', lambda *args: transport)
    assert module.main(['--once']) == 0 and len(transport.calls) == 1
    rejected = Transport(module.WorkerAuthError('worker_auth_rejected'))
    monkeypatch.setattr(module, 'HttpsTransport', lambda *args: rejected)
    assert module.main(['--once']) == 2 and len(rejected.calls) == 1
    output = capsys.readouterr().out
    assert 'worker_auth_rejected' in output and TOKEN not in output and SECRET not in output


def test_processed_identity_memory_is_bounded_and_once_clears_results():
    responses = []
    for index in range(258):
        responses.extend([job(job_id=f'{index:032d}'), {}])
    transport = Transport(*responses)
    runner = worker(transport, search_fn=lambda p: {})
    for _ in range(258): runner.step()
    assert len(runner._seen) == len(runner._seen_order) == 256
    runner.stop()
    assert runner.run(once=True) == 0 and not runner._seen and runner._pending is None


@pytest.mark.parametrize(('error', 'code'), [
    (module.WorkerProtocolError('cloud_request_rejected'), 'cloud_request_rejected'),
    (module.WorkerProtocolError('invalid_response'), 'invalid_response'),
    (module.WorkerProtocolError(SECRET), 'worker_unavailable'),
    (OSError(SECRET), 'invalid_configuration'),
])
def test_cli_failure_diagnostics_are_allowlisted_constants_only(error, code, monkeypatch, capsys):
    monkeypatch.setattr(module, 'read_token', lambda path: TOKEN)
    def fail(*args): raise error
    monkeypatch.setattr(module, 'HttpsTransport', fail)
    assert module.main(['--once']) == 2
    output = capsys.readouterr().out
    assert 'code=' + code + ':' in output
    assert SECRET not in output and TOKEN not in output


ANCHOR = datetime(2026, 1, 1, tzinfo=timezone.utc)


class StopEvent:
    def __init__(self, clock, *, cancel=False):
        self.clock, self.cancel, self.stopped, self.waits = clock, cancel, False, []
    def is_set(self): return self.stopped
    def set(self): self.stopped = True
    def wait(self, seconds):
        self.waits.append(seconds)
        if self.cancel:
            self.stopped = True
            return True
        self.clock.advance(seconds)
        return False


def timed_job(server_time=ANCHOR.isoformat()):
    response = job()
    response['server_time'] = server_time
    return response


@pytest.mark.parametrize('key', ['fetched_at', 'details_fetched_at', 'searched_at'])
def test_server_anchor_waits_for_observed_timestamp_without_modifying_it(key):
    clock, calls = Clock(), []
    stop = StopEvent(clock)
    stamp = (ANCHOR + timedelta(seconds=1.7)).isoformat()
    result = {'listings': [{key: stamp}], 'source_reports': [], 'unknown_key': SECRET}
    transport = Transport(timed_job(), {})
    runner = worker(transport, clock=clock, stop_event=stop, search_fn=lambda p: calls.append(p) or deepcopy(result))
    assert runner.step() == 1
    assert stop.waits == pytest.approx([1.8]) and len(calls) == 1
    assert outcome(transport) == {'status': 'complete', 'result': result}


def test_monotonic_source_duration_and_latest_known_key_determine_wait():
    clock = Clock()
    stop = StopEvent(clock)
    result = {'searched_at': (ANCHOR + timedelta(seconds=1.7)).isoformat(),
              'listings': [{'fetched_at': (ANCHOR + timedelta(seconds=11.7)).isoformat(),
                            'details_fetched_at': (ANCHOR + timedelta(seconds=12.7)).isoformat()}]}
    def source(_):
        clock.advance(10)
        return deepcopy(result)
    transport = Transport(timed_job(), {})
    runner = worker(transport, clock=clock, stop_event=stop, search_fn=source)
    runner.step()
    assert stop.waits == pytest.approx([2.8])
    assert outcome(transport)['result'] == result


@pytest.mark.parametrize('delta', [5, 10, 100000000])
def test_clock_offset_over_five_second_wait_budget_uploads_only_fixed_failure(delta):
    clock, stop = Clock(), None
    stop = StopEvent(clock)
    original = {'searched_at': (ANCHOR + timedelta(seconds=delta)).isoformat(), 'private': SECRET}
    transport = Transport(timed_job(), {})
    worker(transport, clock=clock, stop_event=stop, search_fn=lambda p: original).step()
    assert stop.waits == [] and outcome(transport)['error']['code'] == 'worker_clock_skew'
    assert SECRET not in json.dumps(outcome(transport))
    assert original['private'] == SECRET


def test_exact_five_second_total_wait_is_allowed():
    clock = Clock()
    stop = StopEvent(clock)
    result = {'searched_at': (ANCHOR + timedelta(seconds=4.9)).isoformat()}
    transport = Transport(timed_job(), {})
    worker(transport, clock=clock, stop_event=stop, search_fn=lambda p: result).step()
    assert stop.waits == [5] and outcome(transport)['status'] == 'complete'


def test_stop_during_clock_wait_discards_result_without_upload_or_second_query():
    clock, calls = Clock(), []
    stop = StopEvent(clock, cancel=True)
    transport = Transport(timed_job())
    runner = worker(transport, clock=clock, stop_event=stop, search_fn=lambda p: calls.append(p) or
                    {'searched_at': (ANCHOR + timedelta(seconds=1)).isoformat()})
    assert runner.step() == 0 and runner.last_event == 'stopped'
    assert len(calls) == len(transport.calls) == 1 and runner._pending is None


@pytest.mark.parametrize('stamp', [None, True, '2026-01-01T00:00:00', 'not-a-date', SECRET, '2026-99-99T00:00:00Z'])
def test_invalid_present_server_time_is_protocol_failure_before_source_call(stamp):
    transport = Transport(timed_job(stamp))
    with pytest.raises(module.WorkerProtocolError, match='invalid_server_time'):
        worker(transport, search_fn=lambda p: pytest.fail('source must not run')).step()
    assert len(transport.calls) == 1


@pytest.mark.parametrize('stamp', ['2026-01-01T00:00:00', SECRET, 10])
def test_invalid_known_result_time_returns_safe_clock_error(stamp):
    transport = Transport(timed_job(), {})
    worker(transport, search_fn=lambda p: {'searched_at': stamp}).step()
    assert outcome(transport)['error']['code'] == 'worker_clock_skew'
    assert SECRET not in json.dumps(outcome(transport))


def test_missing_anchor_legacy_and_past_observations_do_not_wait():
    clock = Clock()
    stop = StopEvent(clock)
    transport = Transport(job(), {}, timed_job(), {})
    runner = worker(transport, clock=clock, stop_event=stop, search_fn=lambda p: {'searched_at': ANCHOR.isoformat()})
    runner.step()
    # Use a new job identity; the previous one is intentionally not rerun.
    transport.responses[0]['job']['job_id'] = 'k' * 32
    runner.step()
    assert stop.waits == [] and outcome(transport)['status'] == 'complete'


def test_cancellation_before_pending_upload_discards_payload_without_network():
    transport = Transport()
    runner = worker(transport)
    runner._pending = {'message': {'private': SECRET}, 'expires': 999, 'attempts': 0}
    runner.stop()
    assert runner._upload() == 0 and runner._pending is None
    assert transport.calls == [] and runner.last_event == 'stopped'
