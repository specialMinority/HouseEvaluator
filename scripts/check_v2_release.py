"""Read local release gates without deploying or approving anything."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.v2.service import Runtime
from backend.v2.security import AccessPolicy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--suppliers')
    parser.add_argument('--state-db')
    parser.add_argument('--registry')
    parser.add_argument('--evidence')
    parser.add_argument('--token-env', default='HOUSE_EVALUATOR_ACCESS_TOKEN')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    try:
        access = AccessPolicy(os.getenv(args.token_env))
        runtime = Runtime(args.db, suppliers_path=args.suppliers, state_db=args.state_db, registry_path=args.registry, release_evidence_path=args.evidence)
        report = runtime.readiness(now=datetime.now(timezone.utc), access_protected=access.protected)
        raw = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as handle:
                handle.write(raw + '\n')
        print(raw)
        return 0 if report['ready'] else 2
    except Exception as error:
        print(json.dumps({'error': 'release_check_failed', 'type': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
