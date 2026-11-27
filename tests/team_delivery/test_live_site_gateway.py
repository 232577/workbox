from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps
from pathlib import Path
from threading import Thread

import pytest

from workbox.team_delivery import live_site
from workbox.team_delivery.contracts import Account, GatewayError, Material, Snapshot, Stage, Task
from workbox.team_delivery.live_site import LiveBrowserGateway


_ANONYMOUS_PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>匿名材料清单测试</title>
<style>.hidden { display: none; }</style>
<div id="auth"></div>
<button onclick="login('材料甲')">模拟登录甲</button>
<button onclick="login('材料乙')">模拟登录乙</button>
<button onclick="login('代码员 · 代码', true)">模拟非材料账号</button>
<button onclick="login('')">模拟退出</button>
<button data-page="progress" onclick="showProgress()">进度</button>
<button data-page="material" onclick="document.getElementById('progress').hidden=true; document.getElementById('matPane').hidden=false; render()">材料清单</button>
<button id="refreshApp" onclick="refresh()">刷新</button>
<span id="refreshStatus"></span>
<section id="progress" hidden>项目进度</section>
<section id="matPane" hidden>
  <label><input type="checkbox" id="matMine" onchange="render()">只看我的</label>
  <input id="matSearch">
  <details id="matFilter"><summary>工序</summary><input type="checkbox" value="待制作"></details>
  <details id="matType"><summary>类型</summary><input type="checkbox" value="软著"></details>
  <select id="matPerson"><option value="">全部人员</option><option value="材料甲">材料甲</option></select>
  <select id="matCodeOwner"><option value="">全部代码负责人</option><option value="sample-a">匿名甲</option></select>
  <select id="matMaterialOwner"><option value="">全部材料负责人</option><option value="sample-a">匿名甲</option></select>
  <input id="matCreatedDate" type="date">
  <input id="matOperationDate" type="date">
  <select id="matOperator"><option value="">全部操作员</option><option value="材料乙">材料乙</option></select>
  <input id="matRemark">
  <div id="matReminders"><button data-stage-reminder="overdue" aria-pressed="false">逾期</button></div>
  <button id="matBatchFocus" class="hidden">已筛选批次</button>
  <dialog id="submitDialog">确认提交</dialog>
  <div id="matList"></div>
  <div id="matPager" hidden><button>上一页</button><button>下一页</button></div>
