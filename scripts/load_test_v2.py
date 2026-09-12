"""Small loopback-only load measurement; never a remote stress test."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from urllib.parse import urlsplit


def measure(base_url, *, requests=60, concurrency=4, token=None, subjects=None, mode='market'):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('only an explicit loopback HTTP(S) origin is allowed')
    if not 10 <= requests <= 500 or not 1 <= concurrency <= 16:
        raise ValueError('requests 10..500 and concurrency 1..16 required')
    if mode not in ('market', 'demo') or (subjects is not None and (not isinstance(subjects, list) or not subjects or len(subjects) > 100 or any(not isinstance(s, dict) for s in subjects))):
        raise ValueError('up to 100 subject objects and market/demo mode required')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    connection_class = HTTPSConnection if parsed.scheme == 'https' else HTTPConnection
    def fetch(path, subject=None):
        connection = connection_class(parsed.hostname, parsed.port, timeout=10)
        started = time.perf_counter()
        try:
            body = json.dumps({'subject': subject, 'mode': mode}).encode('utf-8') if subject is not None else None
            connection.request('POST' if body else 'GET', path, body=body, headers={**headers, **({'Content-Type': 'application/json'} if body else {})})
            response = connection.getresponse()
            raw = response.read(2_000_001)
            return response.status, (time.perf_counter() - started) * 1000, json.loads(raw)
        finally:
            connection.close()
    _, _, readiness = fetch('/api/v2/readiness')
    endpoint = '/api/v2/evaluate' if subjects else '/api/v2/capabilities'
    def one(index):
        try:
            status, elapsed, result = fetch(endpoint, subjects[index % len(subjects)] if subjects else None)
            return status, elapsed, result.get('benchmark_yen') is not None
        except Exception:
            return 0, 10000.0, False
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, range(requests)))
    duration = time.perf_counter() - started
    latencies = sorted(value for _, value, _ in results)
    successes = sum(status == 200 for status, _, _ in results)
    references = sum(status == 200 and reference for status, _, reference in results)
    p95 = latencies[math.ceil(len(latencies) * .95) - 1]
    unique = len({s.get('unit_id') for s in subjects if s.get('unit_id')}) if subjects else 0
    buildings = len({s.get('building_id') for s in subjects if s.get('building_id')}) if subjects else 0
    real = bool(readiness.get('source_ids')) and readiness.get('fresh_listing_count', 0) > 0 and mode == 'market' and unique >= 10 and buildings >= 5
    return {'kind': 'load_test', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'market_operations' if real else 'synthetic_smoke',
            'context_fingerprint': readiness.get('context_fingerprint'),
            'passed': successes == requests and p95 <= 5000,
            'request_count': requests, 'concurrency': concurrency, 'successful_requests': successes,
            'rate_limited_requests': sum(status == 429 for status, _, _ in results),
            'workload': 'evaluate_' + mode if subjects else 'metadata_only',
            'unique_subject_count': unique, 'subject_building_count': buildings, 'reference_result_count': references,
            'p50_ms': round(statistics.median(latencies), 3), 'p95_ms': round(p95, 3),
            'duration_seconds': round(duration, 3), 'requests_per_second': round(requests / duration, 2),
            'endpoint': endpoint, 'threshold_p95_ms': 5000,
            'limitation': 'Loopback bounded workload only; not evidence of Internet or multi-instance production capacity.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--requests', type=int, default=60)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--token-env', default='HOUSE_EVALUATOR_ACCESS_TOKEN')
    parser.add_argument('--output', required=True)
    parser.add_argument('--subjects', help='local JSON array of diverse subject objects for evaluation workload')
    parser.add_argument('--mode', choices=['market', 'demo'], default='market')
    args = parser.parse_args(argv)
    try:
        subjects = None
        if args.subjects:
            path = Path(args.subjects)
            if path.stat().st_size > 2_000_000:
                raise ValueError('subjects file is too large')
            subjects = json.loads(path.read_text(encoding='utf-8'))
        report = measure(args.url, requests=args.requests, concurrency=args.concurrency, token=os.getenv(args.token_env), subjects=subjects, mode=args.mode)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['passed'] else 2
    except Exception as error:
        print(json.dumps({'error': 'load_check_failed', 'type': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
