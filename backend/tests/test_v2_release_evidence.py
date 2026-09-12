from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest

from backend.v2.release import checked_evidence
from scripts.load_test_v2 import measure


def test_drill_evidence_needs_hash_context_real_scope_and_freshness(tmp_path):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    report = {'kind': 'restore_drill', 'scope': 'market_operations', 'context_fingerprint': 'current-code',
              'passed': True, 'checked_at': now.isoformat()}
    registry = tmp_path / 'release.json'
    def write(value, correct_hash=True):
        raw = json.dumps(value).encode()
        (tmp_path / 'report.json').write_bytes(raw)
        registry.write_text(json.dumps({'schema_version': 'release-evidence-1.0', 'reports': {'restore_drill': {'path': 'report.json', 'sha256': hashlib.sha256(raw).hexdigest() if correct_hash else 'bad'}}}), encoding='utf-8')
    write(report)
    assert checked_evidence(registry, now=now, kind='restore_drill', context='current-code')
    for changes in ({'scope': 'synthetic_smoke'}, {'context_fingerprint': 'old'}, {'passed': False},
                    {'checked_at': (now - timedelta(days=8)).isoformat()}, {'checked_at': (now + timedelta(seconds=1)).isoformat()}):
        write({**report, **changes})
        assert not checked_evidence(registry, now=now, kind='restore_drill', context='current-code')
    write(report, correct_hash=False)
    assert not checked_evidence(registry, now=now, kind='restore_drill', context='current-code')


def test_metadata_only_load_measurement_is_not_release_evidence(tmp_path):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    report = {'kind': 'load_test', 'scope': 'market_operations', 'context_fingerprint': 'code', 'passed': True,
              'checked_at': now.isoformat(), 'request_count': 60, 'concurrency': 4, 'workload': 'metadata_only'}
    raw = json.dumps(report).encode()
    (tmp_path / 'report.json').write_bytes(raw)
    registry = tmp_path / 'release.json'
    registry.write_text(json.dumps({'schema_version': 'release-evidence-1.0', 'reports': {'load_test': {'path': 'report.json', 'sha256': hashlib.sha256(raw).hexdigest()}}}), encoding='utf-8')
    assert not checked_evidence(registry, now=now, kind='load_test', context='code')


@pytest.mark.parametrize('url', ['https://example.com', 'http://127.0.0.1?token=secret', 'http://secret@localhost', 'file:///tmp/test'])
def test_load_tester_refuses_remote_and_credential_urls(url):
    with pytest.raises(ValueError):
        measure(url)
