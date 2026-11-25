"""在专用浏览器会话中显示办公室站点的原生工作页面。"""

from hashlib import sha256
from html import escape
import json
from pathlib import Path, PureWindowsPath
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from PySide6.QtCore import QMimeDatabase, QStandardPaths, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest, QWebEngineLoadingInfo, QWebEnginePage, QWebEngineProfile, QWebEngineScript,
    QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from workbox.team_delivery.office_identity import (
    WorkBoxAccount, _PROFILE_LINK, _PROFILE_PATH, _READ_PROFILE, normalize_workbox_url,
)


_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
_PROBE_SECONDS = 8
_PROBE_TIMEOUT_MS = 4000
_INTAKE = 'WorkBoxIntake'
_REPORTING = 'WorkBoxReporting'
_START_PROBE = r'''(read, args) => {
    const request = {sequence: args.sequence, value: null};
    window.workboxOfficeObservation = request;
    function publish(result) {
        if (window.workboxOfficeObservation === request) request.value = JSON.stringify(result);
    }
    read(args).then(publish, () => publish(false));
}'''
_PROBE = r'''async args => {
    if (location.origin !== args.origin || document.querySelector('.form-login')) return null;
    const account = await (''' + _READ_PROFILE + r''')(args);
    if (!account) return account;
    if (!args.roles) return {account};
''' + _PROFILE_LINK + r'''
    const target = args.origin + args.roles;
    const response = await fetch(target, {
        mode: 'same-origin', credentials: 'same-origin', redirect: 'manual', cache: 'no-store',
        signal: AbortSignal.timeout(args.timeout)
    });
    if (response.type === 'opaqueredirect' || response.status === 401) return null;
    if (!response.ok || response.url !== target) return {account, links: [], navigation: []};
    const documentCopy = document.createElement('template');
    documentCopy.innerHTML = await response.text();
    const root = documentCopy.content;
    if (profileId(root, menu, ownLabels, target) !== account.id) return false;
    const links = selector => [...root.querySelectorAll(selector)].map(link => new URL(link.getAttribute('href'), target).href);
    return {account, links: links('ul[aria-label="工作视图"] a[href]'),
        navigation: links('nav[aria-label="工作导航"] a[href]')};
}'''


def _url_path(controller: str, *, plugin: str = '', action: str = 'show', **params: str) -> str:
    query = {'controller': controller, 'action': action}
    if plugin:
        query['plugin'] = plugin
    query.update(params)
    return '/?' + urlencode(query)


_PROJECTS = _url_path('SimpleProjectsController', plugin=_INTAKE)
_UPLOADS = _url_path('SimpleUploadsController', plugin=_INTAKE)
_FILES = _url_path('SimpleFilesController', plugin=_INTAKE)
_WORKFLOW = _url_path('WorkflowController', plugin=_INTAKE)
_SUBMISSION = _url_path('SubmissionController', plugin=_REPORTING)
_STATISTICS = _url_path('ReportingController', plugin=_REPORTING)


def _origin_key(url: QUrl) -> tuple[str, str, int] | None:
    scheme = url.scheme()
    if not url.isValid() or scheme not in ('http', 'https') or not url.host() or '@' in url.authority():
        return None
    return scheme, url.host().lower(), url.port(443 if scheme == 'https' else 80)


def _route_query(url: str) -> dict[str, list[str]]:
    parsed = urlsplit(url)
    return parse_qs(parsed.query, keep_blank_values=True) if parsed.path in ('/', '/index.php') else {}


