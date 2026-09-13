"""Offline security regressions: no external sites or LAN are contacted."""
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.v2 import egress_gateway as gateway, public_fetch, worker_network, supply
from backend.v2.worker_limits import BudgetError, RollingBudget, GATEWAY_RULES
from backend.v2.security import AccessPolicy
from backend.tests.test_v2_remote_worker import worker, Transport, job, outcome
from backend.tests.test_v2_public_fetch import Connection, Response, DETAIL


def test_worker_hourly_budgets_survive_restart_and_expire_at_boundary(tmp_path):
    now = [100000.0]
    path = tmp_path / 'usage.sqlite3'
    budget = RollingBudget(path=path, clock=lambda: now[0])
    for _ in range(12): budget.admit('search')
    for _ in range(30): budget.admit('import')
    budget.close()
    budget = RollingBudget(path=path, clock=lambda: now[0])
    for kind in ('search', 'import'):
        with pytest.raises(BudgetError, match='worker_rate_limited'): budget.admit(kind)
    now[0] += 3600
    budget.admit('search')
    budget.admit('import')
    budget.close()


def test_worker_daily_budget_combines_operations_and_is_rolling():
    now = [100000.0]
    budget = RollingBudget(clock=lambda: now[0])
    for hour in range(3):
        for _ in range(12): budget.admit('search')
        for _ in range(28): budget.admit('import')
        now[0] += 3600
    with pytest.raises(BudgetError, match='worker_rate_limited'): budget.admit('search')
    now[0] = 100000 + 86400
    budget.admit('import')
    budget.close()


def test_quota_admission_is_atomic_across_connections(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    first, second = RollingBudget(path=path), RollingBudget(path=path)
    def admit(index):
        try:
            (first if index % 2 else second).admit('search')
            return True
        except BudgetError as error:
            assert error.code == 'worker_rate_limited'
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(admit, range(32))) == 12
    first.close()
    second.close()


def test_corrupt_budget_and_rollback_fail_closed(tmp_path):
    path = tmp_path / 'bad.sqlite3'
    path.write_bytes(b'not a database')
    with pytest.raises(BudgetError): RollingBudget(path=path)
    now = [1000.0]
    budget = RollingBudget(clock=lambda: now[0])
    budget.admit('search')
    now[0] = 990
    with pytest.raises(BudgetError, match='worker_budget_unavailable'): budget.admit('import')
    assert budget.db.execute('SELECT count(*) FROM budget_events').fetchone()[0] == 1
    budget.close()


def test_gateway_limits_separate_heartbeat_from_source_exhaustion():
    budget = RollingBudget(GATEWAY_RULES)
    for _ in range(60): budget.admit('source')
    with pytest.raises(BudgetError, match='worker_rate_limited'): budget.admit('source')
    budget.admit('cloud')
    budget.close()


