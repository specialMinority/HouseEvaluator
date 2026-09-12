import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

from backend.src.server import create_server
from backend.v2.approval import approved_segments, policy_digest, REQUIRED_MARKET_GATES
from backend.v2.models import ValidationError
from backend.v2.security import AccessPolicy

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
TOKEN = 'test-only-private-pilot-access-code-0000'


class AccessPolicyTest(unittest.TestCase):
    def test_constant_time_credentials_and_no_query_key(self):
        policy = AccessPolicy(TOKEN)
        for bad in (None, '', TOKEN, 'Bearer wrong', 'Basic ' + TOKEN, 'Bearer 한글'):
            self.assertFalse(policy.authenticated(bad))
        self.assertTrue(policy.authenticated('Bearer ' + TOKEN))
        for bad in ('short', 'x' * 31 + '\n', 'x' * 257):
            with self.assertRaises(ValidationError):
                AccessPolicy(bad)

    def test_rate_limits_bounded_and_expiring(self):
        tick = [0.0]
        policy = AccessPolicy(requests_per_minute=2, max_clients=2, clock=lambda: tick[0])
        self.assertTrue(policy.allow_request('a'))
        self.assertTrue(policy.allow_request('a'))
        self.assertFalse(policy.allow_request('a'))
        self.assertTrue(policy.allow_request('b'))
        self.assertFalse(policy.allow_request('c'))
        tick[0] = 60
        self.assertTrue(policy.allow_request('c'))
        self.assertTrue(policy.allow_request('a'))

    def test_rejected_credentials_have_a_separate_bounded_expiring_quota(self):
        tick = [0.0]
        policy = AccessPolicy(TOKEN, requests_per_minute=2, max_clients=1, clock=lambda: tick[0])
        self.assertTrue(policy.allow_request('proxy', authenticated=False))
        self.assertTrue(policy.allow_request('proxy', authenticated=False))
        self.assertFalse(policy.allow_request('proxy', authenticated=False))
        self.assertFalse(policy.allow_request('other-invalid-peer', authenticated=False))
        self.assertTrue(policy.allow_request('proxy', authenticated=True))
        self.assertTrue(policy.allow_request('proxy', authenticated=True))
        self.assertFalse(policy.allow_request('proxy', authenticated=True))
        self.assertFalse(policy.allow_request('other-valid-peer', authenticated=True))
        tick[0] = 60
        self.assertTrue(policy.allow_request('new-invalid-peer', authenticated=False))
        self.assertTrue(policy.allow_request('new-valid-peer', authenticated=True))


class PilotHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = create_server(port=0, db_path=Path(self.temp.name) / 'db.sqlite3', access_token=TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, path, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, raw, dict(response.getheaders())
        finally:
            connection.close()

    def test_static_access_form_and_authentication(self):
        status, html, _ = self.request('/frontend/v2/')
        self.assertEqual(status, 200)
        self.assertIn(b'access-panel', html)
        for endpoint in ('/api/v2/capabilities', '/api/v2/health', '/api/v2/readiness', '/api/v2/supply-status'):
            self.assertEqual(self.request(endpoint)[0], 401)
        self.assertEqual(self.request('/api/v2/capabilities?token=' + TOKEN)[0], 401)
        status, body, _ = self.request('/api/v2/capabilities', {'Authorization': 'Bearer ' + TOKEN})
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertFalse(json.loads(body)['market_data_available'])

    def test_rate_limit_cannot_be_changed_by_forwarded_header(self):
        self.server.access_policy = AccessPolicy(TOKEN, requests_per_minute=1)
        self.assertEqual(self.request('/api/v2/health', {'Authorization': 'Bearer ' + TOKEN})[0], 200)
        status, _, headers = self.request('/api/v2/health', {'Authorization': 'Bearer ' + TOKEN, 'X-Forwarded-For': '203.0.113.1'})
        self.assertEqual(status, 429)
        self.assertEqual(headers['Retry-After'], '60')

    def test_bad_tokens_from_proxy_do_not_exhaust_valid_users_quota(self):
        self.server.access_policy = AccessPolicy(TOKEN, requests_per_minute=2)
        for fake_ip in ('203.0.113.1', '203.0.113.2'):
            self.assertEqual(self.request('/api/v2/health', {
                'Authorization': 'Bearer incorrect', 'X-Forwarded-For': fake_ip,
            })[0], 401)
        status, _, headers = self.request('/api/v2/health', {'Authorization': 'Bearer incorrect'})
        self.assertEqual(status, 429)
        self.assertEqual(headers['Retry-After'], '60')
        valid = {'Authorization': 'Bearer ' + TOKEN}
        self.assertEqual(self.request('/api/v2/health', valid)[0], 200)
        self.assertEqual(self.request('/api/v2/health', valid)[0], 200)
        self.assertEqual(self.request('/api/v2/health', dict(valid, **{'X-Forwarded-For': '203.0.113.99'}))[0], 429)

    def test_pilot_startup_cannot_enable_demo_or_omit_access(self):
        for options in ({}, {'access_token': TOKEN, 'demo_enabled': True}, {'access_token': TOKEN, 'legacy_enabled': True}):
            with self.assertRaises(ValidationError):
                create_server(port=0, db_path=Path(self.temp.name) / 'blocked.sqlite3', pilot_mode=True, **options)

    def test_real_release_without_data_stays_not_ready(self):
        status, raw, _ = self.request('/api/v2/readiness', {'Authorization': 'Bearer ' + TOKEN})
        self.assertEqual(status, 503)
        report = json.loads(raw)
        self.assertFalse(report['ready'])
        self.assertIn('market_segments_validated', report['blockers'])
        self.assertIn('supplier_contracts_configured', report['blockers'])


class ApprovalEvidenceTest(unittest.TestCase):
    def test_tampered_smoke_failed_stale_reports_never_approve(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = {'schema_version': 'market-validation-1.0', 'report_kind': 'market',
                      'policy_version': 'direct-v2.0', 'policy_sha256': policy_digest(), 'source_ids': ['one'],
                      'generated_at': NOW.isoformat(), 'eligibility': {'passed': True},
                      'eligible_segments': ['tokyo/1K'], 'segments': {'tokyo/1K': {'eligible_for_approval': True, 'gates': {key: True for key in REQUIRED_MARKET_GATES}}}}
            entry = {'policy_version': 'direct-v2.0', 'approved': True, 'approved_by': 'fixture-operator',
                     'source_ids': ['one'], 'validated_at': NOW.isoformat(), 'expires_at': (NOW + timedelta(days=1)).isoformat(),
                     'report_path': 'report.json', 'segments': ['tokyo/1K']}
            registry = root / 'registry.json'
            def write(candidate, good_hash=True):
                raw = json.dumps(candidate).encode()
                (root / 'report.json').write_bytes(raw)
                registry.write_text(json.dumps({'schema_version': '2.0', 'reports': [{**entry, 'report_sha256': hashlib.sha256(raw).hexdigest() if good_hash else 'x' * 64}]}), encoding='utf-8')
            write(report)
            self.assertEqual(approved_segments(registry, {'one'}, NOW), {'tokyo/1K'})
            for changes in ({'report_kind': 'synthetic_smoke'}, {'eligibility': {'passed': False}},
                            {'policy_sha256': 'other'}, {'source_ids': ['different']}, {'eligible_segments': []},
                            {'generated_at': (NOW - timedelta(days=31)).isoformat()}, {'generated_at': (NOW + timedelta(seconds=1)).isoformat()}):
                write({**report, **changes})
                self.assertEqual(approved_segments(registry, {'one'}, NOW), set())
            write(report, good_hash=False)
            self.assertEqual(approved_segments(registry, {'one'}, NOW), set())
