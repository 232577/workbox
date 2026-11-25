"""读取员工亲自登录后可见的材料清单。正式站写操作保留禁用。"""

from hashlib import sha256
from itertools import chain
import json
from threading import get_ident
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout, sync_playwright

from workbox.team_delivery.browser import open_browser
from workbox.team_delivery.contracts import Account, GatewayError, Material, RefreshDeferred, Snapshot, Stage, Task


_LIVE_ORIGIN = 'https://zheshijia.dpdns.org'
_TIMEOUT_MS = 10_000
_PAGE_CHECK_MS = 100
_MAX_MATERIAL_PAGES = 20
_STAGES = {'待制作': Stage.READY, '进行中': Stage.WORKING,
           '预审退回': Stage.RETURNED, '已完成': Stage.COMPLETED}

# 用原站已加载的本人身份核对可见账号。会话编号在本机生成，页面重建或
# 换号即失效，不读取 Cookie 或凭据。
_OBSERVE_ACCOUNT = r'''
(() => {
    const marker = crypto.randomUUID();
    const selector = '#who, div[aria-label="当前账号"]';
    let element = null, label = '', lastUsername = '', lastSiteSession = null, epoch = 0;
    const visible = node => node.getClientRects().length > 0 &&
        !['hidden', 'collapse'].includes(getComputedStyle(node).visibility);
    const read = () => {
        const candidates = [...document.querySelectorAll(selector)].filter(visible);
        const captions = candidates.filter(node => node.id === 'who');
        // 正式页面将 #who 放在账号标签容器中。容器和其文字属于同一个标记。
        const nodes = candidates.filter(node => node.id === 'who' ||
            !captions.some(caption => node.contains(caption)));
        const next = nodes.length === 1 ? nodes[0] : null;
        const text = next ? next.textContent.trim() : '';
        const user = typeof me === 'object' && me;
        const username = user && typeof user.account === 'string' &&
            text === user.name + ' · ' + user.role && !user.mustChange ? user.account : '';
        const siteSession = typeof sessionVersion === 'number' ? sessionVersion : null;
        if (next !== element || text !== label || username !== lastUsername || siteSession !== lastSiteSession)
            epoch += 1;
        element = next;
        label = text;
        lastUsername = username;
        lastSiteSession = siteSession;
        return { count: nodes.length, text, username, session: marker + ':' + epoch };
    };
    new MutationObserver(read).observe(document, {
        subtree: true, childList: true, characterData: true, attributes: true,
        attributeFilter: ['class', 'style', 'hidden', 'aria-label', 'id']
    });
    Object.defineProperty(window, '__onlineDropReadVisibleAccount', { value: read });
    let interaction = 0, pagerUntil = 0, composing = false;
    for (const event of ['pointerdown', 'click', 'keydown', 'wheel', 'beforeinput', 'input',
        'compositionstart', 'compositionend', 'focusout']) {
        document.addEventListener(event, action => {
            if (!action.isTrusted) return;
            if (event === 'compositionstart') composing = true;
            if (event === 'compositionend' || event === 'focusout') composing = false;
            if (event === 'focusout') return;
            interaction += 1;
            if (action.target instanceof Element && action.target.closest('#matPager'))
                pagerUntil = performance.now() + 1500;
        }, {capture: true, passive: true});
    }
    Object.defineProperty(window, '__onlineDropReadInteraction', { value: () => ({
        interaction, composing, paging: performance.now() < pagerUntil,
    }) });
})();
'''

