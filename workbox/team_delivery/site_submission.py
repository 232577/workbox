"""Submit one material task through the site's visible native controls."""

from dataclasses import dataclass
from typing import Callable
from unicodedata import normalize
from weakref import WeakKeyDictionary

from playwright.sync_api import Error as BrowserError

from workbox.team_delivery.contracts import Account, ActionUncertain, GatewayError, Stage, Task
from workbox.team_delivery.live_site import LiveBrowserGateway, _READ_MATERIALS


_TIMEOUT_MS = 10_000
_MAX_PAGES = 20
_LABELS = {Stage.READY: '待制作', Stage.RETURNED: '预审退回', Stage.WORKING: '进行中'}
_UNCERTAIN: WeakKeyDictionary = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class SiteSubmissionReceipt:
    task_id: str
    account: str
    submitted_now: bool


_READ_SITE = r'''async ({origin, session, username, title, id}) => {
    const current = () => location.origin === origin &&
        window.__onlineDropReadVisibleAccount()?.session === session;
    if (!current()) return null;
    const get = async path => {
        const response = await fetch(path, {credentials: 'same-origin', mode: 'same-origin',
            redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(4500)});
        return response.ok ? response.json() : null;
    };
    const key = value => typeof value === 'string' ? value.normalize('NFC').trim().toLowerCase() : '';
    const [identity, state] = await Promise.all([get('/api/me'), get('/api/state')]);
    if (!current() || identity?.user?.role !== '材料' || identity.user.mustChange ||
        !key(username) || key(identity.user.account) !== key(username) || !Array.isArray(state?.projects))
        return null;
    const rows = state.projects.filter(project => String(project.id) === id);
    if (rows.length !== 1 || rows[0].name !== title || key(rows[0].matOwner) !== key(username)) return null;
    return {status: rows[0].matStatus, owner: rows[0].matOwnerName || rows[0].matOwner};
}'''


_VISIBLE_OPERATION = r'''(readScope, args) => {
    const {origin, session, activity, id, title, status, owner, operation, move} = args;
    const scope = readScope({origin, readScope: true, protectSelection: true, activity});
    if (scope.foreign || scope.account?.session !== session || !scope.mine || scope.filtered ||
        scope.dialog || scope.selected || scope.editing || scope.paging || scope.userMoved ||
        ['matCodeOwner', 'matMaterialOwner'].some(key => document.getElementById(key)?.value?.trim()) ||
        document.querySelector('#matList[aria-busy="true"], #refreshStatus[data-state="loading"], ' +
            '#matReminders [data-stage-reminder="overdue"][aria-pressed="true"]'))
        return {blocked: true};
    if (move) {
        const result = readScope({origin, activity, move: {...move, session}});
        return {moved: result.moved === true};
    }

    const visible = node => node && node.getClientRects().length > 0 &&
        !['hidden', 'collapse'].includes(getComputedStyle(node).visibility);
    const list = document.getElementById('matList');
    const checked = () => [...list.querySelectorAll('input.mat-check:checked')];
    const rows = [...list.querySelectorAll('input.mat-check[type="checkbox"]')].filter(box => box.value === id);
    if (rows.length > 1) return {blocked: true};
    const box = rows[0];
    if (!box) {
        const pager = document.getElementById('matPager');
        const matches = [...(pager?.innerText || '').matchAll(/第\s*(\d+)\s*\/\s*(\d+)\s*页/g)];
        return matches.length === 1 ? {number: Number(matches[0][1]), total: Number(matches[0][2])} : {blocked: true};
    }
    const row = box.closest('tr');
    const label = row?.querySelector('td.project-cell span.project-name, td:nth-child(2) span.project-name');
    const statusCell = row?.cells[4];
    const ownerCell = row?.cells[6];
    if (!visible(box) || box.disabled || !label || label.getAttribute('title') !== title ||
        label.textContent !== title || box.dataset.status !== status || statusCell?.innerText.trim() !== status ||
        ownerCell?.innerText.trim() !== owner) return {blocked: true};
    if (!operation) return {found: true};

    const batch = box.closest('.batch-card');
    const buttons = [...(batch?.querySelectorAll('button[data-action="batch-task"][data-stage="mat"]') || [])]
        .filter(button => button.dataset.operation === operation && visible(button));
    if (buttons.length !== 1 || checked().length) return {blocked: true};
    const [button] = buttons;
    const reset = () => {
        if (box.isConnected && box.checked && checked().length === 1) box.click();
        return {blocked: true};
    };
    box.click();
    const onlyTarget = () => box.isConnected && checked().length === 1 && checked()[0] === box &&
        window.__onlineDropReadVisibleAccount()?.session === session;
    if (!onlyTarget() || button.matches(':disabled,[aria-disabled="true"]')) return reset();
    button.click();
    if (operation === 'complete-material') {
        const dialog = document.querySelector('dialog#actionDialog[open]');
        const confirm = dialog?.querySelector('#confirmAction');
        const message = dialog?.querySelector('#actionText')?.textContent.trim();
        const valid = dialog?.querySelector('#actionTitle')?.textContent.trim() === '提交材料' &&
            /^确认处理批次「.+」中所选的 1 个项目？提交后，项目将进入代码待领取清单。$/s.test(message || '') &&
            !dialog?.querySelector('#actionField')?.childElementCount && visible(confirm) &&
            confirm.textContent.trim() === '确认提交' && !confirm.matches(':disabled,[aria-disabled="true"]');
        if (!valid || !onlyTarget()) {
            if (valid) dialog.querySelector('#cancelAction')?.click();
            return reset();
        }
        confirm.click();
    }
    return {fired: true, activity: window.__onlineDropReadInteraction().interaction};
}'''

