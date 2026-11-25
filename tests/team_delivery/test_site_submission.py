from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from workbox.team_delivery.contracts import Account, ActionUncertain, GatewayError, Stage, Task
from workbox.team_delivery.live_site import LiveBrowserGateway


_PAGE = '''<!doctype html><meta charset="utf-8">
<div id="who" aria-label="当前账号">匿名材料 · 材料</div>
<label><input id="matMine" type="checkbox" checked>只看我的</label>
<input id="matSearch">
<div id="matList"></div>
<div id="matPager">第 1/1 页
<button data-action="page" data-kind="mat" disabled>上一页</button>
<button data-action="page" data-kind="mat" disabled>下一页</button></div>
<dialog id="actionDialog"><h2 id="actionTitle"></h2><p id="actionText"></p>
  <div id="actionField"></div><button id="cancelAction">取消</button>
  <button id="confirmAction">确认提交</button></dialog>
<script>
const list = document.getElementById('matList');
const popup = document.getElementById('actionDialog');
let me = {account: 'fixture-material', name: '匿名材料', role: '材料', mustChange: false};
let state = {projects: []};
let tamper = false;
let switchInDialog = false;
const refresh = async () => {
    const {projects} = await (await fetch('/api/state')).json();
    state.projects = projects;
    list.innerHTML = `<div class="batch-card"><div class="batch-actions">
        <button disabled data-action="batch-task" data-stage="mat" data-operation="start-material">开始制作</button>
        <button disabled data-action="batch-task" data-stage="mat" data-operation="complete-material">提交材料</button>
        </div><table><thead><tr><th><input type="checkbox" data-select-batch="mat"></th></tr></thead><tbody>` + projects.map(p => `
        <tr><td><input class="row-check mat-check" type="checkbox" value="${p.id}" data-status="${p.matStatus}"></td>
        <td><span class="project-name" title="${p.name}">${p.name}</span></td><td></td><td></td>
        <td>${p.matStatus}</td><td></td><td><span>${p.matOwnerName}</span></td><td></td><td></td><td></td><td></td></tr>
        `).join('') + '</tbody></table></div>';
};
const selection = () => [...list.querySelectorAll('.mat-check:checked')];
list.addEventListener('change', () => {
    list.querySelector('[data-select-batch]').checked = selection().length === 2;
    list.querySelector('[data-operation="start-material"]').disabled =
        !selection().some(box => ['待制作','预审退回'].includes(box.dataset.status));
    list.querySelector('[data-operation="complete-material"]').disabled =
        !selection().some(box => box.dataset.status === '进行中');
});
const write = async (operation, ids) => {
    try {
        const response = await fetch('/api/' + operation, {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: tamper ? [...ids, 202] : ids})});
        if (response.ok) await refresh();
    } catch (error) {}
};
list.addEventListener('click', event => {
    const operation = event.target.dataset.operation;
    const ids = selection().map(box => Number(box.value));
    if (operation === 'start-material') write(operation, ids);
    if (operation === 'complete-material') {
        document.getElementById('actionTitle').textContent = '提交材料';
        document.getElementById('actionText').textContent = `确认处理批次「匿名批次」中所选的 ${ids.length} 个项目？提交后，项目将进入代码待领取清单。`;
        document.getElementById('confirmAction').onclick = () => {
            popup.close();
            write(operation, ids);
        };
        popup.showModal();
        if (switchInDialog) document.getElementById('who').textContent = '未登录';
    }
});
document.getElementById('cancelAction').onclick = () => popup.close();
refresh();
</script>'''


