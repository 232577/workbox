from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton


PROGRAM = '/?controller=SimpleProjectsController&action=show&plugin=WorkBoxIntake'
UPLOADS = '/?controller=SimpleUploadsController&action=show&plugin=WorkBoxIntake'
PACKAGE = '/?controller=MaterialDownloadController&action=package&plugin=WorkBoxIntake'
FILE_BYTES = b'Anonymous package download sample'


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def write(self, body, *, cookie=None, location=None, download=False):
        payload = body if isinstance(body, bytes) else (
            '<!doctype html><meta charset=utf-8><title>匿名办公站</title>' + body).encode()
        self.send_response(303 if location else 200)
        self.send_header('Content-Type', 'application/zip' if download else 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="anonymous.zip"')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.server.visited.append(self.path)
        route = parse_qs(urlsplit(self.path).query)
        controller = route.get('controller', [''])[0]
        action = route.get('action', [''])[0]
        cookie = self.headers.get('Cookie', '')
        actor = '1' if 'KB_SID=dispatcher' in cookie else '2' if 'KB_SID=reader' in cookie else None
        if self.path == '/renew' and actor == '1':
            self.write('', cookie='KB_SID=dispatcher-renewed; Path=/; HttpOnly', location=PROGRAM)
            return
        if controller == 'AuthController' and action == 'logout':
            self.write('', cookie='KB_SID=; Path=/; Max-Age=0; HttpOnly', location='/')
            return
        if actor is None:
            self.write('<form class="form-login" method="post" action="/?controller=AuthController&amp;action=check">'
                       '<input name="username"><input name="password" type="password">'
                       '<button id="login">登录</button></form>')
            return
        name = '匿名分发人' if actor == '1' else '匿名查看人'
        profile = f'/?controller=UserViewController&amp;action=show&amp;user_id={actor}'
        header = '<header class="wb-simple-header"><details class="wb-simple-account"><div>' + (
            f'<a href="{profile}">个人资料</a></div></details></header>')
        if controller == 'UserViewController':
            self.write(header + f'<main id="main"><div id="user-section"><aside class="sidebar"><ul><li>'
                       f'<a href="{profile}">概要</a></li></ul></aside><div class="sidebar-content">'
                       f'<ul class="panel"><li>用户名：<strong>{name}</strong></li></ul></div></div></main>')
        elif controller == 'WorkflowController':
            roles = ('<a href="/?controller=TaskListController&amp;action=show&amp;project_id=1">分发</a>'
                     '<a href="/?controller=WorkflowController&amp;action=code&amp;plugin=WorkBoxIntake">代码/TXT</a>'
                     '<a href="/?controller=SubmissionController&amp;action=show&amp;plugin=WorkBoxReporting">对外提交</a>'
                     if actor == '1' else '')
            self.write(header + '<nav aria-label="工作导航"><a '
                       'href="/?controller=ReportingController&amp;action=show&amp;plugin=WorkBoxReporting">统计</a>'
                       '<a href="/?controller=TaskListController&amp;action=show&amp;project_id=1">项目总表</a>'
                       '<a href="/?controller=SubmissionController&amp;action=show&amp;plugin=WorkBoxReporting">提交</a></nav>'
                       f'<ul class="views" aria-label="工作视图">{roles}</ul>')
        elif self.path == PACKAGE:
            self.write(FILE_BYTES, download=True)
        elif self.path == UPLOADS:
            self.write(header + '<form method="post" enctype="multipart/form-data" action="/handoff">'
                       '<input id="word" type="file" name="manual_file" accept=".docx">'
                       '<button id="send">上传并交接</button></form>')
        else:
            external = escape(self.server.external)
            self.write(header + f'<h1>项目表</h1><a id="package" href="{escape(PACKAGE)}">下载成果</a>'
                       f'<a id="same" target="_blank" href="{escape(UPLOADS)}">上传文件</a>'
                       f'<a id="foreign" target="_blank" href="{external}/external">其他地址</a>'
                       f'<img src="{external}/forbidden.png" alt=""><iframe src="{external}/frame"></iframe>')

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/handoff':
            self.server.uploads.append(data)
            self.write('<p>手册已交接</p>')
        else:
            name = parse_qs(data.decode())['username'][0]
            assert name in ('dispatcher', 'reader')
            self.write('', cookie=f'KB_SID={name}; Path=/; HttpOnly', location=PROGRAM)


@contextmanager
def _site():
    with ThreadingHTTPServer(('127.0.0.1', 0), _Site) as server:
        server.external = ''
        server.visited, server.uploads = [], []
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', server
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def desktop():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def panel(desktop):
    from workbox.team_delivery.office_panel import OfficePanel

    widget = OfficePanel()
    widget.resize(900, 660)
    widget.show()
    try:
        yield widget
    finally:
        widget.shutdown()
        widget.close()
        QTest.qWait(50)


def _wait(check, timeout=10000):
    deadline = monotonic() + timeout / 1000
    while monotonic() < deadline:
        if check():
            return
        QTest.qWait(20)
    assert check(), 'Qt did not reach the expected browser state'


def _js(panel, code, world=QWebEngineScript.ScriptWorldId.MainWorld.value):
    result = []
    panel.page.runJavaScript(code, world, result.append)
    _wait(lambda: bool(result))
    return result[0]


def test_office_login_state_distinguishes_signed_out_from_unknown(panel):
    from workbox.team_delivery.office_connection import OfficeConnection

    with _site() as (origin, _):
        connection = OfficeConnection(panel)
        try:
            assert connection.login_state() == 'unknown'
            panel.connect_to(origin)
            _wait(lambda: connection.login_state() == 'signed-out')
            assert connection.current_account() is None
            panel._observed(panel.page, panel._generation, panel._probe_sequence, 'false')
            assert connection.login_state() == 'unknown'
            assert connection.current_account() is None
        finally:
            connection.close()


def _click(panel, element_id):
    _wait(lambda: _js(panel, f'Boolean(document.getElementById({element_id!r}))'))
    x, y = json.loads(_js(panel, f'''JSON.stringify((() => {{
        const box = document.getElementById({element_id!r}).getBoundingClientRect();
        return [Math.round((box.left + box.right) / 2), Math.round((box.top + box.bottom) / 2)];
    }})())'''))
    view = panel.findChild(QWebEngineView)
    QTest.mouseClick(view.focusProxy() or view, Qt.MouseButton.LeftButton, pos=QPoint(x, y))


def _login(panel, name):
    _wait(lambda: _js(panel, 'Boolean(document.querySelector(".form-login"))'))
    _js(panel, f'document.querySelector("[name=username]").value = {name!r}')
    _js(panel, 'document.querySelector("[name=password]").value = "anonymous-password"')
    _click(panel, 'login')


def test_office_dispatch_uses_current_confirmation_of_both_accounts(panel):
    from workbox.team_delivery.pair_guard import PairGuard

    gate = panel.pair_guard = PairGuard()
    with _site() as (origin, server):
        panel.connect_to(origin)
        _wait(lambda: _js(panel, 'Boolean(document.querySelector(".form-login"))'))
        assert gate.confirm(gate.generation())

        def post(token):
            _js(panel, "window.reply=''; fetch('/handoff',{method:'POST',body:'sample',headers:{"
                "'X-WorkBox-Auto':" + json.dumps(token) + "}}).then(r=>window.reply='ok',"
                "()=>window.reply='blocked'); true")
            _wait(lambda: bool(_js(panel, 'window.reply')))
            return _js(panel, 'window.reply')

        valid = gate.write_token()
        assert post(valid) == 'ok' and gate.finish_write(valid)
        queued = gate.write_token()
        gate.invalidate()  # The other browser started changing its login after the office form was prepared.
        assert post(queued) == 'blocked' and not gate.finish_write(queued)
        assert server.uploads == [b'sample']
        assert gate.confirm(gate.generation())
        _js(panel, "fetch('/?controller=AuthController&action=login'); true")
        _wait(lambda: not gate.confirmed())


def test_office_session_role_routes_and_account_switch(panel):
    with _site() as (origin, server):
        server.external = origin
        changes = []
        panel.account_changed.connect(changes.append)
        panel.connect_to(origin)
        assert panel.page.profile().isOffTheRecord()
        assert panel.account is None
        _login(panel, 'dispatcher')
        _wait(lambda: panel.account is not None and panel.account.username == '匿名分发人')
        dispatch = panel.findChild(QAction, 'officeDispatch')
        _wait(dispatch.isVisible)
        dispatch.trigger()
        _wait(lambda: 'controller=TaskListController' in panel.page.url().toString())
        original_profile = panel.page.profile()
        original_account = panel.account
        panel.connect_to(origin + '/')
        assert panel.page.profile() is original_profile
        assert panel.account == original_account
        panel.navigate(origin + '/renew')
        _wait(lambda: panel.account is not None and panel.account.session != original_account.session)
        assert panel.account.user_id == original_account.user_id
        panel.findChild(QPushButton, 'officeSwitchAccount').click()
        assert panel.account is None and changes[-1] is None
        assert panel.page.profile() is not original_profile
        _login(panel, 'reader')
        _wait(lambda: panel.account is not None and panel.account.username == '匿名查看人')
        assert not dispatch.isVisible()
        assert not panel.findChild(QAction, 'officeSubmission').isVisible()
        assert panel.findChild(QAction, 'officeReporting').isVisible()


def test_office_same_origin_upload_download_and_popup(panel, tmp_path, monkeypatch):
    destination = tmp_path / '匿名成果.zip'
    manual = tmp_path / '匿名手册.docx'
    manual.write_bytes(b'Anonymous manual upload sample')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args, **_kw: (str(destination), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_args, **_kw: (str(manual), ''))
    with _site() as (origin, server), _site() as (other_origin, other):
        server.external = other_origin
        panel.connect_to(origin)
        _login(panel, 'dispatcher')
        _wait(lambda: panel.account is not None)
        _click(panel, 'foreign')
        QTest.qWait(200)
        assert not other.visited
        assert panel.page.url().toString() == origin + PROGRAM
        _click(panel, 'package')
        _wait(destination.exists)
        assert destination.read_bytes() == FILE_BYTES
        _click(panel, 'same')
        _wait(lambda: panel.page.url().toString() == origin + UPLOADS)
        _click(panel, 'word')
        _wait(lambda: _js(panel, 'document.querySelector("#word").files.length') == 1)
        _click(panel, 'send')
        _wait(lambda: len(server.uploads) == 1)
        assert manual.read_bytes() in server.uploads[0]
        assert not other.visited


def test_superseded_office_probe_cannot_overwrite_new_identity(panel, monkeypatch):
    from workbox.team_delivery import office_panel

    with _site() as (origin, _server):
        panel.connect_to(origin)
        _wait(lambda: _js(panel, 'Boolean(document.querySelector(".form-login"))'))
        panel._timer.stop()
        panel._cancel_probe()
        world = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
        _js(panel, 'window.officeTestProbes = []; true', world)
        monkeypatch.setattr(office_panel, '_PROBE',
                            '() => new Promise(resolve => window.officeTestProbes.push(resolve))')

        panel._begin_probe(panel.page, monotonic())
        panel._cancel_probe()
        panel._begin_probe(panel.page, monotonic())
        assert _js(panel, '''
            window.officeTestProbes[1]({account:{id:'2',username:'新账号'}});
            window.officeTestProbes[0]({account:{id:'1',username:'旧账号'}});
            true
        ''', world)
        panel._observe()
        _wait(lambda: panel.account is not None)
        assert panel.account.username == '新账号'
