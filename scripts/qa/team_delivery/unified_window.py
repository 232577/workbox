"""Exercise the native WorkBox project window and its embedded team handoff."""

from hashlib import file_digest
import json
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from urllib.parse import urlsplit

from original_frontend import original_frontend
from workbox.team_delivery.contracts import Outcome
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl, qVersion
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QComboBox, QDialogButtonBox, QFrame, QInputDialog, QLabel, QLineEdit, QTableWidget,
)
from shiboken6 import isValid

from installed_server import assert_closed
from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService
from workbox.desktop.project_change_ui import ProjectChangeDialog
from workbox.desktop.theme import install_theme
from workbox.team_delivery import application as team_application
from workbox.team_delivery.configuration import save_shared_root


LEFT = Qt.MouseButton.LeftButton
TASK_ID = 55
OTHER_ID = 58
PRIMARY = 'fixture-material'
ALTERNATE = 'fixture-material-b'
COMPLETE = '/api/complete-material'
START = '/api/start-material'


def origin(url):
    parsed = urlsplit(url)
    return f'{parsed.scheme}://{parsed.netloc}'


def wait_for(condition, reason, timeout=25):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return
        QTest.qWait(20)
    raise AssertionError(reason)


def interact_with_dialog(app, dialog_type, action, interact):
    handled = []
    deadline = monotonic() + 25
    timer = QTimer()

    def tick():
        dialog = app.activeModalWidget()
        if isinstance(dialog, dialog_type) and dialog.isVisible():
            timer.stop()
            try:
                interact(dialog)
            except Exception as error:
                handled.append(error)
                dialog.reject()
            else:
                handled.append(None)
        elif monotonic() >= deadline:
            timer.stop()
            handled.append(AssertionError(f'未出现 {dialog_type.__name__} 对话框。'))
            if dialog is not None:
                dialog.close()

    timer.timeout.connect(tick)
    timer.start(25)
    try:
        action()
        wait_for(lambda: handled, f'未处理 {dialog_type.__name__} 对话框。', timeout=26)
        if handled[0] is not None:
            raise handled[0]
    finally:
        timer.stop()


def click(control):
    assert control is not None and control.isVisible() and control.isEnabled()
    QTest.mouseClick(control, LEFT)


def shot(widget, output, name):
    QTest.qWait(250)
    assert widget.grab().save(str(output / name))


def web_read(view, script):
    replies = []
    view.page().runJavaScript(script, 0, replies.append)
    wait_for(lambda: bool(replies), 'Qt 网页未回应。', timeout=8)
    return replies[0]


def web_target(view, selector):
    script = """selector => {
        const visible = node => node.getClientRects().length && !node.disabled &&
            getComputedStyle(node).visibility === 'visible';
        for (const element of document.querySelectorAll(selector)) {
            if (!visible(element)) continue;
            element.scrollIntoView({block: 'center', inline: 'nearest'});
            const rect = element.getBoundingClientRect();
            const x = Math.floor(rect.x + rect.width / 2), y = Math.floor(rect.y + rect.height / 2);
            if (element.contains(document.elementFromPoint(x, y))) return JSON.stringify([x, y]);
        }
        return '';
    }"""
    expression = f'({script})({json.dumps(selector)})'
    found = []

    def locate():
        coordinates = web_read(view, expression)
        if coordinates:
            found[:] = json.loads(coordinates)
        return bool(found)

    wait_for(locate, f'Qt 网页上没有可点击控件 {selector}。')
    target = view.focusProxy() or view
    point = QPoint(*found)
    assert target.rect().contains(point), (selector, found, target.rect())
    QTest.mouseClick(target, LEFT, Qt.KeyboardModifier.NoModifier, point)
    return target