_READ_MATERIALS = r'''
({origin, readScope, move, after, reveal, activity, protectForeground, protectSelection = false}) => {
    if (location.origin !== origin) return {foreign: true};
    const account = window.__onlineDropReadVisibleAccount();
    const mine = document.querySelector('#matMine');
    const list = document.querySelector('#matList');
    const visible = node => node && node.getClientRects().length > 0 &&
        !['hidden', 'collapse'].includes(getComputedStyle(node).visibility);
    const batch = document.querySelector('#matBatchFocus');
    const filtered = ['matSearch', 'matPerson', 'matCodeOwner', 'matMaterialOwner', 'matCreatedDate',
        'matOperationDate', 'matOperator', 'matRemark'].some(id => document.getElementById(id)?.value?.trim()) ||
        !!document.querySelector('#matFilter input:checked, #matType input:checked') ||
        !!document.querySelector('#matReminders [data-stage-reminder="overdue"][aria-pressed="true"]') ||
        !!(batch && !batch.hidden && !batch.classList.contains('hidden'));
    const dialog = [...document.querySelectorAll('dialog[open], [role="dialog"], [aria-modal="true"]')].some(visible);
    const selected = (visible(list) || protectSelection) &&
        !!list?.querySelector('input[type="checkbox"]:checked');
    const input = document.activeElement;
    const {interaction, composing, paging} = window.__onlineDropReadInteraction();
    const editing = composing || (visible(input) && !input.disabled && !input.readOnly &&
        (input.isContentEditable || input.matches(
            'textarea, select, input:not([type="checkbox"],[type="radio"],[type="button"],' +
            '[type="submit"],[type="reset"],[type="hidden"])')));
    const userMoved = activity != null && interaction !== activity;
    const foreground = !!protectForeground && document.hasFocus();
    const scope = {account, mine: visible(list) && visible(mine) && mine.checked,
        filtered, dialog, selected, editing, paging, interaction, userMoved, foreground};
    if (readScope || !scope.mine || filtered || dialog || selected || editing || paging || userMoved || foreground ||
        (move && account.session !== move.session) ||
        (after && account.session !== after.session) ||
        (reveal && account.session !== reveal.session))
        return scope;

    const empty = [...list.querySelectorAll(':scope > p.muted')].some(node =>
        visible(node) && ['暂无任务', '当前没有未完成的任务。'].includes(node.innerText.trim()));
    const pager = document.querySelector('#matPager');
    let number = 1, total = 1;
    const controls = {};
    if (visible(pager)) {
        const matches = [...pager.innerText.matchAll(/第\s*(\d+)\s*\/\s*(\d+)\s*页/g)];
        if (matches.length !== 1) return {...scope, invalidPager: true};
        number = Number(matches[0][1]);
        total = Number(matches[0][2]);
        if (!Number.isSafeInteger(total) || number < 1 || number > total)
            return {...scope, invalidPager: true};
        const buttons = [...pager.querySelectorAll('button[data-action="page"][data-kind="mat"]')].filter(visible);
        if (buttons.length !== 2) return {...scope, invalidPager: true};
        for (const [label, target] of [['上一页', number - 1], ['下一页', number + 1]]) {
            const matching = buttons.filter(node =>
                (node.getAttribute('aria-label') || node.innerText).trim() === label);
            if (matching.length !== 1) return {...scope, invalidPager: true};
            const button = matching[0];
            const disabled = button.matches(':disabled, [aria-disabled="true"]');
            const needsButton = target >= 1 && target <= total;
            if (disabled === needsButton || (needsButton && button.dataset.number !== String(target)))
                return {...scope, invalidPager: true};
            if (!disabled) controls[target] = button;
        }
    } else if (!empty && location.hostname !== '127.0.0.1') {
        return {...scope, invalidPager: true};
    }
    if (move) {
        // 同一次读取中再次核验翻页入口，避免网页状态变化后点击了另一批任务的按钮。
        if (number !== move.from || total !== move.total || !controls[move.target])
            return {...scope, invalidPager: true};
        controls[move.target].click();
        return {...scope, moved: true};
    }
    if (after && number === after.from && total === after.total) return false;

    const owner = account.text.split('·').slice(0, -1).join('·').trim();
    const ids = new Set([...list.querySelectorAll('td:first-child input[type="checkbox"]')].map(node => node.value));
    const projects = new Map();
    if (account.username && typeof state === 'object' && Array.isArray(state?.projects)) {
        for (const project of state.projects) {
            const id = String(project.id);
            if (ids.has(id)) projects.set(id, projects.has(id) ? null : project);
        }
    }
    const rows = [];
    const targets = [];
    for (const row of [...list.querySelectorAll('tr')].filter(visible)) {
        const cells = [...row.cells];
        if (cells.length && cells.every(cell => cell.tagName === 'TH')) continue;
        const selector = cells[0]?.querySelector('input[type="checkbox"]');
        const stage = cells[4]?.innerText.trim();
        const status = selector?.getAttribute('data-status');
        const unassigned = stage === '待分发' && status === stage;
        const modern = cells.length === 11;
        const title = modern ? cells[1]?.querySelector('.project-name')?.getAttribute('title')
            : cells[1]?.innerText.trim();
        const touched = modern ? cells[8]?.querySelector('[title]')?.getAttribute('title')
            : cells[7]?.innerText.trim();
        if (modern) {
            const project = projects.get(selector?.value);
            const displayOwner = project?.matOwnerName || project?.matOwner;
            if (!project || typeof project.matOwner !== 'string' ||
                project.matOwner.toLowerCase() !== account.username.toLowerCase() ||
                project.name !== title || project.matStatus !== stage ||
                typeof displayOwner !== 'string' || cells[6]?.textContent.trim() !== displayOwner.trim())
                return {...scope, mismatch: true};
        } else if (!unassigned && cells[9]?.innerText.trim() !== owner) {
            return {...scope, mismatch: true};
        }
        rows.push({
            cells: cells.length,
            id: selector?.value,
            status,
            title,
            stage,
            unassigned,
            touched,
        });
        if (reveal && selector?.value === reveal.id) targets.push(row);
    }
    if (after && number === after.target && total === after.total && !rows.length && !empty)
        return false;
    if (!reveal) return {...scope, number, total, empty, rows};
    const unchanged = number === reveal.number && total === reveal.total &&
        rows.length === reveal.rows.length && rows.every((row, index) =>
            Object.keys(row).every(key => row[key] === reveal.rows[index][key]));
    if (!unchanged || targets.length !== 1) return scope;

    const [target] = targets;
    target.scrollIntoView({block: 'center', inline: 'nearest'});
    target.animate([
        {outline: '3px solid #037f6c', outlineOffset: '-3px'},
        {outline: '3px solid #037f6c', outlineOffset: '-3px', offset: 0.8},
        {outline: '3px solid transparent', outlineOffset: '-3px'},
    ], {duration: 4500});
    return {...scope, revealed: true};
}
'''

