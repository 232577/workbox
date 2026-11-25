"""Serve a separately supplied copy of the original public frontend with anonymous local data."""

from contextlib import contextmanager
import base64
from copy import deepcopy
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import socket
import unicodedata
from threading import Event, RLock, Thread
from time import monotonic
from urllib.parse import urlsplit


class AnonymousSiteFixture:
    """In-process QA administrator; never exposed as an unauthenticated HTTP API.

    ``login_accounts`` maps account to {account,password,name,role}. Both materials
    users deliberately share a display name so account checks cannot use the name.
    All snapshots are detached; callers can serialize events without credentials.
    """

    def __init__(self, title, browser_salt):
        self._lock = RLock()
        self._accounts = {
            account: dict(account=account, password=password, name='材料甲', role='材料')
            for account, password in (('fixture-material', 'qa-material-a'), ('fixture-material-b', 'qa-material-b'))
        }
        self._projects = {}
        self._credentials = {}
        for account, user in self._accounts.items():
            account_key = unicodedata.normalize('NFC', account).strip().lower()
            salt = ('project-flow:browser-password:v1\0' + browser_salt + '\0' + account_key).encode('utf-8')
            digest = hashlib.pbkdf2_hmac('sha256', user['password'].encode('utf-8'), salt, 600000, 32)
            self._credentials[account] = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
        self._events = []
        self._writes = []
        self._sessions = {}
        self._timeouts = {}
        self._started = monotonic()
        self.stopping = Event()
        self.add_project(55, title)

    @property
    def login_accounts(self):
        return deepcopy(self._accounts)

    @property
    def events(self):
        with self._lock:
            return tuple(deepcopy(self._events))

    @property
    def writes(self):
        with self._lock:
            return deepcopy(self._writes)

    def project(self, task_id):
        with self._lock:
            return deepcopy(self._projects[task_id])

    def add_project(self, task_id, name, owner='fixture-material', status='待制作'):
        with self._lock:
            if (type(task_id) is not int or task_id < 1 or task_id in self._projects or
                    owner not in self._accounts or status not in ('待制作', '进行中', '已完成') or not name.strip()):
                raise ValueError('Invalid anonymous project')
            self._projects[task_id] = dict(id=task_id, name=name, matOwner=owner,
                matOwnerName=self._accounts[owner]['name'], matStatus=status, codeStatus='待领取',
                type='普通', batchId='qa-group', batchName='匿名批次', created='2026-01-15')
            self._events.append(dict(method='ADMIN', path='add-project', account='fixture-admin',
                                     project=deepcopy(self._projects[task_id])))

    def reassign_project(self, task_id, owner):
        with self._lock:
            if owner not in self._accounts:
                raise ValueError('Unknown anonymous owner')
            self._projects[task_id].update(matOwner=owner, matOwnerName=self._accounts[owner]['name'])
            self._events.append(dict(method='ADMIN', path='reassign-project', account='fixture-admin',
                                     project=deepcopy(self._projects[task_id])))

    def arm_complete_timeout(self, task_id, delay_seconds=35):
        with self._lock:
            if task_id not in self._projects or not 0 < delay_seconds <= 120:
                raise ValueError('Invalid anonymous timeout')
            self._timeouts[task_id] = delay_seconds

    def public_user(self, account):
        return {**{key: value for key, value in self._accounts[account].items() if key != 'password'},
                'mustChange': False, 'enabled': True}


