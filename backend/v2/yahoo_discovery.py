"""Navigation restricted to Yahoo! Real Estate's observed public rental URLs."""
import re
from urllib.parse import parse_qsl, urlsplit


ORIGIN = 'https://realestate.yahoo.co.jp'


def checked_url(url):
    """Validate the source-specific path before DNS or any network request."""
    from .public_fetch import PublicFetchError
    try:
        if (not isinstance(url, str) or len(url) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in url)
                or any(c in url for c in ('\\', '#', '%'))):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.netloc != 'realestate.yahoo.co.jp'
                or parts.username is not None or parts.password is not None):
            raise ValueError()
        if parts.path == '/robots.txt' or re.fullmatch(r'/rent/detail/_?[0-9a-f]{40}/', parts.path):
            if '?' in url:
                raise ValueError()
            return parts
        ward = re.fullmatch(r'/rent/search/03/13/(131[0-9]{2})/', parts.path)
        station = re.fullmatch(r'/rent/search/station/(?:[1-9][0-9]{3}|[1-9][0-9]{6})/', parts.path)
        if not station and not (ward and 13101 <= int(ward[1]) <= 13123):
            raise ValueError()
        if '?' in url:
            pairs = parse_qsl(parts.query, strict_parsing=True, keep_blank_values=True)
            if len(pairs) != 1 or pairs[0][0] != 'page' or pairs[0][1] not in ('1', '2', '3'):
                raise ValueError()
        return parts
    except (ValueError, TypeError, UnicodeError):
        raise PublicFetchError('endpoint_denied') from None
