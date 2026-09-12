"""Small, anonymous public-page reads; no redirects, credentials, or proxies.

This transport is deliberately limited to SUUMO rental search/detail pages. It
does not grant redistribution rights or establish that a room is still vacant.
"""

from dataclasses import dataclass
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
