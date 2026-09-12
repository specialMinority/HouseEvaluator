"""Bounded, non-identifying error evidence; all HTTP responses are synthetic."""
import gzip
import http.client
import json
import time
from unittest.mock import patch

import pytest

from backend.tests import test_v2_public_fetch as fetch_helpers
from backend.tests.test_v2_public_search import SUBJECT, card
from backend.v2 import public_fetch, public_search
from backend.v2.public_fetch import PublicFetchError, PublicResponse


fetch = fetch_helpers.PublicFetchProtectionTest().fetch
Connection, Response = fetch_helpers.Connection, fetch_helpers.Response
SECRET = 'private-cookie-request-id-internal-host-https://private.invalid/'


@pytest.mark.parametrize(('body', 'classification'), [
    (b'<title>503 Service Unavailable</title>', 'service_unavailable'),
    (b'<h1>Service temporarily unavailable</h1>', 'service_unavailable'),
    (b'<title>Access Denied</title>', 'access_denied'),
    (b'<h1>Request blocked</h1><p>The request could not be satisfied.</p>', 'access_denied'),
    (b'<h1>The request could not be satisfied.</h1>', 'unclassified'),
    (b'<h1>Verify you are human</h1>', 'challenge'),
    (b'<script>captcha = "https://private.invalid/";</script><h1>503</h1>', 'unclassified'),
    (b'', 'unclassified'),
    (b'\xff\xfe\x00 broken non-text body', 'unclassified'),
])
def test_5xx_classification_requires_visible_evidence_and_returns_no_body(body, classification):
    response = Response(body + SECRET.encode(), status=503, headers={
        'Server': 'private-internal-host', 'Set-Cookie': SECRET, 'X-Request-Id': SECRET,
        'Location': SECRET, 'Retry-After': '30',
    })
    result, connection, _, _ = fetch(response)
    assert result.status == 503 and result.body == b''
    assert result.diagnostics == {'classification': classification, 'body_inspection': 'complete', 'retry_after_seconds': 30}
    assert SECRET not in repr(result) and 'private-internal-host' not in repr(result)
    assert len(connection.requests) == 1 and connection.closed.is_set()


def test_5xx_reads_at_most_8k_even_with_huge_or_missing_length():
    for headers in ({}, {'Content-Length': '1000000000'}):
        response = Response(b'x' * 8192 + b'<h1>Access Denied</h1>' + SECRET.encode(),
                            status=503, headers=headers, chunk_size=137)
        result, _, _, _ = fetch(response)
        assert response.body.tell() == 8192
        assert result.diagnostics == {'classification': 'unclassified', 'body_inspection': 'limited'}
        assert result.status == 503 and result.body == b''


@pytest.mark.parametrize('headers', [
    {'Content-Type': 'application/octet-stream'}, {'Content-Type': None},
    {'Content-Encoding': 'gzip'}, {'Content-Encoding': 'br'}, {'Content-Encoding': None},
    {'Content-Length': '-1'}, {'Content-Length': '9' * 10000}, {'Content-Length': SECRET},
])
def test_malformed_and_compressed_error_bodies_preserve_status_without_reading(headers):
    response = Response(gzip.compress(b'Access Denied' * 100000), status=502, headers=headers)
    result, _, _, _ = fetch(response)
    assert result.status == 502 and result.body == b'' and response.reads == 0
    assert result.diagnostics == {'classification': 'unclassified', 'body_inspection': 'unavailable'}


@pytest.mark.parametrize('retry', ['-1', '+1', '1.5', '86401', '9999999999999999999',
                                   'Sun, 13 Sep 2026 12:00:00 GMT', SECRET, '12\r\nCookie: secret', None])
def test_retry_after_does_not_expose_unbounded_or_free_text_values(retry):
    result, _, _, _ = fetch(Response(status=503, headers={'Retry-After': retry}))
    assert 'retry_after_seconds' not in result.diagnostics
    assert SECRET not in repr(result)


