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
from urllib.parse import parse_qs, urlparse

from backend.src.evaluate import InputValidationError, ROOT_DIR, evaluate
from backend.src.url_resolver import get_chintai_pref_municipality_map
try:
    from backend.src.suumo_url_parser import parse_suumo_url
    _SUUMO_PARSER_AVAILABLE = True
except Exception:
    _SUUMO_PARSER_AVAILABLE = False


class _ApiHandler(SimpleHTTPRequestHandler):
    server_version = "wh-eval/0.1"

    def __init__(self, *args, **kwargs):  # noqa: ANN002,ANN003
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/api/municipalities", "/api/municipalities/"):
            self._handle_municipalities(parsed.query)
            return

        if path in ("/", "/index.html"):
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

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/api/evaluate", "/api/evaluate/"):
            self._handle_evaluate()
        elif path in ("/api/parse-url", "/api/parse-url/"):
            self._handle_parse_url()
        else:
            self._send_json(404, {"error": "not_found"})

    def _handle_municipalities(self, query: str) -> None:
        qs = parse_qs(str(query or ""))
        pref = None
        if "prefecture" in qs and qs["prefecture"]:
            pref = str(qs["prefecture"][0] or "").strip().lower()
        if not pref:
            self._send_json(400, {"error": "bad_request", "message": "Missing 'prefecture' query param"})
            return

        mapping, cached = get_chintai_pref_municipality_map(pref, timeout=12)
        if not mapping:
            self._send_json(
                503,
                {
                    "error": "unavailable",
                    "message": "Failed to load municipality list from CHINTAI (WAF/JS challenge or fetch/parsing error)",
                },
            )
            return

        # Stable order: by numeric code, then name.
        items = sorted(mapping.items(), key=lambda kv: (kv[1], kv[0]))
        municipalities = [name for (name, _code) in items]
        self._send_json(
            200,
            {
                "prefecture": pref,
                "source": "chintai_area_index",
                "cached": bool(cached),
                "count": int(len(municipalities)),
                "municipalities": municipalities,
            },
        )

    def _handle_evaluate(self) -> None:
        try:
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                raise InputValidationError("Request body must be a JSON object")
            result = evaluate(payload)
            self._send_json(200, result)
        except InputValidationError as e:
            self._send_json(400, {"error": "bad_request", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": "internal_error", "message": str(e)})

    def _handle_parse_url(self) -> None:
        if not _SUUMO_PARSER_AVAILABLE:
            self._send_json(503, {"error": "unavailable", "message": "URL parser module not loaded"})
            return
        try:
            body = self._read_json_body()
            url = body.get("url", "") if isinstance(body, dict) else ""
            if not url or not isinstance(url, str):
                self._send_json(400, {"error": "bad_request", "message": "Missing 'url' field"})
                return
            parsed = parse_suumo_url(url)
            if "_error" in parsed:
                self._send_json(422, {"error": "parse_failed", "message": parsed["_error"]})
            else:
                self._send_json(200, {"fields": parsed, "source_url": url})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": "internal_error", "message": str(e)})


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    httpd = HTTPServer((host, port), _ApiHandler)
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    # Default-on for the interactive UI/server run. Keep `evaluate()` offline by default
    # so unit tests and scripts stay deterministic unless explicitly enabled.
    os.environ.setdefault("SUUMO_LIVE", "1")
    os.environ.setdefault("LIVE_PROVIDERS", "chintai,suumo")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    serve(host=host, port=port)
