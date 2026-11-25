"""Validate automatic writes from both browser sessions immediately before dispatch."""

from secrets import token_hex
from threading import Lock

from workbox.team_delivery.contracts import GatewayError


class PairGuard:
    def __init__(self):
        self._lock = Lock()
        self._generation = 0
        self._confirmed = False
        self._pending = set()
        self._sent = set()

    def generation(self):
        with self._lock:
            return self._generation

    def confirm(self, generation):
        with self._lock:
            if generation != self._generation:
                return False
            self._confirmed = True
            return True

    def confirmed(self):
        with self._lock:
            return self._confirmed

    def invalidate(self):
        with self._lock:
            self._confirmed = False
            self._generation += 1
            self._pending.clear()

    def write_token(self):
        with self._lock:
            if not self._confirmed:
                raise GatewayError('登录已变化，请重新核对两个账号。')
            token = token_hex(16)
            self._pending.add(token)
            return token

    def accept_request(self, request):
        for name, value in request.httpHeaders().items():
            if bytes(name).lower() != b'x-workbox-auto':
                continue
            token = bytes(value).decode('ascii', errors='replace')
            with self._lock:
                if not self._confirmed or token not in self._pending:
                    return False
                self._pending.remove(token)
                self._sent.add(token)
                return True
        return True  # Ordinary site navigation and actions initiated by the user.

    def finish_write(self, token):
        with self._lock:
            self._pending.discard(token)
            sent = token in self._sent
            self._sent.discard(token)
            return sent
