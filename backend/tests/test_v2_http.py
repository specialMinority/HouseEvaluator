import json
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

from backend.src.server import BoundedServer, _ApiHandler, create_server
from backend.v2.demo import demo_subject


class V2HTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = create_server(port=0, db_path=Path(cls.temp.name) / 'http.sqlite3', demo_enabled=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def request(self, method, path, payload=None, raw=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
            conn.request(method, path, body=data, headers=headers or ({'Content-Type': 'application/json'} if data is not None else {}))
            response = conn.getresponse()
            body = response.read()
            parsed = json.loads(body) if body and 'application/json' in (response.getheader('Content-Type') or '') else body
            return response.status, parsed, dict(response.getheaders())
        finally:
            conn.close()

    def test_public_routes_and_no_repository_disclosure(self):
        status, _, headers = self.request('GET', '/')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/frontend/v2/')
        for path in ('/.git/config', '/backend/src/server.py', '/docs/PRODUCT_PLAN_V2.md', '/.runtime/v2.sqlite3',
                     '/agents/agent_D_benchmark_data/out/benchmark_rent_raw.json', '/frontend/v2/../app.js',
                     '/frontend/v2/%2e%2e/app.js', '/frontend/v2/%5cindex.html', '/frontend/v2/missing.html'):
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(method, path)[0], 404)
        status, body, headers = self.request('GET', '/frontend/v2/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!', body)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('HEAD', '/frontend/v2/')[1], b'')

    def test_legacy_endpoints_retired(self):
        for endpoint in ('/api/evaluate', '/api/parse-url'):
            self.assertEqual(self.request('POST', endpoint, {'url': 'http://127.0.0.1'})[0], 410)

    def test_no_data_and_explicit_demo(self):
        status, capability, _ = self.request('GET', '/api/v2/capabilities')
        self.assertEqual(status, 200)
        self.assertEqual(len(capability['cities']), 3)
        self.assertFalse(capability['market_data_available'])
        subject = demo_subject('fukuoka')
        status, market, _ = self.request('POST', '/api/v2/evaluate', {'subject': subject})
        self.assertEqual(status, 200)
        self.assertFalse(market['is_demo'])
        self.assertIsNone(market['benchmark_yen'])
        status, demo, _ = self.request('POST', '/api/v2/evaluate', {'subject': subject, 'mode': 'demo'})
        self.assertEqual(status, 200)
        self.assertTrue(demo['is_demo'])
        self.assertIsNone(demo['judgment'])
        self.assertEqual(demo['versions']['schema'], '2.0')

    def test_body_rejections_and_unknown_fields(self):
        for raw in (b'{"mode":"market","mode":"demo"}', b'{"value":NaN}', b'[]', b'{', b'\xff'):
            self.assertEqual(self.request('POST', '/api/v2/evaluate', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/api/v2/evaluate', {'mode': []})[0], 400)
        self.assertEqual(self.request('POST', '/api/v2/evaluate', {'subject': demo_subject('tokyo'), 'validated_segments': ['tokyo/1K']})[0], 400)
        self.assertEqual(self.request('POST', '/api/v2/evaluate', raw=b'x' * 65537)[0], 413)
        self.assertEqual(self.request('POST', '/api/v2/evaluate', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)

    def test_health_sanitized_and_concurrent_reads(self):
        def fetch(_):
            return self.request('GET', '/api/v2/health')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, range(12)))
        for status, body, _ in results:
            self.assertEqual(status, 200)
            self.assertEqual(body['status'], 'ok')
            self.assertNotIn(self.temp.name, json.dumps(body))

    def test_worker_saturation_rejected(self):
        server = BoundedServer(('127.0.0.1', 0), _ApiHandler, max_workers=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server._slots.acquire()
        try:
            with socket.create_connection(server.server_address, timeout=3) as conn:
                conn.sendall(b'GET / HTTP/1.0\r\n\r\n')
                self.assertIn(b'503', conn.recv(1024))
        finally:
            server._slots.release()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
