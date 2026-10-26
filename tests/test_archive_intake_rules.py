from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    result = DesktopService(tmp_path/'data/workbox.db')
    result.receive_text('1. 甲平台\n2. 乙平台')
    return result


def archive(path, names):
    with ZipFile(path, 'w') as stream:
        for index, name in enumerate(names):
            stream.writestr(name, '' if name.endswith('/') else f'隔离内容 {index}')
    return path


def test_zip_preserves_exact_original_filename_in_distinct_version_directories(service, tmp_path):
    path = archive(tmp_path/'甲平台_最终成果.ZIP', ['src/a.txt', 'src/b.txt', 'src/c.txt', 'src/d.txt'])
    first = service.snapshots.archive_now(path, 1)['zip']
    assert Path(first['path']).name == path.name
    original = Path(first['path']).read_bytes()
    with ZipFile(path, 'a') as stream:
        stream.writestr('src/后续调整.txt', '新内容')
    second = service.snapshots.archive_now(path, 1)['zip']
    assert Path(second['path']).name == path.name
    assert Path(first['path']).parent != Path(second['path']).parent
    assert Path(first['path']).read_bytes() == original and path.is_file()


@pytest.mark.parametrize('count,state', [(0,'待交付'), (3,'待交付'), (4,'已交付')])
def test_single_project_counts_files_recursively_not_directories(service, tmp_path, count, state):
    path = archive(tmp_path/'甲平台.zip', ['src/', 'src/sub/', *[f'src/sub/{i}.txt' for i in range(count)]])
    result = service.receive_file(path)
    assert result['watching']
    assert service.get_project(1)['status'] == '待开发'
    service.scan_archives(now=0)
    saved = service.scan_archives(now=11)['archived'][0]
    project = service.get_project(1)
    assert project['status'] == state
    assert project['delivered_zip_id'] == (saved['id'] if count > 3 else None)
    facts = service.db.get_zip_file_counts(saved['id'])
    assert [(r['project_id'], r['file_count'], r['decided_status']) for r in facts] == [(1,count,state)]


def test_bundle_uses_per_project_counts_not_archive_total(service, tmp_path):
    names = ['资料/甲平台/', '资料/乙平台/', '资料/共同说明.txt']
    names += [f'资料/甲平台/成果/{i}.txt' for i in range(4)]
    names += [f'资料/乙平台/成果/{i}.txt' for i in range(3)]
    path = archive(tmp_path/'甲平台-2-G.zip', names)
    saved = service.import_zip(path)['zip']
    assert service.get_project(1)['status'] == '已交付'
    assert service.get_project(2)['status'] == '待交付'
    assert {r['project_id']:r['file_count'] for r in service.db.get_zip_file_counts(saved['id'])} == {1:4,2:3}
    assert Path(saved['path']).name == path.name


def test_empty_named_project_has_zero_files_without_blocking_other_project(service, tmp_path):
    path = archive(tmp_path/'甲平台-2-G.zip', ['甲平台/', '乙平台/', *[f'甲平台/{i}.txt' for i in range(4)]])
    saved = service.import_zip(path)['zip']
    assert service.get_project(1)['status'] == '已交付'
    assert service.get_project(2)['status'] == '待交付'
    assert {r['project_id']:r['file_count'] for r in service.db.get_zip_file_counts(saved['id'])} == {1:4,2:0}


def test_repeat_drop_reevaluates_same_zip_after_work_file_returns_project_to_pending(service, tmp_path):
    path = archive(tmp_path/'甲平台.zip', [f'{i}.txt' for i in range(4)])
    first = service.import_zip(path)['zip']
    service.snapshots.approve_version(first['id'])
    work = tmp_path/'甲平台_代码（C#）.txt'
    work.write_text('调整', encoding='utf-8')
    service.receive_file(work)
    assert service.get_project(1)['status'] == '待交付'
    service.receive_file(path)
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(1)['status'] == '已交付'
    assert service.get_project(1)['delivered_zip_id'] == first['id']
    assert service.db.counts()['zips'] == 1
    assert service.db.approved_zip_for_project(1) == first['id']