@pytest.mark.parametrize('retry', ['0', '1', ' 60 ', '86400'])
def test_retry_after_valid_seconds_are_observations_only(retry):
    result, connection, _, _ = fetch(Response(status=503, headers={'Retry-After': retry}))
    assert result.diagnostics['retry_after_seconds'] == int(retry)
    assert len(connection.requests) == 1


@pytest.mark.parametrize(('headers', 'expected'), [
    ({'Server': 'cloudflare', 'CF-Ray': SECRET}, 'cloudflare'),
    ({'Server': 'CloudFront', 'X-Amz-Cf-Id': SECRET}, 'cloudfront'),
    ({'Server': 'AkamaiGHost'}, 'akamai'),
    ({'Via': '1.1 synthetic-private.cloudfront.net (CloudFront)'}, 'cloudfront'),
    ({'Server': 'private-cloudflare.invalid', 'Via': SECRET}, None),
])
def test_edge_hints_are_fixed_brand_observations_and_never_error_attribution(headers, expected):
    result, _, _, _ = fetch(Response(status=503, body=b'', headers=headers))
    assert result.diagnostics.get('edge_hint') == expected
    assert result.diagnostics['classification'] == 'unclassified'
    assert SECRET not in repr(result) and 'synthetic-private' not in repr(result)


@pytest.mark.parametrize('error', [OSError(SECRET), http.client.IncompleteRead(b'secret'),
                                  PublicFetchError('timeout'), ValueError(SECRET)])
def test_diagnostic_read_errors_cannot_replace_the_original_http_status(error):
    def fail_read():
        raise error
    result, connection, _, _ = fetch(Response(status=503, headers={'Retry-After': '15'}, read_hook=fail_read))
    assert result.status == 503 and result.body == b''
    assert result.diagnostics == {'classification': 'unclassified', 'body_inspection': 'unavailable', 'retry_after_seconds': 15}
    assert SECRET not in repr(result) and connection.closed.is_set()


def test_diagnostic_deadline_expiry_preserves_503_and_stops_reading():
    clock = [100.0]
    response = Response(b'<title>Access Denied</title>', status=503, chunk_size=1,
                        read_hook=lambda: clock.__setitem__(0, clock[0] + 2))
    result, _, _, _ = fetch(response, deadline=103, clock=lambda: clock[0])
    assert result.status == 503 and response.reads == 2
    assert result.diagnostics == {'classification': 'unclassified', 'body_inspection': 'unavailable'}


def test_watchdog_stops_a_stalled_5xx_body_without_masking_status():
    response = Response(status=503)
    connection = Connection(response)
    def stalled_read():
        assert connection.closed.wait(1), 'watchdog did not stop diagnostic read'
        raise OSError(SECRET)
    response.read_hook = stalled_read
    started = time.monotonic()
    result, _, _, _ = fetch(response, connection=connection, REQUEST_SECONDS=.04)
    assert time.monotonic() - started < .8
    assert result.status == 503 and result.diagnostics['body_inspection'] == 'unavailable'


@pytest.mark.parametrize('status', [401, 403, 404, 410, 429])
def test_non5xx_responses_still_have_zero_body_reads_and_no_diagnostics(status):
    response = Response(b'Access Denied' + SECRET.encode(), status=status, headers={'Retry-After': '5'})
    result, connection, _, _ = fetch(response)
    assert result.status == status and result.body == b'' and result.diagnostics is None
    assert response.reads == 0 and len(connection.requests) == 1


@pytest.fixture(autouse=True)
def clean_search_runtime(monkeypatch):
    public_search._STATION_CACHE.clear()
    monkeypatch.setattr(public_search.time, 'sleep', lambda _: None)
    yield
    public_search._STATION_CACHE.clear()


