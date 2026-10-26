"""项目文档查询只读、实时且按登记边界隔离。所有文件均为临时夹具。"""

import importlib
import os
import sqlite3
import subprocess
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


class FixtureDB:
    def __init__(self, path):
        self.path = path
        self.read_only = False

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        if self.read_only:
            connection.execute('PRAGMA query_only=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()


@pytest.fixture
def context(tmp_path):
    db = FixtureDB(tmp_path / 'fixture.db')
    root = tmp_path / 'work' / '甲系统'
    root.mkdir(parents=True)
    with db.connect() as connection:
        connection.executescript('''
            CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO projects VALUES(1,'甲系统'),(2,'乙系统');
            CREATE TABLE project_workspaces(project_id INTEGER, path TEXT);
            CREATE TABLE work_files(id INTEGER PRIMARY KEY,project_id INTEGER,path TEXT,
                filename TEXT,source_path TEXT,size INTEGER,mtime_ns INTEGER);
            CREATE TABLE zips(id INTEGER PRIMARY KEY,project_id INTEGER,path TEXT,
                filename TEXT,size INTEGER,mtime_ns INTEGER);
            CREATE TABLE zip_bundle_members(zip_id INTEGER,project_id INTEGER);
            CREATE VIEW zip_project_links AS SELECT id AS zip_id,project_id FROM zips
                WHERE project_id IS NOT NULL UNION SELECT zip_id,project_id FROM zip_bundle_members;
        ''')
        connection.execute('INSERT INTO project_workspaces VALUES(1,?)', (str(root),))
    return SimpleNamespace(db=db), root


def query(service, project_id=1):
    return importlib.import_module('project_documents').list_project_documents(service, project_id)


def register_file(service, root, name, file_id=1, project_id=1):
    path = root / name
    with service.db.connect() as connection:
        connection.execute('INSERT INTO work_files VALUES(?,?,?,?,?,?,?)',
                           (file_id, project_id, str(path), name, str(path), 15, 100))
    return path


def register_zip(service, path, zip_id=1, project_id=1):
    with service.db.connect() as connection:
        connection.execute('INSERT INTO zips VALUES(?,?,?,?,?,?)',
                           (zip_id, project_id, str(path), path.name, 15, 100))


def test_actual_files_are_recursive_and_ids_survive_external_edits(context):
    service, root = context
    (root / '资料').mkdir()
    target = root / '资料' / '手册.docx'
    target.write_bytes(b'local fixture')
    (root / '项目.ZIP').write_bytes(b'not opened or validated')
    first = query(service)
    assert first['workspace_path'] == str(root)
    assert first['errors'] == []
    assert {row['relative_path'] for row in first['documents']} == {'资料/手册.docx', '项目.ZIP'}
    document = next(row for row in first['documents'] if row['name'] == target.name)
    assert document['kind'] == '工作文件' and document['size'] == 13
    assert document['exists'] and document['mtime_ns'] == target.stat().st_mtime_ns
    assert first['documents'] == query(service)['documents']
    target.write_bytes(b'changed')
    updated = next(row for row in query(service)['documents'] if row['name'] == target.name)
    assert updated['id'] == document['id'] and updated['size'] == 7
    assert next(row for row in first['documents'] if row['name'].endswith('ZIP'))['kind'] == 'ZIP工作副本'


def test_registered_missing_files_deduplicate_by_path_and_keep_missing_status(context):
    service, root = context
    register_file(service, root, '已移走.docx')
    register_file(service, root, '已移走.docx', file_id=2)
    register_file(service, root, '现有.txt', file_id=3).write_bytes(b'actual')
    rows = query(service)['documents']
    assert len(rows) == 2
    missing = next(row for row in rows if row['name'] == '已移走.docx')
    assert missing['exists'] is False and missing['size'] is None
    assert missing['origin'] == 'work_files'
    assert next(row for row in rows if row['name'] == '现有.txt')['size'] == 6


def test_other_projects_nested_paths_and_office_locks_are_excluded(context):
    service, root = context
    foreign = root / '乙系统'
    foreign.mkdir()
    (foreign / '保密.txt').write_bytes(b'other project')
    (root / '~$手册.docx').write_bytes(b'lock')
    (root / '本项目.txt').write_bytes(b'own')
    with service.db.connect() as connection:
        connection.execute('INSERT INTO project_workspaces VALUES(2,?)', (str(foreign),))
    register_file(service, foreign, '保密.txt')
    assert [row['name'] for row in query(service)['documents']] == ['本项目.txt']


def test_only_current_real_zip_links_and_physical_paths_are_used(context, tmp_path):
    service, root = context
    shared = tmp_path / 'library' / '甲系统-2.zip'
    shared.parent.mkdir()
    shared.write_bytes(b'fixture')
    register_zip(service, shared, 1, None)
    register_zip(service, shared, 2, 1)
    register_zip(service, tmp_path / '甲系统-similar.zip', 3, 2)
    missing = tmp_path / 'library' / 'old.zip'
    register_zip(service, missing, 4, 1)
    with service.db.connect() as connection:
        connection.execute('INSERT INTO zip_bundle_members VALUES(1,1)')
    rows = query(service)['documents']
    assert {row['path'] for row in rows} == {str(shared), str(missing)}
    assert all(row['kind'] == 'ZIP归档' for row in rows)
    assert next(row for row in rows if row['path'] == str(missing))['exists'] is False
    with service.db.connect() as connection:
        connection.execute('DELETE FROM zip_bundle_members')
        connection.execute('UPDATE zips SET project_id=2 WHERE id=2')
    assert [row['name'] for row in query(service)['documents']] == ['old.zip']


def test_known_archive_library_is_not_scanned_as_work_files(context):
    service, root = context
    library = root / '历史归档库'
    library.mkdir()
    (library / '别人的材料.txt').write_bytes(b'archive fixture')
    own = library / '甲.zip'
    own.write_bytes(b'archive fixture')
    register_zip(service, own)
    with service.db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT)')
        connection.execute('INSERT INTO workflow_archive_roots VALUES(?)', (str(library),))
    rows = query(service)['documents']
    assert len(rows) == 1 and rows[0]['kind'] == 'ZIP归档'