def web_login(view, username, password, *, account_field, password_field, submit_button):
    for selector, value in ((account_field, username), (password_field, password)):
        assert value.isascii()
        target = web_target(view, selector)
        field = json.dumps(selector)
        wait_for(lambda: web_read(view, f'document.activeElement?.matches({field})') is True,
                 f'Qt 网页输入框尚未获得焦点 {selector}。')
        QTest.keyClick(target, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(target, value)
        assert web_read(view, f'document.querySelector({field})?.value.length') == len(value), selector
    web_target(view, submit_button)


def find_site_view(site):
    for widget in QApplication.allWidgets():
        if isinstance(widget, QWebEngineView) and widget.isVisible() and origin(widget.url().toString()) == site:
            return widget
    return None


def site_login(view, credentials):
    web_login(view, credentials['account'], credentials['password'],
              account_field='#loginForm #account', password_field='#loginForm #password',
              submit_button='#loginForm button[type=submit]')
    caption = f"{credentials['name']} · {credentials['role']}"
    wait_for(lambda: web_read(view, "document.getElementById('who')?.innerText.trim()") == caption,
             '原站前端未显示登录后的匿名账号。')
    material = 'button[data-page=material]'
    if not web_read(view, f"document.querySelector('{material}')?.classList.contains('active')"):
        web_target(view, material)
    checkbox = "document.getElementById('matMine')?.checked"
    if web_read(view, checkbox) is True:
        web_target(view, 'label:has(#matMine)')
        wait_for(lambda: web_read(view, checkbox) is False, '原站“只看我的”控件未响应关闭。')
    web_target(view, 'label:has(#matMine)')
    wait_for(lambda: web_read(view, checkbox) is True,
             '原站 Qt 窗口未切换到“只看我的”。')


def site_logout(view):
    web_target(view, '#logout')
    wait_for(lambda: web_read(view, "!!document.querySelector('#loginForm #password')?.getClientRects().length"),
             '原站前端未显示登出后的登录表单。')


def drop_word(window, path):
    target = window.findChild(QFrame, 'dropArea')
    assert target is not None and target.isVisible()
    point = target.mapTo(window, target.rect().center())
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path.resolve(strict=True)))])
    arguments = (Qt.DropAction.CopyAction, mime, LEFT, Qt.KeyboardModifier.NoModifier)
    enter = QDragEnterEvent(point, *arguments)
    drop = QDropEvent(QPointF(point), *arguments)
    QApplication.sendEvent(window, enter)
    assert enter.isAccepted(), '收件区未接受本地 Word 的拖入。'
    QApplication.sendEvent(window, drop)
    assert drop.isAccepted(), '收件区未接受本地 Word 的放下。'


def visible_tasks(window):
    table = window.findChild(QTableWidget, 'tasks')
    assert table is not None and table.isVisible()
    return {int(table.item(row, 1).text().lstrip('#')): table.item(row, 0).text()
            for row in range(table.rowCount())}


def wait_for_tasks(window, expected, *, timeout=35):
    wait_for(lambda: not window.busy and set(visible_tasks(window)) == set(expected),
             f'本人待办未更新为 {expected}。', timeout=timeout)
    return visible_tasks(window)


def refresh_tasks(window, expected):
    buttons = [b for b in window.findChildren(QAbstractButton)
               if b.isVisible() and (b.objectName() == 'refreshTasks' or b.text() == '刷新任务')]
    refresh, = buttons
    wait_for(lambda: refresh.isEnabled() and not window.busy, '刷新任务按钮未就绪。')
    click(refresh)
    return wait_for_tasks(window, expected)


def select_task(dialog, task_id):
    combo = dialog.findChild(QComboBox)
    assert combo is not None
    choices = [combo.itemText(index) for index in range(combo.count())]
    target, = [index for index, value in enumerate(choices) if value.endswith(f'#{task_id}')]
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Home)
    for _ in range(target):
        QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == target, choices
    click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok))


def original_write_attempts(fixture):
    return [event for event in fixture.events
            if event['method'] == 'POST' and event['path'] not in ('/api/login', '/api/logout')]