_ARM_REFRESH = r'''
() => {
    const status = document.querySelector('#refreshStatus');
    if (!status) return false;
    const buttons = [...document.querySelectorAll('#refreshApp')].filter(node =>
        node.getClientRects().length > 0 &&
        !['hidden', 'collapse'].includes(getComputedStyle(node).visibility) &&
        !node.matches(':disabled, [aria-disabled="true"]'));
    if (buttons.length !== 1) return false;
    const isLoading = () => status.dataset.state === 'loading' ||
        /^(正在刷新|刷新中)/.test(status.textContent.trim());
    if (isLoading()) return 'busy';
    const previous = window.__onlineDropRefresh;
    if (previous) previous.observer.disconnect();
    const progress = { started: false, state: '' };
    progress.observer = new MutationObserver(changes => {
        const label = status.textContent.trim();
        const phase = status.dataset.state;
        if (isLoading()) {
            progress.started = true;
            return;
        }
        if (!progress.started) return;
        if (label.includes('失败') || status.classList.contains('error')
                 || phase === 'error' || status.dataset.level === 'error')
            progress.state = 'error';
        else if (label.startsWith('已刷新') && changes.some(change => change.type !== 'attributes'))
            progress.state = 'success';
    });
    progress.observer.observe(status, {
        childList: true, subtree: true, characterData: true, attributes: true
    });
    window.__onlineDropRefresh = progress;
    buttons[0].click();
    return true;
}
'''

_START_GUARDED_REFRESH = f'''
({{origin, session, allowNavigation, protectSelection, protectForeground}}) => {{
    const scope = ({_READ_MATERIALS})({{origin, readScope: true, protectSelection, protectForeground}});
    if ((!allowNavigation && !scope.mine) || scope.filtered || scope.dialog || scope.selected ||
        scope.editing || scope.paging || scope.foreground ||
        scope.account?.session !== session)
        return scope;
    return {{...scope, started: ({_ARM_REFRESH})()}};
}}
'''


def _origin(url: str) -> str:
    address = urlsplit(url)
    return f'{address.scheme}://{address.netloc}'