def test_existing_material_archives_are_history_directory_rows_only(context):
    service, root = context
    history = root / '旧交付'
    history.mkdir()
    (history / '旧手册.docx').write_bytes(b'old archive fixture')
    with service.db.connect() as connection:
        connection.execute('CREATE TABLE project_delivery_archives(id INTEGER, project_id INTEGER, path TEXT, created_at TEXT)')
        connection.execute('INSERT INTO project_delivery_archives VALUES(1,1,?,?)', (str(history), '2026-01-01T00:00:00Z'))
    rows = query(service)['documents']
    assert len(rows) == 1 and rows[0]['kind'] == '材料归档'
    assert rows[0]['is_directory'] and rows[0]['origin'] == 'project_delivery_archives'


def test_query_never_creates_schema_or_missing_folders(context):
    service, root = context
    root.rmdir()
    with service.db.connect() as connection:
        before = list(connection.execute('SELECT sql FROM sqlite_master ORDER BY name'))
        total_before = connection.execute('SELECT total_changes()').fetchone()[0]
    service.db.read_only = True
    result = query(service)
    assert result['documents'] == [] and result['errors']
    assert not root.exists()
    with service.db.connect() as connection:
        assert list(map(tuple, connection.execute('SELECT sql FROM sqlite_master ORDER BY name'))) == list(map(tuple, before))
        assert connection.execute('SELECT total_changes()').fetchone()[0] == total_before


def test_registered_paths_outside_workspace_are_not_exposed(context, tmp_path):
    service, root = context
    outside = register_file(service, tmp_path, 'outside.txt')
    outside.write_bytes(b'outside')
    assert query(service)['documents'] == []


def test_reparse_files_and_directories_are_skipped(context, tmp_path):
    service, root = context
    outside = tmp_path / 'external'
    outside.mkdir()
    (outside / 'secret.txt').write_bytes(b'external fixture')
    try:
        (root / 'linked').symlink_to(outside, target_is_directory=True)
        (root / 'alias.txt').symlink_to(outside / 'secret.txt')
    except OSError as error:
        if os.name != 'nt':
            raise
        # Windows 本地目录联接无需符号链接权限；使用同一临时目录内的夹具。
        command = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'junction'), str(outside)],
                                 capture_output=True)
        if command.returncode:
            pytest.skip(f'环境不允许创建符号链接/目录联接：{error}; {command.stderr!r}')
    result = query(service)
    assert result['documents'] == [] and result['errors']


