"""匿名任务组后端契约：新轮次、原消息与不可变交付版本。"""
import sqlite3

import pytest

from desktop_service import DesktopService
from parser import ParsedProject


def groups(tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    assert hasattr(service, 'task_groups'), 'DesktopService must expose task_groups'
    return service, service.task_groups


def event(service, project_id=1):
    with service.db.connect() as connection:
        project = connection.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
        eid = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (?,?,'匿名批次','manual-files','anonymous/archive')", (project_id, project['name'])).lastrowid
        from task_groups import observe_delivery_event
        observe_delivery_event(connection, eid)
    return eid


def task(api, task_id):
    return next(t for g in api.snapshot(open_only=False) for t in g['tasks'] if t['id'] == task_id)


def test_numbering_duplicate_new_round_nonce_and_continuation(tmp_path):
    service, api = groups(tmp_path)
    first = api.receive('1. 匿名客户系统', request_id='one')
    duplicate = api.receive('1. 匿名客户系统')
    assert duplicate['duplicate'] and duplicate['group_id'] == first['group_id']
    second = api.receive('1. 匿名客户系统', new_round=True, request_id='two')
    assert second['group_id'] != first['group_id']
    assert api.receive('1. 匿名客户系统', new_round=True, request_id='two')['group_id'] == second['group_id']
    supplement = api.receive('1. 匿名客户系统\n2. 匿名合同工具', group_id=first['group_id'])
    assert len(api.snapshot(open_only=False)[1]['tasks']) == 2
    assert len(api.messages(first['group_id'])) == 2
    assert task(api, first['task_ids'][0])['code'].endswith('-01')
    assert len(supplement['task_ids']) == 2
    assert service.db.counts()['projects'] == 2


def test_pending_review_resolution_retains_group_and_original_message(tmp_path):
    service, api = groups(tmp_path)
    received = api.receive('请核对这份需求说明')
    assert received['task_ids'] == []
    assert api.snapshot()[0]['message_review_count'] == 1
    resolved = service.resolve_text(received['receipt_id'], [ParsedProject('匿名确认系统')])
    assert resolved['group_id'] == received['group_id']
    assert len(resolved['task_ids']) == 1
    assert api.messages(received['group_id'])[0]['raw_text'] == '请核对这份需求说明'
    assert api.snapshot()[0]['message_review_count'] == 0


def test_old_delivery_never_completes_new_task_and_explicit_binding_needs_confirmation(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名客户系统')
    old = event(service)
    received = api.receive('1. 匿名客户系统')
    tid = received['task_ids'][0]
    assert not task(api, tid)['material_ready']
    assert api.delivery_choices([tid])[0]['id'] == old
    with pytest.raises(ValueError):
        api.bind_delivery([tid], old)
    api.bind_delivery([tid], old, confirmed=True)
    assert task(api, tid)['material_ready']
    assert not task(api, tid)['handed_over']
    with pytest.raises(ValueError):
        api.handoff([tid])
    api.handoff([tid], confirmed=True)
    assert task(api, tid)['handed_over']


def test_observer_unique_then_void_restore_requires_new_binding_and_handoff(tmp_path):
    service, api = groups(tmp_path)
    tid = api.receive('1. 匿名客户系统')['task_ids'][0]
    eid = event(service)
    api.handoff([tid], confirmed=True)
    old_binding = task(api, tid)['binding_id']
    service.db.void_delivery_events([eid], confirmed=True)
    assert not task(api, tid)['material_ready']
    assert not task(api, tid)['handed_over']
    service.db.restore_delivery_events([eid], confirmed=True)
    assert not task(api, tid)['material_ready']
    api.bind_delivery([tid], eid, confirmed=True)
    assert task(api, tid)['binding_id'] != old_binding
    assert not task(api, tid)['handed_over']
    api.handoff([tid], confirmed=True)
    assert task(api, tid)['handed_over']
    assert len(api.actions(tid)) >= 2


def test_ambiguous_observation_never_guesses_after_task_withdrawn(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名客户系统')['task_ids'][0]
    two = api.receive('1. 匿名客户系统', new_round=True)['task_ids'][0]
    eid = event(service)
    assert task(api, one)['needs_review'] and task(api, two)['needs_review']
    api.withdraw([two], confirmed=True)
    from task_groups import observe_delivery_event
    with service.db.connect() as connection:
        observe_delivery_event(connection, eid)
    assert not task(api, one)['material_ready']
    api.bind_delivery([one], eid, confirmed=True)
    assert task(api, one)['material_ready']


def test_snapshot_is_read_only_and_history_import_is_explicit(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名历史系统')
    event(service)
    with service.db.connect() as connection:
        connection.execute('BEGIN')
        before = connection.total_changes
        assert api.snapshot(connection=connection) == []
        assert connection.total_changes == before
    preview = api.history_candidates()
    assert preview['rows'] and api.snapshot() == []
    with pytest.raises(ValueError):
        api.import_history(preview['token'])
    imported = api.import_history(preview['token'], confirmed=True)
    assert imported['group_ids']
    historical = api.snapshot()[0]['tasks'][0]
    assert historical['needs_review'] and not historical['material_ready']
    assert api.import_history(api.history_candidates()['token'], confirmed=True)['group_ids'] == []


def test_lifecycle_keeps_ids_and_invalidates_prior_handoff(tmp_path):
    service, api = groups(tmp_path)
    first = api.receive('1. 匿名客户系统')
    second = api.receive('1. 匿名合同工具')
    tid = first['task_ids'][0]
    eid = event(service)
    api.handoff([tid], confirmed=True)
    api.undo_handoff([tid], confirmed=True, reason='匿名更正')
    assert not task(api, tid)['handed_over']
    api.handoff([tid], confirmed=True)
    api.reopen([tid], confirmed=True)
    assert not task(api, tid)['material_ready']
    original_code = task(api, tid)['code']
    api.move_task(tid, second['group_id'], confirmed=True)
    assert task(api, tid)['code'] == original_code
    assert task(api, tid)['original_group_id'] == first['group_id']
    api.withdraw([tid], confirmed=True)
    assert task(api, tid)['withdrawn']
    api.reopen([tid], confirmed=True)
    assert not task(api, tid)['withdrawn']
    api.rename_group(second['group_id'], '匿名后续组')
    api.set_color(second['group_id'], '#507080')
    current = next(g for g in api.snapshot() if g['id'] == second['group_id'])
    assert current['title'] == '匿名后续组' and current['color'] == '#507080'


def test_cross_day_continuation_keeps_original_code_and_today_filter(tmp_path, monkeypatch):
    import task_groups
    service, api = groups(tmp_path)
    monkeypatch.setattr(task_groups, '_now', lambda: '2026-11-05T15:59:59Z')
    first = api.receive('1. 匿名跨日系统')
    assert first['group_code'] == 'G20261105-001'
    monkeypatch.setattr(task_groups, '_now', lambda: '2026-11-05T16:00:01Z')
    continued = api.receive('1. 匿名跨日系统\n2. 匿名续入工具', group_id=first['group_id'])
    second = api.receive('1. 匿名次日工具')
    assert continued['group_code'] == first['group_code']
    assert second['group_code'] == 'G20261106-001'
    assert len(api.snapshot(received_day='2026-11-06')) == 2
    assert all(first['group_code'] in task(api, tid)['code'] for tid in continued['task_ids'])
    # 编号不参与物理批次目录命名。
    with service.db.connect() as connection:
        assert not connection.execute("SELECT 1 FROM batches WHERE name LIKE 'G2026%'").fetchone()


def test_explicit_multi_task_binding_atomicity_and_immutable_logs(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名客户系统')['task_ids'][0]
    two = api.receive('1. 匿名客户系统', new_round=True)['task_ids'][0]
    other = api.receive('1. 匿名合同工具')['task_ids'][0]
    eid = event(service)
    with pytest.raises(ValueError):
        api.bind_delivery([one, other], eid, confirmed=True)
    assert not task(api, one)['material_ready']
    assert api.bind_delivery([one, two], eid, confirmed=True)['changed_count'] == 2
    api.handoff([one, two], confirmed=True)
    assert task(api, one)['handed_over'] and task(api, two)['handed_over']
    with service.db.connect() as connection:
        for table in ('task_groups', 'task_items', 'task_group_messages', 'task_actions',
                      'task_delivery_bindings', 'task_delivery_observations', 'task_message_links'):
            with pytest.raises(sqlite3.IntegrityError, match='仅允许追加'):
                connection.execute(f'DELETE FROM {table}')


def test_canonical_merge_preserves_distinct_tasks_and_name_snapshot(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名旧系统')['task_ids'][0]
    two = api.receive('1. 匿名新系统')['task_ids'][0]
    # 匿名夹具只追加与正式合并相同的canonical身份关系。
    with service.db.connect() as connection:
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (1,2,'anonymous-merge','{}')")
    assert task(api, one)['project_id'] == task(api, two)['project_id'] == 2
    assert task(api, one)['original_project_name'] == '匿名旧系统'
    eid = event(service, 2)
    assert not task(api, one)['material_ready'] and not task(api, two)['material_ready']
    api.bind_delivery([one], eid, confirmed=True)
    assert not task(api, two)['material_ready']


def test_history_preview_rejects_changes_without_mutating_legacy_rows(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名历史系统')
    preview = api.history_candidates()
    service.receive_text('1. 匿名新增历史工具')
    with pytest.raises(ValueError, match='重新预览'):
        api.import_history(preview['token'], confirmed=True)
    assert api.snapshot() == []
    with service.db.connect() as connection:
        before = {table: [tuple(r) for r in connection.execute(f'SELECT * FROM {table}')]
                  for table in ('batches', 'messages', 'projects', 'delivery_events')}
    api.import_history(api.history_candidates()['token'], confirmed=True)
    with service.db.connect() as connection:
        after = {table: [tuple(r) for r in connection.execute(f'SELECT * FROM {table}')]
                 for table in before}
    assert before == after


def test_imported_history_duplicate_locates_group_and_reused_projects_are_candidates(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名历史系统')
    service.receive_text('项目名称：匿名历史系统')
    preview = api.history_candidates()
    assert len(preview['rows']) == 2
    imported = api.import_history(preview['token'], confirmed=True)
    assert len(imported['group_ids']) == 2
    duplicate = api.receive('1. 匿名历史系统')
    assert duplicate['duplicate']
    assert duplicate['group_id'] in imported['group_ids']
    assert duplicate['task_ids']


def test_snapshot_supplied_connection_requires_current_database_and_transaction(tmp_path):
    service, api = groups(tmp_path)
    api.receive('1. 匿名客户系统')
    with service.db.connect() as connection:
        with pytest.raises(ValueError, match='事务'):
            api.snapshot(connection=connection)
        connection.execute('BEGIN')
        assert api.snapshot(connection=connection)
    other = DesktopService(tmp_path / 'other' / 'workbox.db')
    with other.db.connect() as connection:
        connection.execute('BEGIN')
        with pytest.raises(ValueError, match='当前'):
            api.snapshot(connection=connection)


def test_duplicate_request_nonce_is_durable_and_rejects_conflicting_retry(tmp_path):
    service, api = groups(tmp_path)
    first = api.receive('1. 匿名客户系统')
    duplicate = api.receive('1. 匿名客户系统', request_id='duplicate-click')
    reopened = DesktopService(service.db.path).task_groups
    assert reopened.receive('1. 匿名客户系统', request_id='duplicate-click')['group_id'] == first['group_id']
    with pytest.raises(ValueError, match='重试标识'):
        reopened.receive('1. 匿名其他系统', request_id='duplicate-click')


def test_reopen_does_not_bind_an_unobserved_pre_reopen_delivery(tmp_path):
    service, api = groups(tmp_path)
    tid = api.receive('1. 匿名客户系统')['task_ids'][0]
    with service.db.connect() as connection:
        eid = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (1,'匿名客户系统','匿名批次','manual-files','anonymous/archive')").lastrowid
    api.reopen([tid], confirmed=True)
    from task_groups import observe_delivery_event
    with service.db.connect() as connection:
        observe_delivery_event(connection, eid)
    assert not task(api, tid)['material_ready']
    api.bind_delivery([tid], eid, confirmed=True)
    assert task(api, tid)['material_ready']


def test_history_multiple_messages_in_one_batch_keep_exact_task_sources(tmp_path):
    service, api = groups(tmp_path)
    service.capture_message('1. 匿名甲系统', '匿名同批次')
    service.capture_message('1. 匿名乙系统', '匿名同批次')
    preview = api.history_candidates()
    assert len(preview['rows']) == 1
    imported = api.import_history(preview['token'], confirmed=True)
    messages = api.messages(imported['group_ids'][0])
    assert len(messages) == 2
    assert messages[0]['task_ids'] != messages[1]['task_ids']
    assert len(messages[0]['task_ids']) == len(messages[1]['task_ids']) == 1
    assert api.receive('1. 匿名甲系统')['task_ids'] == messages[0]['task_ids']


def test_history_messages_preserve_their_individual_receiving_dates(tmp_path):
    service, api = groups(tmp_path)
    service.capture_message('1. 匿名甲系统', '匿名跨日旧批次')
    service.capture_message('1. 匿名乙系统', '匿名跨日旧批次')
    with service.db.connect() as connection:
        connection.execute("UPDATE batches SET created_at='2026-10-01T01:00:00Z'")
        connection.execute("UPDATE messages SET created_at='2026-10-01T01:00:00Z' WHERE id=1")
        connection.execute("UPDATE messages SET created_at='2026-10-02T01:00:00Z' WHERE id=2")
    imported = api.import_history(api.history_candidates()['token'], confirmed=True)
    messages = api.messages(imported['group_ids'][0])
    assert messages[0]['received_at'] == '2026-10-01T01:00:00Z'
    assert messages[1]['received_at'] == '2026-10-02T01:00:00Z'
    assert len(api.snapshot(received_day='2026-10-02')) == 1


def test_explicit_old_version_resolves_known_ambiguity_but_not_future_ambiguity(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名客户系统')
    older = event(service)
    one = api.receive('1. 匿名客户系统')['task_ids'][0]
    two = api.receive('1. 匿名客户系统', new_round=True)['task_ids'][0]
    newer = event(service)
    assert newer > older and task(api, one)['needs_review']
    api.bind_delivery([one], older, confirmed=True)
    selected = task(api, one)
    assert selected['material_ready'] and not selected['needs_review']
    assert not selected['handed_over'] and task(api, two)['needs_review']
    # 明确选择旧版本只解决当时已知的歧义，不掩盖后来出现的新交付。
    newest = event(service)
    assert newest > newer and task(api, one)['needs_review']
    original_binding = task(api, one)['binding_id']
    api.bind_delivery([one], older, confirmed=True)
    selected = task(api, one)
    assert selected['binding_id'] != original_binding
    assert not selected['needs_review'] and selected['material_ready']
    api.handoff([one], confirmed=True)
    assert task(api, one)['handed_over']
    # 清除待核对与人工交接不能由快照读取产生任何写入。
    with service.db.connect() as connection:
        connection.execute('BEGIN')
        before = connection.total_changes
        api.snapshot(connection=connection, open_only=False)
        assert connection.total_changes == before


def test_observation_floor_schema_upgrade_preserves_old_binding_rows(tmp_path):
    from task_schema import initialize
    from task_groups import read_task_snapshot
    service, api = groups(tmp_path)
    api.receive('1. 匿名客户系统')
    event(service)
    with service.db.connect() as connection:
        columns = {r['name'] for r in connection.execute('PRAGMA table_info(task_delivery_bindings)')}
        if 'observed_event_floor' in columns:
            connection.execute('ALTER TABLE task_delivery_bindings DROP COLUMN observed_event_floor')
        before = [tuple(r) for r in connection.execute('SELECT * FROM task_delivery_bindings')]
        connection.execute('BEGIN')
        # 独立读取器仍能只读展示早期开发schema，不能为导出补列。
        assert read_task_snapshot(connection)[0]['tasks'][0]['material_ready']
        connection.commit()
        initialize(connection)
        columns = [r['name'] for r in connection.execute('PRAGMA table_info(task_delivery_bindings)')]
        assert 'observed_event_floor' in columns
        rows = [dict(r) for r in connection.execute('SELECT * FROM task_delivery_bindings')]
        assert [tuple(value for key, value in r.items() if key != 'observed_event_floor') for r in rows] == before
        assert rows[0]['observed_event_floor'] == 0


def test_handoff_rejects_binding_changed_after_confirmation_was_shown(tmp_path):
    service, api = groups(tmp_path)
    tid = api.receive('1. 匿名客户系统')['task_ids'][0]
    event(service)
    displayed_binding = task(api, tid)['binding_id']
    event(service)
    assert task(api, tid)['binding_id'] != displayed_binding
    with pytest.raises(ValueError, match='版本.*变化'):
        api.handoff([tid], confirmed=True, expected_bindings={tid: displayed_binding})
    assert not task(api, tid)['handed_over'] and api.actions(tid) == []


def test_handoff_expected_binding_batch_is_atomic_complete_and_idempotent(tmp_path):
    service, api = groups(tmp_path)
    one, two = api.receive('1. 匿名客户系统\n2. 匿名合同工具')['task_ids']
    event(service, 1)
    event(service, 2)
    expected = {i: task(api, i)['binding_id'] for i in (one, two)}
    event(service, 2)
    with pytest.raises(ValueError, match='版本.*变化'):
        api.handoff([one, two], confirmed=True, expected_bindings=expected)
    assert api.actions(one) == api.actions(two) == []
    for malformed in ({}, {one: expected[one]}, {**expected, 999: 1}, {'1': expected[one], two: expected[two]},
                      {one: True, two: task(api, two)['binding_id']}):
        with pytest.raises(ValueError, match='版本.*完整|版本.*无效'):
            api.handoff([one, two], confirmed=True, expected_bindings=malformed)
        assert api.actions(one) == api.actions(two) == []
    current = {i: task(api, i)['binding_id'] for i in (one, two)}
    assert api.handoff([one, two], confirmed=True, expected_bindings=current)['changed_count'] == 2
    assert api.handoff([one, two], confirmed=True, expected_bindings=current)['changed_count'] == 0
    assert len(api.actions(one)) == len(api.actions(two)) == 1


def test_handoff_can_confirm_exact_ready_old_binding_despite_newer_candidates(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名客户系统')['task_ids'][0]
    event(service)
    selected_binding = task(api, one)['binding_id']
    api.receive('1. 匿名客户系统', new_round=True)
    event(service)
    selected = task(api, one)
    assert selected['needs_review'] and selected['material_ready']
    assert selected['binding_id'] == selected_binding
    api.handoff([one], confirmed=True, expected_bindings={one: selected_binding})
    assert task(api, one)['handed_over']


@pytest.mark.parametrize('archive_path', ['', '   '])
def test_legacy_delivery_without_archive_cannot_make_task_material_ready(tmp_path, archive_path):
    from task_groups import observe_delivery_event
    service, api = groups(tmp_path)
    tid = api.receive('1. 匿名客户系统')['task_ids'][0]
    with service.db.connect() as connection:
        eid = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (1,'匿名客户系统','匿名批次','legacy',?)", (archive_path,)).lastrowid
        observe_delivery_event(connection, eid)
    assert not task(api, tid)['material_ready']
    assert eid not in {row['id'] for row in api.delivery_choices([tid])}
    with pytest.raises(ValueError, match='收录材料'):
        api.bind_delivery([tid], eid, confirmed=True)
    assert not task(api, tid)['material_ready']
    # 旧迁移事实和项目计数保持，任务材料就绪是独立口径。
    history = service.db.delivery_history()
    assert len(history['events']) == 1 and history['events'][0]['id'] == eid
    assert not history['events'][0]['voided']


def test_nonempty_legacy_archive_can_still_be_explicitly_selected_without_disk_checks(tmp_path):
    service, api = groups(tmp_path)
    service.receive_text('1. 匿名客户系统')
    with service.db.connect() as connection:
        eid = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (1,'匿名客户系统','匿名批次','legacy','旧记录/归档位置')").lastrowid
    tid = api.receive('1. 匿名客户系统')['task_ids'][0]
    assert not task(api, tid)['material_ready']
    assert eid in {row['id'] for row in api.delivery_choices([tid])}
    api.bind_delivery([tid], eid, confirmed=True)
    assert task(api, tid)['material_ready'] and task(api, tid)['archive_path'] == '旧记录/归档位置'


def test_pasted_internal_task_code_locates_group_without_new_project_or_reopening(tmp_path):
    service, api = groups(tmp_path)
    first = api.receive('1. 匿名客户系统')
    tid = first['task_ids'][0]
    event(service)
    api.handoff([tid], confirmed=True)
    before = service.db.counts()
    copied = task(api, tid)['code'] + '  匿名客户系统'
    received = api.receive(copied, request_id='internal-code')
    assert received['state'] == 'committed' and received['group_id'] == first['group_id']
    assert received['task_ids'] == [tid] and task(api, tid)['handed_over']
    assert service.db.counts() == before
    assert api.messages(first['group_id'])[-1]['raw_text'] == copied
    assert api.receive(copied, request_id='internal-code')['group_id'] == first['group_id']
    new = api.receive(copied, new_round=True, request_id='internal-new-round')
    assert new['group_id'] != first['group_id'] and new['task_ids'] != [tid]
    assert new['project_ids'] == first['project_ids']
    assert not task(api, new['task_ids'][0])['material_ready']
    assert service.db.counts() == before


def test_unknown_conflicting_or_mixed_internal_codes_are_reviewed_without_guesses(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名甲系统')
    two = api.receive('1. 匿名乙系统')
    one_code, two_code = task(api, one['task_ids'][0])['code'], task(api, two['task_ids'][0])['code']
    for raw in ('G19990101-999-99  匿名未知系统', one_code + '  匿名错误名称',
                one_code + '  匿名甲系统\n' + two_code + '  匿名乙系统'):
        result = api.receive(raw)
        assert result['state'] == 'needs-review' and result['project_ids'] == result['task_ids'] == []
        assert api.messages(result['group_id'])[0]['raw_text'] == raw
        assert service.db.counts()['projects'] == 2
    mixed = one_code + '  匿名甲系统\n' + two_code + '  匿名乙系统'
    explicit = api.receive(mixed, group_id=one['group_id'])
    assert explicit['state'] == 'committed' and explicit['group_id'] == one['group_id']
    assert service.db.counts()['projects'] == 2


def test_internal_codes_match_original_or_current_names_and_keep_exact_task_source(tmp_path):
    service, api = groups(tmp_path)
    one = api.receive('1. 匿名原名系统')
    two = api.receive('1. 匿名原名系统', new_round=True)
    first, second = one['task_ids'][0], two['task_ids'][0]
    api.move_task(second, one['group_id'], confirmed=True)
    with service.db.connect() as connection:
        connection.execute("UPDATE projects SET name='匿名现名系统' WHERE id=1")
    raw = task(api, second)['code'] + '  匿名现名系统'
    result = api.receive(raw)
    assert result['group_id'] == one['group_id'] and result['task_ids'] == [second]
    old_name_result = api.receive(task(api, first)['code'] + '  匿名原名系统')
    assert old_name_result['task_ids'] == [first]
    assert service.db.counts()['projects'] == 1


def test_history_candidates_exclude_modern_sources_but_keep_unmapped_messages_in_same_batch(tmp_path):
    service, api = groups(tmp_path)
    modern = api.receive('1. 匿名现代系统')
    assert api.history_candidates()['rows'] == []
    service.capture_message('1. 匿名未分组工具', modern['batch_name'])
    preview = api.history_candidates()
    candidate, = preview['rows']
    assert [m['raw_text'] for m in candidate['messages']] == ['1. 匿名未分组工具']
    assert [p['name'] for p in candidate['projects']] == ['匿名未分组工具']
    imported = api.import_history(preview['token'], confirmed=True)
    new_group = next(g for g in api.snapshot(open_only=False) if g['id'] == imported['group_ids'][0])
    assert [t['project_name'] for t in new_group['tasks']] == ['匿名未分组工具']
    assert len(api.messages(modern['group_id'])) == 1
    assert api.history_candidates()['rows'] == []


def test_history_import_can_append_only_new_sources_to_previously_imported_batch(tmp_path):
    service, api = groups(tmp_path)
    service.capture_message('1. 匿名旧来源系统', '匿名旧混合批次')
    first = api.import_history(api.history_candidates()['token'], confirmed=True)
    group_id = first['group_ids'][0]
    before_task = api.snapshot()[0]['tasks'][0]
    service.capture_message('1. 匿名后补来源工具', '匿名旧混合批次')
    preview = api.history_candidates()
    candidate, = preview['rows']
    assert [m['raw_text'] for m in candidate['messages']] == ['1. 匿名后补来源工具']
    api.import_history(preview['token'], confirmed=True)
    current, = api.snapshot()
    assert current['id'] == group_id and len(current['tasks']) == 2
    assert current['tasks'][0]['id'] == before_task['id']
    assert [m['sequence'] for m in api.messages(group_id)] == [1, 2]
    assert api.history_candidates()['rows'] == []
