from time import monotonic

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.contracts import Account, RefreshDeferred, Snapshot, Stage, Task
from workbox.team_delivery.desktop import DesktopWindow


class SnapshotGateway:
    def __init__(self):
        self.answer = Snapshot(None)
        self.account = None
        self.refresh_count = 0

    def refresh_snapshot(self):
        self.refresh_count += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def current_account(self):
        return self.account

    def close(self):
        pass


@pytest.fixture
def personal_sync_page(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    gateway = SnapshotGateway()
    controller = IntakeController(
        page, 'http://127.0.0.1:1', None, tmp_path / 'journal',
        settings=tmp_path / 'settings', gateway=gateway, personal_sync_enabled=True,
    )
    received = []
    controller.personal_sync_requested.connect(received.append)
    button = page.findChild(QPushButton, 'syncPersonalProjects')
    controller.start()
    try:
        yield controller, page, gateway, button, received
    finally:
        controller.stop()
        page.close()


def wait_until(check):
    deadline = monotonic() + 4
    while not check() and monotonic() < deadline:
        QTest.qWait(20)
    assert check()


def material_actor():
    return Account('owner', '材料甲', 'material', 'session', 'user-a')


def task(identifier, name, actor, stage):
    return Task(identifier, name, actor.id, stage, 'r1')


def test_only_explicit_sync_uses_the_fresh_complete_own_scope_without_office(personal_sync_page):
    controller, page, gateway, button, received = personal_sync_page
    assert not button.isHidden()
    assert not button.isEnabled()

    actor = material_actor()
    other = Account('other', '材料乙', 'material', 'other-session', 'user-b')
    controller._show_snapshot(Snapshot(actor, (task('10', '旧任务', actor, Stage.READY),)))
    page._search.setText('只显示旧任务')
    assert page._workbox_username == ''
    assert gateway.refresh_count == 0 and received == []
    assert button.isEnabled()

    gateway.answer = Snapshot(actor, (
        task('11', '匿名待制作', actor, Stage.READY),
        task('12', '匿名制作中', actor, Stage.WORKING),
        task('13', '匿名已退回', actor, Stage.RETURNED),
        task('14', '匿名已完成', actor, Stage.COMPLETED),
        task('15', '匿名待核对', actor, Stage.UNKNOWN),
        task('16', '匿名他人任务', other, Stage.READY),
    ))
    button.click()
    button.click()
    wait_until(lambda: bool(received) and not controller._busy)
    assert received == [(('11', '匿名待制作'), ('12', '匿名制作中'), ('13', '匿名已退回'))]
    assert gateway.refresh_count == 1
    assert not button.isEnabled()
    assert '正在同步到个人项目' in page._sync_notice.text()

    controller.set_personal_sync_result('新增 3 个个人项目')
    assert button.isEnabled()
    assert '新增 3 个个人项目' in page._sync_notice.text()


@pytest.mark.parametrize('change', ['different-session', 'deferred', 'duplicate', 'empty'])
def test_old_ambiguous_or_empty_tasks_are_never_forwarded(personal_sync_page, change):
    controller, page, gateway, button, received = personal_sync_page
    actor = material_actor()
    old = task('101', '匿名待办', actor, Stage.READY)
    gateway.account = actor
    controller._show_snapshot(Snapshot(actor, (old,)))
    if change == 'different-session':
        actor = Account(actor.id, actor.name, actor.role, 'other-session', actor.username)
        gateway.answer = Snapshot(actor, (task('103', '匿名账号变化', actor, Stage.READY),))
        expected = '账号已变化'
    elif change == 'deferred':
        gateway.answer = RefreshDeferred('请打开本人清单')
        expected = '暂时无法核对最新待办'
    elif change == 'duplicate':
        gateway.answer = Snapshot(actor, (old, task(old.id, '匿名编号重复', actor, Stage.READY)))
        expected = '任务编号重复'
    else:
        gateway.answer = Snapshot(actor, (task('104', '匿名已完成', actor, Stage.COMPLETED),))
        expected = '没有可同步'
    button.click()
    wait_until(lambda: expected in page._sync_notice.text() and not controller._busy)
    assert gateway.refresh_count == 1
    assert received == []


def test_sync_completion_hides_previous_account_details_after_switch(personal_sync_page):
    controller, page, gateway, button, received = personal_sync_page
    actor = material_actor()
    snapshot = Snapshot(actor, (task('17', '匿名过期待办', actor, Stage.READY),))
    gateway.answer = snapshot
    controller._show_snapshot(snapshot)
    button.click()
    wait_until(lambda: bool(received) and not controller._busy)

    other = Account('other', '材料乙', 'material', 'other-session', 'user-b')
    controller._show_snapshot(Snapshot(other))
    controller.set_personal_sync_result('待核对 #17 匿名过期待办')
    assert '账号已变化' in page._sync_notice.text()
    assert '匿名过期待办' not in page._sync_notice.text()
    assert button.isEnabled()


def test_standalone_team_page_does_not_offer_personal_sync(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    IntakeController(page, 'http://127.0.0.1:1', None, tmp_path / 'journal',
                     settings=tmp_path / 'settings', gateway=SnapshotGateway())
    button = page.findChild(QPushButton, 'syncPersonalProjects')
    assert button is not None and button.isHidden()