def test_windows_reparse_attribute_is_rejected_for_files_and_directories(context, monkeypatch):
    service, root = context
    directory = root / '云目录'
    directory.mkdir()
    (directory / '不应进入.txt').write_bytes(b'fixture')
    file = root / '云文件.docx'
    file.write_bytes(b'fixture')
    original = Path.lstat

    def reparse_metadata(path):
        info = original(path)
        if path in (directory, file):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', reparse_metadata)
    result = query(service)
    assert result['documents'] == []
    assert len(result['errors']) == 2


def test_listing_does_not_read_content_and_supports_cancellation(context, monkeypatch):
    service, root = context
    (root / '手册.docx').write_bytes(b'never read content')
    (root / '项目.zip').write_bytes(b'never inspect archive')
    service.db.read_only = True

    def forbidden_open(*args, **kwargs):
        raise AssertionError('查询不能打开文档内容')

    monkeypatch.setattr(Path, 'open', forbidden_open)
    assert len(query(service)['documents']) == 2
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        importlib.import_module('project_documents').list_project_documents(service, 1, cancel_event=event)


def test_bounded_listing_reports_partial_results(context, monkeypatch):
    service, root = context
    for index in range(8):
        (root / f'{index}.txt').write_bytes(b'fixture')
    module = importlib.import_module('project_documents')
    monkeypatch.setattr(module, 'MAX_DOCUMENTS', 3)
    result = query(service)
    assert len(result['documents']) == 3
    assert any('上限' in error for error in result['errors'])


def test_unregistered_and_unknown_projects_do_not_adopt_similar_directories(context):
    service, root = context
    (root.parent / '乙系统').mkdir()
    assert query(service, 2)['workspace_path'] is None
    assert query(service, 2)['documents'] == []
    assert query(service, 999)['documents'] == []
    assert query(service, 999)['errors']


@pytest.mark.parametrize('directory_name', ['.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache'])
def test_dependency_and_cache_directories_are_not_scanned(context, monkeypatch, directory_name):
    service, root = context
    source = root / 'src'
    source.mkdir()
    (source / '业务.py').write_bytes(b'working source fixture')
    excluded = source / directory_name
    excluded.mkdir()
    register_file(service, excluded, '缓存.txt').write_bytes(b'cache fixture')
    (excluded / '更深').mkdir()
    (excluded / '更深' / '生成.txt').write_bytes(b'nested cache fixture')
    # 同名普通文件不是依赖目录，仍需正常显示。
    (root / directory_name).write_bytes(b'ordinary file fixture')
    original = os.scandir

    def guarded_scandir(path):
        assert not Path(path).is_relative_to(excluded), '查询穿透了依赖或缓存目录'
        return original(path)

    monkeypatch.setattr(os, 'scandir', guarded_scandir)
    result = query(service)
    assert result['errors'] == []
    assert {row['relative_path'] for row in result['documents']} == {'src/业务.py', directory_name}
    assert result == query(service)


def test_linked_archives_remain_visible_inside_excluded_directories(context, monkeypatch):
    service, root = context
    excluded = root / '.cache'
    excluded.mkdir()
    archive = excluded / '已登记.zip'
    archive.write_bytes(b'archive fixture')
    register_zip(service, archive)
    material = excluded / '历史交付'
    material.mkdir()
    (material / '旧手册.docx').write_bytes(b'archive content fixture')
    with service.db.connect() as connection:
        connection.execute('CREATE TABLE project_delivery_archives(id INTEGER, project_id INTEGER, path TEXT, created_at TEXT)')
        connection.execute('INSERT INTO project_delivery_archives VALUES(1,1,?,?)',
                           (str(material), '2026-01-01T00:00:00Z'))
    original = os.scandir

    def guarded_scandir(path):
        assert not Path(path).is_relative_to(excluded), '归档关联不应使查询扫描缓存目录'
        return original(path)

    monkeypatch.setattr(os, 'scandir', guarded_scandir)
    rows = query(service)['documents']
    assert [(row['name'], row['kind']) for row in rows] == [('已登记.zip', 'ZIP归档'), ('历史交付', '材料归档')]
