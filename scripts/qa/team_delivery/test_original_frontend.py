"""HTTP boundary regression for the anonymous original-site acceptance fixture."""

from http.cookiejar import CookieJar
from http.client import RemoteDisconnected
import base64
import hashlib
import json
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from original_frontend import original_frontend


@pytest.fixture
def running(tmp_path):
    for name in ('public-index.html', 'public-app.js', 'app.css', 'dashboard.js', 'excel-export.js', 'notifications.js'):
        (tmp_path / name).write_text('anonymous fixture', encoding='utf-8')
    (tmp_path / 'public-app.js').write_text('const CLOUD_PASSWORD_SITE_SALT = "anonymous-test-salt";', encoding='utf-8')
    with original_frontend(tmp_path, '匿名同名系统') as fixture:
        yield fixture


def client(base):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(path, body=None, timeout=2):
        if path == '/api/login' and body:
            salt = ('project-flow:browser-password:v1\0anonymous-test-salt\0' + body['account']).encode()
            digest = hashlib.pbkdf2_hmac('sha256', body['password'].encode(), salt, 600000, 32)
            body = {**body, 'password': base64.urlsafe_b64encode(digest).decode().rstrip('='),
                    'credentialScheme': 'pbkdf2-sha256-v1'}
        data = None if body is None else json.dumps(body).encode()
        request = Request(base + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=timeout) as reply:
                return reply.status, json.load(reply)
        except HTTPError as error:
            return error.code, json.load(error)

    return call


def test_native_login_is_required_and_logout_invalidates_cookie(running):
    base, fixture = running
    call = client(base)
    assert call('/api/me')[0] == 401
    assert call('/api/bootstrap-status') == (200, {'setupRequired': False})
    assert call('/api/state')[0] == 401
    assert call('/api/assignment-notifications')[0] == 401
    assert call('/api/start-material', {'ids': [55]})[0] == 401
    assert call('/api/login', {'account': 'fixture-material', 'password': 'wrong'})[0] == 401
    assert call('/api/login', {'account': 'fixture-material', 'password': 'qa-material-a'})[0] == 200
    assert call('/api/me')[1]['user']['account'] == 'fixture-material'
    assert call('/api/assignment-notifications') == (200, {'notifications': [], 'unread': 0})
    assert call('/api/logout', {})[0] == 200
    assert call('/api/me')[0] == 401


def test_admin_reassignment_same_name_and_singleton_write_checks(running):
    base, fixture = running
    call = client(base)
    call('/api/login', {'account': 'fixture-material', 'password': 'qa-material-a'})
    fixture.add_project(56, '匿名同名系统')
    assert len(call('/api/state')[1]['projects']) == 2
    assert call('/api/start-material', {'ids': [55, 56]})[0] == 409
    assert call('/api/start-material', {'ids': [True]})[0] == 409
    fixture.reassign_project(55, 'fixture-material-b')
    assert call('/api/start-material', {'ids': [55]})[0] == 409
    assert call('/api/start-material', {'ids': [56]})[0] == 200
    snapshot = fixture.project(56)
    snapshot['name'] = 'must not mutate'
    assert fixture.project(56)['name'] == '匿名同名系统'
    assert fixture.login_accounts['fixture-material']['name'] == fixture.login_accounts['fixture-material-b']['name']


def test_complete_timeout_is_committed_once_and_state_is_authoritative(running):
    base, fixture = running
    call = client(base)
    call('/api/login', {'account': 'fixture-material', 'password': 'qa-material-a'})
    assert call('/api/start-material', {'ids': [55]})[0] == 200
    fixture.arm_complete_timeout(55, delay_seconds=0.5)
    with pytest.raises((TimeoutError, socket.timeout)):
        call('/api/complete-material', {'ids': [55]}, timeout=0.05)
    status, state = call('/api/state')
    assert status == 200 and state['projects'][0]['matStatus'] == '已完成'
    assert call('/api/complete-material', {'ids': [55]})[0] == 409
    events = fixture.events
    committed = [event for event in events if event.get('response') == 'timeout-after-commit']
    assert len(committed) == 1 and committed[0]['account'] == 'fixture-material'
    assert committed[0]['delay_seconds'] == 0.5
    assert committed[0]['elapsed_seconds'] >= 0
    readback = [event for event in events if event['path'] == '/api/state'][-1]
    assert readback['projects'][0]['matStatus'] == '已完成'
    events[0]['account'] = 'must not mutate'
    assert fixture.events[0]['account'] != 'must not mutate'


def test_every_business_attempt_is_recorded_without_login_password(running):
    base, fixture = running
    call = client(base)
    call('/api/complete-material', {'ids': [55]})
    call('/api/login', {'account': 'fixture-material', 'password': 'qa-material-a'})
    call('/api/start-material', {'ids': [55, 56]})
    assert fixture.writes == [('/api/complete-material', {'ids': [55]}),
                              ('/api/start-material', {'ids': [55, 56]})]
    assert 'qa-material-a' not in json.dumps(fixture.events)
    attempts = [event for event in fixture.events if event['path'] in ('/api/start-material', '/api/complete-material')]
    assert [event['status'] for event in attempts] == [401, 409]
    assert all('request' in event for event in attempts)


def test_injected_timeout_never_delivers_a_late_success(running):
    base, fixture = running
    call = client(base)
    call('/api/login', {'account': 'fixture-material', 'password': 'qa-material-a'})
    call('/api/start-material', {'ids': [55]})
    fixture.arm_complete_timeout(55, delay_seconds=0.02)
    with pytest.raises(RemoteDisconnected):
        call('/api/complete-material', {'ids': [55]}, timeout=2)
    assert call('/api/state')[1]['projects'][0]['matStatus'] == '已完成'
