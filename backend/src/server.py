"""HTTP server: separates transport layer from evaluation logic.

Entrypoint for Cloud Run / local development:
    python -m backend.src.server
"""
from __future__ import annotations

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Final

from backend.src.evaluate import InputValidationError, ROOT_DIR, evaluate


class _ApiHandler(SimpleHTTPRequestHandler):
    server_version = "wh-eval/0.1"

    def __init__(self, *args, **kwargs):  # noqa: ANN002,ANN003
        # Serve static files from repo root so frontend can fetch specs/benchmark data
        # via same-origin paths (e.g. /spec_bundle_v0.1.2/...).
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/frontend/")
            self.end_headers()
            return
        return super().do_GET()

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/evaluate":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise InputValidationError("Request body must be a JSON object")
            result = evaluate(payload)
            self._send_json(200, result)
        except InputValidationError as e:
            self._send_json(400, {"error": "bad_request", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": "internal_error", "message": str(e)})


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    httpd = HTTPServer((host, port), _ApiHandler)
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    serve(host=host, port=port)