def test_worker_rejects_over_budget_before_source_and_upload_retry_is_not_new_work(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_SEARCH_SOURCE', 'suumo')
    monkeypatch.setenv('HOUSE_EVALUATOR_IMPORT_SOURCES', 'chintai,yahoo_realestate')
    from backend.v2.remote_worker import WorkerError
    budget = RollingBudget(((('search',), 3600, 1),))
    calls = []
    transport = Transport(job(), WorkerError(), {}, job(job_id='k' * 32), {})
    runner = worker(transport, budget=budget, search_fn=lambda p: calls.append(p) or {})
    runner.step()
    runner.step()  # retry only the result upload
    assert len(calls) == 1 and outcome(transport)['status'] == 'complete'
    runner.step()
    assert len(calls) == 1 and outcome(transport)['error']['code'] == 'worker_rate_limited'
    assert budget.db.execute('SELECT count(*) FROM budget_events').fetchone()[0] == 1
    budget.close()


def test_invalid_job_does_not_spend_source_budget():
    budget = RollingBudget()
    transport = Transport(job(payload={'command': 'invalid'}), {})
    worker(transport, budget=budget, search_fn=lambda p: pytest.fail('source called')).step()
    assert outcome(transport)['status'] == 'failed'
    assert budget.db.execute('SELECT count(*) FROM budget_events').fetchone()[0] == 0
    budget.close()


@pytest.mark.parametrize('line', [
    b'CONNECT 127.0.0.1:443\n', b'CONNECT 192.168.1.1:443\n', b'CONNECT 169.254.169.254:443\n',
    b'CONNECT [::1]:443\n', b'CONNECT suumo.jp.evil.invalid:443\n', b'CONNECT suumo.jp:80\n',
    b'CONNECT suumo.jp:443\r\n', b'CONNECT user@suumo.jp:443\n', b'CONNECT https://suumo.jp:443\n',
    b'CONNECT suumo.jp:443\nGET /private', b'x' * 129,
])
def test_gateway_rejects_unapproved_destinations_before_network(line, monkeypatch):
    monkeypatch.setattr(gateway, '_bounded_dns', lambda *a: pytest.fail('DNS reached'))
    with pytest.raises(ValueError): gateway.destination(line)


@pytest.mark.parametrize('host', sorted(worker_network.ALLOWED_HOSTS))
def test_exact_allowed_destination(host):
    assert gateway.destination(('CONNECT ' + host + ':443\n').encode()) == host


@pytest.mark.parametrize('address', ['127.0.0.1', '192.168.1.1', '169.254.169.254', '100.64.0.1',
                                     '::1', 'fc00::1', 'fe80::1', '224.0.0.1', 'ff02::1',
                                     '::ffff:8.8.8.8', '2002:0808:0808::1', '64:ff9b::808:808'])
def test_mixed_dns_answer_rejects_entire_destination(address):
    def resolver(*args, **kwargs):
        return [(None, None, None, None, (ip, 443)) for ip in ['8.8.8.8', address]]
    with pytest.raises(supply.SupplyError, match='network_target_denied'):
        supply._public_addresses('suumo.jp', 443, resolver=resolver)


def test_gateway_connects_only_once_to_validated_literal_ip(monkeypatch):
    request = Mock()
    request.recv.side_effect = [bytes([c]) for c in b'CONNECT suumo.jp:443\n']
    dns = Mock(return_value=['8.8.8.8'])
    connection, relay = Mock(), Mock()
    monkeypatch.setattr(gateway, '_bounded_dns', dns)
    monkeypatch.setattr(gateway.socket, 'create_connection', connection)
    monkeypatch.setattr(gateway, 'relay', relay)
    server = SimpleNamespace(budget=Mock())
    gateway.Handler(request, None, server)
    assert dns.call_count == connection.call_count == relay.call_count == 1
    assert connection.call_args.args == (('8.8.8.8', 443),)
    request.sendall.assert_called_once_with(b'OK\n')
    connection.return_value.close.assert_called_once()


def test_gateway_budget_failure_never_resolves_or_connects(monkeypatch):
    request = Mock()
    request.recv.side_effect = [bytes([c]) for c in b'CONNECT suumo.jp:443\n']
    monkeypatch.setattr(gateway, '_bounded_dns', lambda *a: pytest.fail('DNS reached'))
    server = SimpleNamespace(budget=Mock())
    server.budget.admit.side_effect = BudgetError('worker_rate_limited')
    gateway.Handler(request, None, server)
    request.sendall.assert_called_once_with(b'DENY\n')


@pytest.mark.parametrize('direction', ['upload', 'download'])
def test_gateway_stops_over_byte_budget(direction, monkeypatch):
    monkeypatch.setattr(gateway, 'UPLOAD_BYTES', 4)
    monkeypatch.setattr(gateway, 'DOWNLOAD_BYTES', 4)
    source, client = socket.socketpair()
    upstream, target = socket.socketpair()
    thread = threading.Thread(target=gateway.relay, args=(client, upstream, time.monotonic()+1))
    try:
        thread.start()
        (source if direction == 'upload' else target).sendall(b'oversize')
        thread.join(2)
        assert not thread.is_alive()
        receiver = target if direction == 'upload' else source
        receiver.settimeout(.03)
        with pytest.raises(TimeoutError): receiver.recv(1)
    finally:
        for item in (source, client, upstream, target): item.close()


def test_gateway_total_deadline_stops_idle_tunnel():
    source, client = socket.socketpair()
    upstream, target = socket.socketpair()
    try:
        start = time.monotonic()
        gateway.relay(client, upstream, start + .03)
        assert time.monotonic() - start < .5
    finally:
        for item in (source, client, upstream, target): item.close()


def test_isolated_fetch_has_no_worker_dns_or_direct_fallback(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_EGRESS_SOCKET', worker_network.SOCKET_PATH)
    monkeypatch.setattr(public_fetch, '_bounded_dns', lambda *a: pytest.fail('worker DNS reached'))
    monkeypatch.setattr(public_fetch, '_PinnedHTTPSConnection', lambda *a, **k: pytest.fail('direct reached'))
    connection = Connection(Response())
    monkeypatch.setattr(public_fetch, 'GatewayHTTPSConnection', lambda *a, **k: connection)
    assert public_fetch.fetch_public(DETAIL, deadline=time.monotonic()+2).status == 200
    def fail(*a, **k): raise OSError('gateway_down')
    connection.request = fail
    with pytest.raises(public_fetch.PublicFetchError):
        public_fetch.fetch_public(DETAIL, deadline=time.monotonic()+2)


def test_gateway_tls_validates_original_hostname(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_EGRESS_SOCKET', worker_network.SOCKET_PATH)
    monkeypatch.setattr(worker_network.socket, 'AF_UNIX', 1, raising=False)
    sock, context = Mock(), Mock()
    sock.recv.side_effect = [b'O', b'K', b'\n']
    monkeypatch.setattr(worker_network.socket, 'socket', Mock(return_value=sock))
    connection = worker_network.GatewayHTTPSConnection('suumo.jp', context=context)
    connection.connect()
    sock.connect.assert_called_once_with(worker_network.SOCKET_PATH)
    sock.sendall.assert_called_once_with(b'CONNECT suumo.jp:443\n')
    context.wrap_socket.assert_called_once_with(sock, server_hostname='suumo.jp', do_handshake_on_connect=False)
    context.wrap_socket.return_value.do_handshake.assert_called_once()
    connection.close()


def test_wrong_gateway_path_is_configuration_error(monkeypatch):
    monkeypatch.setenv('HOUSE_EVALUATOR_EGRESS_SOCKET', '/tmp/attacker.sock')
    with pytest.raises(ValueError): worker_network.gateway_enabled()


def test_failed_login_budget_does_not_lock_out_valid_users():
    policy = AccessPolicy('x' * 43)
    assert all(policy.allow_request('proxy', authenticated=False) for _ in range(10))
    assert not policy.allow_request('proxy', authenticated=False)
    assert policy.allow_request('proxy', authenticated=True)
