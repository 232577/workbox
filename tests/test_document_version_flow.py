"""匿名服务入口：新稿使用标准名，旧稿、归属与交付历史均可追溯。"""

from pathlib import Path

import pytest

from desktop_service import DesktopService
from folder_intake import FolderIntake
from native_files import identity
from project_documents import list_project_documents
from workspace_manager import copy_verified


@pytest.fixture
def service(tmp_path):
    result = DesktopService(tmp_path / 'data' / 'workbox.db')
    result.receive_text('1. 匿名文档系统\n2. 另一个匿名系统')
    return result


def rows(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]


@pytest.mark.parametrize('suffix', ['_操作手册.docx', '_代码（Python）.docx', '_申请表重新下载.txt'])
def test_receive_new_revision_keeps_standard_name_and_history(service, tmp_path, suffix):
    source = tmp_path / ('匿名文档系统' + suffix)
    source.write_bytes(b'first anonymous document')
    first = service.receive_file(source)
    project_id = first['file']['project_id']
    assert project_id == 1
    canonical = Path(first['file']['path'])
    old_identity = identity(canonical.stat())
    delivered = service.confirm_delivery(project_id, confirmed=True)
    archive_path = Path(delivered['archive']['path']) / source.name
    before_events = rows(service, 'delivery_events')
    before_archives = rows(service, 'project_delivery_archives')
    before_log = rows(service, 'file_intake_events')

    source.write_bytes(b'new anonymous document')
    second = service.receive_file(source)

    assert Path(second['file']['path']) == canonical
    assert canonical.name == source.name
    assert canonical.read_bytes() == source.read_bytes() == b'new anonymous document'
    stored = rows(service, 'work_files')
    old = next(row for row in stored if row['id'] == first['file']['id'])
    assert old['project_id'] == project_id
    assert old['path'] != str(canonical)
    assert Path(old['path']).read_bytes() == b'first anonymous document'
    assert identity(Path(old['path']).stat()) == old_identity
    assert old['sha256'] == first['file']['sha256']
    assert old['source_path'] == str(source)
    assert archive_path.read_bytes() == b'first anonymous document'
    assert rows(service, 'delivery_events') == before_events
    assert rows(service, 'project_delivery_archives') == before_archives
    assert rows(service, 'file_intake_events')[:len(before_log)] == before_log
    assert service.get_project(project_id)['status'] == '待交付'
    assert service.get_project(2)['status'] == '待开发'
    assert len(service.list_projects()) == 2

    documents = list_project_documents(service, project_id)
    assert not documents['errors']
    listed = {row['path']: row for row in documents['documents']}
    assert listed[str(canonical)]['name'] == source.name
    assert old['path'] not in listed
    history = list_project_documents(service, project_id, include_history=True)
    assert not history['errors']
    historical = next(row for row in history['documents'] if row['path'] == old['path'])
    assert historical['exists'] and historical['kind'] == '历史文稿'
    assert historical['work_file_id'] == first['file']['id']
    assert historical['relative_path'] == source.name
    with service.db.connect() as connection:
        link = dict(connection.execute('''SELECT s.*,r.path AS history_root FROM work_file_supersessions s
            JOIN document_history_roots r ON r.id=s.history_root_id WHERE s.old_file_id=?''',
            (first['file']['id'],)).fetchone())
    assert link['new_file_id'] == second['file']['id']
    assert Path(old['path']).is_relative_to(Path(link['history_root']))
    assert not Path(old['path']).is_relative_to(canonical.parent)
    assert service.receive_file(source)['duplicate']
    assert len(rows(service, 'work_files')) == 2
    assert canonical.read_bytes() == b'new anonymous document'
    assert Path(old['path']).read_bytes() == archive_path.read_bytes() == b'first anonymous document'
    assert rows(service, 'delivery_events') == before_events
    assert rows(service, 'project_delivery_archives') == before_archives


