"""An explicit, bounded operational probe before enabling a new public source.

The operator invokes this module; ordinary application startup never runs it.
No request is retried and no response content is logged or persisted.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from .public_fetch import PublicFetchError, fetch_public
from .public_search import RobotsPolicy, _blocked, _decode


def probe(*, fetcher=None):
    fetcher = fetcher or fetch_public
    deadline = time.monotonic() + 22
    report = {'source_id': 'yahoo_realestate', 'started_at': datetime.now(timezone.utc).isoformat(), 'requests': [], 'status': 'unavailable'}
    stage = 'robots'
    robots_url = 'https://realestate.yahoo.co.jp/robots.txt'
    search_url = 'https://realestate.yahoo.co.jp/rent/search/03/13/13104/'
    try:
        response = fetcher(robots_url, deadline=deadline)
        report['requests'].append({'stage': stage, 'http_status': response.status, 'bytes': len(response.body)})
        if response.status != 200:
            return report
        policy = RobotsPolicy(_decode(response))
        if not policy.allows(search_url):
            report['status'] = 'robots_blocked'
            return report
        delay = max(.15, policy.delay)
        if time.monotonic() + delay + .2 >= deadline:
            raise PublicFetchError('timeout')
        time.sleep(delay)
        stage = 'search'
        response = fetcher(search_url, deadline=deadline)
        report['requests'].append({'stage': stage, 'http_status': response.status, 'bytes': len(response.body)})
        if response.status != 200:
            return report
        text = _decode(response)
        if _blocked(text):
            report['status'] = 'source_blocked'
            return report
        report['status'] = 'html_received'
        report['has_listing_marker'] = 'ListCassetteRoom__item' in text
    except PublicFetchError as error:
        report['failure_stage'] = stage
        report['error_code'] = error.code
    except Exception:
        report['failure_stage'] = stage
        report['error_code'] = 'unexpected_failure'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='Start the normal app after the probe, regardless of outcome.')
    args = parser.parse_args()
    print('PUBLIC_SOURCE_PREFLIGHT ' + json.dumps(probe(), ensure_ascii=True), flush=True)
    if args.serve:
        from backend.src.server import serve
        serve(host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')))


if __name__ == '__main__':
    main()
