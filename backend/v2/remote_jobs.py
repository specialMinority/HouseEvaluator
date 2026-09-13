"""An authenticated, bounded pull queue for a fixed operator-managed worker.

Only validated search/import jobs are dispatched. Jobs are never redelivered
after a lease expires; an upload retry must contain the same completed result.
"""
from copy import deepcopy
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .listing_import import ListingImportError, validate_payload as validate_import
from .personal import validate_listing, station_key
from .public_fetch import checked_url
from .public_search import build_search_url

MAX_RESULT_BYTES = 1024 * 1024
_MESSAGES = {
    'worker_offline': (503, '조회 서비스 연결이 끊겼습니다. 잠시 후 연결 상태를 다시 확인해 주세요.'),
    'worker_busy': (429, '조회 요청이 진행 중입니다. 잠시 후 다시 시도해 주세요.'),
    'worker_timeout': (503, '조회 작업의 제한 시간이 지났습니다. 연결 상태를 확인해 주세요.'),
    'worker_invalid': (400, '조회 작업 요청 형식이 올바르지 않습니다.'),
    'worker_mismatch': (409, '조회 작업자의 설정이 서버와 일치하지 않습니다.'),
    'job_stale': (409, '종료되거나 만료된 조회 작업입니다.'),
    'invalid_result': (400, '조회 작업 결과를 확인할 수 없습니다.'),
    'search_failed': (503, '매물 검색을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.'),
}


class WorkerError(Exception):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else 'worker_invalid'
        self.status, self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _error(code, kind):
    if not isinstance(code, str):
        code = 'search_failed'
    if kind == 'import':
        error = ListingImportError(code)
        if error.code == code:
            return {'code': error.code, 'message': error.message}
    error = WorkerError(code if code in _MESSAGES else 'search_failed')
    return {'code': error.code, 'message': error.message}


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,80}', value) is not None


def _encoded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise WorkerError('invalid_result') from None
    if len(raw) > MAX_RESULT_BYTES:
        raise WorkerError('invalid_result')
    return raw


def _detail(url, source):
    parts = checked_url(url)
    return bool(
        source == 'suumo' and parts.hostname == 'suumo.jp' and re.fullmatch(r'/chintai/(?:bc|jnc)_[0-9]{8,16}/', parts.path)
        or source == 'chintai' and parts.hostname == 'www.chintai.net' and parts.path.startswith('/detail/bk-')
        or source == 'yahoo_realestate' and parts.hostname == 'realestate.yahoo.co.jp' and re.fullmatch(r'/rent/detail/_?[0-9a-f]{44}/', parts.path))


def _validate_result(kind, payload, value, search_source, import_ids):
    """Validate listings and bounded metadata before returning worker data to UI."""
    _encoded(value)
    if not isinstance(value, dict):
        raise WorkerError('invalid_result')
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 20000 or depth > 14:
            raise WorkerError('invalid_result')
        if isinstance(item, dict):
            if any(not isinstance(k, str) or len(k) > 100 or k.lower() in ('html', 'cookie', 'authorization', 'token', 'lease_token', 'worker_token') for k in item):
                raise WorkerError('invalid_result')
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            if len(item) > 600:
                raise WorkerError('invalid_result')
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str) and len(item) > 4096:
            raise WorkerError('invalid_result')
    if kind == 'search':
        rows = value.get('listings')
        reports = value.get('source_reports')
        if not isinstance(rows, list) or len(rows) > 60 or not isinstance(reports, list) or len(reports) != 1:
            raise WorkerError('invalid_result')
        if not isinstance(reports[0], dict) or reports[0].get('source_id') != search_source:
            raise WorkerError('invalid_result')
        source = search_source
    else:
        rows = [value.get('listing')]
        source = value.get('source', {}).get('id') if isinstance(value.get('source'), dict) else None
        if source not in import_ids or value.get('status') not in ('ok', 'partial'):
            raise WorkerError('invalid_result')
        if value['source'].get('url') != payload['url']:
            raise WorkerError('invalid_result')
    for row in rows:
        try:
            normalized = validate_listing(row)
            if row.get('source_id') != source or not _detail(row.get('source_url'), source) or not normalized['fetched_at']:
                raise ValueError
            if kind == 'search' and any(normalized[f] is None for f in ('city', 'municipality', 'layout', 'area_sqm', 'rent_yen')):
                raise ValueError
            if kind == 'import' and row['source_url'] != payload['url']:
                raise ValueError
            if kind == 'search' and any(normalized[f] != payload['subject'][f] for f in ('city', 'municipality', 'layout')):
                raise ValueError
            if kind == 'search' and (station_key(normalized['station_name']) != station_key(payload['subject']['station_name'])
                                     or not payload['subject']['area_sqm'] * .8 <= normalized['area_sqm'] <= payload['subject']['area_sqm'] * 1.2):
                raise ValueError
        except Exception:
            raise WorkerError('invalid_result') from None
    return deepcopy(value)


