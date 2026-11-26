from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from workbox.team_delivery.contracts import ActionUncertain, GatewayError, Stage
from workbox.team_delivery.qt_site import QtSiteGateway, QtSiteSession


_PAGE = br'''<!doctype html><meta charset=utf-8>
<div id="who" aria-label="current-account"></div>
<main id="app"><button id="idle">ordinary content</button>
<input id="editing"><div id="matList"><input type="checkbox" class="mat-check" value="202"></div>
<dialog id="active"><button>continue</button></dialog></main>
<script>document.getElementById('who').textContent = 'Alias' + ' \u00b7 ' + '\u6750\u6599';</script>'''


def until(predicate, seconds=12):
    end = monotonic() + seconds
    while monotonic() < end:
        current = predicate()
        if current:
            return current
        QTest.qWait(20)
    pytest.fail('anonymous original-site Qt operation timed out')


def execute(worker, function, *args, **kwargs):
    pending = worker.submit(function, *args, **kwargs)
    until(pending.done)
    return pending.result()


def page_script(page, source):
    result = []
    page.runJavaScript(source, result.append)
    until(lambda: result)
    return result[0]


def install_completion_view(page):
    """Install the anonymous site's in-place refresh and completed-project controls."""
    page_script(page, r'''(() => {
        const list = document.getElementById('matList');
        const controls = document.createElement('section');
        controls.innerHTML = `
            <button id="refreshApp">刷新系统数据</button>
            <span id="refreshStatus"></span>
            <label><input type="checkbox" id="matMine" checked>只看我的</label>
            <button id="matCompletedToggle" aria-expanded="false">已完成项目</button>`;
        list.before(controls);
        const mine = document.getElementById('matMine');
        const toggle = document.getElementById('matCompletedToggle');
        const status = document.getElementById('refreshStatus');
        const view = window.fixtureCompletionView = {ready: false, refreshes: 0, delay: 0, failRefresh: false};
        let projects = [];

        const load = async () => {
            const response = await fetch('/api/state', {cache: 'no-store'});
            if (!response.ok) throw new Error('fixture refresh failed');
            projects = (await response.json()).projects;
        };
        const render = () => {
            const table = document.createElement('table');
            for (const project of projects) {
                if (mine.checked && project.matOwner !== 'fixture') continue;
                const completed = project.matStatus === '已完成';
                if (completed && toggle.getAttribute('aria-expanded') !== 'true') continue;
                const row = table.insertRow();
                row.classList.toggle('is-completed-project', completed);
                for (let index = 0; index < 11; index++) row.insertCell();
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'mat-check';
                checkbox.value = String(project.id);
                checkbox.dataset.status = project.matStatus;
                row.cells[0].append(checkbox);
                const name = document.createElement('span');
                name.className = 'project-name';
                name.title = name.textContent = project.name;
                row.cells[1].append(name);
                row.cells[4].textContent = project.matStatus;
                row.cells[6].textContent = project.matOwnerName || project.matOwner;
            }
            list.replaceChildren(table);
        };
        mine.addEventListener('change', render);
        toggle.addEventListener('click', () => {
            toggle.setAttribute('aria-expanded', toggle.getAttribute('aria-expanded') !== 'true');
            render();
        });
        document.getElementById('refreshApp').addEventListener('click', async () => {
            view.refreshes++;
            status.dataset.state = 'loading';
            status.textContent = '正在刷新系统数据…';
            try {
                if (view.delay) await new Promise(resolve => setTimeout(resolve, view.delay));
                if (view.failRefresh) throw new Error('fixture refresh failed');
                await load();
                mine.checked = false;
                toggle.setAttribute('aria-expanded', 'false');
                render();
                status.dataset.state = 'success';
                status.textContent = '已刷新系统数据';
            } catch (_) {
                status.dataset.state = 'error';
                status.textContent = '刷新失败';
            }
        });
        load().then(() => {
            render();
            view.ready = true;
        });
        return true;
    })()''')
    until(lambda: page_script(page, 'window.fixtureCompletionView?.ready'))