def verify_background_and_ambiguity(app, window, fixture, path, title, expect_uncommitted, results, output):
    initial = refresh_tasks(window, [TASK_ID])
    fixture.add_project(56, '匿名后台新增', owner=PRIMARY)
    fixture.add_project(57, '匿名后台改派', owner=ALTERNATE)
    added = wait_for_tasks(window, [TASK_ID, 56])
    shot(window, output, 'unified-queue-added.png')
    fixture.reassign_project(56, ALTERNATE)
    fixture.reassign_project(57, PRIMARY)
    reassigned = wait_for_tasks(window, [TASK_ID, 57])
    shot(window, output, 'unified-queue-reassigned.png')
    fixture.reassign_project(57, ALTERNATE)
    fixture.add_project(OTHER_ID, title, owner=PRIMARY)
    duplicates = refresh_tasks(window, [TASK_ID, OTHER_ID])
    assert set(duplicates.values()) == {title}

    def cancel(dialog):
        combo = dialog.findChild(QComboBox)
        choices = {combo.itemText(index) for index in range(combo.count())}
        assert {f'{title} · #{number}' for number in duplicates} <= choices, choices
        shot(dialog, output, 'unified-same-name-choice.png')
        click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel))

    interact_with_dialog(app, QInputDialog, lambda: drop_word(window, path), cancel)
    wait_for(lambda: not window.busy, '取消同名项目后收件区没有恢复。')
    assert not original_write_attempts(fixture)
    expect_uncommitted()

    def select_stale(dialog):
        fixture.reassign_project(TASK_ID, ALTERNATE)
        select_task(dialog, TASK_ID)

    prior = len(results)
    interact_with_dialog(app, QInputDialog, lambda: drop_word(window, path), select_stale)
    wait_for(lambda: len(results) >= prior + 2 and not window.busy, '编号选择期间改派后未返回交件结果。')
    stale_outcome = results[-1].outcome
    assert stale_outcome != Outcome.SUBMITTED and results[-1].receipt_id is None, results[-1]
    assert not original_write_attempts(fixture)
    expect_uncommitted()
    shot(window, output, 'unified-stale-task-blocked.png')
    fixture.reassign_project(OTHER_ID, ALTERNATE)
    fixture.reassign_project(TASK_ID, PRIMARY)
    refresh_tasks(window, [TASK_ID])
    return {'initial': initial, 'added_in_background': added, 'reassigned_in_background': reassigned, 'duplicate_title': duplicates,
            'cancelled_without_write': True, 'stale_task_id': TASK_ID, 'stale_outcome': stale_outcome.value}


def verify_timeout_readback(fixture):
    events = fixture.events
    write_index, = [index for index, event in enumerate(events)
                    if event.get('method') == 'POST' and event.get('path') == COMPLETE
                    and event.get('response') == 'timeout-after-commit']
    reads = [event for event in events[write_index + 1:]
             if event.get('method') == 'GET' and event.get('path') == '/api/state' and event.get('account') == PRIMARY
             and any(row['id'] == TASK_ID and row['matOwner'] == PRIMARY and row['matStatus'] == '已完成'
                     for row in event.get('projects', []))]
    assert reads, '未记录到写请求超时后，同一原站账号对同一编号的完成状态回读。'
    origin_write = events[write_index]
    return {'withheld_response_event': origin_write, 'matching_state_reads_after_commit': len(reads),
            'first_matching_read_after_seconds': round(reads[0]['elapsed_seconds'] - origin_write['elapsed_seconds'], 3)}


