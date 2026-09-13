import pytest
from backend.v2 import source_preflight
from backend.v2.public_fetch import PublicResponse, PublicFetchError

@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_denied_robots_stops_without_listing_request(status):
    calls=[]
    def fetcher(url, *, deadline):
        calls.append(url)
        return PublicResponse(status)
    report=source_preflight.probe(fetcher=fetcher)
    assert len(calls)==1 and calls[0].endswith('/robots.txt')
    assert report['status']=='unavailable' and report['requests'][0]['http_status']==status

def test_robot_rule_is_respected(monkeypatch):
    calls=[]
    def fetcher(url, *, deadline):
        calls.append(url)
        return PublicResponse(200,b'User-agent: *\nDisallow: /rent/search/\n','text/plain')
    report=source_preflight.probe(fetcher=fetcher)
    assert len(calls)==1 and report['status']=='robots_blocked'

def test_success_only_reports_fixed_metadata_no_body(monkeypatch):
    monkeypatch.setattr(source_preflight.time,'sleep',lambda delay:None)
    calls=[]
    def fetcher(url, *, deadline):
        calls.append((url,deadline))
        return PublicResponse(200,b'User-agent: *\nDisallow:\n','text/plain') if len(calls)==1 else PublicResponse(200,b'<div class="ListCassetteRoom__item">PRIVATE_RESPONSE_MARKER</div>')
    report=source_preflight.probe(fetcher=fetcher)
    assert len(calls)==2 and calls[0][1]==calls[1][1]
    assert report['status']=='html_received' and report['has_listing_marker']
    assert 'PRIVATE_RESPONSE_MARKER' not in repr(report)

def test_exception_is_not_logged_or_retried():
    def fetcher(url, *, deadline):
        raise RuntimeError('sensitive arbitrary error')
    report=source_preflight.probe(fetcher=fetcher)
    assert report['error_code']=='unexpected_failure' and 'sensitive' not in repr(report)
