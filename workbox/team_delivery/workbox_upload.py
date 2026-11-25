"""Prepare a verified manual on the user's selected native WorkBox project page."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as BrowserError

from workbox.team_delivery.contracts import GatewayError, Material
from workbox.team_delivery.storage import MaterialStore, StorageError


_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
_CHOOSE = '请在 WorkBox 窗口打开本人负责的对应项目，进入有「上传并交接」按钮的手册上传页。'
_NATIVE_INPUT = r'''({origin, id, title}) => {
    function route(url, action) {
        const p = url.searchParams;
        return url.origin === origin && !url.username && !url.password &&
            ['/', '/index.php'].includes(url.pathname) && !url.hash &&
            [['controller','SimpleUploadsController'], ['plugin','WorkBoxIntake'],
             ['action',action], ['business_id',id]].every(([k,v]) =>
                p.getAll(k).length === 1 && p.get(k) === v);
    }
    if (!route(new URL(location.href), 'show')) return null;
    const sections = document.querySelectorAll('[data-workbox-simple-page="uploads"]');
    if (sections.length !== 1) return null;
    const names = sections[0].querySelectorAll(':scope > p.wb-simple-filename > strong');
    if (names.length !== 1 || names[0].textContent.trim() !== title) return null;
    const forms = [...sections[0].querySelectorAll('form')].filter(f =>
        f.method === 'post' && f.enctype === 'multipart/form-data' && route(new URL(f.action), 'handoffManual'));
    if (forms.length !== 1) return null;
    const form = forms[0], fields = form.querySelectorAll('input[type=file][name=manual_file]');
    const buttons = [...form.querySelectorAll('button[type=submit]')].filter(b =>
        !b.disabled && !b.hasAttribute('formaction') && b.textContent.trim() === '上传并交接');
    if (fields.length !== 1 || fields[0].form !== form || fields[0].disabled ||
        !fields[0].getClientRects().length || buttons.length !== 1 ||
        form.querySelector('select[name=manual_material_id]')?.value) return null;
    return fields[0];
}'''


def _project(page, origin):
    if page is None or page.is_closed():
        raise GatewayError(_CHOOSE)
    url = urlsplit(page.url)
    ids = parse_qs(url.query).get('business_id', [])
    if f'{url.scheme}://{url.netloc}' != origin or len(ids) != 1 or not _UUID.fullmatch(ids[0]):
        raise GatewayError(_CHOOSE)
    return ids[0]


def _manual_input(page, origin, title, business_id):
    handle = page.evaluate_handle(_NATIVE_INPUT, {'origin': origin, 'title': title, 'id': business_id})
    if handle.as_element() is None:
        handle.dispose()
        raise GatewayError('当前 WorkBox 项目、文件名或手册工序不匹配。' + _CHOOSE)
    return handle.as_element()


def manual_target(page, origin: str, title: str) -> str:
    project = _project(page, origin)
    try:
        _manual_input(page, origin, title, project).dispose()
    except BrowserError:
        raise GatewayError('暂时无法读取 WorkBox 手册上传页，请重新核对。') from None
    return project


class _SelectedManual:
    """The browser can read the file until it replaces/closes the upload document."""

    def __init__(self, page):
        self._page = page
        self._temp = TemporaryDirectory(prefix='WorkBox-manual-')
        self.root = Path(self._temp.name).resolve()
        page.on('domcontentloaded', self._document_ended)
        page.on('close', self._document_ended)

    def _document_ended(self, *_):
        try:
            self.close()
        except PermissionError:
            # Windows may still hold the file during navigation; retry on close.
            pass

    def close(self):
        if self._page is not None:
            self._temp.cleanup()
            self._page.remove_listener('domcontentloaded', self._document_ended)
            self._page.remove_listener('close', self._document_ended)
            self._page = None

    def copy_original(self, material):
        if material.path.resolve(strict=True) != material.path:
            raise StorageError('归档手册的路径已变化。')
        store = MaterialStore(self.root)
        with store.stage_word(material.path) as staged:
            if staged.sha256 != material.sha256:
                raise StorageError('归档手册内容已变化。')
            return staged.path.rename(self.root / material.name)


def fill_manual_form(page, origin: str, title: str, business_id: str, material: Material, *, check_accounts=None):
    if _project(page, origin) != business_id:
        raise GatewayError('WorkBox 已切换项目，请重新核对。')
    if material.path.stem != title or material.path.name != material.name or material.path.suffix.lower() != '.docx':
        raise GatewayError('归档手册的文件名与当前项目不一致。')
    selection = _SelectedManual(page)
    attached = False
    try:
        snapshot = selection.copy_original(material)
        field = _manual_input(page, origin, title, business_id)
        try:
            # Keep this exact form element while rechecking both logged-in users.
            # A replacement document cannot receive the file via a live locator.
            if check_accounts is not None:
                check_accounts()
            if field.evaluate('el => el.files.length') != 0:
                raise GatewayError('上传页已有选好的文件。请在网页核对或清空后重试。')
            field.set_input_files(snapshot, timeout=5000)
            if field.evaluate('el => el.files.length === 1 ? el.files[0].name : null') != material.name:
                raise GatewayError('WorkBox 未确认文件选择，请在网页核对。')
            page.bring_to_front()
            attached = True
            return selection
        finally:
            field.dispose()
    except BrowserError:
        raise GatewayError('WorkBox 页面已变化，请核对当前上传表单。') from None
    except (StorageError, OSError):
        raise GatewayError('归档手册已变化或无法准备上传副本，请重新核对原件。') from None
    finally:
        if not attached:
            selection.close()
