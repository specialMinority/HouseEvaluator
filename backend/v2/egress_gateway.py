"""Bounded opaque TLS relay, reachable only on a dedicated Docker Unix socket.

It never sees HTTP credentials or bodies. Only four exact public HTTPS hosts
are reachable. No listening TCP port, arbitrary proxy URL, or tunnel fallback.
"""
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import stat
import threading
import time

from .supply import SupplyError, _bounded_dns
from .worker_limits import BudgetError, GATEWAY_RULES, RollingBudget
from .worker_network import ALLOWED_HOSTS, SOCKET_PATH

TUNNEL_SECONDS = 12
UPLOAD_BYTES = 2 * 1024 * 1024
DOWNLOAD_BYTES = 8 * 1024 * 1024


def destination(line):
    if not isinstance(line, bytes) or len(line) > 128:
        raise ValueError()
    match = re.fullmatch(rb'CONNECT ([a-z0-9.-]+):443\n', line)
    host = match[1].decode('ascii') if match else None
    if host not in ALLOWED_HOSTS:
        raise ValueError()
    return host


def relay(client, upstream, deadline):
    counts = [0, 0]
    while time.monotonic() < deadline:
        ready, _, _ = select.select([client, upstream], [], [], min(.25, max(.001, deadline - time.monotonic())))
        for incoming in ready:
            index = 0 if incoming is client else 1
            outgoing = upstream if index == 0 else client
            incoming.settimeout(max(.001, deadline - time.monotonic()))
            data = incoming.recv(65536)
            if not data:
                return
            counts[index] += len(data)
            if counts[index] > (UPLOAD_BYTES if index == 0 else DOWNLOAD_BYTES):
                return
            outgoing.settimeout(max(.001, deadline - time.monotonic()))
            outgoing.sendall(data)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream, accepted = None, False
        deadline = time.monotonic() + TUNNEL_SECONDS
        try:
            line = b''
            while len(line) < 128 and not line.endswith(b'\n'):
                self.request.settimeout(max(.001, deadline - time.monotonic()))
                if time.monotonic() >= deadline:
                    return
                chunk = self.request.recv(1)
                if not chunk:
                    return
                line += chunk
            host = destination(line)
            self.server.budget.admit('cloud' if host == 'houseevaluator-personal.onrender.com' else 'source')
            address = _bounded_dns(host, 443, max(.001, deadline - time.monotonic()))[0]
            # DNS validation returns a literal public IP; do not resolve twice.
            upstream = socket.create_connection((address, 443), timeout=max(.001, deadline - time.monotonic()))
            self.request.sendall(b'OK\n')
            accepted = True
            relay(self.request, upstream, deadline)
        except (OSError, ValueError, SupplyError, BudgetError):
            if not accepted:
                try:
                    self.request.settimeout(.1)
                    self.request.sendall(b'DENY\n')
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()


class GatewayServer(socketserver.ThreadingMixIn, getattr(socketserver, 'UnixStreamServer', socketserver.TCPServer)):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, path, budget):
        if os.name != 'posix':
            raise ValueError('gateway_requires_container')
        self.budget, self.slots = budget, threading.BoundedSemaphore(4)
        super().__init__(path, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # No peer messages, paths, TLS bytes or exception details in logs.
        pass


def main():
    if os.name != 'posix':
        raise SystemExit('gateway_requires_container')
    path = Path(SOCKET_PATH)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit('invalid_gateway_socket')
        path.unlink()
    budget = RollingBudget(GATEWAY_RULES, path='/state/gateway/budget.sqlite3')
    with GatewayServer(SOCKET_PATH, budget) as server:
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == '__main__':
    main()