class DesktopBrowserRuntime:
    """一个桌面线程共用驱动，两套系统仍各自创建浏览器和登录会话。"""

    def __init__(self):
        self._driver = None

    def driver(self):
        if self._driver is None:
            self._driver = sync_playwright().start()
        return self._driver

    def close(self):
        if self._driver is not None:
            try:
                self._driver.stop()
            finally:
                self._driver = None


class LiveBrowserGateway:
    """受控浏览器只读网关，调用与清理由同一线程执行。"""

    def __init__(self, base_url: str = _LIVE_ORIGIN, *, runtime=None):
        address = urlsplit(base_url)
        anonymous = (address.scheme == 'http' and address.hostname == '127.0.0.1'
                     and address.port is not None and address.netloc == f'127.0.0.1:{address.port}')
        if ((not anonymous and _origin(base_url) != _LIVE_ORIGIN) or address.path not in ('', '/')
                or address.query or address.fragment):
            raise ValueError('请使用正式站首页或本机匿名测试地址。')
        self.base_url = base_url.rstrip('/')
        self._runtime = runtime
        self._owner = None
        self._playwright = None
        self._browser = None
        self._page = None
        self._account = None
        self._observation = None
        self._refresh_incomplete = False

    def _check_thread(self) -> None:
        if self._owner is not None and self._owner != get_ident():
            raise RuntimeError('正式站浏览器须由创建它的线程操作和关闭。')

    def _forget_account(self) -> None:
        self._account = self._observation = None

    def _require_origin(self) -> None:
        if _origin(self._page.url) != self.base_url:
            self._forget_account()
            raise GatewayError('受控页面已离开本站，请在助手中重新打开登录。')

    def open_login(self) -> None:
        """员工在专用可见窗口手动操作登录，网关读取该窗口的登录结果。"""
        self._check_thread()
        if self._page is not None and not self._page.is_closed():
            try:
                if _origin(self._page.url) != self.base_url:
                    self._forget_account()
                    self._page.goto(self.base_url + '/', wait_until='domcontentloaded')
                    self._require_origin()
                self._page.bring_to_front()
                return
            except BrowserError:
                self.close()

        self.close()
        self._owner = get_ident()
        try:
            self._playwright = self._runtime.driver() if self._runtime is not None else sync_playwright().start()
            self._browser = open_browser(self._playwright)
            context = self._browser.new_context()
            context.set_default_timeout(_TIMEOUT_MS)
            permitted = json.dumps(self.base_url)
            context.add_init_script(f'if (window === window.top && location.origin === {permitted}) '
                                    f'{{ {_OBSERVE_ACCOUNT} }}')
            self._page = context.new_page()
            self._page.goto(self.base_url + '/', wait_until='domcontentloaded')
            self._require_origin()
            self._page.bring_to_front()
        except (BrowserError, GatewayError) as error:
            self.close()
            if isinstance(error, GatewayError):
                raise
            raise GatewayError('暂时无法打开正式站登录页。') from None

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
                self._forget_account()
                self._refresh_incomplete = False

    def _visible_account(self, observed: dict) -> Account | None:
        if observed['count'] == 0 or (observed['count'] == 1 and observed['text'] == '未登录'):
            self._forget_account()
            return None
        parts = [text.strip() for text in observed['text'].rsplit('·', 1)]
        if observed['count'] != 1 or len(parts) != 2 or not all(parts):
            self._forget_account()
            raise GatewayError('无法确认当前账号，请核对正式站右上角。')
        role = {'材料': 'material', '代码': 'code'}.get(parts[1])
        if role is None:
            self._forget_account()
            return None
        if self._observation != observed['session']:
            # 只供本机区分当前会话的准备记录，绝非站方身份或授权凭据。
            local_session = uuid4().hex
            self._account = Account('live-' + local_session, parts[0], role, local_session, observed['username'])
            self._observation = observed['session']
        return self._account

    def _read_tasks(self, rows: list[dict], account: Account, seen: set[str]) -> tuple[Task, ...]:
        tasks = []
        for row in rows:
            identifier = row.get('id', '')
            number = identifier.lstrip('0')
            status = row.get('stage')
            stage = _STAGES.get(status)
            if (row['cells'] not in (11, 12) or not identifier.isascii() or not identifier.isdigit()
                    or not number or not row.get('title') or row.get('status') != status):
                raise GatewayError('无法核对材料清单的编号、负责人或工序，已暂停读取。')
            if number in seen:
                raise GatewayError('本人材料清单有重复编号，请核对后重新同步。')
            seen.add(number)
            if row['unassigned']:
                continue
            if stage is None:
                raise GatewayError('无法核对材料清单的工序，已暂停读取。')
            # 这是展示变化的指纹，不能作为正式站版本或写入依据。
            basis = (identifier, row['title'], account.name, status, row.get('touched', ''))
            revision = 'observed-' + sha256(json.dumps(basis, ensure_ascii=False).encode('utf-8')).hexdigest()
            tasks.append(Task(identifier, row['title'], account.id, stage, revision))
        return tuple(tasks)

    def current_account(self) -> Account | None:
        """只核对当前页面可见账号。"""
        self._check_thread()
        if self._page is None or self._page.is_closed():
            self._forget_account()
            return None
        self._require_origin()
        try:
            return self._visible_account(self._page.evaluate('window.__onlineDropReadVisibleAccount()'))
        except BrowserError:
            self._forget_account()
            raise GatewayError('暂时无法核对正式站当前账号。') from None

    def _check_list_scope(self, observed: dict, account: Account, *, require_mine: bool = False) -> None:
        self._require_origin()
        if observed.get('foreign'):
            self._forget_account()
            raise GatewayError('受控页面已离开本站，请在助手中重新打开登录。')
        if self._visible_account(observed['account']) != account:
            raise GatewayError('当前账号已变化，请重新同步。')
        if require_mine and not observed['mine']:
            raise RefreshDeferred('请在正式站打开材料清单并勾选“只看我的”，让助手继续同步。')
        if observed['filtered']:
            raise RefreshDeferred('材料清单启用了其他筛选，请清除筛选后重新同步。')
        if observed['dialog']:
            raise RefreshDeferred('请先处理正式站当前对话框，再同步材料清单。')
        if observed['selected']:
            raise RefreshDeferred('正式站材料清单有已勾选的项目，请先处理或取消勾选，再同步。')
        if observed['editing']:
            raise RefreshDeferred('正式站正在输入，离开输入框后会自动同步。')
        if observed['paging'] or observed['userMoved']:
            raise RefreshDeferred('网页正在操作，稍后同步材料清单。')
        if observed['foreground']:
            raise RefreshDeferred('原站窗口正在使用，切到其他窗口后会自动同步。')

    def _check_material_page(self, observed: dict, account: Account) -> None:
        self._check_list_scope(observed, account, require_mine=True)
        if observed.get('invalidPager'):
            raise GatewayError('无法核对本人材料清单的分页，请在正式站核对。')
        if observed.get('mismatch'):
            raise GatewayError('本人清单含有无法核对负责人的项目，已暂停读取。')
        if not observed['rows'] and (not observed['empty'] or observed['total'] != 1):
            raise GatewayError('材料清单缺少项目页或明确空态，请稍后刷新。')

    def _move_material_page(self, account: Account, number: int, target: int,
                            total: int, deadline: float, activity: int | None = None) -> dict:
        if monotonic() >= deadline:
            raise GatewayError('未能及时核对所有材料清单页，请手动同步重试。')
        move = {'from': number, 'target': target, 'total': total, 'session': self._observation}
        scope = {'origin': self.base_url, 'activity': activity, 'protectForeground': activity is not None}
        clicked = self._page.evaluate(_READ_MATERIALS, {**scope, 'move': move})
        self._check_list_scope(clicked, account, require_mine=True)
        if not clicked.get('moved'):
            raise GatewayError('翻页前材料清单分页已变化，请重新同步。')
        try:
            observed = self._page.wait_for_function(
                _READ_MATERIALS, arg={**scope, 'after': move},
                polling=_PAGE_CHECK_MS,
                timeout=max(1, int((deadline - monotonic()) * 1000))).json_value()
        except BrowserTimeout:
            raise GatewayError('未能及时核对所有材料清单页，请手动同步重试。') from None
        self._check_material_page(observed, account)
        if observed['number'] != target or observed['total'] != total:
            raise GatewayError('材料清单页码或总页数发生变化，请重新同步。')
        return observed

    def _read_material_pages(self, account: Account, reveal: Task | None = None, *,
                             activity: int | None = None) -> tuple[Task, ...]:
        deadline = monotonic() + _TIMEOUT_MS / 1000
        scope = {'origin': self.base_url, 'activity': activity, 'protectForeground': activity is not None}
        observed = self._page.evaluate(_READ_MATERIALS, scope)
        self._check_material_page(observed, account)
        number, total = observed['number'], observed['total']
        start = number
        if total > _MAX_MATERIAL_PAGES:
            raise GatewayError(f'本人材料清单超过 {_MAX_MATERIAL_PAGES} 页，请在正式站核对。')
        seen: set[str] = set()
        pages = {number: (observed['rows'], self._read_tasks(observed['rows'], account, seen))}
        # 扫完相邻各页后回到起始页，回程也校验同一清单和已读项目。
        if number - 1 <= total - number:
            targets = chain(range(number - 1, 0, -1), range(2, total + 1),
                            range(total - 1, start - 1, -1))
        else:
            targets = chain(range(number + 1, total + 1), range(total - 1, 0, -1),
                            range(2, start + 1))
        for target in targets:
            observed = self._move_material_page(account, number, target, total, deadline, activity)
            rows = observed['rows']
            if target in pages:
                if rows != pages[target][0]:
                    raise GatewayError('翻页期间材料清单发生变化，请重新同步。')
            else:
                pages[target] = (rows, self._read_tasks(rows, account, seen))
            number = target
        if reveal is not None:
            self._show_material_row(account, reveal, pages, number, total, deadline)
        return tuple(task for page in range(1, total + 1) for task in pages[page][1])

    def _show_material_row(self, account: Account, expected: Task, pages: dict, number: int,
                           total: int, deadline: float) -> None:
        match = next(((page, task) for page, (_, tasks) in pages.items()
                      for task in tasks if task.id == expected.id), None)
        if match is None:
            raise GatewayError(f'本人材料清单未找到项目 #{expected.id}，请在网页上核对。')
        target, current = match
        if current != expected:
            raise GatewayError(f'项目 #{expected.id} 的名称、工序或显示状态已变化，请核对。')
        step = 1 if target >= number else -1
        for page in range(number + step, target + step, step):
            observed = self._move_material_page(account, number, page, total, deadline)
            if observed['rows'] != pages[page][0]:
                raise GatewayError('翻页期间材料清单发生变化，请重新定位。')
            number = page
        reveal = {'id': expected.id, 'number': target, 'total': total,
                  'rows': pages[target][0], 'session': self._observation}
        observed = self._page.evaluate(_READ_MATERIALS, {'origin': self.base_url, 'reveal': reveal})
        self._check_list_scope(observed, account, require_mine=True)
        if not observed.get('revealed'):
            raise GatewayError(f'定位前项目 #{expected.id} 或材料清单已变化，请重新核对。')
        self._page.bring_to_front()

    def snapshot(self) -> Snapshot:
        account = self.current_account()
        if account is None or account.role != 'material':
            return Snapshot(account)
        return self._snapshot_materials(account)

    def _snapshot_materials(self, account: Account, reveal: Task | None = None) -> Snapshot:
        if self._refresh_incomplete:
            raise GatewayError('上次同步未确认成功，请先从正式站重新刷新。')
        try:
            mine = self._page.locator('#matMine').filter(visible=True)
            if mine.count() != 1:
                self._check_list_scope(self._page.evaluate(_READ_MATERIALS, {
                    'origin': self.base_url, 'readScope': True, 'protectSelection': reveal is not None}), account)
                navigation = self._page.locator('button[data-page="material"]').filter(visible=True)
                if navigation.count() != 1:
                    raise GatewayError('无法定位唯一的材料清单入口。')
                navigation.click()
                self._require_origin()
            mine.wait_for(state='visible')
            if not mine.is_checked():
                self._check_list_scope(self._page.evaluate(_READ_MATERIALS, {
                    'origin': self.base_url, 'readScope': True}), account)
                mine.check()
            self._require_origin()
            return Snapshot(account, self._read_material_pages(account, reveal))
        except BrowserError:
            self._forget_account()
            raise GatewayError('暂时无法核对正式站本人材料清单。') from None

    def refresh_snapshot(self) -> Snapshot:
        """通过站内可见刷新按钮同步一次；snapshot 和 task 单独读取页面。"""
        return self._refresh_snapshot(in_place=False)

    def poll_snapshot(self) -> Snapshot:
        """只在已经登录且无人操作的本人材料清单中自动同步。"""
        return self._refresh_snapshot(in_place=True)

    def reveal_task(self, account: Account, expected: Task) -> None:
        """在已登录网页的本人清单定位已保存原件对应的项目行。"""
        if account.role != 'material' or expected.owner_id != account.id:
            raise GatewayError('请使用原来的材料账号定位该项目。')
        if (expected.stage not in (Stage.READY, Stage.WORKING) or not expected.revision
                or not expected.id.isascii() or not expected.id.isdecimal() or not expected.id.lstrip('0')):
            raise GatewayError('原件记录的项目编号或工序无法核对。')
        self._refresh_snapshot(in_place=False, expected_account=account, reveal=expected)

    def _refresh_snapshot(self, *, in_place: bool, expected_account: Account | None = None,
                          reveal: Task | None = None) -> Snapshot:
        account = self.current_account()
        if expected_account is not None and account != expected_account:
            raise GatewayError('当前账号已变化，请使用原来的材料账号重新核对。')
        if account is None or account.role != 'material':
            return Snapshot(account)
        try:
            # 刷新前在同一次网页操作内确认账号和用户操作状态。
            scoped = self._page.evaluate(_START_GUARDED_REFRESH, {
                'origin': self.base_url, 'session': self._observation,
                'allowNavigation': not in_place, 'protectSelection': not in_place, 'protectForeground': in_place})
            self._check_list_scope(scoped, account, require_mine=in_place)
            if scoped.get('started') == 'busy':
                raise RefreshDeferred('正式站正在刷新，稍后同步材料清单。')
            if not scoped.get('started'):
                raise GatewayError('无法核对正式站刷新入口。')
            self._refresh_incomplete = True
            self._require_origin()
            state = self._page.wait_for_function(
                'origin => location.origin === origin && window.__onlineDropRefresh?.state',
                arg=self.base_url, timeout=_TIMEOUT_MS, polling=_PAGE_CHECK_MS).json_value()
            self._require_origin()
            if state != 'success':
                raise GatewayError('正式站刷新失败，请稍后重试。')
            self._refresh_incomplete = False
            if in_place:
                # 刷新后只读取当前内容，复核本人视图，员工切页时不重新打开清单。
                return Snapshot(account, self._read_material_pages(account, activity=scoped['interaction']))
            if reveal is not None:
                if self.current_account() != account:
                    raise GatewayError('刷新期间账号已变化，请重新核对。')
                return self._snapshot_materials(account, reveal)
            current = self.snapshot()
            if current.account != account:
                return Snapshot(None)
            return current
        except BrowserError:
            raise GatewayError('未能从正式站确认刷新成功，请稍后重试。') from None
        finally:
            if not self._page.is_closed() and _origin(self._page.url) == self.base_url:
                try:
                    self._page.evaluate('window.__onlineDropRefresh?.observer.disconnect()')
                except BrowserError:
                    pass

    def task(self, task_id: str) -> Task | None:
        return next((task for task in self.snapshot().tasks if task.id == task_id), None)

    def begin(self, account: Account, task: Task, request_id: str) -> str:
        raise GatewayError('真实站仅支持只读准备，暂不能代办开始制作。')

    def submit(self, account: Account, task: Task, request_id: str, material: Material) -> None:
        raise GatewayError('真实站仅支持只读准备，暂不能代办提交材料。')

    def read_submission(self, account, task):
        from workbox.team_delivery.site_submission import read_site_submission
        return read_site_submission(self, account, task)

    def submit_verified(self, account, task, *, check_accounts=None):
        from workbox.team_delivery.site_submission import submit_via_visible_site
        return submit_via_visible_site(self, account, task, check_accounts=check_accounts)