@pytest.fixture
def anonymous_site():
    user = {'account': 'fixture-material', 'name': '匿名材料', 'role': '材料', 'mustChange': False}
    projects = [{'id': identifier, 'name': '同名匿名项目', 'matOwner': user['account'],
                 'matOwnerName': user['name'], 'matStatus': '待制作'} for identifier in (201, 202)]
    posts = []
    failures = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, body, *, status=200, media='application/json'):
            raw = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', media + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/':
                self._reply(_PAGE, media='text/html')
            elif self.path == '/api/me':
                self._reply(json.dumps({'user': user}))
            elif self.path == '/api/state':
                self._reply(json.dumps({'projects': projects}))
            else:
                self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            posts.append((self.path, body))
            expected, changed = {'/api/start-material': ('待制作', '进行中'),
                                 '/api/complete-material': ('进行中', '已完成')}[self.path]
            for row in projects:
                if row['id'] in body['ids']:
                    assert row['matStatus'] == expected
                    if 'retain' not in failures:
                        row['matStatus'] = changed
            if 'lose-reply' in failures:
                self.close_connection = True
                return
            self._reply('{"ok":true}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = LiveBrowserGateway(f'http://127.0.0.1:{server.server_port}')
    try:
        yield browser, user, projects, posts, failures
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join()


def _open(anonymous_site, stage):
    browser, user, projects, _, _ = anonymous_site
    projects[0]['matStatus'] = {Stage.READY: '待制作', Stage.WORKING: '进行中', Stage.COMPLETED: '已完成'}[stage]
    browser.open_login()
    browser._page.locator('input.mat-check[value="201"]').wait_for()
    account = browser.current_account()
    if not getattr(account, 'username', ''):
        # The integration branch supplies this field after validating the site's login identity.
        @dataclass(frozen=True, slots=True)
        class IdentifiedAccount(Account):
            username: str = ''

        account = IdentifiedAccount(account.id, account.name, account.role, account.session, user['account'])
        browser._account = account
    return browser, account, Task('201', '同名匿名项目', account.id, stage, 'observed-revision')


def test_only_exact_same_name_row_uses_native_start_submit_and_readback(anonymous_site):
    from workbox.team_delivery.site_submission import submit_via_visible_site

    browser, account, task = _open(anonymous_site, Stage.READY)
    receipt = submit_via_visible_site(browser, account, task)
    _, user, projects, posts, _ = anonymous_site
    assert (receipt.task_id, receipt.account, receipt.submitted_now) == ('201', user['account'], True)
    assert posts == [('/api/start-material', {'ids': [201]}), ('/api/complete-material', {'ids': [201]})]
    assert [row['matStatus'] for row in projects] == ['已完成', '待制作']
    assert submit_via_visible_site(browser, account, task).submitted_now is False
    assert len(posts) == 2


@pytest.mark.parametrize('condition', [
    'selection', 'batch-selection', 'editing', 'same-display-account-changed', 'wrong-owner', 'changed-title',
])
def test_refuses_live_scope_and_task_changes(anonymous_site, condition):
    from workbox.team_delivery.site_submission import submit_via_visible_site

    browser, account, task = _open(anonymous_site, Stage.WORKING)
    _, user, projects, posts, _ = anonymous_site
    if condition == 'selection':
        browser._page.locator('.mat-check[value="202"]').check()
    elif condition == 'batch-selection':
        browser._page.locator('[data-select-batch="mat"]').evaluate('(node) => node.checked = true')
    elif condition == 'editing':
        browser._page.locator('#matSearch').focus()
    elif condition == 'same-display-account-changed':
        user['account'] = 'same-name-different-account'
        projects[0]['matOwner'] = user['account']
    elif condition == 'wrong-owner':
        projects[0]['matOwner'] = 'same-name-different-account'
    elif condition == 'changed-title':
        projects[0]['name'] += '更名'
    with pytest.raises(GatewayError):
        submit_via_visible_site(browser, account, task)
    assert posts == []


@pytest.mark.parametrize('condition', ['wrong-payload', 'logout-in-dialog', 'lost-unchanged'])
def test_never_reports_or_repeats_an_unconfirmed_write(anonymous_site, condition, monkeypatch):
    from workbox.team_delivery import site_submission

    monkeypatch.setattr(site_submission, '_TIMEOUT_MS', 1500)
    browser, account, task = _open(anonymous_site, Stage.WORKING)
    _, _, projects, posts, failures = anonymous_site
    if condition == 'wrong-payload':
        browser._page.evaluate('tamper = true')
    elif condition == 'logout-in-dialog':
        browser._page.evaluate('switchInDialog = true')
    else:
        failures.update({'retain', 'lose-reply'})
    with pytest.raises(GatewayError):
        site_submission.submit_via_visible_site(browser, account, task)
    expected = [('/api/complete-material', {'ids': [201]})] if failures else []
    assert posts == expected and projects[0]['matStatus'] == '进行中'
    if failures:
        with pytest.raises(ActionUncertain):
            site_submission.submit_via_visible_site(browser, account, task)
        assert posts == expected


def test_readback_uses_latest_exact_identity_without_touching_browser_controls(anonymous_site):
    from workbox.team_delivery.site_submission import read_site_submission

    browser, account, task = _open(anonymous_site, Stage.WORKING)
    _, _, projects, posts, _ = anonymous_site
    browser._page.locator('#matSearch').fill('员工正在筛选')
    assert read_site_submission(browser, account, task) is False
    projects[0]['matStatus'] = '已完成'
    assert read_site_submission(browser, account, task) is True
    assert browser._page.locator('#matSearch').input_value() == '员工正在筛选'
    assert posts == []
    projects[0]['name'] += '改名'
    with pytest.raises(GatewayError):
        read_site_submission(browser, account, task)