@pytest.fixture
def site():
    app = QApplication.instance() or QApplication([])
    user = {'account': 'fixture', 'name': 'Alias', 'role': '材料', 'mustChange': False}
    rows = [{'id': key, 'name': '同名匿名件', 'matOwner': owner, 'matOwnerName': 'Alias', 'matStatus': '待制作'}
            for key, owner in ((201, 'fixture'), (202, 'fixture'), (203, 'another'))]
    history, failures = [], set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, material, media='application/json', status=200):
            raw = material if isinstance(material, bytes) else json.dumps(material).encode()
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            history.append(('GET', self.path, None))
            if self.path == '/':
                self.reply(_PAGE, 'text/html; charset=utf-8')
            elif self.path == '/api/me':
                self.reply({'user': user})
            elif self.path == '/api/state':
                release = monotonic() + 5
                while 'hold-state' in failures and monotonic() < release:
                    sleep(.01)
                try:
                    self.reply({'projects': rows})
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            history.append(('POST', self.path, body))
            if self.path == '/api/logout':
                self.reply({'ok': True})
                return
            source, target = {'/api/start-material': (('待制作', '预审退回'), '进行中'),
                              '/api/complete-material': (('进行中',), '已完成')}[self.path]
            selected = [row for row in rows if row['id'] in body['ids']]
            assert len(selected) == 1 and selected[0]['matStatus'] in source
            if 'retain' not in failures:
                selected[0]['matStatus'] = target
            if 'drop-reply' in failures:
                self.close_connection = True
                return
            if 'proxy-html' in failures:
                self.reply(b'<h1>Proxy Error</h1>', 'text/html')
                return
            if 'rejected-status' in failures:
                self.reply({'ok': True}, status=403)
                return
            if 'rejected-json' in failures:
                self.reply({'ok': False, 'error': '匿名限制'})
                return
            self.reply({} if 'empty-json' in failures else {'ok': True})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = QtSiteSession(f'http://127.0.0.1:{server.server_port}')
    gateway = QtSiteGateway(session)
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            yield session, gateway, worker, user, rows, history, failures
    finally:
        session.close()
        QTest.qWait(30)
        server.shutdown()
        server.server_close()
        thread.join()


def open_site(fixture):
    session, gateway, worker, *_ = fixture
    execute(worker, gateway.open_login)
    until(lambda: any(item[1] == '/' for item in fixture[5]))
    until(lambda: page_script(session.page, "document.getElementById('who')?.textContent.includes('Alias')"))
    return session, gateway, worker


def writes(history):
    return [(path, body) for method, path, body in history if method == 'POST']


def test_personal_write_uses_original_login_alone_and_verified_same_task(site):
    session, gateway, worker = open_site(site)
    history = site[5]
    snapshot = execute(worker, gateway.snapshot)
    actor, task = snapshot.account, snapshot.tasks[1]
    with pytest.raises(GatewayError):
        execute(worker, gateway.start_verified, actor, task, personal=True)
    assert writes(history) == []

    session.set_personal_writes_enabled(True)
    started = execute(worker, gateway.start_verified, actor, task, personal=True)
    assert started.stage is Stage.WORKING
    completed = execute(worker, gateway.complete_verified, actor, started, personal=True)
    assert completed.stage is Stage.COMPLETED
    assert execute(worker, gateway.read_submission, actor, task)
    assert writes(history) == [('/api/start-material', {'ids': [202]}),
                               ('/api/complete-material', {'ids': [202]})]


