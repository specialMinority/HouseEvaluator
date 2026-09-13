"""Explicit operator PC worker for ephemeral public-source jobs.

Run with ``python -m backend.v2.remote_worker``. The source is selected before
startup; this is not a fallback after a different host's denied source request.
No job, token, URL, or page content is written to disk or application logs.
"""
from collections import deque
from copy import deepcopy
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import ssl
import threading
import time

from . import listing_import, public_search
from .personal import validate_listing
from .personal_jobs import SearchBusy

ORIGIN = 'https://houseevaluator-personal.onrender.com'
MAX_BYTES = 1024 * 1024
HTTP_SECONDS = 10
_PATHS = frozenset(('/api/v2/worker/claim', '/api/v2/worker/result'))
_ID = re.compile(r'[A-Za-z0-9_-]{16,128}')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{32,256}')
_ERRORS = {
    'invalid_job': '작업 요청 형식을 확인하지 못했습니다.',
    'unsupported_job': '지원하지 않는 작업 종류입니다.',
    'invalid_subject': '비교 대상의 필수 조건과 입력 범위를 확인해 주세요.',
    'worker_busy': '조회 작업이 진행 중입니다. 잠시 후 다시 시도해 주세요.',
    'worker_unavailable': '조회 작업을 완료하지 못했습니다. 직접 입력을 사용할 수 있습니다.',
    'result_too_large': '조회 결과가 전송 한도를 초과했습니다. 직접 입력을 사용해 주세요.',
    'already_processed': '이미 처리한 작업입니다. 새 작업을 요청해 주세요.',
}


class WorkerError(Exception):
    """Fixed codes only: neither remote bodies nor socket errors are included."""

    def __init__(self, code='worker_unavailable'):
        self.code = code
        super().__init__(code)


class WorkerAuthError(WorkerError):
    pass


class WorkerProtocolError(WorkerError):
    pass


class WorkerBusy(WorkerError):
    pass


class WorkerLeaseExpired(WorkerError):
    pass


def _json_bytes(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError):
        raise WorkerProtocolError('invalid_json') from None
    if len(data) > MAX_BYTES:
        raise WorkerProtocolError('message_too_large')
    return data


def _read_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def finite(value):
        raise ValueError()

    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=finite)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise WorkerProtocolError('invalid_response') from None
    if not isinstance(value, dict):
        raise WorkerProtocolError('invalid_response')
    return value


def validate_origin(value):
    # The operator-controlled destination is fixed. Jobs can never supply a
    # different origin or send the dedicated worker credential to another host.
    if value != ORIGIN:
        raise WorkerProtocolError('invalid_origin')
    return value


