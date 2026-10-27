from pathlib import Path
import sqlite3
from threading import Event
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def configure(service, **changes):
    settings = service.workspaces.settings()
    settings.update(changes)
    return service.workspaces.configure(**settings)


def package(path, members=4, *, names=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        for name in names or [f'{index}.txt' for index in range(members)]:
            archive.writestr(name, 'isolated')
    return path


def project(service, name='甲系统'):
    return service.capture_message(name, name)['project_ids'][0]


def test_defaults_persist_independently_and_legacy_configure_preserves_policy(service, tmp_path):
    defaults = service.workspaces.settings()
    assert {key: defaults[key] for key in ('auto_create_projects', 'auto_deliver_zip', 'zip_delivery_min_files')} == {
        'auto_create_projects': True, 'auto_deliver_zip': True, 'zip_delivery_min_files': 4}
    configured = configure(service, auto_create_projects=False, auto_deliver_zip=False,
                           zip_delivery_min_files=7, stable_seconds=15)
    assert DesktopService(service.db.path).workspaces.settings() == configured
    other = DesktopService(tmp_path / 'other-device' / 'workbox.db')
    assert other.workspaces.settings()['auto_create_projects'] is True
    saved = service.workspaces.configure(tmp_path / 'next-work', tmp_path / 'next-archive', 20)
    assert saved['auto_create_projects'] is False and saved['auto_deliver_zip'] is False
    assert saved['zip_delivery_min_files'] == 7


@pytest.mark.parametrize('field,value', [
    ('auto_create_projects', 0), ('auto_create_projects', 'false'), ('auto_deliver_zip', 1),
    ('auto_deliver_zip', []), ('zip_delivery_min_files', True), ('zip_delivery_min_files', 0),
    ('zip_delivery_min_files', 10001), ('zip_delivery_min_files', 4.0), ('zip_delivery_min_files', '4'),
])
def test_invalid_policy_is_atomic_and_creates_no_directories(service, tmp_path, field, value):
    before = service.workspaces.settings()
    work, archive = tmp_path / 'invalid-work', tmp_path / 'invalid-archive'
    with pytest.raises(ValueError):
        service.workspaces.configure(work, archive, 15, **{field: value})
    assert service.workspaces.settings() == before
    assert not work.exists() and not archive.exists()


@pytest.mark.parametrize('kind', ['ordinary', 'zip', 'bundle', 'folder'])
def test_disabled_creation_keeps_unknown_intake_for_choice(service, tmp_path, kind):
    configure(service, auto_create_projects=False)
    if kind == 'ordinary':
        source = tmp_path / '未知系统.txt'
        source.write_text('isolated')
    elif kind == 'zip':
        source = package(tmp_path / '未知系统.zip')
    elif kind == 'bundle':
        source = package(tmp_path / '未知系统-2.zip', names=['未知系统/a.txt', '第二系统/a.txt'])
    else:
        source = tmp_path / '未知系统'
        source.mkdir()
        (source / '说明.txt').write_text('isolated')
    result = service.receive_path(source)
    assert not service.list_projects()
    assert result.get('intake_choice') or result.get('needs_choice')
    assert source.exists()
    assert '先创建' in str(result)


def test_creation_disabled_allows_text_named_and_explicit_intake(service, tmp_path):
    configure(service, auto_create_projects=False)
    pid = project(service)
    source = tmp_path / '甲系统_操作手册.txt'
    source.write_text('isolated')
    result = service.receive_path(source)
    assert result['file']['project_id'] == pid
    unknown = tmp_path / '说明.txt'
    unknown.write_text('isolated second')
    assert service.receive_path(unknown, pid, manual=True)['file']['project_id'] == pid
    assert len(service.list_projects()) == 1
    assert service.get_project(pid)['status'] == '待交付'


@pytest.mark.parametrize('automatic,minimum,count,expected', [
    (False, 4, 8, '待交付'), (True, 6, 5, '待交付'), (True, 6, 6, '已交付'),
    (True, 1, 1, '已交付'), (True, 10000, 4, '待交付'),
])
def test_new_zip_decision_uses_persisted_policy(service, tmp_path, automatic, minimum, count, expected):
    pid = project(service)
    configure(service, auto_deliver_zip=automatic, zip_delivery_min_files=minimum)
    source = package(tmp_path / '甲系统.zip', count)
    saved = service.snapshots.archive_now(source, pid)['zip']
    assert Path(saved['path']).read_bytes() == source.read_bytes()
    assert service.get_project(pid)['status'] == expected
    fact, = service.db.get_zip_file_counts(saved['id'])
    assert fact['auto_deliver_zip'] is automatic
    assert fact['zip_delivery_min_files'] == minimum
    assert fact['decided_status'] == expected
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM delivery_events').fetchone()[0] == int(expected == '已交付')
    if not automatic:
        service.confirm_delivery(pid, saved['id'], confirmed=True)
        assert service.get_project(pid)['status'] == '已交付'


def test_bundle_counts_use_each_project_and_disabled_creation_allows_matches(service, tmp_path):
    ids = service.capture_message('1. 甲系统\n2. 乙系统', '本批')['project_ids']
    configure(service, auto_create_projects=False, zip_delivery_min_files=5)
    source = package(tmp_path / '甲系统-2.zip', names=[
        *[f'甲系统/sub/{n}.txt' for n in range(5)], *[f'乙系统/{n}.txt' for n in range(4)]])
    assert service.receive_path(source)['watching']
    service.scan_archives(0)
    assert service.scan_archives(10)['archived']
    assert [service.get_project(pid)['status'] for pid in ids] == ['已交付', '待交付']


def test_policy_changes_do_not_replay_existing_facts_or_relink(service, tmp_path):
    pid = project(service)
    configure(service, auto_deliver_zip=False)
    source = package(tmp_path / '甲系统.zip')
    record = service.snapshots.archive_now(source, pid)['zip']
    old = service.db.get_zip_file_counts(record['id'])
    configure(service, auto_deliver_zip=True, zip_delivery_min_files=1)
    assert service.get_project(pid)['status'] == '待交付'
    assert service.snapshots.archive_now(source, pid)['duplicate']
    service.link_zip(record['id'], pid)
    assert service.db.get_zip_file_counts(record['id']) == old
    assert service.get_project(pid)['status'] == '待交付'
    package(source, 5)
    new = service.snapshots.archive_now(source, pid)['zip']
    assert new['id'] != record['id'] and service.get_project(pid)['status'] == '已交付'
    assert service.db.get_zip_file_counts(record['id']) == old


def test_relink_preserves_original_decision_and_uses_current_policy_for_new_project(service, tmp_path):
    a, b = service.capture_message('1. 甲系统\n2. 乙系统', '本批')['project_ids']
    configure(service, auto_deliver_zip=False, zip_delivery_min_files=3)
    record = service.snapshots.archive_now(package(tmp_path / '材料.zip', 2), a)['zip']
    original = service.db.get_zip_file_counts(record['id'])[0]
    configure(service, auto_deliver_zip=True, zip_delivery_min_files=2)
    service.link_zip(record['id'], b)
    assert service.get_project(b)['status'] == '已交付'
    facts = service.db.get_zip_file_counts(record['id'], include_unlinked=True)
    assert len(facts) == 2 and facts[0] == original
    assert service.db.get_zip_file_counts(record['id'])[0]['project_id'] == b


def test_bundle_rerecognition_preserves_original_decisions(service, tmp_path):
    ids = service.capture_message('1. 甲系统\n2. 乙系统', '本批')['project_ids']
    configure(service, auto_deliver_zip=False)
    source = package(tmp_path / '甲系统-2.zip', names=[
        *[f'甲系统/{n}.txt' for n in range(4)], *[f'乙系统/{n}.txt' for n in range(4)]])
    record = service.snapshots.archive_now(source)['zip']
    old = service.db.get_zip_file_counts(record['id'])
    configure(service, auto_deliver_zip=True)
    service.snapshots.recognize_bundle(record['id'])
    assert service.db.get_zip_file_counts(record['id']) == old
    assert [service.get_project(pid)['status'] for pid in ids] == ['待交付', '待交付']


def test_different_named_identical_ordinary_files_are_both_preserved(service, tmp_path):
    pid = project(service)
    application = tmp_path / '甲系统_申请表.docx'
    manual = tmp_path / '甲系统_操作手册.docx'
    application.write_bytes(b'identical isolated bytes')
    manual.write_bytes(application.read_bytes())
    first = service.receive_path(application)
    second = service.receive_path(manual)
    assert not first['duplicate'] and not second['duplicate']
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert (target / application.name).read_bytes() == application.read_bytes()
    assert (target / manual.name).read_bytes() == manual.read_bytes()
    repeated = service.receive_path(manual)
    assert repeated['duplicate'] and repeated['file']['id'] == second['file']['id']


def test_disabling_auto_delivery_never_replays_old_delivered_decision(service, tmp_path):
    pid = project(service)
    source = package(tmp_path / '甲系统.zip')
    record = service.snapshots.archive_now(source, pid)['zip']
    old = service.db.get_zip_file_counts(record['id'])
    material = tmp_path / '甲系统_申请表.docx'
    material.write_bytes(b'new work')
    service.receive_path(material)
    assert service.get_project(pid)['status'] == '待交付'
    configure(service, auto_deliver_zip=False)
    with service.db.connect() as connection:
        events = connection.execute('SELECT count(*) FROM delivery_events').fetchone()[0]
    service.snapshots.archive_now(source, pid)
    service.link_zip(record['id'], pid)
    assert service.get_project(pid)['status'] == '待交付'
    assert service.db.get_zip_file_counts(record['id']) == old
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM delivery_events').fetchone()[0] == events


def test_failed_save_rolls_back_all_settings(service, tmp_path):
    before = configure(service, auto_create_projects=False)
    with service.db.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_policy BEFORE UPDATE ON workflow_settings
            WHEN NEW.key='zip_delivery_min_files' BEGIN SELECT RAISE(ABORT,'isolated rejection'); END""")
    with pytest.raises(sqlite3.DatabaseError, match='isolated rejection'):
        configure(service, auto_create_projects=True, auto_deliver_zip=False,
                  zip_delivery_min_files=9, stable_seconds=15,
                  work_root=tmp_path / 'changed-work', archive_root=tmp_path / 'changed-archive')
    assert service.workspaces.settings() == before


def test_legacy_count_fact_is_not_backfilled_when_settings_change(service, tmp_path):
    pid = project(service)
    source = package(tmp_path / '甲系统.zip')
    record = service.snapshots.archive_now(source, pid)['zip']
    with service.db.connect() as connection:
        connection.execute('DELETE FROM zip_file_count_policies WHERE zip_id=?', (record['id'],))
    before = service.db.get_zip_file_counts(record['id'])
    configure(service, auto_deliver_zip=False)
    service.snapshots.archive_now(source, pid)
    restarted = DesktopService(service.db.path)
    assert restarted.db.get_zip_file_counts(record['id']) == before
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM zip_file_count_policies WHERE zip_id=?', (record['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('protected', ['archived', 'deleted'])
def test_disabled_creation_respects_protected_project_names(service, tmp_path, protected):
    pid = project(service)
    configure(service, auto_create_projects=False)
    if protected == 'archived':
        service.bulk_update_projects([pid], archived=True)
    else:
        service.delete_project(pid, confirmed=True)
    source = tmp_path / '甲系统_操作手册.docx'
    source.write_bytes(b'isolated')
    assert service.receive_path(source)['intake_choice']
    assert len(service.list_projects(archived=None, deleted=None)) == 1
    assert source.read_bytes() == b'isolated'


def test_disabled_creation_folder_cancel_and_manual_retry_keep_prior_success(service, tmp_path):
    from folder_intake import FolderIntake
    pid = project(service)
    configure(service, auto_create_projects=False)
    root = tmp_path / 'source' / '甲系统'
    root.mkdir(parents=True)
    for name in ('a.txt', 'b.txt'):
        (root / name).write_bytes(name.encode())
    event = Event()
    def progress(done, total):
        if done == 1:
            event.set()
    cancelled = service.receive_path(root, cancel_event=event, progress=progress)
    assert cancelled['cancelled'] and cancelled['copied'] == 1
    retried = service.receive_path(root)
    assert retried['duplicate'] == 1 and retried['copied'] == 1
    other = tmp_path / 'unknown'
    (other / '文档').mkdir(parents=True)
    (other / '文档' / '说明.txt').write_bytes(b'choice')
    unresolved = service.receive_path(other)
    item, = unresolved['items']
    assert unresolved['needs_choice'] == 1
    result = FolderIntake(service).receive_item(item, pid)
    assert result['copied'] == 1
    target = Path(service.workspaces.get_workspace(pid)['path']) / '文档' / '说明.txt'
    assert target.read_bytes() == b'choice'


def test_disabled_delivery_failure_retry_preserves_source_and_one_archive(service, tmp_path, monkeypatch):
    pid = project(service)
    configure(service, auto_deliver_zip=False)
    source = package(tmp_path / '甲系统.zip')
    apply = service.db._apply_zip_file_counts
    def reject(*args, **kwargs):
        raise RuntimeError('isolated failure')
    monkeypatch.setattr(service.db, '_apply_zip_file_counts', reject)
    with pytest.raises(RuntimeError, match='isolated failure'):
        service.snapshots.archive_now(source, pid)
    assert service.db.counts()['zips'] == 0
    copies = list(Path(service.workspaces.settings()['archive_root']).rglob('*.zip'))
    assert len(copies) == 1 and copies[0].read_bytes() == source.read_bytes()
    monkeypatch.setattr(service.db, '_apply_zip_file_counts', apply)
    service.scan_archives(0)
    assert service.db.counts()['zips'] == 1
    assert service.get_project(pid)['status'] == '待交付'
    assert len(list(Path(service.workspaces.settings()['archive_root']).rglob('*.zip'))) == 1
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM delivery_events').fetchone()[0] == 0


def test_disabled_creation_partial_bundle_waits_for_choice(service, tmp_path):
    project(service)
    configure(service, auto_create_projects=False)
    source = package(tmp_path / '甲系统-2.zip', names=['甲系统/a.txt', '乙系统/a.txt'])
    result = service.receive_path(source)
    assert result.get('intake_choice')
    assert len(service.list_projects()) == 1
    assert service.snapshots.list_sources() == []


def test_folder_rechecks_creation_switch_at_bundle_dispatch(service, tmp_path, monkeypatch):
    root = tmp_path / '本次材料'
    source = package(root / '甲系统-2.zip', names=['甲系统/a.txt', '乙系统/a.txt'])
    receive = service.receive_file
    def switch_before_receive(*args, **kwargs):
        configure(service, auto_create_projects=False)
        return receive(*args, **kwargs)
    monkeypatch.setattr(service, 'receive_file', switch_before_receive)
    result = service.receive_path(root)
    assert result['needs_choice'] == 1 and result['watching'] == 0
    assert not service.list_projects()
    assert source.exists()