</section>
<script>
let fixtureForeground = false;
document.hasFocus = () => fixtureForeground;
let fixtureModern = false, me, state, sessionVersion;
let account = '', mode = '', refreshMode = '', refreshCount = 0;
let currentPage = 1, pageCount = 0, pageMode = '';
const pageClicks = [];
const tasks = [
    ['201', '匿名同名项目', '待制作', '材料甲'],
    ['202', '匿名同名项目', '进行中', '材料乙'],
];
function login(name, raw=false, username='') {
    const auth = document.getElementById('auth');
    auth.replaceChildren();
    account = name;
    const current = document.createElement('div');
    current.id = 'who';
    current.setAttribute('aria-label', '当前账号');
    current.textContent = name ? (raw ? name : name + ' · 材料') : '未登录';
    if (fixtureModern) {
        me = name ? {name, account: username, role: '材料'} : null;
        sessionVersion = (sessionVersion || 0) + 1;
    }
    auth.append(current);
    render();
}
function showProgress() {
    document.getElementById('matPane').hidden = true;
    document.getElementById('progress').hidden = false;
}
function refresh() {
    refreshCount++;
    const feedback = document.getElementById('refreshStatus');
    feedback.textContent = '刷新中…';
    setTimeout(async () => {
        if (refreshMode === 'error') {
            feedback.textContent = '刷新失败，请重试';
        } else {
            if (refreshMode === 'progress') showProgress();
            if (refreshMode === 'all') document.getElementById('matMine').checked = false;
            if (refreshMode === 'backend') {
                const response = await fetch('/fixture-tasks', {cache: 'no-store'});
                if (!response.ok) {
                    feedback.textContent = '刷新失败，请重试';
                    return;
                }
                tasks.splice(0, tasks.length, ...await response.json());
            } else {
                tasks[0][2] = '进行中';
            }
            render();
            feedback.textContent = '已刷新 11:00';
        }
    }, 50);
}
function setupPages(page, problem='') {
    tasks.push(
        ['203', '匿名同名项目', '进行中', '材料甲'],
        ['204', '匿名末页项目', '预审退回', '材料甲'],
    );
    currentPage = page;
    pageCount = problem === 'too-many' ? 999 : 3;
    pageMode = problem;
    render();
}
function showPage(target) {
    pageClicks.push(target);
    if (pageMode === 'stuck') return;
    setTimeout(() => {
        currentPage = pageMode === 'jump' ? 3 : target;
        if (pageMode === 'switch') {
            login('材料乙');
            return;
        }
        if (pageMode === 'count-change') pageCount = 4;
        if (pageMode === 'filter-change') document.getElementById('matSearch').value = '匿名';
        render();
    }, 20);
}
function render() {
    const list = document.getElementById('matList');
    list.replaceChildren();
    if (pageCount) {
        const pager = document.getElementById('matPager');
        pager.hidden = false;
        pager.innerHTML = '<button data-action="page" data-kind="mat">上一页</button>' +
            '<span>第 ' + currentPage + '/' + pageCount + ' 页</span>' +
            '<button data-action="page" data-kind="mat">下一页</button>';
        const [previous, next] = pager.querySelectorAll('button');
        previous.dataset.number = currentPage - 1;
        previous.disabled = currentPage <= 1;
        previous.onclick = () => showPage(currentPage - 1);
        next.dataset.number = currentPage + (pageMode === 'wrong-control' ? 2 : 1);
        next.disabled = currentPage >= pageCount;
        next.onclick = () => showPage(currentPage + 1);
    }
    if (['loading', 'empty', 'only-completed'].includes(mode) || (pageMode === 'missing' && currentPage === 2)) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = mode === 'loading' ? '加载中…'
            : mode === 'only-completed' ? '当前没有未完成的任务。' : '暂无任务';
        list.append(empty);
        return;
    }
    const table = document.createElement('table');
    const body = table.createTBody();
    list.append(table);
    const ownTasks = tasks.filter(task =>
        !document.getElementById('matMine').checked ||
        (fixtureModern ? task[4] === me?.account : task[3] === account) || mode === 'mixed');
    const visibleTasks = pageCount > 1
        ? [ownTasks[pageMode === 'duplicate' && currentPage === 2 ? 0 : currentPage - 1]].filter(Boolean)
        : ownTasks;
    for (const [id, title, status, owner, username, touched] of visibleTasks) {
        const row = body.insertRow();
        for (let i=0; i<(fixtureModern ? 11 : 12); i++) row.insertCell();
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.value = id;
        check.dataset.status = mode === 'unknown' ? '未知工序' : status;
        row.cells[0].append(check);
        row.cells[1].textContent = title;
        row.cells[4].textContent = mode === 'conflict' ? '预审退回' : check.dataset.status;
        if (fixtureModern) {
            const name = document.createElement('span');
            name.className = 'project-name';
            name.title = name.textContent = title;
            row.cells[1].replaceChildren(name);
            row.cells[6].textContent = owner;
            const operated = document.createElement('span');
            operated.title = touched;
            operated.textContent = '2026-11-20 11:00';
            row.cells[8].append(operated);
            continue;
        }
        row.cells[7].textContent = '2026-11-20 11:00';
        row.cells[9].textContent = owner;
    }
}
login('');
</script></html>'''


@pytest.fixture
def anonymous_site():
    requests = []
    backend = {'tasks': ()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(('GET', self.path))
            if self.path == '/':
                content_type = 'text/html; charset=utf-8'
                body = _ANONYMOUS_PAGE.encode('utf-8')
            elif self.path == '/fixture-tasks':
                content_type = 'application/json; charset=utf-8'
                body = dumps(backend['tasks'], ensure_ascii=False).encode('utf-8')
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(('POST', self.path))
            self.send_error(405)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, backend
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def gateway(anonymous_site):
    address, requests, _ = anonymous_site
    instance = LiveBrowserGateway(address)
    try:
        yield instance, requests
    finally:
        instance.close()


@contextmanager
def _short_action_timeout(live, monkeypatch, timeout_ms):
    page = live._page
    default_timeout_ms = live_site._TIMEOUT_MS
    with monkeypatch.context() as action:
        action.setattr(live_site, '_TIMEOUT_MS', timeout_ms)
        page.set_default_timeout(timeout_ms)
        try:
            yield
        finally:
            page.set_default_timeout(default_timeout_ms)


def test_desktop_timer_updates_own_tasks_without_manual_refresh(anonymous_site, tmp_path, monkeypatch):
    from concurrent.futures import Future
    from time import monotonic, sleep
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from workbox.team_delivery.application import IntakeController
    from workbox.team_delivery.desktop import DesktopWindow

    open_browser = live_site.open_browser

    def open_isolated(driver):
        def launch(**options):
            options['headless'] = True
            return driver.chromium.launch(**options)
        return open_browser(SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

    monkeypatch.setattr(live_site, 'open_browser', open_isolated)
    address, requests, backend = anonymous_site
    backend['tasks'] = (
        ('301', '匿名待完成', '待制作', '材料甲'),
        ('302', '匿名待改派', '待制作', '材料甲'),
    )
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    legacy_gateway = live_site.LiveBrowserGateway(address)
    controller = IntakeController(
        window, address, tmp_path, tmp_path / 'journal.sqlite', settings=tmp_path / 'settings.json',
        gateway=legacy_gateway)
    signed_in = Future()

    def sign_in():
        try:
            controller._worker._connect()
            gateway = controller._worker._gateway
            gateway.open_login()
            gateway._page.get_by_role('button', name='模拟登录甲').click()
            gateway._page.evaluate("refreshMode = 'backend'")
            signed_in.set_result(gateway.current_account())
        except Exception as error:
            signed_in.set_exception(error)

    def open_own_view():
        browser_page = controller._worker._gateway._page
        browser_page.get_by_role('button', name='材料清单', exact=True).click()
        browser_page.locator('#matMine').check()

    def wait_for(check, seconds, operation):
        deadline = monotonic() + seconds
        while not check() and monotonic() < deadline:
            app.processEvents()
            # Browser commands and the anonymous HTTP server run on Python threads.
            sleep(0.01)
        assert check(), (operation, app.applicationState(), window._sync_notice.text(), displayed_ids(), requests)

    def displayed_ids():
        return [window._tasks.item(row, 1).text() for row in range(window._tasks.rowCount())]

    controller._commands.put((sign_in, ()))
    controller.start()
    try:
        # The isolated browser may try Chrome and Edge, each with a 30-second launch timeout.
        wait_for(signed_in.done, 75, '启动匿名浏览器并登录')
        actor = signed_in.result()
        assert actor is not None and actor.role == 'material'
        window.show()
        window.activateWindow()
        wait_for(lambda: app.applicationState() == Qt.ApplicationState.ApplicationActive
                 and app.activeWindow() is window, 10, '桌面团队交件前台')
        assert controller._timer.interval() == 15_000
        wait_for(lambda: '只看我的' in window._sync_notice.text(), 12, '本人视图指引')
        assert window._empty.text() == '等待同步'
        assert ('GET', '/fixture-tasks') not in requests
        controller._commands.put((open_own_view, ()))
        wait_for(lambda: displayed_ids() == ['301', '302'], 18, '首次自动同步')

        window.hide()
        backend['tasks'] = (
            ('301', '匿名待完成', '已完成', '材料甲'),
            ('302', '匿名待改派', '待制作', '材料乙'),
            ('303', '匿名新增', '待制作', '材料甲'),
        )
        assert displayed_ids() == ['301', '302']
        wait_for(lambda: displayed_ids() == ['303'], 18, '团队页隐藏时自动同步')
        assert requests.count(('GET', '/fixture-tasks')) == 2
        assert not any(method == 'POST' for method, _ in requests)
    finally:
        controller.stop()
        window.close()
    assert not controller._thread.is_alive()
    assert controller._worker._gateway._page is None
    assert legacy_gateway._playwright is None


def test_only_own_tasks_are_read_by_stable_number_and_session(gateway):
    live, requests = gateway
    assert live.snapshot() == Snapshot(None) and requests == []
    live.open_login()
    assert live.snapshot() == Snapshot(None)
    live._page.get_by_role('button', name='模拟登录甲').click()
    first = live.snapshot()
    assert first.account and first.account.name == '材料甲' and first.account.role == 'material'
    row, = first.tasks
    assert (row.id, row.title, row.owner_id, row.stage) == ('201', '匿名同名项目', first.account.id, Stage.READY)
    assert row.handoff is None and live._page.locator('#matMine').is_checked()
    assert live.snapshot() == first
    assert live.task('201') == row and live.task('202') is None

    live._page.get_by_role('button', name='模拟登录乙').click()
    other = live.snapshot()
    assert other.account.id != first.account.id and other.account.session != first.account.session
    assert [task.id for task in other.tasks] == ['202']
    assert live.task('201') is None
    live._page.get_by_role('button', name='模拟退出').click()
    assert live.snapshot() == Snapshot(None)
    assert not any(method == 'POST' for method, _ in requests)


def test_unknown_stage_wrong_owner_and_non_material_fail_closed(gateway):
    live, _ = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert [task.id for task in live.snapshot().tasks] == ['201']
    for mode in ('unknown', 'conflict', 'mixed', 'loading'):
        live._page.evaluate('(value) => { mode = value; render(); }', mode)
        with pytest.raises(GatewayError):
            live.snapshot()
    live._page.evaluate("mode = 'empty'; render();")
    assert live.snapshot().tasks == ()
    live._page.get_by_role('button', name='模拟非材料账号').click()
    code = live.current_account()
    assert code.name == '代码员' and code.role == 'code'
    assert live.snapshot() == live.poll_snapshot() == Snapshot(code)


def test_reads_current_eleven_column_view_and_identifies_same_name_accounts(gateway):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.evaluate("""
        () => {
            fixtureModern = true;
            login('同名员工', false, 'sample-a');
            const indicator = document.getElementById('who');
            const name = document.createElement('span');
            name.id = 'who';
            name.textContent = indicator.textContent;
            indicator.removeAttribute('id');
            indicator.replaceChildren(name);
            tasks.splice(0, tasks.length,
                ['301', '匿名  双空格项目', '待制作', '同名员工', 'sample-a', '2026-11-20T11:00:01']);
            state = {projects: [{id: 301, name: tasks[0][1], matStatus: '待制作',
                matOwner: 'sample-a', matOwnerName: '同名员工'}]};
            render();
        }
    """)
    first = live.snapshot()
    assert first.account.username == 'sample-a'
    assert [(task.id, task.title) for task in first.tasks] == [('301', '匿名  双空格项目')]
    page.evaluate("tasks[0][5] = '2026-11-20T11:00:02'; render()")
    assert live.snapshot().tasks[0].revision != first.tasks[0].revision

    page.evaluate("state.projects[0].matOwner = 'sample-b'")
    with pytest.raises(GatewayError, match='负责人'):
        live.snapshot()

    page.evaluate("me = {...me, account: 'sample-b'}; tasks[0][4] = 'sample-b'; render()")
    second = live.snapshot()
    assert second.account.name == first.account.name
    assert second.account.username == 'sample-b' and second.account.session != first.account.session
    assert second.tasks[0].owner_id == second.account.id

    page.evaluate("mode = 'only-completed'; render()")
    assert live.snapshot() == Snapshot(second.account)

    page.evaluate("""document.body.insertAdjacentHTML('beforeend',
        '<div aria-label="当前账号">另一个人 · 材料</div>')""")
    with pytest.raises(GatewayError, match='无法确认当前账号'):
        live.current_account()


def test_completed_and_unassigned_rows_with_or_without_the_single_page_controls(gateway):
    live, _ = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    original = live.snapshot()
    live._page.evaluate('''() => {
        tasks.push(['203', '匿名已完成项目', '已完成', '材料甲']);
        tasks.push(['204', '匿名待分发项目', '待分发', '材料甲']);
        render();
    }''')
    current = live.snapshot()
    assert [(task.id, task.stage) for task in current.tasks] == [('201', Stage.READY), ('203', Stage.COMPLETED)]
    assert original.account == current.account
    live._page.evaluate('pageCount = 1; render()')
    assert live.snapshot() == current
    live._page.locator('#matPager').evaluate("(pager) => { pager.innerHTML = '<button>下一页</button>'; }")
    with pytest.raises(GatewayError, match='分页'):
        live.snapshot()


def test_reads_all_pages_from_the_middle_and_keeps_identically_named_tasks(gateway):
    live, requests = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    first = live.snapshot()
    live._page.evaluate('setupPages(2)')

    assert live.current_account() == first.account
    assert live._page.evaluate('pageClicks') == []
    current = live.snapshot()
    assert current.account == first.account
    assert [(row.id, row.title, row.stage) for row in current.tasks] == [
        ('201', '匿名同名项目', Stage.READY),
        ('203', '匿名同名项目', Stage.WORKING),
        ('204', '匿名末页项目', Stage.RETURNED),
    ]
    assert live._page.evaluate('pageClicks') == [1, 2, 3, 2]
    assert live._page.evaluate('currentPage') == 2
    assert live.task('201') == current.tasks[0]
    assert live.task('202') is None
    assert [task.id for task in live.poll_snapshot().tasks] == ['201', '203', '204']
    assert live._page.evaluate('currentPage') == 2
    assert live._page.evaluate('refreshCount') == 1
    assert not any(method == 'POST' for method, _ in requests)
    live._page.get_by_role('button', name='模拟退出').click()
    assert live.current_account() is None


@pytest.mark.parametrize(('problem', 'expected_clicks', 'reason'), [
    ('duplicate', [2], '重复编号'),
    ('missing', [2], '缺少项目页'),
    ('jump', [2], '页码'),
    ('switch', [2], '账号'),
    ('count-change', [2], '总页数'),
    ('filter-change', [2], '启用了其他筛选'),
    ('stuck', [2], '未能及时核对'),
    ('too-many', [], '超过'),
    ('wrong-control', [], '分页'),
])
def test_rejects_inconsistent_or_unverifiable_pagination(gateway, monkeypatch, problem, expected_clicks, reason):
    live, requests = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert live.snapshot().tasks
    live._page.evaluate('(problem) => setupPages(1, problem)', problem)

    with _short_action_timeout(live, monkeypatch, 800), pytest.raises(GatewayError, match=reason):
        live.snapshot()
    assert live._page.evaluate('pageClicks') == expected_clicks
    assert live._page.evaluate('refreshCount') == 0
    assert not any(method == 'POST' for method, _ in requests)


def test_rejects_a_filtered_view_without_clearing_filters_or_paging(gateway):
    live, _ = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert live.snapshot().tasks
    for field, value in (
        ('matSearch', '匿名'),
        ('matPerson', '材料甲'), ('matCreatedDate', '2026-11-20'),
        ('matOperationDate', '2026-11-20'), ('matOperator', '材料乙'), ('matRemark', '匿名'),
        ('matCodeOwner', 'sample-a'), ('matMaterialOwner', 'sample-a'),
    ):
        locator = live._page.locator('#' + field)
        previous = locator.input_value()
        locator.evaluate('(node, value) => { node.value = value; }', value)
        with pytest.raises(GatewayError, match='筛选'):
            live.snapshot()
        assert locator.input_value() == value
        locator.evaluate('(node, value) => { node.value = value; }', previous)
    for detail in ('matFilter', 'matType'):
        option = live._page.locator(f'#{detail} input')
        option.evaluate('node => { node.checked = true; }')
        with pytest.raises(GatewayError, match='筛选'):
            live.snapshot()
        assert option.is_checked()
        option.evaluate('node => { node.checked = false; }')
    reminder = live._page.locator('#matReminders button')
    reminder.evaluate("node => node.setAttribute('aria-pressed', 'true')")
    with pytest.raises(GatewayError, match='筛选'):
        live.poll_snapshot()
    assert reminder.get_attribute('aria-pressed') == 'true'
    reminder.evaluate("node => node.setAttribute('aria-pressed', 'false')")
    live._page.locator('#matBatchFocus').evaluate("node => node.classList.remove('hidden')")
    with pytest.raises(GatewayError, match='筛选'):
        live.snapshot()
    assert live._page.evaluate('[pageClicks.length, refreshCount]') == [0, 0]


def test_defers_paging_while_the_employee_has_selected_a_task_or_opened_a_dialog(gateway):
    live, _ = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert live.snapshot().tasks
    live._page.evaluate('setupPages(1)')
    selection = live._page.locator('#matList input[type="checkbox"]')
    selection.check()
    with pytest.raises(GatewayError, match='选中|勾选'):
        live.snapshot()
    with pytest.raises(GatewayError, match='选中|勾选'):
        live.poll_snapshot()
    assert selection.is_checked()

    selection.uncheck()
    live._page.locator('#submitDialog').evaluate('node => node.showModal()')
    with pytest.raises(GatewayError, match='对话框'):
        live.snapshot()
    with pytest.raises(GatewayError, match='对话框'):
        live.poll_snapshot()
    assert live._page.locator('#submitDialog').is_visible()
    assert live._page.evaluate('[pageClicks.length, refreshCount]') == [0, 0]


def test_automatic_refresh_leaves_other_pages_and_user_filters_alone(gateway):
    live, _ = gateway
    assert live.poll_snapshot() == Snapshot(None)
    live.open_login()
    assert live.poll_snapshot() == Snapshot(None)
    live._page.get_by_role('button', name='模拟登录甲').click()
    initial = live.snapshot()
    live._page.get_by_role('button', name='进度', exact=True).click()
    with pytest.raises(GatewayError, match='材料清单.*只看我的'):
        live.poll_snapshot()
    assert live._page.locator('#progress').is_visible()
    assert live._page.evaluate('refreshCount') == 0

    live._page.get_by_role('button', name='材料清单').click()
    live._page.locator('#matMine').uncheck()
    with pytest.raises(GatewayError, match='材料清单.*只看我的'):
        live.poll_snapshot()
    assert not live._page.locator('#matMine').is_checked()
    assert live._page.evaluate('refreshCount') == 0

    live._page.locator('#matMine').check()
    assert live.poll_snapshot().account == initial.account
    assert live._page.evaluate('refreshCount') == 1


def test_automatic_refresh_leaves_focused_original_site_untouched_and_detects_logout(gateway):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    live.snapshot()
    page.evaluate('setupPages(2); fixtureForeground = true')
    with pytest.raises(GatewayError, match='原站窗口正在使用'):
        live.poll_snapshot()
    assert page.evaluate('[currentPage, pageClicks.length, refreshCount]') == [2, 0, 0]
    assert page.context.pages == [page]
    page.get_by_role('button', name='模拟退出').click()
    assert live.poll_snapshot() == Snapshot(None)
    assert page.evaluate('refreshCount') == 0


def test_automatic_refresh_waits_for_focused_empty_filter_and_an_existing_refresh(gateway):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    live.snapshot()
    search = page.locator('#matSearch')
    search.focus()
    with pytest.raises(GatewayError, match='正在输入'):
        live.poll_snapshot()
    assert search.evaluate('node => document.activeElement === node')
    search.blur()
    page.evaluate("document.querySelector('#refreshStatus').textContent = '正在刷新…'")
    with pytest.raises(GatewayError, match='正在刷新'):
        live.poll_snapshot()
    assert page.evaluate('refreshCount') == 0


@pytest.mark.parametrize(('foreground', 'message'), [
    (False, '正在输入'),
    (True, '原站窗口正在使用'),
])
def test_automatic_refresh_stops_paging_when_employee_returns_to_original_site(gateway, foreground, message):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    live.snapshot()
    page.evaluate("""
        foreground => {
            setupPages(1);
            document.querySelector('#refreshApp').addEventListener('click', () => {
                if (foreground) fixtureForeground = true;
                else document.querySelector('#matSearch').focus();
            });
        }
    """, foreground)
    with pytest.raises(GatewayError, match=message):
        live.poll_snapshot()
    assert page.evaluate('[pageClicks.length, refreshCount]') == [0, 1]
    if not foreground:
        assert page.locator('#matSearch').evaluate('node => document.activeElement === node')


def test_automatic_refresh_does_not_interleave_with_manual_paging(gateway):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    live.snapshot()
    page.evaluate('setupPages(1)')
    page.get_by_role('button', name='下一页').click()
    with pytest.raises(GatewayError, match='网页正在操作'):
        live.poll_snapshot()
    assert page.evaluate('pageClicks') == [2]
    assert page.evaluate('refreshCount') == 0


def test_background_refresh_and_paging_do_not_depend_on_animation_frames(gateway, monkeypatch):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    assert live.snapshot().tasks
    page.evaluate('setupPages(1); window.requestAnimationFrame = () => 0')
    with _short_action_timeout(live, monkeypatch, 2000):
        assert [task.id for task in live.poll_snapshot().tasks] == ['201', '203', '204']
    assert page.evaluate('currentPage') == 1


def test_automatic_refresh_reads_a_changed_task_on_a_different_page(gateway):
    live, _ = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    live.snapshot()
    page.evaluate("""
        () => {
            setupPages(1);
            tasks[0][2] = '进行中';
            render();
            document.querySelector('#refreshApp').addEventListener('click', () => {
                tasks[3][2] = '已完成';
            });
        }
    """)
    before = live.snapshot()
    after = live.poll_snapshot()
    assert after.tasks[0] == before.tasks[0]
    assert after.tasks[-1].id == '204' and after.tasks[-1].stage == Stage.COMPLETED
    assert page.evaluate('currentPage') == 1


@pytest.mark.parametrize('mode', ['progress', 'all'])
def test_automatic_refresh_rechecks_the_personal_view_after_refresh(gateway, mode):
    live, _ = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert live.snapshot().tasks
    live._page.evaluate("(mode) => { setupPages(1); refreshMode = mode; }", mode)
    with pytest.raises(GatewayError, match='材料清单.*只看我的'):
        live.poll_snapshot()
    assert live._page.locator('#progress').is_visible() == (mode == 'progress')
    assert live._page.evaluate("[document.querySelector('#matMine').checked, pageClicks.length, refreshCount]") == [
        mode == 'progress', 0, 1,
    ]


def test_refresh_uses_visible_control_and_waits_for_a_fresh_result(gateway):
    live, requests = gateway
    assert live.refresh_snapshot() == Snapshot(None) and requests == []
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    before = live.snapshot()
    after = live.refresh_snapshot()
    assert after.account == before.account
    assert after.tasks[0].stage is Stage.WORKING
    assert after.tasks[0].revision != before.tasks[0].revision
    assert live.task('201') == after.tasks[0]
    assert live._page.evaluate('refreshCount') == 1
    assert live.refresh_snapshot() == after
    assert live._page.evaluate('refreshCount') == 2
    live._page.evaluate("refreshMode = 'error'")
    with pytest.raises(GatewayError, match='刷新'):
        live.refresh_snapshot()
    with pytest.raises(GatewayError, match='刷新'):
        live.task('201')
    live._page.evaluate("refreshMode = ''")
    assert live.refresh_snapshot() == after
    assert not any(method == 'POST' for method, _ in requests)


@pytest.mark.parametrize(('start', 'identifier', 'target', 'from_progress'), [
    (1, '203', 2, False), (2, '201', 1, False), (3, '203', 2, True),
])
def test_reveal_finds_the_exact_same_name_task_across_pages(gateway, start, identifier, target, from_progress):
    live, requests = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    page.evaluate("""start => {
        tasks[0][2] = '进行中';
        setupPages(start);
        window.scrolledTasks = [];
        const scroll = Element.prototype.scrollIntoView;
        Element.prototype.scrollIntoView = function(options) {
            window.scrolledTasks.push(this.querySelector('input[type="checkbox"]')?.value);
            scroll.call(this, options);
        };
    }""", start)
    snapshot = live.snapshot()
    expected = next(task for task in snapshot.tasks if task.id == identifier)
    if from_progress:
        page.get_by_role('button', name='进度', exact=True).click()

    live.reveal_task(snapshot.account, expected)

    assert page.evaluate('currentPage') == target
    assert page.evaluate('refreshCount') == 1
    assert page.evaluate('window.scrolledTasks') == [identifier]
    row = page.locator('#matList tr').filter(has=page.locator(f'input[value="{identifier}"]'))
    assert row.evaluate('row => row.getAnimations().length') == 1
    assert page.locator('#matList input:checked, dialog[open]').count() == 0
    assert set(requests) <= {('GET', '/'), ('GET', '/favicon.ico')}
    # 正常同步从定位后的页开始，并在扫描完成后返回该页。
    assert live.snapshot() == snapshot
    assert page.evaluate('currentPage') == target


@pytest.mark.parametrize(('setup', 'message', 'refreshes'), [
    ("login('材料乙')", '账号', 0),
    ("tasks[0][2] = '待制作'; render()", '工序', 1),
    ("tasks[1][3] = '材料乙'; render()", '未找到', 1),
    ("tasks[1][1] = '改名后的项目'; render()", '工序', 1),
    ("document.getElementById('matSearch').value = '匿名'", '筛选', 0),
    ("document.querySelector('#matList input').checked = true", '已勾选', 0),
    ("document.getElementById('submitDialog').showModal()", '对话框', 0),
])
def test_reveal_stops_when_current_account_task_or_browser_scope_changed(gateway, setup, message, refreshes):
    live, requests = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    page.evaluate("tasks[1][3] = '材料甲'")
    snapshot = live.snapshot()
    # 模拟页面刷新会改第一行的工序，故用该行证明读取的是刷新后的情况。
    expected = snapshot.tasks[0] if 'tasks[0]' in setup else snapshot.tasks[1]
    page.evaluate(f'() => {{ {setup} }}')

    with pytest.raises(GatewayError, match=message):
        live.reveal_task(snapshot.account, expected)

    assert page.evaluate('refreshCount') == refreshes
    assert page.evaluate("() => document.querySelector('#matList').getAnimations({subtree: true}).length") == 0
    assert set(requests) <= {('GET', '/'), ('GET', '/favicon.ico')}


def test_reveal_refuses_a_number_duplicated_on_another_page(gateway):
    live, requests = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    page.evaluate("() => { tasks[0][2] = '进行中'; setupPages(1); }")
    snapshot = live.snapshot()
    page.evaluate("pageMode='duplicate'")

    with pytest.raises(GatewayError, match='重复编号'):
        live.reveal_task(snapshot.account, snapshot.tasks[0])

    assert page.evaluate("() => document.querySelector('#matList').getAnimations({subtree: true}).length") == 0
    assert page.locator('#matList input:checked, dialog[open]').count() == 0
    assert set(requests) <= {('GET', '/'), ('GET', '/favicon.ico')}


def test_reveal_rechecks_the_visible_fingerprint_in_the_scroll_operation(gateway, monkeypatch):
    live, requests = gateway
    live.open_login()
    page = live._page
    page.get_by_role('button', name='模拟登录甲').click()
    page.evaluate("tasks[0][2] = '进行中'")
    snapshot = live.snapshot()
    evaluate = page.evaluate

    def change_row_before_scroll(script, arg=None):
        if isinstance(arg, dict) and 'reveal' in arg:
            evaluate("document.querySelector('#matList tr').cells[7].textContent = '2026-11-20 11:01'")
        return evaluate(script, arg)

    monkeypatch.setattr(page, 'evaluate', change_row_before_scroll)
    with pytest.raises(GatewayError, match='定位前'):
        live.reveal_task(snapshot.account, snapshot.tasks[0])

    assert page.locator('#matList tr').evaluate('row => row.getAnimations().length') == 0
    assert page.locator('#matList input:checked, dialog[open]').count() == 0
    assert set(requests) <= {('GET', '/'), ('GET', '/favicon.ico')}


def test_real_write_actions_are_disabled_without_opening_a_browser():
    live = LiveBrowserGateway('https://zheshijia.dpdns.org/')
    account = Account('anonymous', '匿名', 'material', 'session')
    task = Task('201', '匿名任务', account.id, Stage.WORKING, 'observed')
    material = Material('匿名任务.docx', Path('匿名任务.docx'), 'x' * 64)
    try:
        with pytest.raises(GatewayError, match='真实站'):
            live.begin(account, task, 'begin-1')
        with pytest.raises(GatewayError, match='真实站'):
            live.submit(account, task, 'submit-1', material)
        assert live.snapshot() == Snapshot(None)
    finally:
        live.close()


@pytest.mark.parametrize('edit', [
    "status.setAttribute('data-unrelated', 'true')",
    "status.textContent = '已刷新 11:00'",
])
def test_refresh_requires_a_new_loading_to_terminal_transition(gateway, monkeypatch, edit):
    live, requests = gateway
    live.open_login()
    live._page.get_by_role('button', name='模拟登录甲').click()
    assert live.refresh_snapshot().tasks
    live._page.locator('#refreshApp').evaluate(
        'node => { node.onclick = () => { const status = document.querySelector("#refreshStatus"); ' + edit + '; }; }')
    with _short_action_timeout(live, monkeypatch, 700), pytest.raises(GatewayError, match='刷新'):
        live.refresh_snapshot()
    assert not any(method == 'POST' for method, _ in requests)


@pytest.mark.parametrize('url', [
    'http://zheshijia.dpdns.org/', 'https://zheshijia.dpdns.org.evil.invalid/',
    'https://zheshijia.dpdns.org:8443/', 'https://zheshijia.dpdns.org/private',
])
def test_other_bases_are_rejected(url):
    with pytest.raises(ValueError):
        LiveBrowserGateway(url)
