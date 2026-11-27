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


def test_same_name_needs_unambiguous_local_binding_and_survives_relogin(personal):
    service, site = personal
    title = task().title
    local = service.db.create_capture('旧项目', '匿名本地批次', [('无关项目', False)])['project_ids'][0]
    first_candidate = service.db.create_capture(title, '第一个本地批次', [(title, False)])['project_ids'][0]
    first_task, second_task = task(), task(identifier='56')
    assert site.sync(ALICE, [first_task, second_task]) == 0
    assert site.binding_for_task(ALICE, first_task) is site.binding_for_task(ALICE, second_task) is None
    assert site.sync(ALICE, [first_task]) == 1
    first = site.binding_for_task(ALICE, task())
    assert first == first_candidate
    assert site.sync(ALICE, [first_task, second_task]) == 0
    assert site.binding_for_task(ALICE, second_task) is None
    second = service.db.create_capture(title, '第二个本地批次', [(title, False)])['project_ids'][0]
    assert site.bind_existing(ALICE, second_task, second, confirmed=True) == second
    assert len({local, first, second}) == service.db.counts()['projects'] == 3
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


@pytest.mark.parametrize('suffix', ['V1.0', ' V1.0'])
def test_versioned_task_requires_explicit_binding_to_existing_unversioned_project(personal, suffix):
    service, site = personal
    title = task().title
    current = replace(task(), title=title + suffix)
    pid, = service.db.create_capture('本地消息', '匿名批次', [(title, False)])['project_ids']
    service.ensure_workspaces(pid)
    history_event = deliver(service, pid, '原有文件.txt')
    workspace = service.workspaces.get_workspace(pid)['path']
    history = service.db.delivery_history()

    assert site.sync(ALICE, [current]) == 0
    assert site.unlinked_tasks(ALICE, [current]) == (current,)
    assert service.db.counts()['projects'] == 1
    with pytest.raises(ValueError):
        site.bind_existing(ALICE, current, pid, confirmed=False)

    assert site.bind_existing(ALICE, current, pid, confirmed=True) == pid
    assert site.sync(ALICE, [current]) == 0
    assert site.binding_for_task(ALICE, current) == pid
    assert site.binding_for(pid).title == current.title
    assert service.get_project(pid)['name'] == title and service.get_project(pid)['status'] == '已交付'
    assert service.workspaces.get_workspace(pid)['path'] == workspace
    assert (Path(workspace) / '原有文件.txt').read_text(encoding='utf-8') == 'anonymous material'
    assert service.db.delivery_history() == history
    assert site.delivery_for_event(history_event) is None


def test_versioned_binding_checks_full_match_distinct_versions_and_source_number(personal):
    service, site = personal
    title = task().title
    plain, wrong, exact = service.db.create_capture('本地消息', '匿名批次', [
        (title, False), (title + ' V2.0', False), (title + ' V1.0', False),
    ])['project_ids']
    source = replace(task(), title=title + ' V1.0')
    competing = replace(source, id='56')
    with pytest.raises(ValueError, match='名称'):
        site.bind_existing(ALICE, source, wrong, confirmed=True)
    assert site.sync(ALICE, [source]) == 1
    assert site.binding_for_task(ALICE, source) == exact
    assert site.sync(ALICE, [source, competing]) == 0
    assert site.unlinked_tasks(ALICE, [source, competing]) == (competing,)
    assert site.bind_existing(ALICE, competing, plain, confirmed=True) == plain
    with pytest.raises(ValueError, match='已有绑定'):
        site.bind_existing(ALICE, replace(source, id='57'), plain, confirmed=True)
    assert site.binding_for_task(ALICE, source) == exact
    assert site.binding_for_task(ALICE, competing) == plain


def test_versioned_task_does_not_duplicate_a_versionless_historical_name(personal):
    service, site = personal
    title = task().title
    pid, = service.db.create_capture('本地消息', '匿名批次', [(title, False)])['project_ids']
    rename = service.preview_project_rename(pid, '匿名现用名称')
    service.rename_project(pid, '匿名现用名称', expected_token=rename['token'])
    source = replace(task(), title=title + ' V1.0')

    assert site.sync(ALICE, [source]) == 0
    assert site.unlinked_tasks(ALICE, [source]) == (source,)
    assert service.db.counts()['projects'] == 1
    with pytest.raises(ValueError, match='名称'):
        site.bind_existing(ALICE, source, pid, confirmed=True)


