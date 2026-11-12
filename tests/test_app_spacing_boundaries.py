"""APP spacing must share identity checks for renames, provisioning and bundles."""

from zipfile import ZipFile

import pytest

from bundle_analyzer import analyze_bundle
from desktop_service import DesktopService
from file_matcher import match_work_file_projects


@pytest.mark.parametrize('spacing', [' ', '\u3000', '\t'])
def test_rename_preview_finds_existing_app_but_never_merges_without_confirmation(tmp_path, spacing):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    target = service.receive_text('匿名预约APP')['project_ids'][0]
    source = service.receive_text('匿名其他系统')['project_ids'][0]
    plan = service.preview_project_rename(source, '匿名预约' + spacing + 'APP')
    assert plan['kind'] == 'merge'
    assert plan['target']['id'] == target
    assert service.db.counts()['projects'] == 2
    assert service.get_project(source)['name'] == '匿名其他系统'
    with pytest.raises(ValueError, match='确认|合并'):
        service.rename_project(source, '匿名预约' + spacing + 'APP', expected_token=plan['token'])
    assert service.db.counts()['projects'] == 2


def test_provision_transaction_reuses_app_identity_after_name_variation(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    target = service.receive_text('匿名预约APP')['project_ids'][0]
    source = tmp_path / '匿名预约 APP.txt'
    source.write_bytes(b'anonymous material')
    result = service._capture_file_projects(source, ['匿名预约 APP'])
    assert result['project_ids'] == [target]
    assert result['created_project_ids'] == []
    assert service.db.counts()['projects'] == 1


def test_bundle_uses_app_spacing_for_first_name_and_member_identity(tmp_path):
    path = tmp_path / '匿名预约APP-2.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('匿名预约 APP/current.txt', b'first')
        archive.writestr('匿名查询\u3000App/current.txt', b'second')
    rows = [dict(id=1, name='匿名预约APP', batch_id=1),
            dict(id=2, name='匿名查询APP', batch_id=1)]
    result = analyze_bundle(path, rows)
    assert result['state'] == 'matched'
    assert result['project_ids'] == [1, 2]
    assert result['provision_names'] == []
    # 合包索引沿用既有 NFKC 路径表示；实际 ZIP 成员原名仍完整保留。
    assert [m['folder_name'] for m in result['matches']] == ['匿名预约 APP', '匿名查询 App']
    with ZipFile(path) as archive:
        assert archive.namelist() == ['匿名预约 APP/current.txt', '匿名查询\u3000App/current.txt']


def test_bundle_spacing_variants_are_duplicate_members_not_two_new_projects(tmp_path):
    path = tmp_path / '匿名预约APP-2.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('匿名预约APP/current.txt', b'first')
        archive.writestr('匿名预约 APP/current.txt', b'second')
    result = analyze_bundle(path, [])
    assert result['state'] == 'needs-review'
    assert result['project_ids'] == result['provision_names'] == []
    assert '重复' in result['reason']


@pytest.mark.parametrize('materials', [
    ['mobileAPP/a.txt', 'mobile APP/b.txt'],
    ['mobileAPP', 'mobile APP'],
])
def test_app_spacing_does_not_collapse_distinct_material_paths_in_bundle(tmp_path, materials):
    path = tmp_path / 'Alpha-2.zip'
    entries = [*(f'Alpha/{name}' for name in materials), 'Beta/c.txt']
    with ZipFile(path, 'w') as archive:
        for entry in entries:
            archive.writestr(entry, entry.encode('utf-8'))
    rows = [dict(id=1, name='Alpha', batch_id=1),
            dict(id=2, name='Beta', batch_id=1)]
    result = analyze_bundle(path, rows)
    assert result['state'] == 'matched', result['reason']
    assert result['project_ids'] == [1, 2]
    assert [match['file_count'] for match in result['matches']] == [2, 1]
    with ZipFile(path) as archive:
        assert archive.namelist() == entries
        assert [archive.read(entry) for entry in entries] == [entry.encode('utf-8') for entry in entries]


@pytest.mark.parametrize(('name', 'filename'), [
    ('匿名预约APP', '匿名预约 APP-代码（Python）.txt'),
    ('匿名预约APP', '匿名预约 APP (1).docx'),
    ('匿名预约APP', '匿名预约 APP-final.docx'),
    ('匿名预约APP v1.0', '匿名预约 APP v1.0_任意附注.docx'),
])
def test_spacing_is_ignored_after_material_or_version_suffix_recognition(name, filename):
    project = dict(id=1, name=name)
    assert match_work_file_projects(filename, [project])['candidates'] == [project]


def test_legacy_stored_alias_key_remains_usable_without_rewriting_history(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    pid = service.receive_text('匿名新预约工具')['project_ids'][0]
    with service.db.connect() as connection:
        connection.execute('''INSERT INTO project_name_aliases
            (project_id,name,normalized_name,operation_id) VALUES (?,?,?,?)''',
            (pid, '匿名旧预约 APP', '匿名旧预约 app', 'anonymous-legacy-alias'))
    result = service.receive_text('匿名旧预约APP')
    assert result['reused_project_ids'] == [pid]
    assert result['created_project_ids'] == []
    source = tmp_path / '匿名旧预约APP_任意附注.docx'
    source.write_bytes(b'anonymous source')
    assert [p['id'] for p in service.file_routing(source)['candidates']] == [pid]
    with service.db.connect() as connection:
        aliases = connection.execute('SELECT name,normalized_name FROM project_name_aliases').fetchall()
    assert [tuple(row) for row in aliases] == [('匿名旧预约 APP', '匿名旧预约 app')]
