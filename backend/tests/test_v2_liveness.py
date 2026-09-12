"""Liveness stays independent of data, credentials, and market release gates."""
import json
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from backend.src.server import create_server


class LivenessTest(unittest.TestCase):
    def test_liveness_survives_auth_exhaustion_and_storage_failure(self):
        with TemporaryDirectory() as directory:
            server = create_server(port=0, db_path=Path(directory) / 'test.sqlite3',
                                   access_token='test-only-liveness-token-0123456789', requests_per_minute=1)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, method='GET'):
                connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()
            try:
                self.assertEqual(request('/api/v2/capabilities')[0], 401)
                self.assertEqual(request('/api/v2/capabilities')[0], 429)
                with patch.object(server.runtime, 'health', side_effect=RuntimeError('storage failed')), patch.object(server.runtime, 'readiness', side_effect=RuntimeError('release failed')):
                    status, body = request('/healthz')
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body), {'status': 'ok'})
                    self.assertEqual(request('/healthz', 'HEAD'), (200, b''))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