def test_personal_completion_refreshes_the_original_view_and_scrolls_to_the_exact_number(site):
    session, gateway, worker = open_site(site)
    rows, history = site[4:6]
    rows[0]['matStatus'] = '已完成'
    rows[1]['matStatus'] = '进行中'
    install_completion_view(session.page)
    page_script(session.page, r'''(() => {
        const style = document.createElement('style');
        style.textContent = '#matList tr {height: 320px} body::after {content: ""; display: block; height: 100vh}';
        document.head.append(style);
    })()''')
    snapshot = execute(worker, gateway.snapshot)
    actor, task = snapshot.account, snapshot.tasks[1]
    session.set_personal_writes_enabled(True)
    completed = execute(worker, gateway.complete_verified, actor, task, personal=True)
    assert page_script(session.page, """document.querySelector('input[value="202"]').dataset.status""") == '进行中'

    shown = execute(worker, gateway.show_personal_completion, actor, completed)
    visible = page_script(session.page, r'''JSON.stringify([
        window.fixtureCompletionView.refreshes,
        document.getElementById('matMine').checked,
        document.getElementById('matCompletedToggle').getAttribute('aria-expanded'),
        [...document.querySelectorAll('.is-completed-project input')].map(node => [node.value, node.dataset.status])
    ])''')
    assert shown, visible
    assert json.loads(visible) == [1, True, 'true', [['201', '已完成'], ['202', '已完成']]]
    until(lambda: page_script(session.page, r'''(() => {
        const row = document.querySelector('input[value="202"]').closest('tr').getBoundingClientRect();
        return Math.abs((row.top + row.bottom - innerHeight) / 2) < 40;
    })()'''))
    assert writes(history) == [('/api/complete-material', {'ids': [202]})]


@pytest.mark.parametrize('change', ['editing', 'filter', 'identity', 'revision'])
def test_personal_completion_leaves_an_unavailable_original_view_untouched(site, change):
    session, gateway, worker = open_site(site)
    rows, history = site[4:6]
    rows[0]['matStatus'] = '已完成'
    install_completion_view(session.page)
    snapshot = execute(worker, gateway.snapshot)
    if change == 'editing':
        page_script(session.page, "document.getElementById('editing').focus()")
    elif change == 'filter':
        page_script(session.page, r'''document.getElementById('matList').insertAdjacentHTML(
            'beforebegin', '<input id="matSearch" value="正在核对的匿名项目">')''')
    elif change == 'identity':
        session._identity_changed()
    else:
        rows[0]['lastOperatedAt'] = '2026-11-26T06:00:00Z'

    assert not execute(worker, gateway.show_personal_completion, snapshot.account, snapshot.tasks[0])
    visible = page_script(session.page, r'''JSON.stringify([
        window.fixtureCompletionView.refreshes,
        document.getElementById('matCompletedToggle').getAttribute('aria-expanded')
    ])''')
    assert json.loads(visible) == [0, 'false']
    assert not writes(history)


def test_personal_write_allows_completion_view_expanded_only_by_workbox(site):
    session, gateway, worker = open_site(site)
    rows, history = site[4:6]
    rows[0]['matStatus'] = '已完成'
    install_completion_view(session.page)
    snapshot = execute(worker, gateway.snapshot)
    actor, completed, task = snapshot.account, *snapshot.tasks
    session.set_personal_writes_enabled(True)
    assert execute(worker, gateway.show_personal_completion, actor, completed)
    started = execute(worker, gateway.start_verified, actor, task, personal=True)
    assert started.stage is Stage.WORKING

    view = session.window.findChild(QWebEngineView)
    view.setFocus()
    page_script(session.page, "document.getElementById('matCompletedToggle').focus()")
    for expanded in ('false', 'true'):
        QTest.keyClick(view.focusProxy(), Qt.Key.Key_Space)
        until(lambda: page_script(session.page,
                                  "document.getElementById('matCompletedToggle').getAttribute('aria-expanded')")
              == expanded)
    with pytest.raises(GatewayError, match='原站正在'):
        execute(worker, gateway.complete_verified, actor, started, personal=True)
    assert writes(history) == [('/api/start-material', {'ids': [202]})]


def test_personal_completion_refresh_failure_keeps_verified_delivery_without_repeating_write(site):
    session, gateway, worker = open_site(site)
    rows, history = site[4:6]
    rows[0]['matStatus'] = '进行中'
    install_completion_view(session.page)
    page_script(session.page, 'window.fixtureCompletionView.failRefresh = true')
    snapshot = execute(worker, gateway.snapshot)
    actor, task = snapshot.account, snapshot.tasks[0]
    session.set_personal_writes_enabled(True)
    completed = execute(worker, gateway.complete_verified, actor, task, personal=True)

    assert not execute(worker, gateway.show_personal_completion, actor, completed)
    assert page_script(session.page, "document.getElementById('refreshStatus').dataset.state") == 'error'
    assert execute(worker, gateway.read_submission, actor, completed)
    assert writes(history) == [('/api/complete-material', {'ids': [201]})]


