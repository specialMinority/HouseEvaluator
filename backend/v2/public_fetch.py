"""Small, anonymous public-page reads; no redirects, credentials, or proxies.

This transport is deliberately limited to SUUMO rental search/detail pages. It
does not grant redistribution rights or establish that a room is still vacant.
"""

from dataclasses import dataclass
import html
import http.client
import re
import socket
import threading
import time
from urllib.parse import parse_qsl, urlsplit
import zlib

from .supply import SupplyError, _bounded_dns, _PinnedHTTPSConnection


USER_AGENT = "HouseEvaluator-PublicComparison/2.0"
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
MAX_ERROR_DIAGNOSTIC_BYTES = 8 * 1024
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60
REQUEST_SECONDS = 9
_QUERY = frozenset(("ar", "bs", "ta", "sc", "md", "mb", "mt", "pc", "cb", "ct", "et", "cn", "rn", "ek", "ts", "page", "ra"))


class PublicFetchError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PublicResponse:
    status: int
    body: bytes = b""
    content_type: str = "text/html; charset=UTF-8"
    diagnostics: dict | None = None


def safe_diagnostics(value):
    """Only fixed, non-identifying observations can leave the transport."""
    if not isinstance(value, dict):
        return None
    result = {}
    if value.get("classification") in ("access_denied", "challenge", "service_unavailable", "unclassified"):
        result["classification"] = value["classification"]
    if value.get("body_inspection") in ("complete", "limited", "unavailable"):
        result["body_inspection"] = value["body_inspection"]
    if value.get("edge_hint") in ("cloudflare", "cloudfront", "akamai"):
        result["edge_hint"] = value["edge_hint"]
    retry = value.get("retry_after_seconds")
    if type(retry) is int and 0 <= retry <= MAX_RETRY_AFTER_SECONDS:
        result["retry_after_seconds"] = retry
    return result or None


def _error_classification(body):
    # Inspect only bounded visible text. Script URLs, headers and opaque IDs
    # neither identify a cause nor belong in an operational report.
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"<!--.*?(?:-->|$)|<(script|style)\b[^>]*>.*?(?:</\1\s*>|$)", " ", text, flags=re.I | re.S)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    text = " ".join(text.lower().split())
    if any(marker in text for marker in ("verify you are human", "verify that you are human", "captcha", "ロボットではないことを確認")):
        return "challenge"
    if any(marker in text for marker in ("access denied", "access forbidden", "request blocked", "アクセス制限", "お客様のアクセスを制限", "不正なアクセスを検知")):
        return "access_denied"
    if any(marker in text for marker in ("service unavailable", "service temporarily unavailable", "temporarily unavailable", "一時的にサービスをご利用いただけません")):
        return "service_unavailable"
    return "unclassified"


def _error_diagnostics(response, connection, *, deadline, check_time):
    """Best-effort 5xx inspection must never replace the received HTTP status.

    Both bytes read and visible text examined are capped at 8 KiB. Compressed,
    non-text or malformed responses remain unclassified; no raw values escape.
    The caller's existing socket/watchdog deadline also covers this read.
    """
    result = {"classification": "unclassified", "body_inspection": "unavailable"}
    try:
        # Fixed branding hints are observations, not attribution of the error
        # to that provider. Never return an arbitrary Server/Via value or ID.
        server = response.getheader("Server", "")
        via = response.getheader("Via", "")
        brands = {"cloudflare": "cloudflare", "cloudfront": "cloudfront", "akamaighost": "akamai"}
        if isinstance(server, str) and len(server) <= 128 and server.strip().lower() in brands:
            result["edge_hint"] = brands[server.strip().lower()]
        elif isinstance(via, str) and len(via) <= 1024 and re.search(r"\b[0-9a-z.-]+\.cloudfront\.net\s+\(cloudfront\)", via, re.I):
            result["edge_hint"] = "cloudfront"
        retry = response.getheader("Retry-After")
        if isinstance(retry, str) and re.fullmatch(r"[0-9]{1,5}", retry.strip()):
            seconds = int(retry.strip())
            if seconds <= MAX_RETRY_AFTER_SECONDS:
                result["retry_after_seconds"] = seconds
        check_time()
        content_type = response.getheader("Content-Type", "")
        encoding = response.getheader("Content-Encoding", "identity")
        if (not isinstance(content_type, str) or content_type.split(";", 1)[0].strip().lower()
                not in ("text/html", "text/plain", "application/xhtml+xml")
                or not isinstance(encoding, str) or encoding.strip().lower() != "identity"):
            return result
        length = response.getheader("Content-Length")
        if length is not None and (not isinstance(length, str) or not re.fullmatch(r"[0-9]{1,10}", length)):
            return result
        chunks, size, complete = [], 0, False
        while size < MAX_ERROR_DIAGNOSTIC_BYTES:
            check_time()
            if connection.sock:
                connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            chunk = response.read(min(2048, MAX_ERROR_DIAGNOSTIC_BYTES - size))
            check_time()
            if not chunk:
                complete = True
                break
            size += len(chunk)
            chunks.append(chunk)
        check_time()
        result["classification"] = _error_classification(b"".join(chunks))
        result["body_inspection"] = "complete" if complete else "limited"
    except Exception:
        # This optional observation cannot hide an already received 5xx behind
        # a decode/read/timeout error or leak an exception's sensitive text.
        pass
    return result