@contextmanager
def original_frontend(assets, title):
    assets = assets.resolve(strict=True)
    files = {'/': ('public-index.html', 'text/html'), '/app.js': ('public-app.js', 'text/javascript'),
             '/app.css': ('app.css', 'text/css')}
    for script in ('dashboard.js', 'excel-export.js', 'notifications.js'):
        files['/' + script] = (script, 'text/javascript')
    static = {url: ((assets / name).read_bytes(), mime) for url, (name, mime) in files.items()}
    salt_match = re.search(r'const CLOUD_PASSWORD_SITE_SALT\s*=\s*("[^"\r\n]+")',
                           (assets / 'public-app.js').read_text(encoding='utf-8'))
    if salt_match is None:
        raise ValueError('The unchanged public frontend must supply its browser credential salt.')
    fixture = AnonymousSiteFixture(title, json.loads(salt_match.group(1)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data, media='application/json', cookie=None):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf8')
            self.send_response(status)
            self.send_header('Content-Type', media + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            if cookie is not None:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # The one-shot timeout deliberately lets its caller disconnect.

        def identity(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
            except Exception:
                return None, None
            token = cookie.get('anonymous_original_session')
            token = token.value if token else None
            return fixture._sessions.get(token), token

        def event(self, status, account, **facts):
            fixture._events.append(dict(method=self.command, path=urlsplit(self.path).path,
                                        account=account, status=status,
                                        elapsed_seconds=round(monotonic() - fixture._started, 6), **facts))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in static:
                self.reply(200, *static[path])
            else:
                with fixture._lock:
                    account, _ = self.identity()
                    if path == '/api/bootstrap-status':
                        self.event(200, account)
                        self.reply(200, {'setupRequired': False})
                    elif path not in ('/api/me', '/api/state', '/api/assignment-notifications'):
                        self.reply(404, {})
                    elif not account:
                        self.event(401, None)
                        self.reply(401, {'error': '请登录'})
                    elif path == '/api/me':
                        self.event(200, account)
                        self.reply(200, {'user': fixture.public_user(account)})
                    elif path == '/api/assignment-notifications':
                        self.event(200, account)
                        self.reply(200, {'notifications': [], 'unread': 0})
                    else:
                        projects = deepcopy(list(fixture._projects.values()))
                        self.event(200, account, projects=projects)
                        self.reply(200, {'users': [fixture.public_user(user) for user in fixture._accounts],
                                         'projects': projects})

        def do_POST(self):
            path = urlsplit(self.path).path
            business_write = path in ('/api/start-material', '/api/complete-material')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 65536:
                    raise ValueError()
                request = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(request, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                if business_write:
                    with fixture._lock:
                        account, _ = self.identity()
                        fixture._writes.append((path, None))
                        self.event(400, account, request=None)
                self.reply(400, {'error': '匿名请求格式错误'})
                return
            delay = 0
            with fixture._lock:
                account, token = self.identity()
                if business_write:
                    fixture._writes.append((path, deepcopy(request)))
                if path == '/api/login':
                    login_account = request.get('account')
                    user = fixture._accounts.get(login_account) if isinstance(login_account, str) else None
                    if (set(request) != {'account', 'password', 'credentialScheme'} or not user or
                            request['credentialScheme'] != 'pbkdf2-sha256-v1' or
                            not isinstance(request['password'], str) or
                            not hmac.compare_digest(request['password'], fixture._credentials[user['account']])):
                        self.event(401, None)
                        self.reply(401, {'error': '账号或密码错误'})
                        return
                    token = secrets.token_urlsafe(24)
                    fixture._sessions[token] = user['account']
                    self.event(200, user['account'])
                    self.reply(200, {'ok': True, 'user': fixture.public_user(user['account'])},
                               cookie=f'anonymous_original_session={token}; HttpOnly; SameSite=Strict; Path=/')
                    return
                if not account:
                    self.event(401, None, **({'request': request} if business_write else {}))
                    self.reply(401, {'error': '请登录'})
                    return
                if path == '/api/logout':
                    fixture._sessions.pop(token, None)
                    self.event(200, account)
                    self.reply(200, {'ok': True}, cookie='anonymous_original_session=; Max-Age=0; Path=/')
                    return
                stages = {'/api/start-material': ('待制作', '进行中'),
                          '/api/complete-material': ('进行中', '已完成')}
                ids = request.get('ids')
                task_id = ids[0] if type(ids) is list and len(ids) == 1 and type(ids[0]) is int else None
                project = fixture._projects.get(task_id)
                if (path not in stages or set(request) != {'ids'} or not project or
                        project['matOwner'] != account or project['matStatus'] != stages[path][0]):
                    self.event(409, account, request=request)
                    self.reply(409, {'error': '匿名任务或状态不匹配'})
                    return
                project['matStatus'] = stages[path][1]
                if path == '/api/complete-material':
                    delay = fixture._timeouts.pop(task_id, 0)
                self.event(200, account, request=request, project=deepcopy(project),
                           response='timeout-after-commit' if delay else 'ok', delay_seconds=delay)
            if delay:
                fixture.stopping.wait(delay)
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self.reply(200, {'ok':True})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', fixture
    finally:
        fixture.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join()