class WorkerJobs:
    def __init__(self, search_source, import_ids, *, clock=time.monotonic):
        self.search_source, self.import_ids = search_source, list(import_ids)
        self.clock, self.lock = clock, threading.RLock()
        self.jobs, self.last_seen, self.closed = {}, None, False

    def _expire(self):
        now = self.clock()
        for key, job in list(self.jobs.items()):
            if now - job['created'] >= 900:
                del self.jobs[key]
            elif job['status'] in ('pending', 'running') and (now >= job['deadline'] or (job['status'] == 'running' and now >= job['lease_until'])):
                job.update(status='failed', error=_error('worker_timeout', job['kind']), result=None, payload=None)

    def state(self):
        with self.lock:
            self._expire()
            age = None if self.last_seen is None else max(0, self.clock() - self.last_seen)
            running = any(j.get('lease_until', 0) > self.clock() for j in self.jobs.values())
            return {'online': not self.closed and (running or age is not None and age <= 30),
                    'last_seen_seconds': round(age, 1) if age is not None else None}

    def start(self, kind, payload):
        if kind == 'search':
            if not isinstance(payload, dict) or set(payload) != {'subject'}:
                raise WorkerError('worker_invalid')
            payload = {'subject': validate_listing(payload['subject'], subject=True)}
            build_search_url(payload['subject'])  # Validate supported region and station before dispatch.
        elif kind == 'import':
            validate_import(payload)
            payload = dict(payload)
        else:
            raise WorkerError('worker_invalid')
        with self.lock:
            if not self.state()['online']:
                raise WorkerError('worker_offline')
            if sum(j['status'] in ('pending', 'running') for j in self.jobs.values()) >= 3:
                raise WorkerError('worker_busy')
            if len(self.jobs) >= 32:
                terminal = [(key, j) for key, j in self.jobs.items() if j['status'] not in ('pending', 'running') and j.get('lease_until', 0) <= self.clock()]
                if not terminal:
                    raise WorkerError('worker_busy')
                del self.jobs[min(terminal, key=lambda pair: pair[1]['created'])[0]]
            identity, now = secrets.token_urlsafe(24), self.clock()
            self.jobs[identity] = {'kind': kind, 'payload': deepcopy(payload), 'created': now, 'deadline': now + 100,
                                   'status': 'pending', 'result': None, 'error': None}
            return {'job_id': identity, 'status': 'pending'}

    def get(self, kind, identity):
        with self.lock:
            self._expire()
            job = self.jobs.get(identity)
            if not job or job['kind'] != kind:
                return None
            value = {'job_id': identity, 'status': job['status']}
            if job['status'] == 'complete':
                value['result'] = deepcopy(job['result'])
            if job['status'] == 'failed':
                value.update(error=deepcopy(job['error']), message=job['error']['message'])
            return value

    def cancel(self, kind, identity):
        with self.lock:
            self._expire()
            job = self.jobs.get(identity)
            if not job or job['kind'] != kind or job['status'] not in ('pending', 'running'):
                return False
            job.update(status='cancelled', result=None, payload=None)
            return True

    def claim(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'worker_id', 'protocol', 'search_source', 'import_sources'} or not _identity(payload.get('worker_id')) or type(payload.get('protocol')) is not int or payload['protocol'] != 1:
            raise WorkerError('worker_invalid')
        if payload['search_source'] != self.search_source or payload['import_sources'] != self.import_ids:
            raise WorkerError('worker_mismatch')
        with self.lock:
            self._expire()
            if self.closed:
                raise WorkerError('worker_offline')
            self.last_seen = self.clock()
            # Cancelled jobs also retain their lease until the original worker finishes.
            if any(j.get('lease_until', 0) > self.clock() for j in self.jobs.values()):
                return {'job': None, 'poll_after_seconds': 10}
            pending = [(key, j) for key, j in self.jobs.items() if j['status'] == 'pending']
            if not pending:
                return {'job': None, 'poll_after_seconds': 10}
            key, job = min(pending, key=lambda pair: pair[1]['created'])
            lease = secrets.token_urlsafe(32)
            job.update(status='running', worker_id=payload['worker_id'], lease_token=lease, lease_until=self.clock() + 75)
            return {'job': {'job_id': key, 'lease_token': lease, 'kind': job['kind'], 'payload': deepcopy(job['payload']), 'lease_seconds': 75}, 'poll_after_seconds': 1}

    def finish(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'worker_id', 'job_id', 'lease_token', 'outcome'} or any(not _identity(payload.get(k)) for k in ('worker_id', 'job_id', 'lease_token')):
            raise WorkerError('worker_invalid')
        digest = hashlib.sha256(_encoded(payload['outcome'])).hexdigest()
        with self.lock:
            self._expire()
            job = self.jobs.get(payload['job_id'])
            if not job or job.get('worker_id') != payload['worker_id'] or not secrets.compare_digest(job.get('lease_token', ''), payload['lease_token']):
                raise WorkerError('job_stale')
            if job['status'] in ('complete', 'failed') and job.get('result_digest') == digest:
                return {'accepted': True, 'duplicate': True}
            if job['status'] != 'running':
                if job['status'] in ('cancelled', 'failed'):
                    job['lease_until'] = 0
                raise WorkerError('job_stale')
            outcome = payload['outcome']
            if not isinstance(outcome, dict):
                raise WorkerError('invalid_result')
            if outcome.get('status') == 'complete' and set(outcome) == {'status', 'result'}:
                result = _validate_result(job['kind'], job['payload'], outcome['result'], self.search_source, self.import_ids)
                job.update(status='complete', result=result)
            elif outcome.get('status') == 'failed' and set(outcome) == {'status', 'error'} and isinstance(outcome['error'], dict):
                job.update(status='failed', error=_error(outcome['error'].get('code'), job['kind']))
            else:
                raise WorkerError('invalid_result')
            job.update(result_digest=digest, payload=None, lease_until=0)
            self.last_seen = self.clock()
            return {'accepted': True, 'duplicate': False}

    def close(self):
        with self.lock:
            self.closed = True
            self.jobs.clear()


class WorkerSearchAdapter:
    def __init__(self, broker):
        self.broker = broker

    def start(self, subject):
        return self.broker.start('search', {'subject': subject})

    def get(self, identity):
        return self.broker.get('search', identity)

    def cancel(self, identity):
        return self.broker.cancel('search', identity)

    def close(self):
        self.broker.close()
