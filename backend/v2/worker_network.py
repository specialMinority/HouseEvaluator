"""TLS through the operator's Unix-socket gateway; never a user-supplied proxy."""
import http.client
import os
import socket
import time

SOCKET_PATH = '/run/houseevaluator-egress/https.sock'
ALLOWED_HOSTS = frozenset(('houseevaluator-personal.onrender.com', 'suumo.jp', 'www.chintai.net', 'realestate.yahoo.co.jp'))


def gateway_enabled():
    value = os.getenv('HOUSE_EVALUATOR_EGRESS_SOCKET')
    if value is None:
        return False
    if value != SOCKET_PATH:
        raise ValueError('invalid_egress_configuration')
    return True


class GatewayHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=443, *, timeout=10, context=None):
        if host not in ALLOWED_HOSTS or port != 443 or not gateway_enabled():
            raise ValueError('invalid_egress_configuration')
        super().__init__(host, port=443, timeout=timeout, context=context)

    def connect(self):
        deadline = getattr(self, '_absolute_deadline', time.monotonic() + self.timeout)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._transport = sock
        try:
            sock.settimeout(max(.001, deadline - time.monotonic()))
            sock.connect(SOCKET_PATH)
            sock.sendall(('CONNECT ' + self.host + ':443\n').encode('ascii'))
            reply = b''
            while not reply.endswith(b'\n') and len(reply) < 16:
                sock.settimeout(max(.001, deadline - time.monotonic()))
                chunk = sock.recv(1)
                if not chunk:
                    break
                reply += chunk
            if reply != b'OK\n' or time.monotonic() >= deadline:
                raise OSError('egress_denied')
            sock.settimeout(max(.001, deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
            self._transport = self.sock
            self.sock.do_handshake()
        except BaseException:
            self._transport.close()
            raise