def read_token(path):
    try:
        path = Path(path)
        if str(path).startswith(('\\\\', '//')):
            raise ValueError()
        absolute = path.absolute()
        for part in (absolute, *absolute.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError()
        if not absolute.is_file():
            raise ValueError()
        with absolute.open('rb') as stream:
            raw = stream.read(1025)
        if len(raw) > 1024:
            raise ValueError()
        token = raw.decode('ascii').strip()
        if not _TOKEN.fullmatch(token):
            raise ValueError()
        return token
    except (OSError, ValueError, TypeError, UnicodeError):
        raise WorkerProtocolError('invalid_token_file') from None


class HttpsTransport:
    """Direct verified TLS, no environment proxy and no redirect handler."""

    def __init__(self, origin, token, *, connection_factory=None, clock=time.monotonic):
        self.origin = validate_origin(origin)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise WorkerProtocolError('invalid_token_file')
        self._token = token
        self._connect = connection_factory or http.client.HTTPSConnection
        self._clock = clock

    def __call__(self, path, payload):
        if path not in _PATHS:
            raise WorkerProtocolError('invalid_endpoint')
        data = _json_bytes(payload)
        deadline = self._clock() + HTTP_SECONDS
        connection = None
        try:
            connection = self._connect('houseevaluator-personal.onrender.com', 443,
                                       timeout=HTTP_SECONDS, context=ssl.create_default_context())
            connection.request('POST', path, body=data, headers={
                'Authorization': 'Bearer ' + self._token,
                'Content-Type': 'application/json; charset=utf-8', 'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'User-Agent': 'HouseEvaluator-OperatorWorker/1',
            })
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WorkerError('cloud_timeout')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            response = connection.getresponse()
            if response.status in (401, 403):
                raise WorkerAuthError('worker_auth_rejected')
            if 300 <= response.status < 400:
                raise WorkerProtocolError('redirect_denied')
            if response.status == 429:
                raise WorkerBusy('worker_busy')
            if response.status in (409, 410) and path.endswith('/result'):
                raise WorkerLeaseExpired('lease_expired')
            if response.status >= 500:
                raise WorkerError('cloud_unavailable')
            if not 200 <= response.status < 300:
                raise WorkerProtocolError('cloud_request_rejected')
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise WorkerProtocolError('invalid_response')
            if response.getheader('Content-Encoding', 'identity').strip().lower() not in ('', 'identity'):
                raise WorkerProtocolError('encoding_denied')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 12 or int(length) > MAX_BYTES):
                raise WorkerProtocolError('message_too_large')
            chunks, count = [], 0
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise WorkerError('cloud_timeout')
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(65536, MAX_BYTES + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > MAX_BYTES:
                    raise WorkerProtocolError('message_too_large')
            if self._clock() >= deadline:
                raise WorkerError('cloud_timeout')
            if length is not None and count != int(length):
                raise WorkerProtocolError('invalid_response')
            return _read_json(b''.join(chunks))
        except WorkerError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise WorkerError('cloud_unavailable') from None
        finally:
            if connection is not None:
                connection.close()


def _failed(code):
    if code in _ERRORS:
        message = _ERRORS[code]
    elif code in listing_import._ERRORS:
        message = listing_import.ListingImportError(code).message
    else:
        code, message = 'worker_unavailable', _ERRORS['worker_unavailable']
    return {'status': 'failed', 'error': {'code': code, 'message': message}}


class RemoteWorker:
    """One source operation at a time; bounded in-memory result retransmission."""

    def __init__(self, transport, *, search_fn=None, import_fn=None, search_source='suumo',
                 import_source_ids=('chintai', 'yahoo_realestate'), worker_id=None,
                 clock=time.monotonic, stop_event=None):
        if search_source not in ('suumo', 'chintai', 'yahoo_realestate'):
            raise WorkerProtocolError('invalid_source_configuration')
        if (not isinstance(import_source_ids, (tuple, list)) or len(import_source_ids) != len(set(import_source_ids))
                or any(value not in ('chintai', 'yahoo_realestate') for value in import_source_ids)):
            raise WorkerProtocolError('invalid_source_configuration')
        self.worker_id = worker_id or secrets.token_urlsafe(24)
        if not isinstance(self.worker_id, str) or not _ID.fullmatch(self.worker_id):
            raise WorkerProtocolError('invalid_worker_id')
        self._transport, self._clock = transport, clock
        self._search, self._import = search_fn or public_search.search, import_fn or listing_import.import_listing
        self._real_search, self._real_import = search_fn is None, import_fn is None
        self.search_source, self.import_source_ids = search_source, tuple(import_source_ids)
        self._stop = stop_event or threading.Event()
        self._pending = None
        self._seen, self._seen_order = set(), deque()
        self._failures = 0
        self.last_event = 'starting'

    def stop(self):
        self._stop.set()

    def _backoff(self):
        delay = min(300, 30 * (2 ** min(self._failures, 4)))
        self._failures = min(self._failures + 1, 4)
        return delay

    def _post(self, path, payload):
        _json_bytes(payload)
        try:
            response = self._transport(path, deepcopy(payload))
        except WorkerError:
            raise
        except Exception:
            raise WorkerError('cloud_unavailable') from None
        _json_bytes(response)  # The injected transport has the same bounds.
        if not isinstance(response, dict):
            raise WorkerProtocolError('invalid_response')
        return response

    def _execute(self, job):
        payload, kind = job.get('payload'), job.get('kind')
        if kind not in ('search', 'import'):
            return _failed('unsupported_job')
        if not isinstance(payload, dict):
            return _failed('invalid_job')
        try:
            if kind == 'search':
                if set(payload) != {'subject'}:
                    return _failed('invalid_job')
                subject = validate_listing(payload['subject'], subject=True)
                public_search.build_search_url(subject)  # Geography and exact supported station input.
                if self._real_search and public_search.selected_source() != self.search_source:
                    return _failed('worker_unavailable')
                result = self._search(subject)
            else:
                url = listing_import.validate_payload(payload)
                source_id, _, _ = listing_import._provider(url)
                if source_id not in self.import_source_ids:
                    return _failed('source_disabled')
                if self._real_import and {source['id'] for source in listing_import.import_sources()} != set(self.import_source_ids):
                    return _failed('worker_unavailable')
                result = self._import(payload)
            if not isinstance(result, dict):
                return _failed('worker_unavailable')
            return {'status': 'complete', 'result': result}
        except listing_import.ListingImportError as error:
            return _failed(error.code)
        except SearchBusy:
            return _failed('worker_busy')
        except public_search.PublicFetchError:
            return _failed('invalid_subject' if kind == 'search' else 'worker_unavailable')
        except ValueError:
            return _failed('invalid_subject' if kind == 'search' else 'invalid_job')
        except Exception:
            return _failed('worker_unavailable')

    def _upload(self):
        pending = self._pending
        if self._clock() >= pending['expires'] or pending['attempts'] >= 3:
            self._pending = None
            self.last_event = 'result_expired'
            return 10
        pending['attempts'] += 1
        try:
            self._post('/api/v2/worker/result', pending['message'])
        except WorkerAuthError:
            self._pending = None
            raise
        except WorkerLeaseExpired:
            self._pending = None
            self.last_event = 'result_expired'
            return 10
        except WorkerBusy:
            self.last_event = 'cloud_busy'
            return 10
        except WorkerProtocolError:
            self._pending = None
            raise
        except WorkerError:
            self.last_event = 'upload_retry'
            delay = self._backoff()
            if self._clock() + delay >= pending['expires'] or pending['attempts'] >= 3:
                self._pending = None
                self.last_event = 'result_expired'
            return delay
        busy = pending['message']['outcome'].get('error', {}).get('code') in ('worker_busy', 'import_busy')
        self._pending = None
        self._failures = 0
        self.last_event = 'result_sent'
        return 10 if busy else 1

    def step(self):
        """One claim/execution/upload cycle; return a bounded polling delay."""
        if self._stop.is_set():
            return 0
        if self._pending is not None:
            return self._upload()
        try:
            response = self._post('/api/v2/worker/claim', {
                'worker_id': self.worker_id, 'protocol': 1, 'search_source': self.search_source,
                'import_sources': list(self.import_source_ids),
            })
        except WorkerBusy:
            self.last_event = 'cloud_busy'
            return 10
        except (WorkerAuthError, WorkerProtocolError):
            raise
        except WorkerError:
            self.last_event = 'cloud_unavailable'
            return self._backoff()
        self._failures = 0
        if 'job' not in response:
            raise WorkerProtocolError('invalid_response')
        poll = response.get('poll_after_seconds', 10)
        if type(poll) is not int or not 1 <= poll <= 30:
            raise WorkerProtocolError('invalid_response')
        job = response['job']
        if job is None:
            self.last_event = 'idle'
            return max(10, poll)
        if (not isinstance(job, dict) or not isinstance(job.get('job_id'), str) or not _ID.fullmatch(job['job_id'])
                or not isinstance(job.get('lease_token'), str) or not _TOKEN.fullmatch(job['lease_token'])
                or type(job.get('lease_seconds')) is not int or job['lease_seconds'] != 75):
            raise WorkerProtocolError('invalid_job_envelope')
        expires = self._clock() + job['lease_seconds']
        if job['job_id'] in self._seen:
            outcome = _failed('already_processed')
        else:
            self._seen.add(job['job_id'])
            self._seen_order.append(job['job_id'])
            if len(self._seen_order) > 256:
                self._seen.remove(self._seen_order.popleft())
            outcome = self._execute(job)
        message = {'worker_id': self.worker_id, 'job_id': job['job_id'], 'lease_token': job['lease_token'], 'outcome': outcome}
        try:
            _json_bytes(message)
        except WorkerProtocolError:
            message['outcome'] = _failed('result_too_large')
        self._pending = {'message': deepcopy(message), 'expires': expires, 'attempts': 0}
        return self._upload()

    def run(self, *, once=False):
        try:
            while not self._stop.is_set():
                delay = self.step()
                if once:
                    return 0 if self.last_event in ('idle', 'result_sent') else 3
                self._stop.wait(delay)
            return 0
        finally:
            self._pending = None
            self._seen.clear()
            self._seen_order.clear()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='perform one claim cycle, then exit')
    args = parser.parse_args(argv)
    worker = None
    previous = {}
    try:
        origin = validate_origin(os.getenv('HOUSE_EVALUATOR_WORKER_ORIGIN', ORIGIN))
        token = read_token(os.getenv('HOUSE_EVALUATOR_WORKER_TOKEN_FILE', '.runtime/worker-token.txt'))
        search_source = public_search.selected_source()
        sources = [source['id'] for source in listing_import.import_sources()]
        worker = RemoteWorker(HttpsTransport(origin, token), search_source=search_source, import_source_ids=sources)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: worker.stop())
        return worker.run(once=args.once)
    except KeyboardInterrupt:
        return 0
    except WorkerAuthError:
        print('worker_auth_rejected: 전용 워커 인증 설정을 확인한 후 다시 시작해 주세요.', flush=True)
        return 2
    except (WorkerError, ValueError, OSError):
        print('worker_configuration_or_protocol_error: 워커 설정과 서버 프로토콜을 확인해 주세요.', flush=True)
        return 2
    finally:
        if worker is not None:
            worker.stop()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