def checked_url(url):
    """Validate an exact endpoint; query values are bounded numeric filters."""
    try:
        if not isinstance(url, str) or len(url) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in url):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "suumo.jp" or parts.hostname != "suumo.jp"
                or parts.username is not None or parts.password is not None or parts.fragment or "\\" in url):
            raise ValueError()
        if re.fullmatch(r"/chintai/jnc_[0-9]{8,16}/", parts.path):
            if parts.query and not re.fullmatch(r"bc=[0-9]{8,16}", parts.query):
                raise ValueError()
        elif parts.path == "/robots.txt" or re.fullmatch(r"/chintai/bc_[0-9]{8,16}/", parts.path):
            if parts.query:
                raise ValueError()
        elif re.fullmatch(r"/chintai/(?:tokyo|osaka|fukuoka)/(?:sc_[a-z]+|ek_[0-9]{5}|ensen|en_[a-z0-9_]{1,80})/", parts.path):
            if parts.query:
                raise ValueError()
        elif parts.path == "/jj/chintai/ichiran/FR301FC001/":
            pairs = parse_qsl(parts.query, strict_parsing=True, keep_blank_values=True)
            if not pairs or len(pairs) > len(_QUERY) or len({k for k, _ in pairs}) != len(pairs):
                raise ValueError()
            formats = {'rn': r'[0-9]{4}', 'ek': r'[0-9]{9}', 'ra': r'[0-9]{3}', 'ts': r'[123]', 'page': r'[123]'}
            if any(k not in _QUERY or not re.fullmatch(formats.get(k, r"[0-9]{1,7}(?:\.[0-9]{1,2})?"), v) for k, v in pairs):
                raise ValueError()
            # SUUMO's form requires these values even for an unrestricted
            # price search. Never permit a target-dependent rent boundary.
            values = dict(pairs)
            if ('rn' in values) != ('ek' in values) or ('ek' in values and not values['ek'].startswith(values['rn'])):
                raise ValueError()
            if values.get("cb", "0.0") != "0.0" or values.get("ct", "9999999") != "9999999":
                raise ValueError()
        else:
            raise ValueError()
        return parts
    except (ValueError, UnicodeError):
        raise PublicFetchError("endpoint_denied") from None


def fetch_public(url, *, deadline):
    parts = checked_url(url)
    local_deadline = min(deadline, time.monotonic() + REQUEST_SECONDS)
    remaining = local_deadline - time.monotonic()
    if remaining <= 0:
        raise PublicFetchError("timeout")
    try:
        address = _bounded_dns("suumo.jp", 443, remaining)[0]
    except SupplyError as exc:
        raise PublicFetchError(exc.code) from None
    remaining = local_deadline - time.monotonic()
    if remaining <= 0:
        raise PublicFetchError("timeout")
    connection = _PinnedHTTPSConnection("suumo.jp", address, timeout=remaining)
    connection._absolute_deadline = local_deadline
    expired = threading.Event()

    def interrupt():
        expired.set()
        transport = getattr(connection, "_transport", None) or getattr(connection, "sock", None)
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except OSError:
            pass

    def check_time():
        if expired.is_set() or time.monotonic() >= local_deadline:
            raise PublicFetchError("timeout")

    watchdog = threading.Timer(remaining, interrupt)
    watchdog.daemon = True
    watchdog.start()
    limit = MAX_ROBOTS_BYTES if parts.path == "/robots.txt" else MAX_PAGE_BYTES
    try:
        connection.request("GET", parts.path + ("?" + parts.query if parts.query else ""), headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9", "Accept-Encoding": "identity",
        })
        check_time()
        response = connection.getresponse()
        check_time()
        if 300 <= response.status < 400:
            raise PublicFetchError("redirect_denied")
        if 500 <= response.status <= 599:
            diagnostics = _error_diagnostics(response, connection, deadline=local_deadline, check_time=check_time)
            return PublicResponse(response.status, diagnostics=diagnostics)
        if response.status != 200:
            return PublicResponse(response.status)
        content_type = response.getheader("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() not in ("text/html", "text/plain", "application/xhtml+xml"):
            raise PublicFetchError("content_type_denied")
        encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        if encoding not in ("identity", "gzip"):
            raise PublicFetchError("encoding_denied")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise PublicFetchError("payload_too_large")
        chunks, size = [], 0
        while True:
            check_time()
            if connection.sock:
                connection.sock.settimeout(max(0.001, local_deadline - time.monotonic()))
            chunk = response.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise PublicFetchError("payload_too_large")
            chunks.append(chunk)
        check_time()
        body = b"".join(chunks)
        if encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decoder.decompress(body, limit + 1)
            if len(body) > limit or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                raise PublicFetchError("payload_too_large")
        check_time()
        return PublicResponse(200, body, content_type)
    except (OSError, http.client.HTTPException, zlib.error):
        raise PublicFetchError("timeout" if expired.is_set() else "network_failed") from None
    finally:
        watchdog.cancel()
        connection.close()
