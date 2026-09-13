"""Small, process-local controls for a single-instance private pilot."""
from collections import OrderedDict
import hmac
import threading
import time
import unicodedata
from urllib.parse import quote

from .models import ValidationError


def access_code_header_value(code, *, allow_passphrase=False):
    """Return the ASCII credential after ``Bearer ``, never decode a request.

    Strong ASCII tokens keep their existing literal wire representation. The
    personal-only opt-in normalizes Unicode to NFC and matches JavaScript's
    encodeURIComponent for the entire code when it contains non-ASCII text.
    """
    if not allow_passphrase:
        if not isinstance(code, str) or not 32 <= len(code) <= 256 or any(not 33 <= ord(c) <= 126 for c in code):
            raise ValidationError("접속 코드는 공백 없는 ASCII 문자 32~256자로 설정하세요.")
        return code
    if not isinstance(code, str):
        raise ValidationError("개인 접속 코드는 공백·제어 문자 없는 1~256자로 설정하세요.")
    normalized = unicodedata.normalize('NFC', code)
    if (not 1 <= len(normalized) <= 256 or any(char.isspace() or unicodedata.category(char) in ('Cc', 'Cs', 'Cf') for char in normalized)):
        raise ValidationError("개인 접속 코드는 공백·제어 문자 없는 1~256자로 설정하세요.")
    if normalized.isascii():
        return normalized
    return quote(normalized, safe="~()*!.'-_", encoding='utf-8', errors='strict')


class AccessPolicy:
    def __init__(self, token=None, *, requests_per_minute=120, max_clients=4096, clock=time.monotonic,
                 allow_passphrase=False):
        wire_value = access_code_header_value(token, allow_passphrase=allow_passphrase) if token is not None else None
        if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 10000:
            raise ValidationError("분당 요청 한도는 1~10000이어야 합니다.")
        self._token = wire_value
        self.limit = requests_per_minute
        self.max_clients = max_clients
        self.clock = clock
        self._clients = OrderedDict()
        self._unauthenticated_clients = OrderedDict()
        self._lock = threading.Lock()

    @property
    def protected(self):
        return self._token is not None

    def authenticated(self, header):
        if not self.protected:
            return True
        if not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        try:
            return hmac.compare_digest(header[7:].encode("ascii"), self._token.encode("ascii"))
        except UnicodeError:
            return False

    def allow_request(self, client_ip, *, authenticated=True):
        # Never trust a user-supplied X-Forwarded-For header for identity.
        # A reverse proxy may be every user's TCP peer. Invalid credentials
        # must not consume the shared allowance for authenticated requests.
        now = self.clock()
        with self._lock:
            clients = self._clients if authenticated else self._unauthenticated_clients
            while clients:
                first, (start, _) = next(iter(clients.items()))
                if now - start < 60:
                    break
                del clients[first]
            if client_ip not in clients:
                if len(clients) >= self.max_clients:
                    return False
                clients[client_ip] = (now, 1)
                return True
            start, count = clients[client_ip]
            if count >= self.limit:
                return False
            clients[client_ip] = (start, count + 1)
            return True
