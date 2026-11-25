import pytest
from PySide6.QtCore import QByteArray

from workbox.team_delivery.contracts import GatewayError
from workbox.team_delivery.pair_guard import PairGuard


class Request:
    def __init__(self, token=None):
        self.token = token

    def httpHeaders(self):
        if self.token is None:
            return {}
        return {QByteArray(b'x-workbox-auto'): QByteArray(self.token.encode())}


def test_changed_login_blocks_both_queued_writes_and_old_confirmation():
    guard = PairGuard()
    original = guard.generation()
    assert guard.confirm(original)
    office, site = guard.write_token(), guard.write_token()
    guard.invalidate()
    assert not guard.confirm(original)
    with pytest.raises(GatewayError):
        guard.write_token()
    assert guard.confirm(guard.generation())
    for token in (office, site):
        assert not guard.accept_request(Request(token))
        assert not guard.finish_write(token)
    assert guard.accept_request(Request())


def test_dispatched_request_stays_uncertain_across_logout_and_cannot_be_reused():
    guard = PairGuard()
    assert guard.confirm(guard.generation())
    token = guard.write_token()
    assert guard.accept_request(Request(token))
    assert not guard.accept_request(Request(token))
    guard.invalidate()
    assert guard.finish_write(token)
    assert not guard.accept_request(Request(token))