def _shortcut_routes(origin: str, links: list[str], navigation: list[str]) -> dict[str, str]:
    routes = {}
    if origin + _STATISTICS in navigation:
        routes['report'] = origin + _STATISTICS
    key = _origin_key(QUrl(origin))
    for url in links:
        address = QUrl(url)
        if _origin_key(address) != key or address.hasFragment():
            continue
        query = _route_query(url)
        if query == _route_query(_url_path('WorkflowController', plugin=_INTAKE, action='code')):
            routes['queue'] = origin + _url_path('SimpleProjectsController', plugin=_INTAKE, filter='queue')
            routes['code'] = origin + _url_path('WorkflowController', plugin=_INTAKE, action='code', filter='mine')
        elif query == _route_query(_SUBMISSION):
            routes['submit'] = origin + _SUBMISSION
        elif set(query) == {'controller', 'action', 'project_id'} and query['controller'] == ['TaskListController'] and query['action'] == ['show']:
            ids = query['project_id']
            if len(ids) == 1 and ids[0].isascii() and ids[0].isdigit() and ids[0][0] != '0':
                routes['dispatch'] = origin + _url_path('TaskListController', project_id=ids[0])
    return routes


class _SiteRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self, key, parent, pair_guard=None):
        super().__init__(parent)
        self._key = key
        self._pair_guard = pair_guard

    def interceptRequest(self, info):
        url = info.requestUrl()
        if _origin_key(url) != self._key:
            info.block(True)
        elif self._pair_guard is not None:
            action = _route_query(url.toString())
            if (action.get('controller') == ['AuthController']
                    and action.get('action') in (['login'], ['check'], ['logout'])):
                self._pair_guard.invalidate()
            if not self._pair_guard.accept_request(info):
                info.block(True)


class _OfficePage(QWebEnginePage):
    def __init__(self, profile, view, panel, key):
        super().__init__(profile, view)
        self._panel, self._key = panel, key
        settings = self.settings()
        settings.setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
        for name in (
            'LocalContentCanAccessFileUrls', 'LocalContentCanAccessRemoteUrls', 'JavascriptCanAccessClipboard',
            'JavascriptCanPaste', 'JavascriptCanOpenWindows', 'PluginsEnabled', 'PdfViewerEnabled', 'DnsPrefetchEnabled',
        ):
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute, name), False)
        self.newWindowRequested.connect(self._open_from_link)
        self.permissionRequested.connect(lambda request: request.deny())

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if _origin_key(url) != self._key:
            if is_main_frame:
                self._panel._notice('链接不属于当前办公室站点。')
            return False
        if is_main_frame:
            action = _route_query(url.toString())
            if action.get('controller') == ['AuthController'] and action.get('action') in (
                ['login'], ['check'], ['logout'],
            ):
                self._panel._invalidate_account()
        return True

    def _open_from_link(self, request):
        # A user may open the site's own image preview in the same view.
        if request.isUserInitiated():
            self._panel.navigate(request.requestedUrl().toString())

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        allowed = (self.FileSelectionMode.FileSelectOpen, self.FileSelectionMode.FileSelectOpenMultiple)
        if mode not in allowed or _origin_key(self.url()) != self._key or self._panel.account is None:
            return []
        actor = self._panel.account
        page_url = self.url()
        patterns = set()
        mime_database = QMimeDatabase()
        for value in accepted_mime_types:
            if value.startswith('.') and value[1:].isalnum():
                patterns.add('*' + value)
            elif '/' in value:
                patterns.update(mime_database.mimeTypeForName(value).globPatterns())
        filters = ('可上传文件 (' + ' '.join(sorted(patterns)) + ');;' if patterns else '') + '所有文件 (*)'
        if mode == self.FileSelectionMode.FileSelectOpen:
            selected, _ = QFileDialog.getOpenFileName(self._panel, '选择上传文件', '', filters)
            files = [selected] if selected else []
        else:
            files, _ = QFileDialog.getOpenFileNames(self._panel, '选择上传文件', '', filters)
        if self._panel.page is not self or self._panel.account != actor or self.url() != page_url:
            return []
        return files