@pytest.fixture
def delivery_before_binding(personal):
    service, site = personal
    current = task(stage=Stage.WORKING)
    pid, = service.db.create_capture('本地消息', '匿名批次', [(current.title, False)])['project_ids']
    service.ensure_workspaces(pid)
    event_id = deliver(service, pid)
    assert site.sync(ALICE, [current]) == 1
    assert site.binding_for_task(ALICE, current) == pid
    assert site.pending(ALICE) == ()
    assert site.delivery_for_event(event_id) is None
    return service, site, pid, current, event_id


def test_existing_delivery_sync_reuses_confirmed_event_and_preserves_local_history(delivery_before_binding):
    service, site, pid, current, event_id = delivery_before_binding
    project = service.get_project(pid)
    history = service.db.delivery_events_for_project(pid)
    before = service.db.delivery_history()
    archive = Path(history[0]['archive_path'])
    archived_file = archive / '材料.txt'
    original_bytes = archived_file.read_bytes()
    archive_versions = sorted(path.name for path in archive.parent.iterdir())
    preview = site.preview_delivery_sync(pid, ALICE, current)
    assert (preview.event_id, preview.occurred_at) == (event_id, history[0]['occurred_at'])
    with pytest.raises(ValueError, match='确认'):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id)
    assert site.delivery_for_event(event_id) is None

    queued = site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)
    assert (queued.event_id, queued.task_id, queued.title, queued.status) == (
        event_id, current.id, current.title, 'pending',
    )
    assert site.pending(ALICE) == (queued,)
    with pytest.raises(ValueError, match='队列'):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)

    claimed = site.claim(event_id, ALICE, current)
    assert claimed is not None
    with pytest.raises(ValueError, match='核对'):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)
    assert site.record_receipt(event_id, account_username=ALICE.username, task_id=current.id,
                               delivery_id='verified-receipt')
    with pytest.raises(ValueError, match='已确认'):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)
    assert site.pending(ALICE) == ()
    assert service.get_project(pid) == project
    assert service.db.delivery_events_for_project(pid) == history
    assert service.db.delivery_history() == before
    assert archived_file.read_bytes() == original_bytes
    assert sorted(path.name for path in archive.parent.iterdir()) == archive_versions


def test_existing_delivery_sync_rechecks_account_observation_and_delivery_event(delivery_before_binding):
    service, site, pid, current, event_id = delivery_before_binding
    for account, task_view in (
        (BOB, replace(current, owner_id=BOB.id)),
        (ALICE, replace(current, revision='stale-revision')),
        (ALICE, replace(current, title='相似但不同的匿名名称')),
        (ALICE, replace(current, stage=Stage.COMPLETED)),
    ):
        with pytest.raises(ValueError):
            site.queue_existing_delivery(pid, account, task_view, expected_event_id=event_id, confirmed=True)
    with pytest.raises(ValueError):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id + 1, confirmed=True)
    service.set_status(pid, '待交付')
    with pytest.raises(ValueError):
        site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)
    assert site.delivery_for_event(event_id) is None


def test_existing_delivery_sync_will_not_reuse_pre_return_material(delivery_before_binding):
    _service, site, pid, current, event_id = delivery_before_binding
    returned = replace(current, stage=Stage.RETURNED, revision='returned')
    rework = replace(current, revision='rework')
    for task_view in (returned, rework):
        site.sync(ALICE, [task_view])
        with pytest.raises(ValueError, match='退回'):
            site.queue_existing_delivery(pid, ALICE, task_view, expected_event_id=event_id, confirmed=True)
    assert site.delivery_for_event(event_id) is None