def test_personal_write_stops_when_original_identity_changes_after_reservation(site):
    session, gateway, worker = open_site(site)
    session.set_personal_writes_enabled(True)
    snapshot = execute(worker, gateway.snapshot)

    def swap_at_gate(phase, event):
        if (phase, event) == ('start', 'before_dispatch'):
            session._mark_original_auth_change()

    with pytest.raises(GatewayError) as failure:
        execute(worker, gateway.start_verified, snapshot.account, snapshot.tasks[0], personal=True,
                phase_observer=swap_at_gate)
    assert not isinstance(failure.value, ActionUncertain)
    assert writes(site[5]) == []


def test_personal_write_never_passes_qt_egress_after_local_claim_revoked(site):
    session, gateway, worker = open_site(site)
    session.set_personal_writes_enabled(True)
    snapshot = execute(worker, gateway.snapshot)
    checks = []

    def revoked():
        checks.append(True)
        return False

    with pytest.raises(GatewayError) as failure:
        execute(worker, gateway.start_verified, snapshot.account, snapshot.tasks[0], personal=True, check_local=revoked)
    assert not isinstance(failure.value, ActionUncertain)
    assert checks == [True] and writes(site[5]) == []


def test_qt_original_site_loads_without_legacy_browser_dependency():
    subprocess.run([sys.executable, '-B', '-c',
                    "import sys; import workbox.team_delivery.qt_site; "
                    "assert not any(name.partition('.')[0] == 'playwright' for name in sys.modules)"], check=True)


def test_session_stays_offline_until_open_and_background_reads_only_own_projects(site):
    session, gateway, worker, user, rows, history, _ = site
    assert (session.page, session.window) == (None, None)
    assert execute(worker, gateway.current_account) is None
    assert execute(worker, gateway.poll_snapshot).tasks == ()
    assert history == []
    open_site(site)
    assert session.page.profile().isOffTheRecord()
    first = execute(worker, gateway.poll_snapshot)
    assert first.account.username == 'fixture'
    assert [(item.id, item.stage) for item in first.tasks] == [('201', Stage.READY), ('202', Stage.READY)]
    session.window.close()
    rows[0]['matStatus'] = '进行中'
    assert execute(worker, gateway.poll_snapshot).tasks[0].stage is Stage.WORKING
    assert not session.window.isVisible()
    assert not writes(history)
    user['account'] = 'replacement'
    switched = execute(worker, gateway.poll_snapshot)
    assert switched.account != first.account and switched.tasks == ()


def test_submit_transitions_only_the_verified_task_and_repeat_is_read_only(site):
    _, gateway, worker = open_site(site)
    _, _, _, _, rows, history, _ = site
    snap = execute(worker, gateway.poll_snapshot)
    task = snap.tasks[0]
    phases = []
    receipt = execute(worker, gateway.submit_verified, snap.account, task,
                      phase_observer=lambda *args: phases.append(args))
    assert (receipt.task_id, receipt.account, receipt.submitted_now) == ('201', 'fixture', True)
    assert phases == [('start', 'before_dispatch'), ('start', 'verified'),
                      ('complete', 'before_dispatch'), ('complete', 'verified')]
    assert writes(history) == [('/api/start-material', {'ids': [201]}),
                                ('/api/complete-material', {'ids': [201]})]
    assert rows[1]['matStatus'] == rows[2]['matStatus'] == '待制作'
    assert execute(worker, gateway.read_submission, snap.account, task)
    assert not execute(worker, gateway.submit_verified, snap.account, task).submitted_now
    assert len(writes(history)) == 2


@pytest.mark.parametrize('script', ["document.querySelector('.mat-check').checked=true",
                                   "document.getElementById('active').showModal()",
                                   "document.getElementById('editing').focus()"])
def test_write_yields_to_existing_page_interaction(site, script):
    session, gateway, worker = open_site(site)
    snap = execute(worker, gateway.poll_snapshot)
    page_script(session.page, script)
    with pytest.raises(GatewayError):
        execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
    assert not writes(site[5])
    assert execute(worker, gateway.poll_snapshot) == snap


