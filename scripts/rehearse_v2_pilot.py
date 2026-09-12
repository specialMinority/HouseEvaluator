"""Exercise an isolated local Docker deployment with synthetic data only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
import uuid

ROOT = Path(__file__).resolve().parents[1]


def rehearse(output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = 'houseevaluator-drill-' + uuid.uuid4().hex[:12]
    image_name = project + ':local'
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update(HOUSE_EVALUATOR_ACCESS_TOKEN=token, HOUSE_EVALUATOR_DEMO='1',
               HOUSE_EVALUATOR_PILOT='0', HOUSE_EVALUATOR_PORT='0',
               HOUSE_EVALUATOR_IMAGE=image_name,
               HOUSE_EVALUATOR_REQUESTS_PER_MINUTE='240')
    command = ['docker', 'compose', '--project-name', project, '--file', str(ROOT / 'compose.yaml')]
    hidden = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    checks, details = {}, {}
    created = False
    opener = build_opener(ProxyHandler({}))

    def run(args, *, docker=False, timeout=90):
        result = subprocess.run((['docker'] if docker else command) + args, cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=timeout, creationflags=hidden)
        if result.returncode:
            # Neither command environment nor Docker inspection may expose the access code.
            with (output / 'diagnostics.txt').open('a', encoding='utf-8') as diagnostic:
                diagnostic.write(args[0] + '\n' + result.stderr.replace(token, '[REDACTED]') + '\n')
            raise RuntimeError('container_command_failed:' + args[0])
        return result.stdout.strip()

    def address():
        value = run(['port', 'api', '8080']).splitlines()[0]
        if not value.startswith('127.0.0.1:') or not value.split(':')[-1].isdigit():
            raise RuntimeError('non_loopback_deployment_rejected')
        return 'http://' + value

    def request(base, path, body=None, authenticated=True):
        headers = {'Authorization': 'Bearer ' + token} if authenticated else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        query = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with opener.open(query, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def await_up():
        base = address()
        end = time.monotonic() + 40
        while time.monotonic() < end:
            try:
                if request(base, '/healthz', authenticated=False) == (200, {'status': 'ok'}):
                    return base
            except (URLError, OSError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError('liveness_timeout')

    def inventory():
        code = "import sqlite3,json,hashlib; c=sqlite3.connect('file:/data/v2.sqlite3?mode=ro',uri=True); rows=c.execute('SELECT source_id,snapshot_id,content_hash FROM v2_current ORDER BY source_id').fetchall(); print(json.dumps({'source_count':len(rows),'snapshot_hash':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}))"
        return json.loads(run(['exec', '-T', 'api', 'python', '-c', code]))

    report = {'schema_version': 'deployment-rehearsal-1.0', 'kind': 'deployment_rehearsal',
              'scope': 'synthetic_smoke', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'passed': False, 'evidence_registered': False, 'checks': checks, 'details': details,
              'limitations': ['No actual listing license, market validation, TLS, external alert delivery or user study.',
                              'Only this temporary Docker project and its synthetic data are exercised.']}
    with TemporaryDirectory(prefix='houseevaluator-config-') as config_directory:
        config = Path(config_directory)
        for target, source in [('suppliers.json', 'config/suppliers.example.json'),
                               ('registry.json', 'docs/validation_registry.example.json'),
                               ('release.json', 'config/release_evidence.example.json')]:
            value = json.loads((ROOT / source).read_text(encoding='utf-8'))
            if target == 'suppliers.json' and value.get('suppliers') != []:
                raise RuntimeError('empty_supplier_example_required')
            (config / target).write_text(json.dumps(value), encoding='utf-8')
        env['HOUSE_EVALUATOR_CONFIG_DIR'] = config.as_posix()
        try:
            print('Building local deployment image...', flush=True)
            run(['build', 'api'], timeout=600)
            created = True
            run(['up', '--no-build', '-d', 'api'])
            base = await_up()
            checks['loopback_liveness'] = True
            checks['unauthenticated_api_denied'] = request(base, '/api/v2/capabilities', authenticated=False)[0] == 401
            status, capability = request(base, '/api/v2/capabilities')
            checks['empty_market_not_fabricated'] = status == 200 and not capability['market_data_available']
            for city in ('tokyo', 'osaka', 'fukuoka'):
                _, example = request(base, '/api/v2/demo-subject?city=' + city)
                status, result = request(base, '/api/v2/evaluate', {'subject': example['subject'], 'mode': 'demo'})
                checks['synthetic_comparison_' + city] = status == 200 and result['is_demo'] and result['judgment'] is None and result['sample']['unit_count'] == 24
            before = inventory()
            details['synthetic_source_count'] = before['source_count']
            details['snapshot_hash_before_restart'] = before['snapshot_hash']
            uid = run(['exec', '-T', 'api', 'python', '-c', 'import os; print(os.getuid())'])
            checks['non_root_uid'] = uid == '10001'
            container_id = run(['ps', '-q', 'api'])
            inspect = json.loads(run(['inspect', '--format', '{{json .HostConfig}}', container_id], docker=True))
            checks['read_only_container_root'] = inspect['ReadonlyRootfs'] is True
            checks['bounded_logs'] = inspect['LogConfig']['Config'].get('max-size') == '10m'
            details['image_id'] = run(['inspect', '--format', '{{.Image}}', container_id], docker=True)
            health_end = time.monotonic() + 40
            while time.monotonic() < health_end:
                if run(['inspect', '--format', '{{.State.Health.Status}}', container_id], docker=True) == 'healthy':
                    checks['docker_healthcheck_healthy'] = True
                    break
                time.sleep(.5)
            checks.setdefault('docker_healthcheck_healthy', False)
            print('Testing abrupt stop, restart and retained snapshots...', flush=True)
            run(['kill', '-s', 'SIGKILL', 'api'])
            run(['start', 'api'])
            base = await_up()
            checks['snapshots_survive_abrupt_restart'] = inventory() == before
            env.update(HOUSE_EVALUATOR_DEMO='0', HOUSE_EVALUATOR_PILOT='1')
            run(['up', '--no-build', '--force-recreate', '-d', 'api'])
            base = await_up()
            checks['snapshots_survive_container_recreation'] = inventory() == before
            status, ready = request(base, '/api/v2/readiness')
            checks['market_release_stays_blocked'] = status == 503 and ready['ready'] is False and not ready['eligible_segments']
            checks['pilot_disables_demo'] = ready['checks']['demo_disabled'] is True
            status, denied = request(base, '/api/v2/evaluate', {'subject': example['subject'], 'mode': 'market'})
            checks['pilot_evaluation_blocked'] = status == 503 and denied.get('error') == 'release_not_ready'
            details['context_fingerprint'] = ready['context_fingerprint']
            details['release_blockers'] = ready['blockers']
            run(['--profile', 'ingestion', 'up', '--no-build', '-d', 'ingestion'])
            run(['exec', '-T', 'ingestion', 'python', 'scripts/run_v2_ingestion.py', '--config', '/config/suppliers.json', '--db', '/data/v2.sqlite3', '--state-db', '/data/ingestion.sqlite3', '--once'])
            state = json.loads(run(['exec', '-T', 'ingestion', 'python', 'scripts/run_v2_ingestion.py', '--state-db', '/data/ingestion.sqlite3', '--status']))
            checks['empty_supplier_worker_starts'] = state['status'] == 'not_configured' and state['source_count'] == 0
            print('Running container restore and local alert drills...', flush=True)
            run(['exec', '-T', 'api', 'python', 'scripts/drill_v2_restore.py', '--db', '/data/v2.sqlite3', '--backup-dir', '/backups', '--suppliers', '/config/suppliers.json', '--state-db', '/data/ingestion.sqlite3', '--output', '/data/reports/restore.json'])
            run(['cp', 'api:/data/reports/restore.json', str(output / 'restore.json')])
            restored = json.loads((output / 'restore.json').read_text(encoding='utf-8'))
            checks['container_restore_drill'] = restored['passed'] is True and restored['scope'] == 'synthetic_smoke' and restored['context_fingerprint'] == ready['context_fingerprint']
            run(['exec', '-T', 'api', 'python', 'scripts/drill_v2_alerts.py', '--suppliers', '/config/suppliers.json', '--output', '/data/reports/alerts.json'])
            run(['cp', 'api:/data/reports/alerts.json', str(output / 'alerts.json')])
            alerted = json.loads((output / 'alerts.json').read_text(encoding='utf-8'))
            checks['container_local_alert_drill'] = alerted['passed'] is True and alerted['scope'] == 'synthetic_smoke' and alerted['external_delivery_verified'] is False and alerted['context_fingerprint'] == ready['context_fingerprint']
            report['passed'] = all(checks.values())
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            report['error_type'] = type(error).__name__
            # Keep error codes bounded; external command stdout/stderr can include config details.
            report['error_code'] = str(error) if isinstance(error, RuntimeError) else 'rehearsal_failed'
        finally:
            if created:
                try:
                    run(['--profile', 'ingestion', 'down', '--volumes'], timeout=60)
                    checks['isolated_containers_and_volumes_removed'] = True
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    checks['isolated_containers_and_volumes_removed'] = False
                    report['cleanup_project'] = project
            try:
                # A unique tag belongs only to this rehearsal; never remove shared images.
                run(['image', 'rm', image_name], docker=True, timeout=60)
                checks['isolated_image_removed'] = True
            except (OSError, RuntimeError, subprocess.SubprocessError):
                checks['isolated_image_removed'] = False
            report['passed'] = report['passed'] and all(checks.values())
            (output / 'deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='fresh report directory; synthetic Docker volumes are removed after the drill')
    args = parser.parse_args()
    try:
        report = rehearse(args.output_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['passed'] else 2
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'error': 'rehearsal_initialization_failed', 'type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