@pytest.mark.parametrize('new_stage', [Stage.WORKING, Stage.RETURNED])
def test_queued_existing_delivery_never_submits_a_new_site_revision(delivery_before_binding, new_stage):
    _service, site, pid, current, event_id = delivery_before_binding
    site.queue_existing_delivery(pid, ALICE, current, expected_event_id=event_id, confirmed=True)
    claim = site.claim(event_id, ALICE, current)
    assert site.claim_is_current(claim, ALICE)

    changed = replace(current, revision='new-revision', stage=new_stage)
    site.sync(ALICE, [changed])
    assert not site.claim_is_current(claim, ALICE)
    assert site.mark_not_sent(claim)
    assert site.claim(event_id, ALICE, changed) is None
    assert site.pending(ALICE)[0].status == 'needs_check'
    assert not site.record_receipt(event_id, account_username=ALICE.username, task_id=current.id,
                                   delivery_id='different-revision-receipt')


def test_automatic_sync_reuses_manual_history_and_pins_only_new_deliveries(personal):
    service, site = personal
    current = task()
    manual = service.sync_site_tasks([(current.id, current.title)])
    pid, = manual['created_project_ids']
    workspace = service.workspaces.get_workspace(pid)
    historical = deliver(service, pid)
    assert site.sync(ALICE, [current]) == 1
    assert site.binding_for_task(ALICE, current) == pid
    assert service.workspaces.get_workspace(pid) == workspace
    assert service.db.counts()['projects'] == 1
    assert service.get_project(pid)['status'] == '已交付'
    assert site.delivery_for_event(historical) is None
    assert not site.pending(ALICE)
    latest = deliver(service, pid, '新办理.txt')
    assert site.pending(ALICE)[0].event_id == latest
    assert service.db.delivery_history()['project_count'] == 1


def test_manual_sync_recognizes_automatic_task_by_id_even_after_title_changes(personal):
    service, site = personal
    current = task()
    site.sync(ALICE, [current])
    pid = site.binding_for_task(ALICE, current)
    result = service.sync_site_tasks([(current.id, '已更名的匿名项目')])
    assert result['unchanged_project_ids'] == [pid]
    assert result['created_project_ids'] == result['linked_project_ids'] == result['conflicts'] == []
    assert service.db.counts()['projects'] == 1


def test_manual_links_do_not_resurrect_deleted_or_conflicting_projects(personal):
    service, site = personal
    current = task()
    pid, = service.sync_site_tasks([(current.id, current.title)])['created_project_ids']
    service.db.delete_project(pid, confirmed=True)
    assert site.sync(ALICE, [current]) == 0
    assert site.binding_for_task(ALICE, current) is None
    assert service.db.counts()['projects'] == 0

    service.db.restore_deleted_project(pid)
    other = replace(current, id='56')
    assert site.sync(ALICE, [other]) == 0
    assert site.binding_for_task(ALICE, other) is None
    assert service.db.counts()['projects'] == 1


def test_manual_and_automatic_import_concurrently_share_one_local_identity(personal):
    service, site = personal
    current = task()
    with ThreadPoolExecutor(2) as pool:
        manual = pool.submit(service.sync_site_tasks, [(current.id, current.title)])
        automatic = pool.submit(site.sync, ALICE, [current])
        manual_ids = manual.result()['project_ids']
        assert automatic.result() == 1
    assert manual_ids == [site.binding_for_task(ALICE, current)]
    assert service.db.counts()['projects'] == 1


def test_selected_new_project_resolves_one_ambiguous_number_without_using_other_projects(personal):
    service, site = personal
    first, second = task(), task(identifier='56')
    first_local = service.db.create_capture(first.title, '本地甲批次', [(first.title, False)])['project_ids'][0]
    second_local = service.db.create_capture(first.title, '本地乙批次', [(first.title, False)])['project_ids'][0]
    assert site.sync(ALICE, [first, second]) == 0
    assert site.unlinked_tasks(ALICE, [first, second]) == (first, second)
    assert not site.unlinked_tasks(None, [first, second])
    with pytest.raises(ValueError):
        site.create_for_selected(ALICE, first, confirmed=False)
    with pytest.raises(ValueError):
        site.create_for_selected(BOB, first, confirmed=True)
    chosen = site.create_for_selected(ALICE, first, confirmed=True)
    assert chosen not in {first_local, second_local}
    assert site.create_for_selected(ALICE, first, confirmed=True) == chosen
    assert site.unlinked_tasks(ALICE, [first, second]) == (second,)
    assert service.sync_site_tasks([(first.id, first.title)])['unchanged_project_ids'] == [chosen]
    assert site.bind_existing(ALICE, second, second_local, confirmed=True) == second_local
    assert not site.unlinked_tasks(ALICE, [first, second])