def search_failure(stage, status=503, diagnostics=None, error=None):
    calls = []
    def fetcher(url, *, deadline):
        kind = ('robots' if url.endswith('/robots.txt') else 'search' if '/ichiran/' in url
                else 'metadata' if url.endswith('/ensen/') else 'detail')
        calls.append(kind)
        if kind == stage:
            if error:
                raise error
            return PublicResponse(status, SECRET.encode(), diagnostics=diagnostics)
        if kind == 'robots':
            return PublicResponse(200, b'User-agent: *\nDisallow:\n', 'text/plain')
        if kind == 'search':
            return PublicResponse(200, card().encode())
        raise AssertionError('unexpected request after failure')
    if stage == 'detail':
        with patch.object(public_search, '_resolve_station', return_value=None):
            return public_search.search(SUBJECT, fetcher=fetcher), calls
    return public_search.search(SUBJECT, fetcher=fetcher), calls


@pytest.mark.parametrize(('stage', 'calls'), [('robots', ['robots']), ('search', ['robots', 'search']),
                                           ('metadata', ['robots', 'search', 'metadata']),
                                           ('detail', ['robots', 'search', 'detail'])])
def test_each_failure_stage_reports_status_and_only_allowed_diagnostic_values(stage, calls):
    diagnostics = {'classification': 'service_unavailable', 'body_inspection': 'limited',
                   'retry_after_seconds': 30, 'edge_hint': 'cloudfront',
                   'body': SECRET, 'set_cookie': SECRET, 'request_id': SECRET, 'url': SECRET}
    result, actual_calls = search_failure(stage, diagnostics=diagnostics)
    report = result['source_reports'][0]
    assert actual_calls == calls
    assert report['failure_stage'] == stage and report['http_status'] == 503
    assert report['status'] == 'unavailable'
    assert report['error_code'] == ('robots_unavailable' if stage == 'robots' else 'http_503')
    assert report['diagnostics'] == {key: diagnostics[key] for key in
                                     ('classification', 'body_inspection', 'retry_after_seconds', 'edge_hint')}
    assert SECRET not in json.dumps(result)
    diagnostics['classification'] = SECRET
    assert report['diagnostics']['classification'] == 'service_unavailable'


@pytest.mark.parametrize('stage', ['robots', 'search', 'metadata', 'detail'])
@pytest.mark.parametrize('status', [401, 403, 429])
def test_access_denials_immediately_stop_at_every_stage(stage, status):
    result, calls = search_failure(stage, status=status,
                                  diagnostics={'classification': 'service_unavailable', 'retry_after_seconds': 1})
    report = result['source_reports'][0]
    assert calls[-1] == stage and calls.count(stage) == 1
    assert report['status'] == 'blocked' and report['error_code'] == 'source_blocked'
    assert report['http_status'] == status and report['failure_stage'] == stage
    assert 'diagnostics' not in report


@pytest.mark.parametrize('stage', ['robots', 'search', 'metadata', 'detail'])
def test_no_http_response_is_distinct_from_a_received_http_failure(stage):
    result, calls = search_failure(stage, error=PublicFetchError('network_failed'))
    report = result['source_reports'][0]
    assert calls[-1] == stage and report['http_status'] is None
    assert report['failure_stage'] == stage and report['error_code'] == 'network_failed'
    assert 'diagnostics' not in report


@pytest.mark.parametrize('diagnostics', [
    {'classification': SECRET, 'body_inspection': SECRET, 'edge_hint': SECRET, 'retry_after_seconds': True},
    {'classification': [], 'body_inspection': {}, 'retry_after_seconds': 86401},
    {'retry_after_seconds': SECRET}, SECRET, None,
])
def test_invalid_diagnostics_are_never_forwarded_to_source_reports(diagnostics):
    result, _ = search_failure('search', diagnostics=diagnostics)
    report = result['source_reports'][0]
    assert 'diagnostics' not in report and SECRET not in json.dumps(result)
    assert report['http_status'] == 503