class OfficePanel(QWidget):
    """Qt 主线程内使用的办公室工作台，账号信息通过信号传出。"""

    account_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pair_guard = None
        self.origin = ''
        self.account: WorkBoxAccount | None = None
        self.page: QWebEnginePage | None = None
        self._profile = self._view = None
        self._cookie_key = None
        self._generation = 0
        self._probe_sequence = 0
        self._pending = self._reading = False
        self._next_probe = self._next_roles = self._deadline = 0.0
        self._role_routes: dict[str, str] = {}
        self._downloads = {}
        self._opening_download = False
        self._timer = QTimer(self, interval=250)
        self._timer.timeout.connect(self._observe)

        controls = QHBoxLayout()
        self._back = QPushButton('返回', objectName='officeBack')
        self._back.clicked.connect(self._go_back)
        self._refresh = QPushButton('刷新', objectName='officeRefresh')
        self._refresh.clicked.connect(self._reload)
        self._primary = []
        for label, path in (('项目表', _PROJECTS), ('上传文件', _UPLOADS), ('文件管理', _FILES)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, target=path: self._open(target))
            self._primary.append(button)
            controls.addWidget(button)

        self._roles = QPushButton('更多工作', objectName='officeRoles')
        self._menu = QMenu(self._roles)
        self._roles.setMenu(self._menu)
        self._role_actions = {}
        for key, label, name in (
            ('dispatch', '手册分发', 'officeDispatch'),
            ('queue', '代码待领取', 'officeCodeQueue'),
            ('code', '代码/TXT 上传', 'officeCodeUpload'),
            ('submit', '对外提交登记', 'officeSubmission'),
            ('report', '工作统计', 'officeReporting'),
        ):
            action = self._menu.addAction(label)
            action.setObjectName(name)
            action.triggered.connect(lambda _checked=False, target=key: self.navigate(self._role_routes.get(target, '')))
            self._role_actions[key] = action
        controls.addWidget(self._roles)
        controls.addStretch(1)
        controls.addWidget(self._back)
        controls.addWidget(self._refresh)

        self._account_label = QLabel()
        self._account_label.setTextFormat(Qt.TextFormat.PlainText)
        self._account_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._switch = QPushButton('换账号', objectName='officeSwitchAccount')
        self._switch.clicked.connect(self.switch_account)
        identity = QHBoxLayout()
        identity.addWidget(self._account_label, 1)
        identity.addWidget(self._switch)

        self._message = QLabel(objectName='officeStatus')
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addLayout(controls)
        self._layout.addLayout(identity)
        self._layout.addWidget(self._message)
        self._notice('请先连接办公室站点。')
        self._show_session()

    def connect_to(self, origin: str) -> None:
        """连接同一地址时保留当前会话，换地址时开启新会话。"""
        normalized = normalize_workbox_url(origin)
        if normalized == self.origin and self.page is not None:
            return
        self.shutdown()
        self.origin = normalized
        profile = QWebEngineProfile(self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._interceptor = _SiteRequests(_origin_key(QUrl(self.origin)), profile, self.pair_guard)
        profile.setUrlRequestInterceptor(self._interceptor)
        view = QWebEngineView(self)
        view.setAccessibleName('办公室站点')
        page = _OfficePage(profile, view, self, _origin_key(QUrl(self.origin)))
        view.setPage(page)
        self._profile, self._view, self.page = profile, view, page
        self._layout.addWidget(view, 1)
        page.loadStarted.connect(lambda: self._starting(page))
        page.loadFinished.connect(lambda ok: self._loaded(page, ok))
        page.loadingChanged.connect(lambda info: self._load_changed(page, info))
        page.renderProcessTerminated.connect(lambda *_args: self._engine_stopped(page))
        profile.cookieStore().cookieAdded.connect(self._cookie_added)
        profile.cookieStore().cookieRemoved.connect(self._cookie_removed)
        profile.downloadRequested.connect(self._download)
        if self.isVisible():
            self._timer.start()
        self._show_session()
        self._open('/')

    def show_work(self) -> None:
        self._open(_PROJECTS)

    def navigate(self, url: str) -> bool:
        address = QUrl(url)
        if self.page is None or _origin_key(address) != _origin_key(QUrl(self.origin)):
            self._notice('链接不属于当前办公室站点。')
            return False
        self.page.load(address)
        return True

    def switch_account(self) -> None:
        origin = self.origin
        if origin:
            self.shutdown()
            self.connect_to(origin)

    def shutdown(self) -> None:
        self._timer.stop()
        view, profile = self._view, self._profile
        self._view = self._profile = self.page = None
        self._cookie_key = None
        self.origin = ''
        self._invalidate_account()
        for request in tuple(self._downloads.values()):
            if isValid(request) and not request.isFinished():
                request.cancel()
        self._downloads.clear()
        if view is not None:
            view.stop()
            self._layout.removeWidget(view)
            view.hide()
            # The page in the view must die before its off-the-record profile.
            profile.setParent(QApplication.instance())
            view.setParent(None)
            view.destroyed.connect(profile.deleteLater)
            view.deleteLater()
        self._notice('请先连接办公室站点。')

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.page is not None:
            self._next_probe = self._next_roles = 0
            self._timer.start()
            self._observe()

    def hideEvent(self, event) -> None:
        # Preserve the last displayed identity for the parent, but stop passive reads.
        self._timer.stop()
        self._cancel_probe()
        super().hideEvent(event)

    def _open(self, path):
        if self.origin:
            self.navigate(self.origin + path)

    def _go_back(self):
        if self._view is not None:
            self._view.back()

    def _reload(self):
        if self._view is not None:
            self._view.reload()

    def _notice(self, message, *, loading=False):
        self._loading_notice = loading
        self._message.setText(message)
        self._message.setVisible(bool(message))

    def _show_session(self):
        user = self.account
        text = f'办公室账号：{user.username}' if user else ('请在下方登录办公室账号' if self.origin else '办公室未连接')
        self._account_label.setText(text)
        self._account_label.setToolTip(escape(text))
        for button in self._primary:
            button.setEnabled(user is not None)
        self._refresh.setEnabled(self.page is not None)
        self._back.setEnabled(self.page is not None and self.page.history().canGoBack())
        self._switch.setEnabled(self.page is not None)
        for key, action in self._role_actions.items():
            action.setVisible(user is not None and key in self._role_routes)
        self._roles.setEnabled(user is not None and bool(self._role_routes))

    def _record_account(self, account):
        previous = self.account
        self.account = account
        self._show_session()
        if previous != account:
            if self.pair_guard is not None:
                self.pair_guard.invalidate()
            self.account_changed.emit(account)

    def _cancel_probe(self):
        self._generation += 1
        self._pending = self._reading = False
        self._next_probe = 0

    def _invalidate_account(self):
        self._cancel_probe()
        self._next_roles = 0
        self._role_routes = {}
        self._record_account(None)

    def _starting(self, page):
        if page is self.page:
            self._cancel_probe()
            self._notice('正在读取办公室页面…', loading=True)

    def _loaded(self, page, ok):
        if page is not self.page:
            return
        self._next_probe = 0
        if ok:
            self._notice('')
            self._observe()
        elif self._loading_notice:
            self._notice('')
        self._show_session()

    def _load_changed(self, page, info):
        if (page is self.page and info.status() == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus
                and info.errorCode() != -3):  # Chromium's ERR_ABORTED also represents attachment downloads.
            self._invalidate_account()
            self._notice('无法打开办公室页面，请检查连接后刷新。')

    def _engine_stopped(self, page):
        if page is self.page:
            self._invalidate_account()
            self._notice('办公室页面已中断，请刷新后重试。')

    def _session_cookie(self, cookie):
        if (self._profile is None or self.sender() is not self._profile.cookieStore()
                or bytes(cookie.name()) != b'KB_SID' or cookie.path() != '/' or not cookie.isHttpOnly()):
            return None
        if cookie.domain().lstrip('.').lower() != QUrl(self.origin).host().lower():
            return None
        return sha256(bytes(cookie.value())).digest()

    @Slot(QNetworkCookie)
    def _cookie_added(self, cookie):
        key = self._session_cookie(cookie)
        if key is not None and key != self._cookie_key:
            self._cookie_key = key
            self._invalidate_account()

    @Slot(QNetworkCookie)
    def _cookie_removed(self, cookie):
        key = self._session_cookie(cookie)
        if key is not None and key == self._cookie_key:
            self._cookie_key = None
            self._invalidate_account()

    def _observe(self):
        page = self.page
        if (page is None or not self.isVisible() or page.isLoading() or
                _origin_key(page.url()) != _origin_key(QUrl(self.origin))):
            return
        now = monotonic()
        if not self._pending:
            if now >= self._next_probe:
                self._begin_probe(page, now)
        elif now >= self._deadline:
            self._invalidate_account()
            self._next_probe = now + _PROBE_SECONDS
        elif not self._reading:
            self._reading = True
            generation, sequence = self._generation, self._probe_sequence
            read = ('(() => { const result = window.workboxOfficeObservation; '
                    f'return result?.sequence === {sequence} ? result.value : null; }})()')
            page.runJavaScript(read, _WORLD, lambda result: self._observed(page, generation, sequence, result))

    def _begin_probe(self, page, now):
        roles = _WORKFLOW if not self.account or now >= self._next_roles else ''
        self._probe_sequence += 1
        parameters = {'origin': self.origin, 'path': _PROFILE_PATH, 'roles': roles, 'timeout': _PROBE_TIMEOUT_MS,
                      'sequence': self._probe_sequence}
        self._pending = True
        self._deadline = now + (_PROBE_TIMEOUT_MS / 1000) * 2 + 1
        self._next_probe = now + _PROBE_SECONDS
        page.runJavaScript(f'({_START_PROBE})(({_PROBE}), {json.dumps(parameters)});', _WORLD)

    def _observed(self, page, generation, sequence, result):
        if page is not self.page or generation != self._generation or sequence != self._probe_sequence:
            return
        self._reading = False
        if not isinstance(result, str) or not result:
            return
        self._pending = False
        state = json.loads(result)
        if not state:
            self._role_routes = {}
            self._next_roles = 0
            self._record_account(None)
            return
        fresh = state['account']
        account = self.account
        if account is None or (account.user_id, account.username) != (fresh['id'], fresh['username']):
            account = WorkBoxAccount(self.origin, fresh['id'], fresh['username'], uuid4().hex)
            self._role_routes = {}
            self._next_roles = 0
        if 'links' in state:
            self._role_routes = _shortcut_routes(self.origin, state['links'], state['navigation'])
            self._next_roles = monotonic() + 30
        self._record_account(account)

    def _download(self, request):
        url = request.url()
        query = _route_query(url.toString())
        operation = tuple(tuple(query.get(field, ())) for field in ('controller', 'action', 'plugin'))
        allowed = {
            (('MaterialDownloadController',), (action,), (_INTAKE,)) for action in ('material', 'package', 'image')
        } | {(('ReportingController',), ('export',), (_REPORTING,))}
        actor = self.account
        if (self._opening_download or not actor or request.page() is not self.page or request.isSavePageDownload()
                or _origin_key(url) != _origin_key(QUrl(self.origin)) or operation not in allowed):
            request.cancel()
            return
        name = PureWindowsPath(request.suggestedFileName()).name
        directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        self._opening_download = True
        try:
            path, _ = QFileDialog.getSaveFileName(self, '另存办公室文件', str(Path(directory) / name))
        finally:
            self._opening_download = False
        if not isValid(request):
            return
        if not path or actor != self.account or request.page() is not self.page:
            request.cancel()
            return
        target = Path(path)
        request.setDownloadDirectory(str(target.parent))
        request.setDownloadFileName(target.name)
        self._downloads[request.id()] = request
        request.isFinishedChanged.connect(lambda: self._download_finished(request))
        request.accept()
        self._notice('正在下载办公室文件…')

    def _download_finished(self, request):
        if not request.isFinished() or self._downloads.pop(request.id(), None) is None:
            return
        complete = request.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
        self._notice('办公室文件已保存。' if complete else '办公室文件未保存，请重新下载。')
