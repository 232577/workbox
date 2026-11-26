from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from workbox.desktop.desktop_service import DesktopService
from workbox.projects.original_site import OriginalSiteProjects
from workbox.team_delivery.contracts import Account, Stage, Task


ALICE = Account('session-a', '匿名同名', 'material', 'browser-a', 'anonymous-a')
BOB = Account('session-b', '匿名同名', 'material', 'browser-b', 'anonymous-b')


@pytest.fixture
def personal(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    return service, OriginalSiteProjects(service.db, service.workspaces)


def task(account=ALICE, identifier='55', stage=Stage.READY, revision='initial'):
    return Task(identifier, '匿名同名项目', account.id, stage, revision)


def deliver(service, project_id, name='材料.txt'):
    workspace = Path(service.workspaces.get_workspace(project_id)['path'])
    (workspace / name).write_text('anonymous material', encoding='utf-8')
    service.set_status(project_id, '待交付')
    return service.confirm_delivery(project_id, confirmed=True)['event_id']


def test_import_keeps_same_name_projects_separate_and_survives_relogin(personal):
    service, site = personal
    title = task().title
    local = service.db.create_capture(title, '匿名本地批次', [(title, False)])['project_ids'][0]
    assert site.sync(ALICE, [task(), task(identifier='56')]) == 2
    first = site.binding_for_task(ALICE, task())
    second = site.binding_for_task(ALICE, task(identifier='56'))
    assert len({local, first, second}) == 3
    assert site.binding_for(local) is None
    assert service.get_project(first)['name'] == service.get_project(second)['name'] == title
    assert service.get_project(first)['original_task_id'] == '55'
    assert service.get_project(second)['original_account_username'] == ALICE.username
    assert service.get_project(local)['original_task_id'] == ''
    assert {row['original_task_id'] for row in service.project_workbench_snapshot()['projects']} == {'', '55', '56'}
    assert service.workspaces.get_workspace(first)['path'] != service.workspaces.get_workspace(second)['path']
    relogin = replace(ALICE, id='session-a-relogin', session='browser-new')
    assert site.sync(relogin, [task(relogin), task(relogin, '56')]) == 0
    assert site.binding_for_task(relogin, task(relogin)) == first
    assert service.db.delivery_history()['event_count'] == 0


def test_invalid_snapshot_cannot_partially_import_or_deactivate(personal):
    service, site = personal
    assert site.sync(ALICE, [task()]) == 1
    for invalid in ([task(identifier='56'), task(BOB)], [task(), task()], [task(identifier='01')]):
        with pytest.raises(ValueError):
            site.sync(ALICE, invalid)
    assert site.sync(None, ()) == 0
    assert site.sync(replace(ALICE, role='code'), [task()]) == 0
    assert service.db.counts()['projects'] == 1
    assert site.binding_for_task(ALICE, task()) is not None


def test_claim_survives_restart_without_resending_and_accepts_exact_readback(personal):
    service, site = personal
    site.sync(ALICE, [task()])
    pid = site.binding_for_task(ALICE, task())
    first = deliver(service, pid)
    event = deliver(service, pid, '再确认.txt')
    assert [item.event_id for item in site.pending(ALICE)] == [event]
    assert site.delivery_for_event(first).status == 'superseded'
    assert site.claim(event, ALICE, None) is None
    assert site.claim(event, BOB, task(BOB)) is None
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: site.claim(event, ALICE, task()), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    restarted = OriginalSiteProjects(service.db)
    assert restarted.sync(ALICE, []) == 1
    assert restarted.pending(ALICE)[0].status == 'needs_check'
    assert restarted.claim(event, ALICE, task()) is None
    assert not restarted.record_receipt(event, account_username=BOB.username, task_id='55', delivery_id='ws-1')
    assert not restarted.record_receipt(event, account_username=ALICE.username, task_id='56', delivery_id='ws-1')
    assert restarted.record_receipt(event, account_username=ALICE.username, task_id='55', delivery_id='ws-1')
    assert restarted.record_receipt(event, account_username=ALICE.username, task_id='55', delivery_id='ws-1')
    assert not restarted.record_receipt(event, account_username=ALICE.username, task_id='55', delivery_id='ws-other')
    assert not restarted.pending(ALICE)


def test_reassignment_pins_old_event_to_old_account_without_blocking_undispatched(personal):
    service, site = personal
    site.sync(ALICE, [task()])
    first = site.binding_for_task(ALICE, task())
    old_event = deliver(service, first)
    site.sync(BOB, [task(BOB)])
    second = site.binding_for_task(BOB, task(BOB))
    assert first != second
    assert site.binding_for_task(ALICE, task()) is None
    assert site.claim(old_event, ALICE, task()) is None
    assert site.pending(ALICE)[0].status == 'needs_check'
    assert not site.pending(BOB)
    new_event = deliver(service, second)
    assert site.claim(new_event, BOB, task(BOB)) is not None
    assert service.db.delivery_history()['project_count'] == 1
    assert service.db.delivery_history()['event_count'] == 2


def test_explicit_return_after_confirmed_round_accepts_one_new_manual_delivery(personal):
    service, site = personal
    initial = task(stage=Stage.RETURNED, revision='prior-return')
    site.sync(ALICE, [initial])
    pid = site.binding_for_task(ALICE, initial)
    event = deliver(service, pid)
    claim = site.claim(event, ALICE, initial)
    assert claim and site.mark_not_sent(claim)
    next_claim = site.claim(event, ALICE, initial)
    assert next_claim and not site.mark_not_sent(claim)
    assert site.record_receipt(event, account_username=ALICE.username, task_id='55', delivery_id='round-one')
    site.sync(ALICE, [initial])  # Same old returned observation after the receipt.
    assert site.delivery_for_event(deliver(service, pid, '仍是第一轮.txt')).status == 'superseded'
    returned = replace(initial, revision='new-return')
    assert site.sync(ALICE, [returned]) == 1
    assert site.sync(ALICE, [returned]) == 0
    second = deliver(service, pid, '第二轮.txt')
    assert (site.pending(ALICE)[0].event_id, site.pending(ALICE)[0].round_id) == (second, 2)
    claim = site.claim(second, ALICE, returned)
    assert claim and claim.round_id == 2
    assert site.record_receipt(second, account_username=ALICE.username, task_id='55', delivery_id='round-two')
    site.sync(ALICE, [initial])
    old_return = deliver(service, pid, '旧退回状态.txt')
    assert site.delivery_for_event(old_return).status == 'superseded'
    assert service.db.delivery_history()['project_count'] == 1


def test_explicit_existing_binding_requires_confirmation_and_matching_name(personal):
    service, site = personal
    title = task().title
    chosen, other = service.db.create_capture('本地消息', '匿名批次', [
        (title, False), ('另一个项目', False),
    ])['project_ids']
    with pytest.raises(ValueError):
        site.bind_existing(ALICE, task(), chosen, confirmed=False)
    with pytest.raises(ValueError):
        site.bind_existing(ALICE, task(), other, confirmed=True)
    assert site.bind_existing(ALICE, task(), chosen, confirmed=True) == chosen
    assert site.sync(ALICE, [task()]) == 0
    assert site.binding_for_task(ALICE, task()) == chosen
    assert service.db.counts()['projects'] == 2


def test_revision_and_voided_events_cannot_reuse_old_material(personal):
    service, site = personal
    site.sync(ALICE, [task()])
    pid = site.binding_for_task(ALICE, task())
    first = deliver(service, pid)
    service.set_status(pid, '待交付')
    assert not site.pending(ALICE)
    assert site.claim(first, ALICE, task()) is None

    new_event = deliver(service, pid, '新版.txt')
    claim = site.claim(new_event, ALICE, task())
    assert claim and site.claim_is_current(claim, ALICE)
    service.db.void_delivery_events([new_event], confirmed=True, reason='匿名验证')
    assert not site.claim_is_current(claim, ALICE)
    assert site.pending(ALICE)[0].status == 'needs_check'

    final_event = deliver(service, pid, '重做.txt')
    assert site.claim(final_event, ALICE, task()) is None
    assert site.mark_not_sent(claim)
    assert [item.event_id for item in site.pending(ALICE)] == [final_event]
    assert site.claim(final_event, ALICE, task()) is not None


def test_reassignment_cannot_send_a_second_time_until_prior_write_is_final(personal):
    service, site = personal
    site.sync(ALICE, [task()])
    old_pid = site.binding_for_task(ALICE, task())
    old_event = deliver(service, old_pid)
    old_claim = site.claim(old_event, ALICE, task())
    assert old_claim
    site.sync(BOB, [task(BOB)])
    new_pid = site.binding_for_task(BOB, task(BOB))
    new_event = deliver(service, new_pid)
    assert not site.claim_is_current(old_claim, ALICE)
    assert site.pending(BOB)[0].status == 'needs_check'
    assert site.claim(new_event, BOB, task(BOB)) is None
    assert not site.record_receipt(new_event, account_username=BOB.username, task_id='55', delivery_id='wrong-round')

    assert site.record_receipt(old_event, account_username=ALICE.username, task_id='55', delivery_id='previous-owner')
    reopened = task(BOB, stage=Stage.RETURNED, revision='assigned-return')
    site.sync(BOB, [reopened])
    assert site.claim(new_event, BOB, reopened) is None
    valid = deliver(service, new_pid, '返工.txt')
    assert site.claim(valid, BOB, reopened) is None
    assert site.pending(BOB)[0].round_id == 1


def test_additive_upgrade_keeps_existing_project_facts(personal):
    service, site = personal
    previous = service.db.create_capture('既有本地材料', '之前批次', [('既有本地材料', False)])
    old_id = previous['project_ids'][0]
    service.workspaces.ensure_projects([old_id])
    event = deliver(service, old_id)
    with service.db.connect() as connection:
        connection.execute('DROP TABLE original_site_deliveries')
        connection.execute('DROP TABLE original_site_bindings')
    reopened = DesktopService(service.db.path)
    site = OriginalSiteProjects(reopened.db, reopened.workspaces)
    assert reopened.get_project(old_id)['original_task_id'] == ''
    assert site.sync(ALICE, [task()]) == 1
    assert [row['id'] for row in reopened.db.delivery_history()['events']] == [event]


def test_retry_workspace_creation_does_not_duplicate_committed_binding(personal, monkeypatch):
    service, site = personal
    with monkeypatch.context() as patch:
        def fail(_project_ids):
            raise OSError('isolated workspace error')
        patch.setattr(service.workspaces, 'ensure_projects', fail)
        with pytest.raises(OSError, match='isolated workspace error'):
            site.sync(ALICE, [task()])
    project_id = site.binding_for_task(ALICE, task())
    assert project_id and service.db.counts()['projects'] == 1
    assert site.sync(ALICE, [task()]) == 1
    assert site.sync(ALICE, [task()]) == 0
    assert Path(service.workspaces.get_workspace(project_id)['path']).is_dir()


def test_archived_deleted_or_merged_project_cannot_dispatch_old_delivery(personal):
    service, site = personal
    current = task()
    site.sync(ALICE, [current])
    target = site.binding_for_task(ALICE, current)
    event = deliver(service, target)
    service.bulk_update_projects([target], archived=True)
    assert not site.pending(ALICE) and site.claim(event, ALICE, current) is None
    service.bulk_update_projects([target], archived=False)
    service.db.delete_project(target, confirmed=True)
    assert not site.pending(ALICE) and site.claim(event, ALICE, current) is None
    service.db.restore_deleted_project(target)

    source = service.capture_message('匿名其他项目', '另一本地批次')['project_ids'][0]
    plan = service.preview_project_rename(source, current.title)
    service.rename_project(source, current.title, expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    assert not site.pending(ALICE) and site.claim(event, ALICE, current) is None
    fresh = deliver(service, target, '再次确认合并后的材料.txt')
    assert site.claim(fresh, ALICE, current) is not None