def test_material_filters_and_visible_role_dialog_only_block_automatic_writes(site):
    session, gateway, worker = open_site(site)
    snap = execute(worker, gateway.poll_snapshot)
    examples = (
        '<section role="dialog">visible custom dialog</section>',
        '<section id="matReminders"><button data-stage-reminder="overdue" aria-pressed="true">Overdue</button></section>',
        '<input id="matRemark" value="remark">',
        '<input id="matCreatedDate" type="date" value="2026-11-25">',
        '<input id="matOperationDate" type="date" value="2026-11-25">',
        '<details id="matFilter"><input type="checkbox" checked></details>',
        '<details id="matType" open><summary>Types</summary></details>',
        '<button id="matBatchFocus">Batch focus</button>',
        '<button id="matCompletedToggle" aria-expanded="true">Show completed</button>',
        '<input id="matMine" type="checkbox">',
    )
    for html in examples:
        page_script(session.page, f'''(() => {{
            document.getElementById('filter-probe')?.remove();
            const probe = document.createElement('div'); probe.id = 'filter-probe';
            probe.innerHTML = {json.dumps(html)}; document.body.append(probe);
        }})()''')
        with pytest.raises(GatewayError, match='原站正在'):
            execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
        assert not writes(site[5]), html
        assert execute(worker, gateway.poll_snapshot) == snap


def test_unknown_write_response_only_allows_readback_without_resend(site):
    _, gateway, worker = open_site(site)
    _, _, _, _, rows, history, failures = site
    rows[0]['matStatus'] = '进行中'
    snap = execute(worker, gateway.poll_snapshot)
    failures.update(('retain', 'drop-reply'))
    stages = []
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.complete_verified, snap.account, snap.tasks[0],
                phase_observer=lambda *args: stages.append(args))
    assert stages == [('complete', 'before_dispatch'), ('complete', 'uncertain')]
    assert execute(worker, gateway.read_material_task, snap.account, snap.tasks[0]).stage is Stage.WORKING
    assert not execute(worker, gateway.read_submission, snap.account, snap.tasks[0])
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.complete_verified, snap.account, snap.tasks[0])
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
    assert writes(history) == [('/api/complete-material', {'ids': [201]})]


def test_verified_working_after_lost_start_receipt_allows_one_complete_but_no_start_replay(site):
    _, gateway, worker = open_site(site)
    _, _, _, _, _, history, failures = site
    snap = execute(worker, gateway.poll_snapshot)
    task, phases = snap.tasks[0], []

    def observe(*args):
        phases.append(args)

    failures.add('drop-reply')
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.start_verified, snap.account, task, phase_observer=observe)
    working = execute(worker, gateway.read_material_task, snap.account, task)
    assert working.stage is Stage.WORKING
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.start_verified, snap.account, task, phase_observer=observe)
    failures.discard('drop-reply')
    done = execute(worker, gateway.complete_verified, snap.account, working, phase_observer=observe)
    assert done.stage is Stage.COMPLETED
    assert phases == [('start', 'before_dispatch'), ('start', 'uncertain'),
                      ('complete', 'before_dispatch'), ('complete', 'verified')]
    assert writes(history) == [('/api/start-material', {'ids': [201]}),
                                ('/api/complete-material', {'ids': [201]})]


