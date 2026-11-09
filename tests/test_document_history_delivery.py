"""Current drafts, immutable deliveries and history ownership share one storage flow."""

import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from project_documents import list_project_documents


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='文稿轮换与项目目录租约使用 Windows API')


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def records(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY 1')]


def project(service, name):
    return service.capture_message(name, '匿名批次 ' + name)['project_ids'][0]


def working_files(service, pid):
    root = Path(service.workspaces.get_workspace(pid)['path'])
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def documents(service, pid, *, history=False):
    result = list_project_documents(service, pid, include_history=history)
    assert result['errors'] == []
    return result['documents']


def tree(path):
    root = Path(path)
    return {item.relative_to(root).as_posix(): item.read_bytes()
            for item in root.rglob('*') if item.is_file()}


def test_a_to_b_to_a_keeps_one_current_draft_and_two_explicit_historical_versions(service, tmp_path):
    pid = project(service, '材料示例系统')
    source = tmp_path / '说明书.docx'
    imported = []
    for content in (b'version A', b'version B', b'version A'):
        source.write_bytes(content)
        result = service.workspaces.import_file(source, pid)
        assert result['duplicate'] is False
        imported.append(result['file'])
        assert working_files(service, pid) == {source.name: content}
        assert [(row['name'], row['kind']) for row in documents(service, pid)] == [
            (source.name, '工作文件')]
        assert source.read_bytes() == content

    assert len({row['id'] for row in imported}) == 3
    assert len({row['path'] for row in imported}) == 1
    links = records(service, 'work_file_supersessions')
    assert {(row['old_file_id'], row['new_file_id']) for row in links} == {
        (imported[0]['id'], imported[1]['id']), (imported[1]['id'], imported[2]['id'])}
    history = [row for row in documents(service, pid, history=True) if row['kind'] == '历史文稿']
    assert {row['work_file_id'] for row in history} == {row['id'] for row in imported[:2]}
    assert sorted(Path(row['path']).read_bytes() for row in history) == [b'version A', b'version B']
    root = Path(service.workspaces.get_workspace(pid)['path'])
    assert all(not Path(row['path']).is_relative_to(root) for row in history)
    assert len({row['path'] for row in history}) == 2
    assert service.workspaces.document_versions.recover() == []


@pytest.mark.parametrize('entrypoint', ['workspace', 'desktop', 'path-auto'])
def test_single_file_intake_cannot_turn_history_back_into_current_materials(service, tmp_path, entrypoint):
    pid = project(service, '材料示例系统')
    source = tmp_path / '说明书.docx'
    for content in (b'old draft', b'current draft'):
        source.write_bytes(content)
        service.workspaces.import_file(source, pid)
    historical = next(row for row in documents(service, pid, history=True) if row['kind'] == '历史文稿')
    before_files = records(service, 'work_files')
    before_links = records(service, 'work_file_supersessions')
    before_events = records(service, 'delivery_events')
    before_projects = records(service, 'projects')
    with pytest.raises(ValueError):
        if entrypoint == 'workspace':
            service.workspaces.import_file(historical['path'], pid)
        elif entrypoint == 'desktop':
            service.receive_file(historical['path'], pid, manual=True)
        else:
            service.receive_path(historical['path'])
    assert working_files(service, pid) == {source.name: b'current draft'}
    assert Path(historical['path']).read_bytes() == b'old draft'
    assert records(service, 'work_files') == before_files
    assert records(service, 'work_file_supersessions') == before_links
    assert records(service, 'delivery_events') == before_events
    assert records(service, 'projects') == before_projects


def test_each_material_delivery_contains_only_current_draft_and_preserves_prior_zip_and_events(service, tmp_path):
    pid = project(service, '材料示例系统')
    original_zip = tmp_path / '材料示例系统.zip'
    with ZipFile(original_zip, 'w') as archive:
        archive.writestr('原始说明.txt', b'immutable original delivery')
    package = service.import_zip(original_zip)['zip']
    service.confirm_delivery(pid, package['id'], confirmed=True)
    zip_record = dict(service.db.get_zip(package['id']))
    zip_bytes = Path(zip_record['path']).read_bytes()
    original_zip_bytes = original_zip.read_bytes()
    previous_events = records(service, 'delivery_events')
    archived_trees = {}
    previous_archives = []
    deliveries = []
    source = tmp_path / '说明书.docx'

    for content in (b'version A', b'version B', b'version A'):
        source.write_bytes(content)
        service.workspaces.import_file(source, pid)
        assert working_files(service, pid) == {source.name: content}
        assert records(service, 'delivery_events') == previous_events
        result = service.confirm_delivery(pid, confirmed=True)
        delivery = result['archive']
        deliveries.append(delivery)
        assert [row['path'] for row in delivery['manifest']] == [source.name]
        assert tree(delivery['path']) == {source.name: content}
        for path, saved in archived_trees.items():
            assert tree(path) == saved
        archived_trees[delivery['path']] = tree(delivery['path'])
        current_archives = records(service, 'project_delivery_archives')
        assert current_archives[:len(previous_archives)] == previous_archives
        previous_archives = current_archives
        current_events = records(service, 'delivery_events')
        assert current_events[:len(previous_events)] == previous_events
        assert len(current_events) == len(previous_events) + 1
        previous_events = current_events
        assert dict(service.db.get_zip(package['id'])) == zip_record
        assert Path(zip_record['path']).read_bytes() == zip_bytes
        assert original_zip.read_bytes() == original_zip_bytes
        with ZipFile(zip_record['path']) as archive:
            assert archive.namelist() == ['原始说明.txt']
            assert archive.read('原始说明.txt') == b'immutable original delivery'

    assert deliveries[0]['id'] != deliveries[1]['id']
    assert deliveries[2]['id'] == deliveries[0]['id']
    assert len(previous_events) == 4
    assert len(previous_archives) == 2
    assert len([row for row in documents(service, pid, history=True) if row['kind'] == '历史文稿']) == 2


@pytest.mark.parametrize('operation', ['rename', 'merge'])
def test_history_follows_project_ownership_without_moving_old_drafts_into_materials(service, tmp_path, operation):
    source_id = project(service, '来源示例系统')
    source = tmp_path / '来源说明.docx'
    for content in (b'source old', b'source current'):
        source.write_bytes(content)
        service.workspaces.import_file(source, source_id)
    first_delivery = service.confirm_delivery(source_id, confirmed=True)
    previous_events = records(service, 'delivery_events')
    previous_archives = records(service, 'project_delivery_archives')
    archive_bytes = tree(first_delivery['archive']['path'])
    before = {row['id']: row for row in records(service, 'work_files')}
    history_before = {row['work_file_id']: (row['path'], Path(row['path']).read_bytes())
                      for row in documents(service, source_id, history=True) if row['kind'] == '历史文稿'}
    target_id = None
    if operation == 'merge':
        target_id = project(service, '保留示例系统')
        target_source = tmp_path / '保留说明.docx'
        for content in (b'target old', b'target current'):
            target_source.write_bytes(content)
            service.workspaces.import_file(target_source, target_id)
        history_before.update({row['work_file_id']: (row['path'], Path(row['path']).read_bytes())
                               for row in documents(service, target_id, history=True) if row['kind'] == '历史文稿'})
    new_name = '保留示例系统' if target_id else '更正示例系统'
    plan = service.preview_project_rename(source_id, new_name, target_id)
    assert plan['kind'] == operation, plan['warning']
    result = service.rename_project(source_id, new_name, expected_token=plan['token'],
                                    merge_target_id=target_id, confirmed_merge=bool(target_id))
    retained_id = target_id or source_id
    assert result['project_id'] == retained_id
    assert records(service, 'delivery_events') == previous_events
    previous_archives = [dict(row, project_id=retained_id) for row in previous_archives]
    assert records(service, 'project_delivery_archives') == previous_archives
    assert tree(first_delivery['archive']['path']) == archive_bytes
    after = {row['id']: row for row in records(service, 'work_files')}
    for old_id, (path, content) in history_before.items():
        assert after[old_id]['project_id'] == retained_id
        assert after[old_id]['path'] == path
        assert Path(path).read_bytes() == content
        if old_id in before:
            assert after[old_id]['sha256'] == before[old_id]['sha256']
            assert after[old_id]['source_path'] == before[old_id]['source_path']
    history = [row for row in documents(service, retained_id, history=True) if row['kind'] == '历史文稿']
    assert {row['work_file_id'] for row in history} == set(history_before)
    if target_id:
        assert not [row for row in documents(service, source_id, history=True) if row['kind'] == '历史文稿']
    expected = {source.name: b'source current'}
    if target_id:
        expected[target_source.name] = b'target current'
    assert working_files(service, retained_id) == expected

    # Another intake after ownership changes must extend history without leaking it into delivery.
    source.write_bytes(b'source latest')
    service.workspaces.import_file(source, retained_id)
    expected[source.name] = b'source latest'
    delivered = service.confirm_delivery(retained_id, confirmed=True)
    assert tree(delivered['archive']['path']) == expected
    assert {row['path'] for row in delivered['archive']['manifest']} == set(expected)
    assert tree(first_delivery['archive']['path']) == archive_bytes
    assert records(service, 'delivery_events')[:len(previous_events)] == previous_events
    assert records(service, 'project_delivery_archives')[:len(previous_archives)] == previous_archives
    assert service.recover_project_changes() == []
    assert service.workspaces.document_versions.recover() == []