def verify_personal_projects(service, controller, title, manual, output):
    app = QApplication.instance()
    panel = controller.panel
    names = [title, '匿名资料系统2', '匿名资料.V2系统', *(f'匿名资料系统{i}' for i in range(4, 9))]
    receipt = service.receive_text('\n'.join(f'{i}. {name}' for i, name in enumerate(names, 1)))
    project_ids = receipt['project_ids']
    assert len(project_ids) == 8
    service.receive_text('匿名资料批次外系统')
    managed = Path(service.receive_path(manual, project_ids[0], manual=True)['file']['path'])
    assert managed.is_relative_to(Path(service.workspaces.settings()['work_root']))
    assert managed != manual and managed.read_bytes() == manual.read_bytes()

    controller.show_panel()
    panel.resize(960, 520)
    controller.refresh()
    batch_value = f"receipt:{receipt['receipt_id']}"
    batch_index = panel.batch_filter.findData(batch_value)
    assert batch_index > 0
    panel.search.setText('匿名')
    panel.batch_filter.setCurrentIndex(batch_index)
    view = panel.project_list

    def visible_ids():
        return {view.item(i).data(Qt.UserRole) for i in range(view.count())}

    def selected_ids():
        return {item.data(Qt.UserRole) for item in view.selectedItems()}

    def row_click(row, modifiers=Qt.NoModifier):
        point = view.visualItemRect(view.item(row)).center()
        assert view.viewport().rect().contains(point)
        QTest.mouseClick(view.viewport(), LEFT, modifiers, point)

    wait_for(lambda: visible_ids() == set(project_ids), '未定位到本次个人批次的八个项目。')
    QTest.qWait(100)
    scrollbar = view.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    visible_rows = [row for row in range(view.count())
                    if view.viewport().rect().contains(view.visualItemRect(view.item(row)))]
    assert len(visible_rows) >= 3
    first, middle, last = visible_rows[:3]
    ids = [view.item(row).data(Qt.UserRole) for row in (first, middle, last)]

    def viewport_state():
        QApplication.processEvents()
        return (panel.size(), panel.project_splitter.sizes(), panel.document_side.geometry(),
                view.geometry(), view.viewport().geometry(), scrollbar.value(), view.visualItemRect(view.item(first)))

    row_click(first)
    QTest.qWait(100)
    before_selection = viewport_state()
    assert panel.document_side.isVisible() and all(panel.project_splitter.sizes()) and scrollbar.value() > 0
    assert not panel.bulk_bar.isVisible()
    row_click(last, Qt.ShiftModifier)
    assert selected_ids() == set(ids)
    assert viewport_state() == before_selection
    row_click(middle, Qt.ControlModifier)
    assert selected_ids() == {ids[0], ids[2]}
    assert panel.bulk_tools_button.isVisible() and not panel.bulk_bar.isVisible()
    assert viewport_state() == before_selection

    click(panel.bulk_tools_button)
    wait_for(panel.bulk_bar.isVisible, '批量操作弹窗没有显示。')
    assert panel.bulk_bar.parentWidget() is panel and panel.bulk_bar.windowType() == Qt.Popup
    assert panel.selection_count.text() == '已选 2 项' and viewport_state() == before_selection
    click(panel.bulk_select_all_button)
    assert selected_ids() == set(project_ids) and viewport_state() == before_selection
    shot(panel, output, 'unified-project-bulk-selection.png')
    shot(panel.bulk_bar, output, 'unified-project-bulk-popup.png')
    QTest.keyClick(panel.bulk_bar, Qt.Key_Escape)
    assert not panel.bulk_bar.isVisible() and not panel.bulk_tools_button.isChecked()
    assert selected_ids() == set(project_ids) and viewport_state() == before_selection

    row_click(first)
    QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
    assert selected_ids() == set(project_ids) and viewport_state() == before_selection
    click(panel.bulk_tools_button)
    click(panel.clear_selection_button)
    assert not selected_ids() and not panel.bulk_bar.isVisible() and panel.select_all_button.isVisible()
    assert viewport_state() == before_selection
    click(panel.select_all_button)
    assert selected_ids() == set(project_ids) and viewport_state() == before_selection
    click(panel.bulk_tools_button)
    click(panel.clear_selection_button)
    assert not selected_ids() and not panel.bulk_bar.isVisible()

    panel.resize(660, 480)
    rename_id = project_ids[2]
    before = service.get_project(rename_id)
    old_workspace = Path(before['workspace']['path'])
    archive_marker = '批次保留校验.txt'
    marker_bytes = b'Anonymous existing work file'
    (old_workspace / archive_marker).write_bytes(marker_bytes)
    new_name = names[2].replace('.', '')

    def rename(dialog):
        dialog.name_edit.setText(new_name)
        click(dialog.check_button)
        wait_for(dialog.confirm_button.isEnabled, '个人项目改名预览未准备好。')
        assert dialog.plan['kind'] == 'rename'
        click(dialog.confirm_button)

    assert panel.focus_visible_project(rename_id)
    rename_menu = panel.project_context_menu(rename_id)
    rename_action, = [action for action in rename_menu.actions() if action.text() == '改项目名…']
    interact_with_dialog(app, ProjectChangeDialog, rename_action.trigger, rename)
    wait_for(lambda: not controller.busy, '项目改名未保存。')
    after = service.get_project(rename_id)
    assert after['name'] == new_name and after['batch_id'] == before['batch_id']
    workspace = Path(after['workspace']['path'])
    assert workspace.parent == old_workspace.parent and (workspace / archive_marker).read_bytes() == marker_bytes
    assert panel.batch_filter.currentData() == batch_value and panel.search.text() == '匿名'
    assert visible_ids() == set(project_ids) and view.isVisible()
    assert panel.current_project['id'] == rename_id and panel.current_project['name'] == new_name
    shot(panel, output, 'unified-project-renamed-batch.png')

    panel.resize(1050, 750)
    return managed, project_ids[0], {'batch_members_after_rename':len(project_ids), 'renamed_project_id':rename_id,
                                      'batch_filter_retained':True, 'mouse_and_keyboard_bulk_selection':True,
                                      'splitter_geometry_retained':True, 'bulk_popup_verified':True,
                                      'scrolled_viewport_retained':True, 'renamed_workspace_file_retained':True}


