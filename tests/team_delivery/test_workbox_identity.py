from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from workbox.team_delivery.contracts import GatewayError


_ACCOUNTS = {'17': 'anonymous-admin', '26': 'anonymous-member'}
_PROFILE = '/?controller=UserViewController&action=show'


@pytest.fixture
def workbox_type():
    from workbox.team_delivery.workbox_identity import WorkBoxBrowser

    return WorkBoxBrowser


@pytest.fixture
def server():
    requests = []
    foreign_url = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, html, *, cookie=None, redirect=None):
            data = ('<!doctype html><meta charset="utf-8">' + html).encode('utf-8')
            self.send_response(302 if redirect else 200)
            if cookie is not None:
                self.send_header('Set-Cookie', f'fixture={cookie}; Path=/; HttpOnly; SameSite=Strict')
            if redirect:
                self.send_header('Location', redirect)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            requests.append(self.path)
            url = urlsplit(self.path)
            fields = parse_qs(url.query)
            if url.path == '/choose':
                self.reply('', cookie=fields['as'][0], redirect='/')
                return
            if url.path == '/leave':
                self.reply('', redirect=foreign_url[0])
                return
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            actor = cookie['fixture'].value if 'fixture' in cookie else ''
            if actor == 'foreign' and fields.get('controller') == ['UserViewController']:
                self.reply('', redirect=foreign_url[0])
                return
            if actor not in _ACCOUNTS and fields.get('controller') == ['UserViewController']:
                self.reply('', redirect='/?controller=AuthController&action=login')
                return

            controls = ('<a href="/choose?as=17">模拟登录甲</a> '
                        '<a href="/choose?as=26">模拟登录乙</a> '
                        '<a href="/choose?as=">模拟退出</a> '
                        '<a href="/leave">离开本站</a>').replace('as=">', 'as=none">')
            if actor not in _ACCOUNTS:
                self.reply('<div class="form-login"><form></form></div>' + controls)
                return
            header = f'''<header><div class="menus-container"><div class="dropdown"><ul>
                <li class="no-hover"><strong>同一个显示姓名</strong></li>
                <li><a href="{_PROFILE}&amp;user_id={actor}">个人信息</a></li>
                </ul></div></div></header>'''
            if 'simple_header' in self.server.fixture_modes:
                header = f'''<header class="wb-simple-header"><details class="wb-simple-account">
                    <summary>账户</summary><div><a href="{_PROFILE}&amp;user_id={actor}">个人资料</a>
                    </div></details></header>'''
            if self.headers.get('X-Requested-With') == 'XMLHttpRequest' and fields.get('controller') == ['UserViewController']:
                self.reply('<ul class="panel"><li>用户名： <strong>partial</strong></li></ul>')
                return
            markup = header + controls + f'<a href="{_PROFILE}&amp;user_id=26">查看别人</a>'
            if fields.get('controller') == ['UserViewController']:
                target = fields.get('user_id', [actor])[0]
                if 'probe_mismatch' in self.server.fixture_modes:
                    target = '26' if actor == '17' else '17'
                markup += f'''<section id="main"><section id="user-section" class="sidebar-container">
                    <div class="sidebar"><ul><li><a href="{_PROFILE}&amp;user_id={target}">概要</a></li></ul></div>
                    <div class="sidebar-content"><div class="page-header"><h2>概要</h2></div>
                    <ul class="panel"><li>用户名： <strong>{_ACCOUNTS[target]}</strong></li>
                    <li>姓名： <strong>同一个显示姓名</strong></li></ul></div></section></section>'''
            self.reply(markup)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    http.fixture_modes = set()
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', requests, foreign_url, http.fixture_modes
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


@pytest.mark.parametrize('simple_header', [False, True])
def test_current_workbox_username_and_stable_id_come_from_own_profile(workbox_type, server, simple_header):
    origin, requests, _, modes = server
    if simple_header:
        modes.add('simple_header')
    browser = workbox_type(origin)
    try:
        assert browser.current_account() is None and requests == []
        browser.open_login()
        assert browser.current_account() is None
        browser._page.get_by_role('link', name='模拟登录甲').click()
        first = browser.current_account()
        assert (first.origin, first.user_id, first.username) == (origin, '17', 'anonymous-admin')
        browser._page.get_by_role('link', name='查看别人').click()
        assert browser._page.locator('#user-section strong').first.inner_text() == 'anonymous-member'
        assert browser.current_account() == first
        modes.add('probe_mismatch')
        with pytest.raises(GatewayError):
            browser.current_account()
        modes.clear()
        browser._page.get_by_role('link', name='模拟退出').click()
        assert browser.current_account() is None
        browser._page.get_by_role('link', name='模拟登录乙').click()
        second = browser.current_account()
        assert (second.user_id, second.username) == ('26', 'anonymous-member')
        assert requests.count(_PROFILE) >= 6
        browser.close()
        browser.open_login()
        assert browser.current_account() is None
    finally:
        browser.close()


def test_workbox_probe_refuses_foreign_redirect(workbox_type, server):
    origin, requests, foreign, _ = server
    foreign.append('http://localhost:' + origin.rsplit(':', 1)[1] + '/foreign')
    browser = workbox_type(origin)
    try:
        browser.open_login()
        browser._page.get_by_role('link', name='模拟登录甲').click()
        assert browser.current_account() is not None
        browser._page.goto(origin + '/choose?as=foreign')
        assert browser.current_account() is None
        assert '/foreign' not in requests
        browser._page.get_by_role('link', name='离开本站').click()
        with pytest.raises(GatewayError):
            browser.current_account()
    finally:
        browser.close()


def test_workbox_origin_is_explicit_and_canonical():
    from workbox.team_delivery.workbox_identity import normalize_workbox_url

    assert normalize_workbox_url(' HTTPS://WorkBox.Example:443/ ') == 'https://workbox.example'
    assert normalize_workbox_url('http://[0:0:0:0:0:0:0:1]:8000/') == 'http://[::1]:8000'
    for invalid in ('workbox.example', 'file:///index.php', 'http://a/b', 'http://a/?q=x',
                    'http://a/#profile', 'http://a/?', 'http://a/#', 'http://u:p@a/',
                    'http://user@a/', 'http://a\\@b/', 'http://a:invalid/', 'http://127.1/'):
        with pytest.raises(GatewayError):
            normalize_workbox_url(invalid)