_OPERATE = f'args => ({_VISIBLE_OPERATION})(({_READ_MATERIALS}), args)'


def _read_site(gateway: LiveBrowserGateway, account: Account, args: dict) -> dict:
    try:
        current = gateway.current_account()
        if current == account:
            result = gateway._page.evaluate(_READ_SITE, args)
            if result and gateway.current_account() == account:
                return result
    except BrowserError:
        pass
    raise GatewayError('无法核对原站的登录账号、项目全名、编号或材料负责人。')


def _locate(page, args: dict) -> None:
    seen = set()
    for _ in range(_MAX_PAGES * 2):
        view = page.evaluate(_OPERATE, args)
        if view.get('found'):
            return
        number, total = view.get('number'), view.get('total')
        if not isinstance(number, int) or not isinstance(total, int) or not 1 <= number <= total <= _MAX_PAGES:
            break
        seen.add(number)
        remaining = set(range(1, total + 1)) - seen
        if not remaining:
            break
        target = number + 1 if max(remaining) > number else number - 1
        moved = page.evaluate(_OPERATE, {**args, 'move': {'from': number, 'target': target, 'total': total}})
        if not moved.get('moved'):
            break
    raise GatewayError('原站找不到符合当前账号、工序和项目全名的单项，或网页正在操作。')


def _native_write(page, args: dict, operation: str, unresolved: dict, key: tuple) -> int:
    path = args['origin'] + '/api/' + operation
    ids = {'ids': [int(args['id'])]}
    sent = None
    signing_out = False

    def endpoint(url):
        return url.startswith(args['origin'] + '/api/')

    def native_request(route):
        nonlocal sent, signing_out
        request = route.request
        if request.method in ('GET', 'HEAD'):
            route.fallback()
            return
        if request.url == args['origin'] + '/api/logout':
            signing_out = True
            route.fallback()
            return
        try:
            exact = request.url == path and request.method == 'POST' and request.frame == page.main_frame
            posted = request.post_data_json if exact else None
            permitted = posted == ids and type(posted['ids'][0]) is int and sent is None and not signing_out
        except (BrowserError, ValueError):
            permitted = False
        if not permitted:
            route.fulfill(status=409, json={'error': '未能确认单项材料操作。'})
            return
        sent = request
        unresolved[key] = operation
        route.continue_()

    page.route(endpoint, native_request)
    try:
        with page.expect_response(lambda response: response.url == path and response.request.method == 'POST',
                                  timeout=_TIMEOUT_MS) as result:
            progress = page.evaluate(_OPERATE, {**args, 'operation': operation})
            if not progress.get('fired'):
                raise GatewayError('原站单项勾选或确认内容发生变化，操作已暂停。')
        response = result.value
        if sent is None:
            raise GatewayError('原站未产生预期的单项请求。')
        payload = response.json() if response.request == sent and response.ok else None
        if not isinstance(payload, dict) or payload.get('error') or payload.get('ok') is False:
            raise ActionUncertain('未收到原站的可核对回执，请先在原站核对状态。')
        return progress['activity']
    except (BrowserError, ValueError):
        if sent is not None:
            raise ActionUncertain('原站响应中断，已暂停重发，请先在原站核对状态。') from None
        raise GatewayError('未能触发原站的单项材料操作。') from None
    finally:
        if not page.is_closed():
            try:
                page.unroute(endpoint, native_request)
            except BrowserError:
                pass