@pytest.mark.parametrize('state', ['active', 'archived', 'deleted'])
def test_explicit_new_never_replaces_an_exact_manual_history_link(personal, state):
    service, site = personal
    current = task()
    pid, = service.sync_site_tasks([(current.id, current.title)])['created_project_ids']
    if state == 'archived':
        service.bulk_update_projects([pid], archived=True)
    elif state == 'deleted':
        service.db.delete_project(pid, confirmed=True)
    with pytest.raises(ValueError, match='关联'):
        site.create_for_selected(ALICE, current, confirmed=True)
    assert site.unlinked_tasks(ALICE, [current]) == (current,)
    with service.db.connect() as connection:
        assert connection.execute('SELECT project_id FROM site_task_project_links').fetchone()[0] == pid
        assert connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 1


def test_legacy_manual_history_counts_once_across_reassignment_without_queueing_it(personal):
    service, site = personal
    current = task()
    old_pid, = service.sync_site_tasks([(current.id, current.title)])['created_project_ids']
    old_event = deliver(service, old_pid)
    site.sync(ALICE, [current])
    assert site.binding_for_task(ALICE, current) == old_pid
    assert not site.pending(ALICE)
    site.sync(BOB, [task(BOB)])
    new_pid = site.binding_for_task(BOB, task(BOB))
    new_event = deliver(service, new_pid)
    history = service.db.delivery_history()
    assert new_pid != old_pid
    assert site.claim(new_event, BOB, task(BOB))
    assert history['project_count'] == 1 and history['event_count'] == 2
    assert {row['id']: row['counted'] for row in history['events']} == {old_event: True, new_event: False}

    service.db.set_project_delivery_counting([old_pid], False, confirmed=True, reason='匿名排除')
    history = service.db.delivery_history()
    assert history['project_count'] == 0 and history['valid_event_count'] == 2
    assert next(row for row in history['events'] if row['id'] == old_event)['count_exclusion_reason'] == '匿名排除'


