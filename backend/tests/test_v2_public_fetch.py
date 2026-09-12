from contextlib import ExitStack
import gzip
import io
import threading
import time
import unittest
from unittest.mock import patch

from backend.v2 import public_fetch as transport
from backend.v2.supply import SupplyError


DETAIL = 'https://suumo.jp/chintai/bc_10000001/'
SEARCH = 'https://suumo.jp/jj/chintai/ichiran/FR301FC001/'


class Response:
    def __init__(self, body=b'<html>ok</html>', status=200, headers=None, *, chunk_size=None, read_hook=None):
        self.status = status
        self.headers = {'Content-Type': 'text/html; charset=UTF-8', **(headers or {})}
        self.body = io.BytesIO(body)
        self.reads = 0
        self.chunk_size, self.read_hook = chunk_size, read_hook

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amount):
        self.reads += 1
        if self.read_hook:
            self.read_hook()
        return self.body.read(min(amount, self.chunk_size) if self.chunk_size else amount)


class Connection:
    sock = None

    def __init__(self, response):
        self.response, self.requests, self.closed = response, [], threading.Event()

    def request(self, method, path, *, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed.set()


class PublicFetchProtectionTest(unittest.TestCase):
    def fetch(self, response=None, *, connection=None, url=DETAIL, deadline=None, clock=None, **settings):
        response = response or Response()
        connection = connection or Connection(response)
        with ExitStack() as stack:
            dns = stack.enter_context(patch.object(transport, '_bounded_dns', return_value=['203.0.113.10']))
            factory = stack.enter_context(patch.object(transport, '_PinnedHTTPSConnection', return_value=connection))
            for name, value in settings.items():
                stack.enter_context(patch.object(transport, name, value))
            if clock:
                stack.enter_context(patch.object(transport.time, 'monotonic', clock))
            result = transport.fetch_public(url, deadline=deadline if deadline is not None else time.monotonic() + 2)
        return result, connection, dns, factory

    def test_endpoint_allowlist_rejects_host_confusion_credentials_and_unrelated_paths(self):
        for url in (
            'http://suumo.jp/chintai/bc_10000001/',
            'https://suumo.jp.evil.invalid/chintai/bc_10000001/',
            'https://evil.invalid/?source=suumo.jp/chintai/bc_10000001/',
            'https://suumo.jp@127.0.0.1/chintai/bc_10000001/',
            'https://suumo.jp:444/chintai/bc_10000001/',
            'https://suumo.jp\\evil.invalid/chintai/bc_10000001/',
            DETAIL + '#private', DETAIL + '?token=secret',
            'https://suumo.jp/chintai/../private', 'file:///private',
        ):
            with self.subTest(url=url), self.assertRaises(transport.PublicFetchError) as raised:
                transport.checked_url(url)
            self.assertEqual(raised.exception.code, 'endpoint_denied')
        for url in (DETAIL, 'https://suumo.jp/robots.txt',
                    'https://suumo.jp/chintai/jnc_10000001/?bc=10000002'):
            self.assertEqual(transport.checked_url(url).hostname, 'suumo.jp')

    def test_search_cannot_silently_add_a_target_price_window_or_duplicate_parameters(self):
        for query in ('cb=8.0&ct=9999999', 'cb=0.0&ct=10.0', 'cb=0.0&cb=1.0',
                      'cb=0.0&token=secret', 'md=02&sc=-13104', 'md=02&sc=13104&sc=13105'):
            with self.subTest(query=query), self.assertRaises(transport.PublicFetchError):
                transport.checked_url(SEARCH + '?' + query)
        self.assertEqual(transport.checked_url(SEARCH + '?md=02&sc=13104&cb=0.0&ct=9999999').hostname, 'suumo.jp')

    def test_station_landing_path_is_exact_and_has_no_free_query(self):
        for city in ('tokyo', 'osaka', 'fukuoka'):
            self.assertEqual(transport.checked_url(f'https://suumo.jp/chintai/{city}/ek_13900/').hostname, 'suumo.jp')
        for suffix in ('tokyo/ek_1390/', 'tokyo/ek_139000/', 'tokyo/ek_13900/?url=https://example.com',
                       'tokyo/ek_13900/?pn=2', 'tokyo/ek_13900/../', 'kyoto/ek_13900/'):
            with self.subTest(suffix=suffix), self.assertRaises(transport.PublicFetchError):
                transport.checked_url('https://suumo.jp/chintai/' + suffix)

    def test_official_route_selection_is_read_only_and_has_bounded_path(self):
        for suffix in ('tokyo/ensen/', 'tokyo/en_sobusen/', 'osaka/en_osakasen/', 'fukuoka/en_line_1/'):
            self.assertEqual(transport.checked_url('https://suumo.jp/chintai/' + suffix).hostname, 'suumo.jp')
        for suffix in ('tokyo/ensen/?url=x', 'tokyo/en_sobusen/?rn=0573', 'tokyo/en_sobu%2fsen/',
                       'tokyo/en_' + 'a' * 81 + '/', 'kyoto/ensen/', 'tokyo/ensen/next/'):
            with self.subTest(suffix=suffix), self.assertRaises(transport.PublicFetchError):
                transport.checked_url('https://suumo.jp/chintai/' + suffix)

    def test_station_scope_uses_matching_route_and_full_station_code(self):
        self.assertEqual(transport.checked_url(SEARCH + '?ar=030&bs=040&ra=013&rn=0573&ek=057313900&ts=1&md=03&mb=30&mt=40&cb=0.0&ct=9999999').hostname, 'suumo.jp')
        self.assertEqual(transport.checked_url(SEARCH + '?rn=0573&ek=057313900&ts=1&md=03&mb=30&mt=40&cb=0.0&ct=9999999').hostname, 'suumo.jp')
        for query in ('rn=0573', 'ek=057313900', 'rn=573&ek=057313900', 'rn=0573&ek=13900', 'rn=0573&ek=057313900&ra=13',
                      'rn=0573&ek=057313900&ra=0130', 'rn=0573&ek=057313900&ra=1.3',
                      'rn=0573&ek=000013900', 'rn=0573&ek=0573139000', 'rn=0573&ek=057313900&ts=4',
                      'rn=0573&ek=057313900&ek=057319450', 'rn=0573&ek=057313900&ts=1.0',
                      'rn=0573&ek=057313900&cb=10.0&ct=20.0'):
            with self.subTest(query=query), self.assertRaises(transport.PublicFetchError):
                transport.checked_url(SEARCH + '?' + query)

    def test_pagination_allows_only_three_exact_pages(self):
        prefix = SEARCH + '?rn=0573&ek=057313900&ts=1&cb=0.0&ct=9999999'
        for value in ('1', '2', '3'):
            self.assertEqual(transport.checked_url(prefix + '&page=' + value).hostname, 'suumo.jp')
        for value in ('0', '4', '9999999', '-1', '2.0', '02', '2&page=3', '2&url=https://example.com'):
            with self.subTest(value=value), self.assertRaises(transport.PublicFetchError):
                transport.checked_url(prefix + '&page=' + value)
        with self.assertRaises(transport.PublicFetchError):
            transport.checked_url(prefix + '&pn=2')

    def test_redirects_and_denials_are_not_followed_or_returned_as_page_content(self):
        for status in (301, 302, 307, 308):
            response = Response(b'secret redirect content', status, {'Location': 'http://127.0.0.1/private'})
            connection = Connection(response)
            with self.subTest(status=status), self.assertRaises(transport.PublicFetchError) as raised:
                self.fetch(response, connection=connection)
            self.assertEqual(raised.exception.code, 'redirect_denied')
            self.assertEqual(len(connection.requests), 1)
            self.assertEqual(response.reads, 0)
            self.assertTrue(connection.closed.is_set())
        response = Response(b'private denial page', 403)
        result, connection, _, _ = self.fetch(response)
        self.assertEqual((result.status, result.body), (403, b''))
        self.assertEqual(response.reads, 0)

    def test_unexpected_media_types_or_compression_are_rejected_before_body_reads(self):
        for headers, code in (({'Content-Type': 'application/octet-stream'}, 'content_type_denied'),
                              ({'Content-Type': 'application/json'}, 'content_type_denied'),
                              ({'Content-Encoding': 'br'}, 'encoding_denied'),
                              ({'Content-Encoding': 'gzip, gzip'}, 'encoding_denied')):
            response = Response(headers=headers)
            with self.subTest(headers=headers), self.assertRaises(transport.PublicFetchError) as raised:
                self.fetch(response)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(response.reads, 0)

    def test_oversized_or_malformed_declared_length_is_rejected_before_allocation(self):
        for length in ('65', '-1', 'not-a-number', '9' * 30):
            response = Response(b'x', headers={'Content-Length': length})
            with self.subTest(length=length), self.assertRaises(transport.PublicFetchError) as raised:
                self.fetch(response, MAX_PAGE_BYTES=64)
            self.assertEqual(raised.exception.code, 'payload_too_large')
            self.assertEqual(response.reads, 0)

    def test_streamed_bytes_are_bounded_without_a_content_length_and_robots_has_its_own_cap(self):
        response = Response(b'x' * 65, chunk_size=7)
        with self.assertRaises(transport.PublicFetchError) as raised:
            self.fetch(response, MAX_PAGE_BYTES=64)
        self.assertEqual(raised.exception.code, 'payload_too_large')
        self.assertEqual(response.body.tell(), 65)
        with self.assertRaises(transport.PublicFetchError):
            self.fetch(Response(b'x' * 17), url='https://suumo.jp/robots.txt',
                       MAX_ROBOTS_BYTES=16, MAX_PAGE_BYTES=64)
        result, connection, dns, factory = self.fetch(Response(b'x' * 64), MAX_PAGE_BYTES=64)
        self.assertEqual(result.body, b'x' * 64)
        self.assertTrue(connection.closed.is_set())
        self.assertEqual(dns.call_args.args[:2], ('suumo.jp', 443))
        self.assertEqual(factory.call_args.args, ('suumo.jp', '203.0.113.10'))
        headers = connection.requests[0][2]
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Cookie', headers)
        self.assertEqual(headers['User-Agent'], transport.USER_AGENT)

    def test_gzip_expansion_bomb_truncated_and_concatenated_streams_are_denied(self):
        good = gzip.compress(b'page data')
        bomb = gzip.compress(b'x' * 10000)
        self.assertLess(len(bomb), 128)
        for compressed in (bomb, good[:-4], good + gzip.compress(b'extra stream')):
            with self.subTest(length=len(compressed)), self.assertRaises(transport.PublicFetchError) as raised:
                self.fetch(Response(compressed, headers={'Content-Encoding': 'gzip'}), MAX_PAGE_BYTES=128)
            self.assertEqual(raised.exception.code, 'payload_too_large')
        result, _, _, _ = self.fetch(Response(good, headers={'Content-Encoding': 'gzip'}), MAX_PAGE_BYTES=128)
        self.assertEqual(result.body, b'page data')

    def test_absolute_deadline_stops_progressing_reads_and_prevents_expired_dns_work(self):
        clock = [100.0]
        response = Response(b'long body', chunk_size=1, read_hook=lambda: clock.__setitem__(0, clock[0] + 2))
        connection = Connection(response)
        with self.assertRaises(transport.PublicFetchError) as raised:
            self.fetch(response, connection=connection, deadline=103, clock=lambda: clock[0])
        self.assertEqual(raised.exception.code, 'timeout')
        self.assertEqual(response.reads, 2)
        self.assertTrue(connection.closed.is_set())
        with patch.object(transport, '_bounded_dns') as dns, self.assertRaises(transport.PublicFetchError):
            transport.fetch_public(DETAIL, deadline=time.monotonic() - 1)
        dns.assert_not_called()

    def test_watchdog_interrupts_stalled_response_headers(self):
        class StalledConnection(Connection):
            def getresponse(self):
                if not self.closed.wait(1):
                    raise AssertionError('watchdog did not interrupt stalled headers')
                raise OSError('private stalled transport text')
        connection = StalledConnection(Response())
        start = time.monotonic()
        with self.assertRaises(transport.PublicFetchError) as raised:
            self.fetch(connection=connection, REQUEST_SECONDS=0.04)
        self.assertEqual(raised.exception.code, 'timeout')
        self.assertLess(time.monotonic() - start, 0.8)
        self.assertNotIn('private', str(raised.exception))

    def test_private_dns_and_transport_errors_return_only_fixed_codes(self):
        with (patch.object(transport, '_bounded_dns', side_effect=SupplyError('network_target_denied')),
              patch.object(transport, '_PinnedHTTPSConnection') as connection):
            with self.assertRaises(transport.PublicFetchError) as raised:
                transport.fetch_public(DETAIL, deadline=time.monotonic() + 1)
        self.assertEqual(raised.exception.code, 'network_target_denied')
        connection.assert_not_called()
        connection = Connection(Response())
        with patch.object(connection, 'request', side_effect=OSError('password=secret local-private-path')):
            with self.assertRaises(transport.PublicFetchError) as raised:
                self.fetch(connection=connection)
        self.assertEqual(str(raised.exception), 'network_failed')
        self.assertTrue(connection.closed.is_set())


if __name__ == '__main__':
    unittest.main()
