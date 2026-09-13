"""Bounded local transport for personal samples and optional licensed feeds."""
from __future__ import annotations
import json
import logging
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from backend.v2.costs import calculate_costs
from backend.v2.models import ValidationError
from backend.v2.service import CITIES, ROOT, Runtime
from backend.v2.security import AccessPolicy
from backend.v2.personal import compare as compare_personal, validate_listing
from backend.v2.personal_jobs import SearchBusy, SearchJobs
from backend.v2 import public_search
from backend.v2.listing_import import ListingImportError, import_listing, import_sources, validate_payload as validate_import_payload

MAX_BODY = 65536
LOG = logging.getLogger(__name__)

class BodyError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("중복 JSON 키")
        result[key] = value
    return result

def _reject_constant(value):
    raise ValueError("유한한 JSON 숫자만 허용합니다.")

class _ApiHandler(BaseHTTPRequestHandler):
    server_version = "HouseEvaluator/2.0"
    sys_version = ""

    def setup(self):
        self._request_started = time.monotonic()
        self.request.settimeout(10)
        super().setup()
        self._timed_out = False
        self._deadline = threading.Timer(getattr(self.server, "request_deadline_seconds", 10), self._abort_slow_request)
        self._deadline.daemon = True
        self._deadline.start()

    def _abort_slow_request(self):
        self._timed_out = True
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError, OSError):
            pass

    def finish(self):
        self._deadline.cancel()
        try:
            super().finish()
        except (ConnectionError, TimeoutError, OSError):
            pass

    @property
    def runtime(self):
        return self.server.runtime

    @property
    def legacy_enabled(self):
        return getattr(self.server, "legacy_enabled", os.getenv("HOUSE_EVALUATOR_LEGACY") == "1")

    def _headers(self, status, content_type, size, location=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if status == 429:
            self.send_header("Retry-After", "60")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if location:
            self.send_header("Location", location)
        self.end_headers()

    def _send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_json_body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise BodyError(400, "하나의 유효한 Content-Length가 필요합니다.")
        length = int(lengths[0])
        if length > MAX_BODY:
            raise BodyError(413, "요청은 64 KiB 이하여야 합니다.")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise BodyError(415, "application/json 요청이 필요합니다.")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise BodyError(400, "올바른 JSON 객체가 필요합니다.") from error
        except TimeoutError as error:
            raise BodyError(408, "요청 수신 시간이 초과되었습니다.") from error
        if not isinstance(value, dict):
            raise BodyError(400, "JSON 객체가 필요합니다.")
        return value

    def _dispatch_error(self, error):
        if self._timed_out:
            return
        if isinstance(error, ListingImportError):
            self._send_json(error.status, {"error": error.code, "message": error.message})
        elif isinstance(error, BodyError):
            self._send_json(error.status, {"error": "invalid_body", "message": str(error)})
        elif isinstance(error, (ValidationError, ValueError)):
            self._send_json(400, {"error": "bad_request", "message": str(error)})
        elif isinstance(error, PermissionError):
            self._send_json(403, {"error": "forbidden", "message": str(error)})
        elif isinstance(error, SearchBusy):
            self._send_json(429, {"error": "search_busy", "message": "검색 작업이 진행 중입니다. 잠시 후 다시 시도하거나 직접 입력해 주세요."})
        elif isinstance(error, ConnectionError):
            return
        else:
            LOG.exception("Request failed")
            self._send_json(500, {"error": "internal_error", "message": "요청을 처리하지 못했습니다."})

    def do_HEAD(self):
        self.do_GET()

    def _guard(self, path):
        policy = getattr(self.server, "access_policy", None)
        if not policy or not path.startswith("/api/"):
            return True
        authenticated = policy.authenticated(self.headers.get("Authorization"))
        if not policy.allow_request(self.client_address[0], authenticated=authenticated):
            self._send_json(429, {"error": "rate_limited", "message": "요청이 많습니다. 1분 뒤 다시 시도해 주세요."})
            return False
        if not authenticated:
            self._send_json(401, {"error": "access_required", "message": "제한 공개용 접속 코드를 입력해 주세요."})
            return False
        return True

    def log_message(self, format, *args):
        # Request strings/query values may contain accidental secrets; omit them.
        LOG.info("http_request method=%s", self.command)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if not self._guard(path):
                return
            if path == "/healthz":
                # Process liveness only: no storage, market, auth, or release evidence.
                self._send_json(200, {"status": "ok"})
            elif path in ("/", "/index.html", "/frontend", "/frontend/") and not (self.legacy_enabled and path == "/frontend/"):
                self._headers(302, "text/plain", 0, "/frontend/" if self.legacy_enabled else "/frontend/v2/")
            elif path == "/api/v2/capabilities":
                self._send_json(200, self.runtime.capabilities())
            elif path == "/api/v2/personal/options":
                sources = import_sources()
                self._send_json(200, dict(public_search.options(), enabled=self.server.personal_enabled,
                                         search_enabled=self.server.public_search_enabled,
                                         import_enabled=self.server.public_search_enabled and bool(sources),
                                         import_sources=sources))
            elif path.startswith("/api/v2/personal/search/"):
                self._require_personal()
                result = self.server.search_jobs.get(path.rsplit("/", 1)[-1])
                self._send_json(200 if result else 404, result or {"error": "search_expired", "message": "검색 결과가 만료되었거나 없습니다. 다시 검색해 주세요."})
            elif path == "/api/v2/stations":
                query = parse_qs(parsed.query)
                self._send_json(200, self.runtime.stations(query.get("city", ["tokyo"])[0], mode=query.get("mode", ["market"])[0]))
            elif path == "/api/v2/health":
                self._send_json(200, {"status": "ok", "schema_version": "2.0", "storage": self.runtime.health()})
            elif path == "/api/v2/readiness":
                report = self.runtime.readiness(access_protected=self.server.access_policy.protected, legacy_enabled=self.legacy_enabled)
                self._send_json(200 if report["ready"] else 503, report)
            elif path == "/api/v2/supply-status":
                self._send_json(200, self.runtime.supply_status())
            elif path == "/api/v2/demo-subject":
                if not self.runtime.demo_enabled:
                    raise PermissionError("합성 시연이 비활성화되어 있습니다.")
                city = parse_qs(parsed.query).get("city", ["tokyo"])[0]
                if city not in {c["id"] for c in CITIES}:
                    raise ValidationError("지원되지 않는 도시입니다.")
                from backend.v2.demo import demo_subject
                self._send_json(200, {"subject": demo_subject(city)})
            elif path.rstrip("/") == "/api/municipalities" and self.legacy_enabled:
                self._legacy_municipalities(parsed.query)
            elif path.startswith("/api/"):
                self._send_json(404, {"error": "not_found"})
            else:
                self._serve_static(path)
        except Exception as error:
            self._dispatch_error(error)

    def _serve_static(self, raw_path):
        decoded = unquote(raw_path)
        if "\\" in decoded or "\x00" in decoded or any(p in (".", "..") for p in decoded.split("/")):
            self._send_json(404, {"error": "not_found"})
            return
        if decoded == "/frontend/v2":
            self._headers(302, "text/plain", 0, "/frontend/v2/")
            return
        relative = decoded.lstrip("/")
        if relative.endswith("/"):
            relative += "index.html"
        allowed = relative in {"frontend/v2/index.html", "frontend/v2/app.js", "frontend/v2/styles.css",
                               "frontend/v2/personal.html", "frontend/v2/personal.js", "frontend/v2/personal.css", "frontend/v2/licensed.html"}
        if relative == "frontend/v2/licensed.html":
            relative = "frontend/v2/index.html"
        elif relative == "frontend/v2/index.html" and getattr(self.server, "personal_enabled", False) and not self.runtime.demo_enabled:
            relative = "frontend/v2/personal.html"
        if self.legacy_enabled:
            allowed = allowed or relative in {
                "frontend/index.html", "frontend/src/main.js", "frontend/src/styles.css",
                "frontend/src/ui/app.js", "frontend/src/ui/formView.js", "frontend/src/ui/resultView.js",
                "frontend/src/ui/scoreComponents.js", "frontend/src/ui/riskTiers.js",
                "frontend/src/ui/loadingOverlay.js", "frontend/src/ui/benchmarkSection.js",
                "frontend/src/lib/utils.js", "frontend/src/lib/storage.js", "frontend/src/lib/specLoader.js",
                "frontend/src/lib/benchmarkMeta.js", "frontend/src/lib/api.js",
                "frontend/src/fixtures/mock_response.json", "frontend/src/fixtures/mock_inputs.json",
                "spec_bundle_v0.1.1/S1_InputSchema.json", "spec_bundle_v0.1.2/S1_InputSchema.json",
                "image/Searching.png", "image/Error.png",
            }
        file = (ROOT / relative).resolve()
        if not allowed or not file.is_relative_to(ROOT.resolve()) or not file.is_file():
            self._send_json(404, {"error": "not_found"})
            return
        data = file.read_bytes()
        self._headers(200, (mimetypes.guess_type(file.name)[0] or "application/octet-stream") + "; charset=utf-8", len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self):
        try:
            path = urlparse(self.path).path.rstrip("/")
            if not self._guard(path):
                return
            if path == "/api/v2/personal/import":
                self._require_personal()
                if not self.server.public_search_enabled:
                    self._send_json(403, {"error": "import_disabled", "message": "URL 자동 입력을 잠시 중단했습니다. 직접 입력해 주세요."})
                    return
                payload = self._read_json_body()
                validate_import_payload(payload)
                deadline = self._request_started + getattr(self.server, "request_deadline_seconds", 10) - .5
                self._send_json(200, self.server.import_fn(payload, deadline=deadline))
            elif path == "/api/v2/personal/search":
                self._require_personal()
                if not self.server.public_search_enabled:
                    self._send_json(503, {"error": "public_search_disabled", "message": "자동 검색을 잠시 중단했습니다. 직접 입력으로 비교할 수 있습니다."})
                    return
                payload = self._read_json_body()
                if set(payload) != {"subject"}:
                    raise ValidationError("검색할 subject만 전달해 주세요.")
                subject = validate_listing(payload["subject"], subject=True)
                self._send_json(202, self.server.search_jobs.start(subject))
            elif path == "/api/v2/personal/compare":
                self._require_personal()
                self._send_json(200, compare_personal(self._read_json_body()))
            elif path.startswith("/api/v2/personal/search/") and path.endswith("/cancel"):
                self._require_personal()
                self._read_json_body()
                cancelled = self.server.search_jobs.cancel(path.split("/")[-2])
                self._send_json(200, {"cancelled": cancelled})
            elif path in ("/api/evaluate", "/api/parse-url"):
                if path == "/api/parse-url" or not self.legacy_enabled:
                    self._send_json(410, {"error": "retired_endpoint", "message": "v2 개인 비교 화면의 공개 검색·직접 입력을 사용해 주세요."})
                    return
                from backend.src.evaluate import evaluate
                self._send_json(200, evaluate(self._read_json_body()))
            elif path in ("/api/v2/evaluate", "/api/v2/costs"):
                if path.endswith("/evaluate") and getattr(self.server, "pilot_mode", False):
                    report = self.runtime.readiness(access_protected=self.server.access_policy.protected, legacy_enabled=self.legacy_enabled)
                    if not report["ready"]:
                        self._send_json(503, {"error": "release_not_ready", "message": "실제 시장 검증과 운영 점검을 완료한 뒤 비교를 공개합니다."})
                        return
                payload = self._read_json_body()
                result = self.runtime.evaluate(payload) if path.endswith("/evaluate") else calculate_costs(payload)
                self._send_json(200, result)
            else:
                self._send_json(404, {"error": "not_found"})
        except Exception as error:
            self._dispatch_error(error)

    def _require_personal(self):
        if not getattr(self.server, "personal_enabled", False):
            raise PermissionError("개인 비교가 비활성화되어 있습니다.")

    def _legacy_municipalities(self, query):
        from backend.src.url_resolver import get_chintai_pref_municipality_map
        pref = parse_qs(query).get("prefecture", [""])[0].strip().lower()
        if not pref:
            raise ValidationError("prefecture가 필요합니다.")
        mapping, cached = get_chintai_pref_municipality_map(pref, timeout=5)
        if not mapping:
            self._send_json(503, {"error": "unavailable", "message": "지역 목록을 불러오지 못했습니다."})
            return
        municipalities = [name for name, _ in sorted(mapping.items(), key=lambda kv: (kv[1], kv[0]))]
        self._send_json(200, {"prefecture": pref, "source": "chintai_area_index", "cached": bool(cached), "count": len(municipalities), "municipalities": municipalities})

class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, max_workers=16):
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(.1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                # Let the peer receive the rejection before closing a socket
                # containing unread request bytes (which can otherwise reset it).
                request.shutdown(socket.SHUT_WR)
                deadline, remaining = time.monotonic() + .05, MAX_BODY + 8192
                while remaining and (left := deadline - time.monotonic()) > 0:
                    request.settimeout(left)
                    chunk = request.recv(min(4096, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_close(self):
        jobs = getattr(self, 'search_jobs', None)
        if jobs:
            jobs.close()
        super().server_close()

def create_server(host="127.0.0.1", port=8000, *, db_path=None, demo_enabled=False, registry_path=None, legacy_enabled=False,
                  suppliers_path=None, state_db=None, release_evidence_path=None, access_token=None, requests_per_minute=120, pilot_mode=False,
                  personal_enabled=True, public_search_enabled=True, search_fn=None, import_fn=None):
    import_sources()  # Reject invalid operator configuration before opening a socket.
    access_policy = AccessPolicy(access_token, requests_per_minute=requests_per_minute)
    if pilot_mode and (not access_policy.protected or demo_enabled or legacy_enabled or not suppliers_path):
        raise ValidationError("제한 공개 모드에는 접속 코드와 공급 설정이 필요하며 시연·레거시는 꺼야 합니다.")
    runtime = Runtime(db_path, demo_enabled=demo_enabled, registry_path=registry_path, suppliers_path=suppliers_path,
                      state_db=state_db, release_evidence_path=release_evidence_path)
    httpd = BoundedServer((host, port), _ApiHandler)
    httpd.runtime = runtime
    httpd.legacy_enabled = legacy_enabled
    httpd.access_policy = access_policy
    httpd.pilot_mode = pilot_mode
    # Licensed pilot gates cannot be bypassed through personal endpoints.
    httpd.personal_enabled = personal_enabled and not pilot_mode
    httpd.public_search_enabled = public_search_enabled and httpd.personal_enabled
    httpd.search_jobs = SearchJobs(search_fn or public_search.search)
    httpd.import_fn = import_fn or import_listing
    return httpd

def serve(host="127.0.0.1", port=8000):
    httpd = create_server(host, port, demo_enabled=os.getenv("HOUSE_EVALUATOR_DEMO") == "1",
                         registry_path=os.getenv("HOUSE_EVALUATOR_VALIDATION_REGISTRY"),
                         legacy_enabled=os.getenv("HOUSE_EVALUATOR_LEGACY") == "1",
                         suppliers_path=os.getenv("HOUSE_EVALUATOR_SUPPLIERS"), state_db=os.getenv("HOUSE_EVALUATOR_INGESTION_STATE"),
                         release_evidence_path=os.getenv("HOUSE_EVALUATOR_RELEASE_EVIDENCE"), access_token=os.getenv("HOUSE_EVALUATOR_ACCESS_TOKEN") or None,
                         requests_per_minute=int(os.getenv("HOUSE_EVALUATOR_REQUESTS_PER_MINUTE", "120")), pilot_mode=os.getenv("HOUSE_EVALUATOR_PILOT") == "1",
                         personal_enabled=os.getenv("HOUSE_EVALUATOR_PERSONAL", "1") == "1",
                         public_search_enabled=os.getenv("HOUSE_EVALUATOR_PUBLIC_SEARCH", "1") == "1")
    print(f"HouseEvaluator v2: http://{host}:{port}/frontend/v2/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    serve(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