def test_personal_recovery_from_lost_start_rechecks_claim_at_qt_egress(site, tmp_path):
    from workbox.team_delivery.office_handoff import HandoffLog
    from workbox.team_delivery.site_attempt import submit_personal_site

    session, gateway, worker = open_site(site)
    session.set_personal_writes_enabled(True)
    snapshot = execute(worker, gateway.snapshot)
    task = snapshot.tasks[0]
    journal = HandoffLog(tmp_path / 'attempts.sqlite')

    def deliver(**options):
        return execute(worker, submit_personal_site, gateway, snapshot.account, task, '12', journal,
                       lambda: None, **options)

    failures = site[6]
    failures.add('drop-reply')
    assert deliver(check_local=lambda: True) is None
    assert execute(worker, gateway.read_material_task, snapshot.account, task).stage is Stage.WORKING
    failures.remove('drop-reply')
    checks = iter((True, False, False))
    with pytest.raises(GatewayError, match='本地交付已变化'):
        deliver(verify_only=True, resume_working=True, check_local=lambda: next(checks))
    assert writes(site[5]) == [('/api/start-material', {'ids': [201]})]

    done = deliver(verify_only=True, resume_working=True, check_local=lambda: True)
    assert done is not None and done.stage is Stage.COMPLETED
    assert deliver(verify_only=True) == done
    assert writes(site[5]) == [('/api/start-material', {'ids': [201]}),
                               ('/api/complete-material', {'ids': [201]})]


@pytest.mark.parametrize('response', ['proxy-html', 'rejected-status', 'rejected-json', 'drop-reply'])
def test_completion_seen_after_invalid_post_acknowledgement_is_only_readback(site, response):
    _, gateway, worker = open_site(site)
    _, _, _, _, rows, history, failures = site
    rows[0]['matStatus'] = '进行中'
    snap = execute(worker, gateway.poll_snapshot)
    failures.add(response)
    with pytest.raises(ActionUncertain):
        execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
    assert execute(worker, gateway.read_submission, snap.account, snap.tasks[0])
    assert not execute(worker, gateway.submit_verified, snap.account, snap.tasks[0]).submitted_now
    assert writes(history) == [('/api/complete-material', {'ids': [201]})]


@pytest.mark.parametrize('retain', [True, False])
def test_valid_success_json_without_explicit_ok_still_requires_state_readback(site, retain):
    _, gateway, worker = open_site(site)
    _, _, _, _, rows, history, failures = site
    rows[0]['matStatus'] = '进行中'
    snap = execute(worker, gateway.poll_snapshot)
    failures.add('empty-json')
    if retain:
        failures.add('retain')
        with pytest.raises(ActionUncertain):
            execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
    else:
        assert execute(worker, gateway.submit_verified, snap.account, snap.tasks[0]).submitted_now
    assert execute(worker, gateway.read_submission, snap.account, snap.tasks[0]) is not retain
    assert writes(history) == [('/api/complete-material', {'ids': [201]})]


def write_params(gateway, worker):
    snap = execute(worker, gateway.poll_snapshot)
    row = execute(worker, gateway._request_target, snap.account, snap.tasks[0], 'target')
    return {**row, 'account': gateway._observation}, gateway._epoch


def test_expired_gui_queue_never_dispatches_and_is_known_unsent(site, monkeypatch):
    from workbox.team_delivery import qt_site

    session, gateway, worker = open_site(site)
    params, epoch = write_params(gateway, worker)
    monkeypatch.setattr(qt_site, '_TIMEOUT', .02)
    # Keep the GUI busy until the worker expires. The queued slot must not send later.
    result = worker.submit(session.request, 'start-material', params, expected_epoch=epoch)
    with pytest.raises(GatewayError) as failure:
        result.result(timeout=3)
    assert not isinstance(failure.value, ActionUncertain)
    QTest.qWait(200)
    assert not writes(site[5])


def test_close_releases_worker_before_join_and_cancels_late_authentication_read(site):
    session, gateway, worker = open_site(site)
    params, epoch = write_params(gateway, worker)
    history, failures = site[5:]
    failures.add('hold-state')
    baseline = len([item for item in history if item[1] == '/api/state'])
    result = worker.submit(session.request, 'start-material', params, expected_epoch=epoch)
    until(lambda: len([item for item in history if item[1] == '/api/state']) > baseline)
    session.close()
    failures.discard('hold-state')
    with pytest.raises(ActionUncertain):
        result.result(timeout=1)
    QTest.qWait(250)
    assert not writes(history)


def test_same_display_after_auth_epoch_change_cannot_reuse_previous_permission(site):
    session, gateway, worker = open_site(site)
    params, epoch = write_params(gateway, worker)
    session._identity_changed()
    with pytest.raises(GatewayError, match='登录已变化') as failure:
        execute(worker, session.request, 'start-material', params, expected_epoch=epoch)
    assert not isinstance(failure.value, ActionUncertain)
    assert not writes(site[5])