def test_folder_intake_revisions_keep_relative_path_and_other_folder(service, tmp_path):
    incoming = tmp_path / 'input' / '匿名文档系统'
    document = incoming / '资料' / '材料.txt'
    resource = incoming / '资源' / '材料.txt'
    document.parent.mkdir(parents=True)
    resource.parent.mkdir(parents=True)
    document.write_bytes(b'old document')
    resource.write_bytes(b'other directory')
    first = FolderIntake(service).receive(incoming)
    assert first['copied'] == 2 and first['failed'] == 0
    workspace = Path(service.workspaces.get_workspace(1)['path'])
    original = next(row for row in rows(service, 'work_files')
                    if Path(row['path']) == workspace / '资料' / '材料.txt')
    old_identity = identity(Path(original['path']).stat())
    resource_row = next(row for row in rows(service, 'work_files')
                        if Path(row['path']) == workspace / '资源' / '材料.txt')
    before_events = rows(service, 'delivery_events')
    before_log = rows(service, 'file_intake_events')
    document.write_bytes(b'new document')

    second = FolderIntake(service).receive(incoming)

    assert second['copied'] == 1 and second['duplicate'] == 1 and second['failed'] == 0
    assert (workspace / '资料' / '材料.txt').read_bytes() == document.read_bytes() == b'new document'
    assert (workspace / '资源' / '材料.txt').read_bytes() == b'other directory'
    versions = list((workspace / '资料').iterdir())
    assert versions == [workspace / '资料' / '材料.txt']
    stored = {row['id']: row for row in rows(service, 'work_files')}
    old = stored[original['id']]
    assert {key: value for key, value in old.items() if key not in ('path', 'filename')} == {
        key: value for key, value in original.items() if key not in ('path', 'filename')}
    assert Path(old['path']).read_bytes() == b'old document'
    assert identity(Path(old['path']).stat()) == old_identity
    assert stored[resource_row['id']] == resource_row
    assert resource.read_bytes() == b'other directory'
    assert rows(service, 'delivery_events') == before_events
    assert rows(service, 'file_intake_events')[:len(before_log)] == before_log
    current = list_project_documents(service, 1)
    assert not current['errors']
    assert old['path'] not in {row['path'] for row in current['documents']}
    history = list_project_documents(service, 1, include_history=True)
    assert not history['errors']
    historical = next(row for row in history['documents'] if row['path'] == old['path'])
    assert historical['kind'] == '历史文稿' and historical['exists']
    assert historical['work_file_id'] == original['id']
    assert historical['relative_path'] == '资料/材料.txt'
    with service.db.connect() as connection:
        link = dict(connection.execute('''SELECT s.*,r.path AS history_root FROM work_file_supersessions s
            JOIN document_history_roots r ON r.id=s.history_root_id WHERE s.old_file_id=?''',
            (original['id'],)).fetchone())
    assert link['relative_path'] == '资料/材料.txt'
    assert Path(old['path']).is_relative_to(Path(link['history_root']))
    assert not Path(old['path']).is_relative_to(workspace)
    assert Path(stored[link['new_file_id']]['path']) == workspace / '资料' / '材料.txt'


def test_reimport_from_previous_release_layout_promotes_requested_document(service, tmp_path):
    source = tmp_path / '匿名文档系统_操作手册.docx'
    source.write_bytes(b'old canonical')
    first = service.receive_file(source)
    canonical = Path(first['file']['path'])
    source.write_bytes(b'new document previously given a suffix')
    # 匿名夹具重建旧版输出布局：旧稿占标准名，新稿登记在后缀路径。
    previous_copy = canonical.with_name(canonical.stem + ' (旧版已保存的新稿)' + canonical.suffix)
    metadata = copy_verified(source, previous_copy)
    with service.db.connect() as connection:
        legacy_id = connection.execute(
            'INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) '
            'VALUES (?,?,?,?,?,?,?)',
            (1, str(source), str(previous_copy), source.name, metadata['size'],
             metadata['sha256'], metadata['mtime_ns']),
        ).lastrowid

    result = service.receive_file(source)

    assert Path(result['file']['path']) == canonical
    assert canonical.read_bytes() == source.read_bytes()
    assert previous_copy.read_bytes() == source.read_bytes()
    stored = {row['id']: row for row in rows(service, 'work_files')}
    assert stored[legacy_id]['path'] == str(previous_copy)
    assert Path(stored[first['file']['id']]['path']).read_bytes() == b'old canonical'
    assert service.receive_file(source)['duplicate']