def test_same_content_with_different_filename_keeps_each_original_name(service, tmp_path):
    original = archive(tmp_path/'甲平台.zip', [f'{i}.txt' for i in range(4)])
    other = tmp_path/'甲平台_原样副本.ZIP'
    other.write_bytes(original.read_bytes())
    first = service.snapshots.archive_now(original, 1)['zip']
    second = service.snapshots.archive_now(other, 1)['zip']
    assert Path(first['path']).name == original.name
    assert Path(second['path']).name == other.name
    assert first['id'] != second['id']


def test_case_only_source_rename_preserves_current_extension_spelling(service, tmp_path):
    original = archive(tmp_path/'甲平台.zip', [f'{i}.txt' for i in range(4)])
    service.snapshots.archive_now(original, 1)
    renamed = original.with_suffix('.ZIP')
    original.rename(renamed)
    second = service.snapshots.archive_now(renamed, 1)['zip']
    assert Path(second['path']).name == renamed.name


def test_unmatched_package_waits_until_explicit_project_link(service, tmp_path):
    path = archive(tmp_path/'未知包.zip', [f'{i}.txt' for i in range(4)])
    saved = service.import_zip(path)['zip']
    assert saved['project_id'] is None and service.get_project(1)['status'] == '待开发'
    service.link_zip(saved['id'], 1)
    assert service.get_project(1)['status'] == '已交付'
    assert service.db.get_zip_file_counts(saved['id'])[0]['file_count'] == 4


def test_relinking_pending_single_zip_replaces_current_count_facts(service, tmp_path):
    saved = service.snapshots.archive_now(archive(tmp_path/'资料.zip', ['说明.txt']), 1)['zip']
    service.link_zip(saved['id'], 2)
    assert [row['project_id'] for row in service.db.get_zip_file_counts(saved['id'])] == [2]


def test_bad_or_ambiguous_bundle_never_auto_delivers(service, tmp_path):
    path = archive(tmp_path/'甲平台-2-G.zip', [f'甲平台/{i}.txt' for i in range(4)])
    saved = service.import_zip(path)['zip']
    assert service.db.get_zip_bundle(saved['id'])['state'] == 'needs-review'
    assert service.db.get_zip_file_counts(saved['id']) == []
    assert [p['status'] for p in service.list_projects()] == ['待开发', '待开发']


def test_policy_failure_keeps_original_and_recovers_the_one_named_copy(service, tmp_path, monkeypatch):
    path = archive(tmp_path/'甲平台.zip', [f'{i}.txt' for i in range(4)])
    original = service.db._apply_zip_file_counts
    def fail(*args, **kwargs):
        raise RuntimeError('隔离提交失败')
    monkeypatch.setattr(service.db, '_apply_zip_file_counts', fail)
    with pytest.raises(RuntimeError, match='隔离提交失败'):
        service.import_zip(path)
    assert service.db.counts()['zips'] == 0 and service.get_project(1)['status'] == '待开发'
    copies = list(Path(service.workspaces.settings()['archive_root']).rglob('*.zip'))
    assert len(copies) == 1 and copies[0].name == path.name
    source_bytes = path.read_bytes()
    path.unlink()
    monkeypatch.setattr(service.db, '_apply_zip_file_counts', original)
    service.scan_archives(now=0)
    assert service.db.counts()['zips'] == 1 and service.get_project(1)['status'] == '已交付'
    assert copies[0].read_bytes() == source_bytes


def test_legacy_renamed_archive_stays_while_new_drop_gets_original_name(service, tmp_path):
    path = archive(tmp_path/'甲平台.zip', [f'{i}.txt' for i in range(4)])
    first = service.import_zip(path)['zip']
    old_path = Path(first['path'])
    legacy = old_path.with_name('旧版时间戳_hash.zip')
    old_path.rename(legacy)
    with service.db.connect() as connection:
        connection.execute('UPDATE zips SET path=? WHERE id=?', (str(legacy), first['id']))
    second = service.import_zip(path)['zip']
    assert legacy.is_file() and service.db.get_zip(first['id'])['path'] == str(legacy)
    assert second['id'] != first['id'] and Path(second['path']).name == path.name
