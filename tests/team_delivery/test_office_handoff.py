from hashlib import sha256
from types import SimpleNamespace

import pytest

from workbox.team_delivery.contracts import GatewayError, Material
from workbox.team_delivery.office_handoff import HandoffLog, handoff_manual
from workbox.team_delivery.workbox_identity import WorkBoxAccount


class Office:
    def __init__(self):
        self.sent = []
        self.accepted = False
        self.lose_response = False
        self.member = WorkBoxAccount('http://office', '3', 'manual', 'session')

    def current_account(self):
        return self.member

    def call(self, member, method, *args):
        assert member == self.member
        if method == 'target':
            return 'a4ea2ca0-5359-4d6b-801e-6ef19ea5a14a'
        if method == 'write':
            self.sent.append(args)
            self.accepted = True
            if self.lose_response:
                raise GatewayError('连接中断')
        if method == 'verify':
            return self.accepted
        return True


def test_lost_upload_reply_recovers_only_by_reading_same_handoff(tmp_path):
    from anonymous_document import make_document
    path = tmp_path / '完整项目名.docx'
    make_document(path)
    material = Material(path.name, path, sha256(path.read_bytes()).hexdigest())
    prepared = SimpleNamespace(actor=SimpleNamespace(id='original-user'), task=SimpleNamespace(
        id='55', title=path.stem), material=material)
    office = Office()
    office.lose_response = True
    journal = HandoffLog(tmp_path/'state.sqlite')
    office_id = handoff_manual(office, office.member, prepared, journal, lambda: None)
    assert handoff_manual(office, office.member, prepared, journal, lambda: None) == office_id
    assert len(office.sent) == 1
    office.accepted = False
    with pytest.raises(GatewayError):
        handoff_manual(office, office.member, prepared, journal, lambda: None)
    assert len(office.sent) == 1


def test_expired_gui_request_cannot_send_after_worker_releases_the_reservation():
    from threading import Lock
    from workbox.team_delivery.office_connection import OfficeConnection, _Call
    job = _Call(None, 'write', (), deadline=0)
    connection = SimpleNamespace(_pending={job}, _lock=Lock())
    assert not OfficeConnection._begin_script(connection, job)
    assert not job.started
    job.deadline = float('inf')
    connection._pending.remove(job)
    assert not OfficeConnection._begin_script(connection, job)
    connection._pending.add(job)
    assert OfficeConnection._begin_script(connection, job)
    assert job.started