def test_legacy_history_under_explicit_merge_target_retains_one_workload_identity(personal):
    service, site = personal
    current = task()
    former, = service.sync_site_tasks([(current.id, current.title)])['created_project_ids']
    target, = service.capture_message('现用本机项目', '匿名保留批次')['project_ids']
    plan = service.preview_project_rename(former, '现用本机项目')
    service.rename_project(former, '现用本机项目', expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    historical = deliver(service, target)
    assert site.sync(ALICE, [current]) == 1
    assert site.binding_for_task(ALICE, current) == target
    assert not site.pending(ALICE)

    site.sync(BOB, [task(BOB)])
    successor = site.binding_for_task(BOB, task(BOB))
    latest = deliver(service, successor)
    history = service.db.delivery_history()
    assert history['project_count'] == 1
    assert {row['id']: row['counted'] for row in history['events']} == {historical: True, latest: False}


def test_automatic_sync_does_not_adopt_two_legacy_numbers_merged_into_one_target(personal):
    service, site = personal
    first = task()
    second = replace(task(identifier='56'), title='另一原站同事的项目')
    former, target = service.sync_site_tasks([(first.id, first.title), (second.id, second.title)])['project_ids']
    plan = service.preview_project_rename(former, second.title)
    service.rename_project(former, second.title, expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    assert site.sync(ALICE, [first, second]) == 0
    assert site.unlinked_tasks(ALICE, [first, second]) == (first, second)
    with pytest.raises(ValueError, match='已有绑定'):
        site.bind_existing(ALICE, second, target, confirmed=True)
    assert service.db.counts()['projects'] == 1


def test_user_cannot_rebind_another_accounts_old_project_via_a_merge_target(personal):
    service, site = personal
    current = task()
    site.sync(ALICE, [current])
    former = site.binding_for_task(ALICE, current)
    target = service.db.create_capture('独立本地项目', '另一个批次', [(current.title, False)])['project_ids'][0]
    service.workspaces.ensure_projects([target])
    plan = service.preview_project_rename(former, current.title, merge_target_id=target)
    service.rename_project(former, current.title, expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    with pytest.raises(ValueError, match='已有绑定'):
        site.bind_existing(BOB, task(BOB), target, confirmed=True)
    assert site.sync(BOB, [task(BOB)]) == 1
    assert site.binding_for_task(BOB, task(BOB)) != target


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


@pytest.mark.parametrize('old_outcome', [None, 'completed', 'not_sent'])
def test_bound_merge_source_follows_target_without_replaying_original_event(personal, old_outcome):
    service, site = personal
    current = task()
    site.sync(ALICE, [current])
    source = site.binding_for_task(ALICE, current)
    old_event = deliver(service, source)
    claim = site.claim(old_event, ALICE, current) if old_outcome else None
    target, = service.capture_message('匿名保留项目', '保留批次')['project_ids']
    plan = service.preview_project_rename(source, '匿名保留项目')
    assert plan['kind'] == 'merge'
    service.rename_project(source, '匿名保留项目', expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    assert site.binding_for_task(ALICE, current) == target
    assert not site.unlinked_tasks(ALICE, [current])
    assert service.get_project(target)['original_task_id'] == current.id
    assert site.claim(old_event, ALICE, current) is None
    assert site.delivery_for_event(old_event).project_id == source
    assert service.db.delivery_history()['events'][0]['original_project_id'] == source

    fresh = deliver(service, target, '合并后明确交付.txt')
    if claim is not None:
        assert not site.claim_is_current(claim, ALICE)
        waiting, = site.pending(ALICE)
        assert (waiting.event_id, waiting.status) == (old_event, 'needs_check')
        assert site.claim(fresh, ALICE, current) is None
    if old_outcome == 'not_sent':
        assert site.mark_not_sent(claim)
        assert site.delivery_for_event(old_event).status == 'superseded'
    elif old_outcome == 'completed':
        assert site.record_receipt(old_event, account_username=ALICE.username, task_id=current.id,
                                   delivery_id='legacy-readback')
        assert site.claim(fresh, ALICE, current) is None
        returned = replace(current, stage=Stage.RETURNED, revision='explicit-return-after-merge')
        site.sync(ALICE, [returned])
        fresh = deliver(service, target, '原站重新退回的本机交付.txt')
        current = returned
    assert site.claim(fresh, ALICE, current).project_id == target
    assert service.db.delivery_history()['project_count'] == 1


@pytest.mark.parametrize('target_bound', [False, True])
def test_bound_source_merge_refuses_a_target_linked_to_another_original_task(personal, target_bound):
    service, site = personal
    first = task()
    second = replace(task(identifier='56'), title='另一原站编号的项目')
    site.sync(ALICE, [first])
    source = site.binding_for_task(ALICE, first)
    if target_bound:
        site.sync(ALICE, [first, second])
        target = site.binding_for_task(ALICE, second)
    else:
        target, = service.sync_site_tasks([(second.id, second.title)])['created_project_ids']
    plan = service.preview_project_rename(source, second.title, merge_target_id=target)
    assert plan['kind'] == 'blocked'
    assert '原站' in plan['warning']
    assert service.db.counts()['projects'] == 2


def test_project_merge_cannot_join_two_authenticated_owners_of_the_same_number(personal):
    service, site = personal
    site.sync(ALICE, [task()])
    old = site.binding_for_task(ALICE, task())
    site.sync(BOB, [task(BOB)])
    new = site.binding_for_task(BOB, task(BOB))
    plan = service.preview_project_rename(old, task().title, merge_target_id=new)
    assert plan['kind'] == 'blocked'
    assert '原站' in plan['warning']