def test_new_native_operation_date_invalidates_saved_task_even_when_stage_is_unchanged(site):
    _, gateway, worker = open_site(site)
    snap = execute(worker, gateway.poll_snapshot)
    site[4][0]['lastOperatedAt'] = '2026-11-25T05:00:00Z'
    with pytest.raises(GatewayError, match='发生变化'):
        execute(worker, gateway.submit_verified, snap.account, snap.tasks[0])
    assert not writes(site[5])


def test_explicit_reveal_can_open_native_material_view_and_page_to_exact_id(site):
    session, gateway, worker = open_site(site)
    snap = execute(worker, gateway.poll_snapshot)
    page_script(session.page, r'''(() => {
        const list = document.getElementById('matList');
        list.hidden = true;
        const nav = document.createElement('button');
        nav.dataset.page = 'material';
        nav.textContent = '打开材料清单';
        nav.onclick = () => { list.hidden = false; };
        document.body.append(nav);
        const render = page => list.innerHTML =
            `<table><tr><td><input type="checkbox" class="mat-check" value="${page === 1 ? 202 : 201}"
                    data-status="待制作"></td><td><span class="project-name">同名匿名件</span></td>
                    <td></td><td></td><td>待制作</td><td></td><td>Alias</td></tr></table>` +
            `<div id="matPager">第 ${page}/2 页 <button data-action="page" data-kind="mat">${page === 1 ? '下一页' : '上一页'}</button></div>`;
        list.addEventListener('click', event => {
            if (event.target.dataset.action === 'page') render(2);
        });
        render(1);
    })()''')
    session.window.close()
    execute(worker, gateway.reveal_task, snap.account, snap.tasks[0])
    assert session.window.isVisible()
    assert page_script(session.page, "document.querySelector('input.mat-check').value") == '201'
    assert not writes(site[5])


class _EgressProbe:
    """Expose Qt transport decisions without requiring the controller's separate guard module."""

    def __init__(self):
        self.confirmed = False
        self.issued = False
        self.dispatched = False
        self.denied = []

    def invalidate(self):
        self.confirmed = False

    def write_token(self):
        if not self.confirmed:
            raise GatewayError('登录已变化')
        self.issued = True
        return 'anonymous-once'

    def accept_request(self, request):
        tokens = [bytes(value) for name, value in request.httpHeaders().items()
                  if bytes(name).lower() == b'x-workbox-auto']
        if not tokens:
            return True
        if not self.confirmed:
            self.denied.extend(tokens)
            return False
        self.dispatched = True
        self.confirmed = False
        return True

    def finish_write(self, token):
        assert token == 'anonymous-once'
        return self.dispatched


def test_other_account_invalidated_during_preflight_is_blocked_at_real_qt_egress(site):
    session, gateway, worker, _, _, history, failures = site
    guard = _EgressProbe()
    session._pair_guard = guard
    open_site(site)
    snap = execute(worker, gateway.poll_snapshot)
    phases, baseline = [], []

    def observe(phase, event):
        phases.append((phase, event))
        if event == 'before_dispatch':
            baseline.append(sum(path == '/api/state' for _, path, _ in history))
            failures.add('hold-state')

    guard.confirmed = True
    result = worker.submit(gateway.start_verified, snap.account, snap.tasks[0], phase_observer=observe)
    try:
        until(lambda: guard.issued and sum(path == '/api/state' for _, path, _ in history) > baseline[0])
        guard.invalidate()  # The office side has switched while the original-site read is still in flight.
    finally:
        failures.discard('hold-state')
    until(result.done)
    with pytest.raises(GatewayError, match='未发送') as failure:
        result.result()
    assert not isinstance(failure.value, ActionUncertain)
    assert phases == [('start', 'before_dispatch'), ('start', 'not_sent')]
    assert guard.denied == [b'anonymous-once']
    assert writes(history) == []
    page_script(session.page, "fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); true")
    until(lambda: writes(history))
    assert writes(history) == [('/api/logout', {})]