def unified_window(private, lifecycle, assets, passwords):
    def handoff(*, base, member, title, business_id, manual, output, expect_uncommitted, **_peer_callbacks):
        app = QApplication.instance() or QApplication([])
        assert app.platformName() == 'windows', '联合验收需要交互式 Windows Qt 桌面。'
        app.setApplicationName('WorkBox')
        app.setOrganizationName('WorkBox')
        app.setStyle('Fusion')
        app.setQuitOnLastWindowClosed(False)
        personal_data = private / 'personal'
        share = private / 'team-originals'
        share.mkdir()
        service = DesktopService(personal_data / 'workbox.db')
        save_shared_root(personal_data / '团队交件' / 'settings.json', share)
        install_theme(service)
        controller = FloatingController(service, tray_enabled=False)
        workbox = origin(base)
        assert urlsplit(workbox).hostname == '127.0.0.1'
        user_id, username = member
        evidence = {'engine': 'QtWebEngine', 'qt_version': qVersion(), 'platform': app.platformName(),
                    'stage': 'starting', 'office_business_id': business_id}
        site_view = office_view = None

        with original_frontend(assets, title) as (site, fixture), patch.object(team_application, 'LIVE_SITE_URL', site):
            assert urlsplit(site).hostname == '127.0.0.1' and site != workbox
            try:
                lifecycle['qt_close_pending'] = True
                assert controller.start()
                controller.monitor_timer.stop()
                managed, project_id, regression = verify_personal_projects(service, controller, title, manual, output)
                panel = controller.panel
                assert panel.nav_team.objectName() == 'openTeamDelivery'
                click(panel.nav_team)
                window = panel.team_page
                team = panel.team_controller
                assert panel.tabs.isAncestorOf(window) and window.isVisible() and not window.isWindow()
                wait_for(lambda: not window.busy, '嵌入式团队交件没有就绪。')
                shot(panel, output, 'unified-embedded-workspace.png')
                click(panel.nav_projects)
                assert panel.focus_visible_project(project_id)

                def selected_document():
                    pane = panel.document_pane
                    for row in range(pane.table.rowCount()):
                        pane.table.selectRow(row)
                        if pane.team_action.isEnabled() and pane.current_document()['path'] == str(managed):
                            return True
                    return False

                wait_for(selected_document, '个人项目文档列表没有找到可交件的 Word。')
                assert panel.document_pane.team_action.objectName() == 'sendToTeam'
                shot(panel, output, 'unified-selected-personal-manual.png')
                panel.document_pane.team_action.trigger()
                assert window.isVisible() and panel.team_page is window

                def button(name):
                    result = window.findChild(QAbstractButton, name)
                    assert result is not None, name
                    return result

                def label(name):
                    return window.findChild(QLabel, name).text()

                wait_for(lambda: not window.busy, '个人手册未传到团队工作区。')
                assert manual.name in label('pendingPersonalDocument')
                use_manual = button('usePersonalDocument')
                assert not use_manual.isEnabled(), '个人手册在确认两个账号前不可用于交件。'
                results = []
                snapshots = []
                team.result_ready.connect(results.append)
                team.snapshot_changed.connect(snapshots.append)

                def enter_address(dialog):
                    dialog.setTextValue(workbox)
                    click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok))

                def open_site():
                    click(button('openLogin'))
                    wait_for(lambda: find_site_view(site), '未通过产品入口打开原站 Qt 网页。')
                    view = find_site_view(site)
                    assert view.page().profile().isOffTheRecord()
                    return view

                def return_to_tasks(view):
                    shell = view.window()
                    assert shell is not panel, '原站页面应当有独立的 Qt 窗口。'
                    assert shell.close(), '无法关闭原站窗口回到我的待办。'
                    wait_for(lambda: not view.isVisible() and window.isVisible() and not window.busy,
                             '原站窗口关闭后未回到收件。')

                def await_account(expected):
                    def matched():
                        account = snapshots[-1].account if snapshots else None
                        return (account.username if account else None) == expected and not window.busy

                    wait_for(matched, f'未读到原站材料账号 {expected or "已登出"}。', timeout=35)
                    return snapshots[-1].account if snapshots else None

                evidence['stage'] = 'original-site-native-login'
                site_view = open_site()
                await_account(None)
                wait_for(lambda: any(event['path'] == '/api/bootstrap-status' and event['status'] == 200
                                     for event in fixture.events), '匿名原站登录页尚未完成初始化。')
                assert not original_write_attempts(fixture)
                shot(site_view.window(), output, 'unified-original-before-login.png')
                site_login(site_view, fixture.login_accounts[PRIMARY])
                shot(site_view.window(), output, 'unified-original-native-login.png')
                return_to_tasks(site_view)
                refresh_tasks(window, [TASK_ID])
                primary = await_account(PRIMARY)

                evidence['stage'] = 'office-native-login'
                interact_with_dialog(app, QInputDialog, lambda: click(button('openWorkBoxLogin')), enter_address)
                office = window.office_panel
                office_view = office.findChild(QWebEngineView)
                assert office_view.page() == office.page
                assert office.page.profile().isOffTheRecord() and office.page.profile() != site_view.page().profile()
                web_login(office_view, username, passwords[username],
                          account_field='.form-login input[name=username]',
                          password_field='.form-login input[name=password]',
                          submit_button='.form-login button[type=submit]')
                wait_for(lambda: office.account and office.account.username == username, '嵌入式办公室登录未确认。')
                assert office.account.user_id == user_id
                evidence['office_and_original_separate_off_record_profiles'] = True
                shot(panel, output, 'unified-office-work.png')
                click(button('officeBackToTasks'))
                refresh_tasks(window, [TASK_ID])
                confirm = button('confirmAccounts')
                wait_for(lambda: confirm.isEnabled() and not window.busy, '未读到两边账号。')
                assert username in label('workboxAccount') and primary.name in label('account')
                click(confirm)
                wait_for(lambda: use_manual.isEnabled() and not window.busy, '核对身份后手册未就绪。')

                evidence['stage'] = 'same-name-account-switch'
                fixture.add_project(59, '匿名材料乙账号待办', owner=ALTERNATE)
                assert open_site() is site_view
                site_logout(site_view)
                return_to_tasks(site_view)
                refresh_tasks(window, [])
                await_account(None)
                assert not use_manual.isEnabled()
                assert open_site() is site_view
                site_login(site_view, fixture.login_accounts[ALTERNATE])
                return_to_tasks(site_view)
                refresh_tasks(window, [59])
                alternate = await_account(ALTERNATE)
                assert primary.name == alternate.name and primary.session != alternate.session
                assert primary.username != alternate.username and office.account.username == username
                assert not use_manual.isEnabled() and not original_write_attempts(fixture)
                shot(panel, output, 'unified-same-name-account-switched.png')
                assert open_site() is site_view
                site_logout(site_view)
                return_to_tasks(site_view)
                refresh_tasks(window, [])
                await_account(None)
                assert open_site() is site_view
                site_login(site_view, fixture.login_accounts[PRIMARY])
                return_to_tasks(site_view)
                refresh_tasks(window, [TASK_ID])
                returned = await_account(PRIMARY)
                assert returned.session != primary.session and office.account.user_id == user_id
                assert not use_manual.isEnabled()
                wait_for(lambda: confirm.isEnabled(), '换回本人原站账号后未出现重新确认入口。')
                click(confirm)
                wait_for(lambda: use_manual.isEnabled() and not window.busy, '换号后重新确认未恢复交件。')
                evidence['original_session_regression'] = {
                    'order': [PRIMARY, None, ALTERNATE, None, PRIMARY], 'shared_display_name': primary.name,
                    'new_session_for_restored_account': True, 'office_session_retained': True,
                    'pair_confirmation_reset': True, 'alternate_visible_task_ids': [59],
                }
                expected_auth = [
                    ('/api/login', PRIMARY), ('/api/logout', PRIMARY), ('/api/login', ALTERNATE),
                    ('/api/logout', ALTERNATE), ('/api/login', PRIMARY),
                ]
                auth = [(event['path'], event['account']) for event in fixture.events
                        if event['method'] == 'POST' and event['path'] in ('/api/login', '/api/logout')]
                assert auth == expected_auth, auth
                evidence['original_session_regression']['exact_auth_requests'] = auth

                evidence['stage'] = 'background-and-ambiguous-task'
                evidence['queue_regression'] = verify_background_and_ambiguity(
                    app, window, fixture, managed, title, expect_uncommitted, results, output)

                evidence['stage'] = 'one-word-drop-timeout-and-readback'
                expect_uncommitted()
                fixture.arm_complete_timeout(TASK_ID, delay_seconds=25)
                prior = len(results)
                drop_word(window, managed)
                wait_for(lambda: len(results) > prior and not window.busy, '办公室交接或原站提交未完成。', timeout=130)
                saved = results[-1]
                assert int(saved.task_id) == TASK_ID and saved.receipt_id == business_id, saved
                first_outcome = saved.outcome
                if first_outcome == Outcome.PENDING:
                    shot(panel, output, 'unified-timeout-first-result.png')
                    prior = len(results)
                    drop_word(window, managed)
                    wait_for(lambda: len(results) > prior and not window.busy, '超时后重新拖入未完成读回。', timeout=100)
                    saved = results[-1]
                assert saved.outcome == Outcome.SUBMITTED and int(saved.task_id) == TASK_ID and saved.receipt_id == business_id, saved
                archived = saved.material.path
                assert archived.is_relative_to(share) and archived.read_bytes() == managed.read_bytes() == manual.read_bytes()
                path_field = window.findChild(QLineEdit, 'savedPath')
                assert path_field.isVisible() and Path(path_field.text()) == archived
                assert TASK_ID not in visible_tasks(window)
                assert fixture.project(TASK_ID)['matStatus'] == '已完成'
                assert all(fixture.project(task_id)['matStatus'] == '待制作' for task_id in (56, 57, OTHER_ID, 59))
                evidence['timeout_readback'] = verify_timeout_readback(fixture) | {'first_result': first_outcome.value}
                shot(panel, output, 'unified-receipts.png')
                prior = len(results)
                drop_word(window, managed)
                wait_for(lambda: len(results) > prior and not window.busy, '重复交件未完成回读。', timeout=100)
                assert results[-1].outcome == Outcome.SUBMITTED and results[-1].receipt_id == business_id, results[-1]
                with archived.open('rb') as document:
                    manual_hash = file_digest(document, 'sha256').hexdigest()
                expected_writes = [(START, {'ids': [TASK_ID]}), (COMPLETE, {'ids': [TASK_ID]})]
                assert list(fixture.writes) == expected_writes, fixture.writes
                attempts = original_write_attempts(fixture)
                assert [(event['path'], event.get('request')) for event in attempts] == expected_writes, attempts
                assert all(event['account'] == PRIMARY for event in attempts), attempts
                evidence.update(stage='desktop-passed', embedded_in_main_window=True, personal_project_id=project_id,
                                personal_word_sha256=manual_hash, explicit_personal_selection=True,
                                team_original_sha256=manual_hash, original_site_task_id=TASK_ID,
                                original_site_account=PRIMARY, original_site_write_requests=len(expected_writes),
                                exact_original_site_writes=expected_writes, repeat_drop_verified=True,
                                finished_task_removed_from_visible_queue=True, pr_132=regression)
            finally:
                try:
                    controller.shutdown()
                    controller.dispose_ui()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                    assert controller.closed and not controller.owns_lock
                    assert all(view is None or not isValid(view) or not view.isVisible() for view in (site_view, office_view))
                    evidence['main_window_and_team_browser_closed'] = True
                    lifecycle['qt_close_pending'] = False
                finally:
                    evidence['original_site_events'] = fixture.events
                    (output / 'desktop-observations.json').write_text(
                        json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        assert_closed(urlsplit(site).port)
        evidence['mimic_site_stopped'] = True
        (output / 'desktop-observations.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return {key: value for key, value in evidence.items() if key != 'original_site_events'}

    return handoff