def _target(gateway: LiveBrowserGateway, account: Account, task: Task) -> dict:
    username = getattr(account, 'username', '')
    if (account.role != 'material' or not username or task.owner_id != account.id or not task.title
            or not task.id.isascii() or not task.id.isdecimal() or task.id.startswith('0')
            or len(task.id) > 16 or int(task.id) > 2**53 - 1):
        raise GatewayError('请先核对登录账号和原站材料项目编号。')
    if gateway.current_account() != account:
        raise GatewayError('原站当前账号已变化，请重新配对。')
    return {'origin': gateway.base_url, 'session': gateway._observation, 'id': task.id,
            'title': task.title, 'username': username}


def read_site_submission(gateway: LiveBrowserGateway, account: Account, task: Task) -> bool:
    """Check completion from the current session without interacting with the page."""
    args = _target(gateway, account, task)
    project = _read_site(gateway, account, args)
    if project['status'] == '已完成':
        key = (normalize('NFC', args['username']).strip().lower(), task.id)
        _UNCERTAIN.get(gateway, {}).pop(key, None)
        return True
    if project['status'] not in _LABELS.values():
        raise GatewayError('无法核对原站项目当前的材料工序。')
    return False


def submit_via_visible_site(
    gateway: LiveBrowserGateway, account: Account, task: Task, *, check_accounts: Callable[[], None] | None = None,
) -> SiteSubmissionReceipt:
    """Resolve the requested project and return receipt-backed or read-only completion."""
    if check_accounts is not None:
        check_accounts()
    args = _target(gateway, account, task)
    username = args['username']
    page = gateway._page
    try:
        args['activity'] = page.evaluate('window.__onlineDropReadInteraction().interaction')
        project = _read_site(gateway, account, args)
        unresolved = _UNCERTAIN.setdefault(gateway, {})
        key = (normalize('NFC', username).strip().lower(), task.id)
        if project['status'] == '已完成':
            unresolved.pop(key, None)
            return SiteSubmissionReceipt(task.id, username, False)
        if unresolved.get(key) == 'complete-material' or (
                unresolved.get(key) == 'start-material' and project['status'] != '进行中'):
            raise ActionUncertain('上次材料操作尚未确认，请在原站核对后处理。')
        if project['status'] != _LABELS.get(task.stage):
            raise GatewayError('原站材料工序已变化，请刷新后重新核对。')
        for operation, next_status in ([('complete-material', '已完成')] if task.stage is Stage.WORKING else
                                       [('start-material', '进行中'), ('complete-material', '已完成')]):
            args.update(project)
            _locate(page, args)
            if check_accounts is not None:
                check_accounts()
            if _read_site(gateway, account, args) != project:
                raise GatewayError('原站项目刚刚发生变化，请刷新后重新核对。')
            args['activity'] = _native_write(page, args, operation, unresolved, key)
            try:
                project = _read_site(gateway, account, args)
            except GatewayError as error:
                raise ActionUncertain('已收到原站响应，尚未核对到同一项目的后续工序。') from error
            if project['status'] != next_status:
                raise ActionUncertain('原站已响应，项目工序尚未确认推进，请先核对。')
            unresolved.pop(key, None)
            if operation == 'start-material':
                page.wait_for_function('!document.querySelector("#matList input.mat-check:checked")',
                                       timeout=_TIMEOUT_MS)
        if check_accounts is not None:
            try:
                check_accounts()
            except GatewayError as error:
                raise ActionUncertain('原站已确认材料完成，请恢复账号配对后再核对本次交件。') from error
        return SiteSubmissionReceipt(task.id, username, True)
    except BrowserError:
        raise GatewayError('原站网页状态已变化，请手动核对原站材料工序。') from None
