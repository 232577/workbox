"""读取独立 WorkBox 浏览器会话的 Kanboard 登录身份。"""

import json
import re
from threading import get_ident
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from playwright.sync_api import Error as BrowserError, sync_playwright

from workbox.team_delivery import workbox_upload
from workbox.team_delivery.browser import open_browser
from workbox.team_delivery.contracts import GatewayError
from workbox.team_delivery.office_identity import (
    WorkBoxAccount, _PROFILE_LINK, _PROFILE_PATH, _READ_PROFILE, normalize_workbox_url,
)


_TIMEOUT_MS = 5_000
_USER_ID = re.compile(r'[1-9][0-9]*')


_OBSERVE_LOGIN = r'''
origin => {
    if (window !== window.top || location.origin !== origin) return;
''' + _PROFILE_LINK + r'''
    function report() {
        const id = profileId(document, menu, ownLabels, location.href);
        if (id || document.querySelector('.form-login')) {
            window.workboxObserveLogin(id).catch(() => {});
        }
    }
    document.addEventListener('DOMContentLoaded', report, {once: true});
    window.addEventListener('pageshow', event => { if (event.persisted) report(); });
}
'''


class WorkBoxBrowser:
    """提供单独的可见窗口，账号由用户通过站点原生登录页选择。"""

    def __init__(self, origin: str, *, runtime=None):
        self.origin = normalize_workbox_url(origin)
        self._manual_selection = None
        self._runtime = runtime
        self._owner = None
        self._playwright = None
        self._browser = None
        self._page = None
        self._observed_user_id = None
        self._account = None

    def _check_thread(self) -> None:
        if self._owner is not None and self._owner != get_ident():
            raise RuntimeError('WorkBox 浏览器须由创建它的线程操作和关闭。')

    def _on_origin(self, url: str) -> bool:
        address = urlsplit(url)
        return f'{address.scheme}://{address.netloc}' == self.origin

    def _record_user_id(self, user_id: str | None) -> None:
        if user_id is None or user_id != self._observed_user_id:
            self._account = None
        self._observed_user_id = user_id

    def _observe_login(self, source, user_id) -> None:
        frame = source['frame']
        if frame != source['page'].main_frame or not self._on_origin(frame.url):
            return
        if user_id is None or (isinstance(user_id, str) and _USER_ID.fullmatch(user_id)):
            self._record_user_id(user_id)

    def _observe_auth_request(self, request) -> None:
        # 账号切回同一 ID 之前可能来不及读取中间页，原生登录和退出导航也作废旧确认。
        if not request.is_navigation_request() or not self._on_origin(request.url):
            return
        address = urlsplit(request.url)
        query = parse_qs(address.query)
        if (address.path in ('/', '/index.php') and query.get('controller') == ['AuthController']
                and query.get('action') in (['check'], ['logout'])):
            self._record_user_id(None)

    def _observe_main_navigation(self, frame) -> None:
        if frame == self._page.main_frame and not self._on_origin(frame.url):
            self._record_user_id(None)

    def open_login(self) -> None:
        self._check_thread()
        try:
            if self._page is None or self._page.is_closed():
                self.close()
                self._owner = get_ident()
                self._playwright = self._runtime.driver() if self._runtime is not None else sync_playwright().start()
                self._browser = open_browser(self._playwright)
                context = self._browser.new_context(service_workers='block')
                context.set_default_timeout(_TIMEOUT_MS)
                context.expose_binding('workboxObserveLogin', self._observe_login)
                context.add_init_script(f'({_OBSERVE_LOGIN})({json.dumps(self.origin)})')
                context.on('request', self._observe_auth_request)
                self._page = context.new_page()
                self._page.on('framenavigated', self._observe_main_navigation)

            if not self._on_origin(self._page.url):
                response = self._page.goto(self.origin + '/', wait_until='domcontentloaded', timeout=_TIMEOUT_MS)
                if response is None or not response.ok or not self._on_origin(self._page.url):
                    raise GatewayError('无法打开该 WorkBox 首页，请核对地址和连接。')
            self._page.bring_to_front()
        except (BrowserError, GatewayError) as error:
            self.close()
            if isinstance(error, GatewayError):
                raise
            raise GatewayError('暂时无法打开 WorkBox 登录页。') from None

    def current_account(self) -> WorkBoxAccount | None:
        self._check_thread()
        if self._page is None or self._page.is_closed():
            self._record_user_id(None)
            return None
        if not self._on_origin(self._page.url):
            self._record_user_id(None)
            raise GatewayError('WorkBox 窗口已离开本站，请在助手中重新打开登录。')
        try:
            profile = self._page.evaluate(_READ_PROFILE, {
                'origin': self.origin,
                'path': _PROFILE_PATH,
                'timeout': _TIMEOUT_MS,
            })
        except BrowserError:
            self._record_user_id(None)
            raise GatewayError('暂时无法核对 WorkBox 当前账号。') from None
        if profile is None:
            self._record_user_id(None)
            return None
        if profile is False or not self._on_origin(self._page.url):
            self._record_user_id(None)
            raise GatewayError('无法核对 WorkBox 当前账号，请检查个人信息页。')
        self._record_user_id(profile['id'])
        if self._account is None or self._account.username != profile['username']:
            self._account = WorkBoxAccount(self.origin, profile['id'], profile['username'], uuid4().hex)
        return self._account

    def close(self) -> None:
        self._check_thread()
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            try:
                if self._playwright is not None and self._runtime is None:
                    self._playwright.stop()
            finally:
                self._browser = self._playwright = self._page = self._owner = None
                self._record_user_id(None)
                if self._manual_selection is not None:
                    self._manual_selection.close()
                    self._manual_selection = None

    def manual_target(self, title):
        self._check_thread()
        return workbox_upload.manual_target(self._page, self.origin, title)

    def fill_manual_form(self, title, project, material, *, check_accounts):
        self._check_thread()
        selection = workbox_upload.fill_manual_form(self._page, self.origin, title, project, material, check_accounts=check_accounts)
        previous, self._manual_selection = self._manual_selection, selection
        if previous is not None:
            previous.close()
