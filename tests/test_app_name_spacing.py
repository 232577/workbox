"""Anonymous APP-boundary identity regressions; no personal storage is opened."""

from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from file_matcher import match_work_file_projects
from folder_intake import FolderIntake
from parser import ParsedProject, parse_message
from zip_manager import match_projects


def project(pid, name, aliases=()):
    return dict(id=pid, name=name, name_aliases=list(aliases))


def candidates(stem, projects, kind):
    if kind == 'zip':
        return match_projects(stem + '.zip', projects)
    if kind == 'folder':
        return FolderIntake(SimpleNamespace(workspaces=None))._named(stem, projects)
    return match_work_file_projects(stem + '.docx', projects)['candidates']


@pytest.mark.parametrize(('first', 'second'), [
    ('匿名预约APP', '匿名预约 APP'),
    ('匿名预约  App', '匿名预约\tapp'),
    ('匿名预约\u3000APP', '匿名预约APP'),
    ('匿名预约\u00a0app v1.0', '匿名预约APP v1.0'),
])
def test_list_deduplication_keeps_first_display_name_and_combines_urgency(first, second):
    parsed = parse_message('1. ' + first + '\n2. ' + second + ' [加急]')
    assert [(entry.name, entry.urgent) for entry in parsed] == [(first, True)]


@pytest.mark.parametrize('kind', ['document', 'zip'])
@pytest.mark.parametrize(('registered', 'incoming'), [
    ('匿名预约APP', '匿名预约 APP'),
    ('匿名预约 APP', '匿名预约APP'),
    ('匿名预约APP', '匿名预约   App_客户任意附注'),
    ('匿名预约APP', '匿名预约\u3000app_代码（C#+Python）_v2.0 (2)'),
    ('匿名预约APP', '匿名预约\tAPP_v1.0'),
    ('匿名预约\u00a0App', '匿名预约APP_客户备注_v2.0'),
])
def test_material_matching_ignores_only_whitespace_before_app(registered, incoming, kind):
    row = project(1, registered)
    assert candidates(incoming, [row], kind) == [row]
    assert row['name'] == registered


@pytest.mark.parametrize('kind', ['document', 'zip'])
def test_registered_old_app_name_accepts_spacing_and_open_annotation(kind):
    row = project(1, '匿名新预约工具', ['匿名旧预约APP'])
    assert candidates('匿名旧预约 APP_客户附注_v2.0 (1)', [row], kind) == [row]


@pytest.mark.parametrize('kind', ['document', 'zip', 'folder'])
def test_existing_distinct_ids_with_app_spacing_remain_ambiguous(kind):
    rows = [project(1, '匿名预约APP'), project(2, '匿名预约 APP')]
    assert candidates('匿名预约 APP_客户附注', rows, kind) == rows


@pytest.mark.parametrize('kind', ['document', 'zip'])
@pytest.mark.parametrize(('registered', 'incoming'), [
    ('匿名预约', '匿名预约 APP'),
    ('匿名预约APPLE', '匿名预约 APPLE'),
    ('匿名预约APPLET', '匿名预约 APPLET'),
    ('My ProjectAPP', 'MyProject APP'),
    ('匿名预约1APP', '匿名预约2 APP'),
    ('匿名预约APP', '匿名预约 APP2'),
])
def test_app_normalization_preserves_app_word_number_and_other_meaningful_spaces(registered, incoming, kind):
    # ZIP 的历史完整名宽松规则保持兼容；附注走严格名称边界匹配。
    if kind == 'zip':
        incoming += '_任意附注'
    assert candidates(incoming, [project(1, registered)], kind) == []


def test_nonterminal_app_word_does_not_erase_meaningful_spacing():
    rows = [project(1, '匿名预约APP Tools')]
    for kind in ('document', 'zip', 'folder'):
        assert candidates('匿名预约 APP Tools_任意附注', rows, kind) == []


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def capture(service, name, batch='匿名初始批'):
    return service.capture_message('1. ' + name, batch)['project_ids'][0]


def project_rows(service):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM projects ORDER BY id')]


@pytest.mark.parametrize(('registered', 'incoming'), [
    ('匿名预约APP', '匿名预约 APP'),
    ('匿名预约  App', '匿名预约APP'),
])
def test_text_reuses_existing_identity_without_changing_display_or_workspace(service, registered, incoming):
    pid = capture(service, registered)
    service.bulk_update_projects([pid], status='开发中', urgent=True)
    before = project_rows(service)
    workspace = dict(service.workspaces.get_workspace(pid))

    result = service.receive_text('1. ' + incoming)

    assert result['state'] == 'committed'
    assert result['project_ids'] == result['reused_project_ids'] == [pid]
    assert result['created_project_ids'] == []
    assert project_rows(service) == before
    assert dict(service.workspaces.get_workspace(pid)) == workspace


def test_review_confirmation_deduplicates_app_spacing_and_keeps_first_name(service):
    pending = service.receive_text('请核对下面的项目名称。')

    result = service.resolve_text(pending['id'], [
        ParsedProject('匿名预约 APP'), ParsedProject('匿名预约APP', True),
    ])

    assert len(result['project_ids']) == 1
    row = service.get_project(result['project_ids'][0])
    assert row['name'] == '匿名预约 APP'
    assert row['urgent']


def source_material(tmp_path, name, extension):
    source = tmp_path / 'external' / (name + extension)
    source.parent.mkdir(parents=True, exist_ok=True)
    if extension == '.zip':
        with ZipFile(source, 'w') as archive:
            archive.writestr('anonymous.txt', b'anonymous source contents')
    else:
        source.write_bytes(b'anonymous source contents')
    return source


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_actual_material_reuses_app_identity_and_preserves_original_file(service, tmp_path, extension):
    pid = capture(service, '匿名预约APP')
    source = source_material(tmp_path, '匿名预约  App_任意附注_v2.0 (2)', extension)
    original = source.read_bytes()

    result = service.receive_path(source)

    assert result.get('created_project_ids') == []
    assert service.db.counts()['projects'] == 1
    if extension == '.zip':
        assert result['project_id'] == pid
        target = Path(result['working_zip_path'])
    else:
        assert result['file']['project_id'] == pid
        target = Path(result['file']['path'])
    assert target.name == source.name
    assert target.read_bytes() == source.read_bytes() == original
    assert service.get_project(pid)['name'] == '匿名预约APP'


def test_actual_folder_reuses_app_identity_and_preserves_source_tree(service, tmp_path):
    pid = capture(service, '匿名预约APP')
    root = tmp_path / 'external' / '匿名预约 APP_任意附注_v2.0'
    source = root / '资料' / '原始说明.txt'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'anonymous folder contents')

    result = service.receive_path(root)

    assert result['copied'] == 1
    assert result['failed'] == result['needs_choice'] == 0
    assert result['created_project_ids'] == []
    assert result['project_ids'] == [pid]
    target = Path(service.workspaces.get_workspace(pid)['path']) / '资料' / source.name
    assert target.read_bytes() == source.read_bytes() == b'anonymous folder contents'
    assert service.db.counts()['projects'] == 1


@pytest.mark.parametrize('boundary', ['archived', 'deleted'])
@pytest.mark.parametrize('intake', ['text', 'document'])
def test_app_spacing_cannot_bypass_protected_project(service, tmp_path, boundary, intake):
    pid = capture(service, '匿名预约APP')
    if boundary == 'archived':
        service.bulk_update_projects([pid], archived=True)
    else:
        service.delete_project(pid, confirmed=True)
    before = project_rows(service)

    if intake == 'text':
        result = service.receive_text('1. 匿名全新工具\n2. 匿名预约 APP')
        assert result['state'] == 'needs-review'
        assert result['project_ids'] == []
    else:
        source = source_material(tmp_path, '匿名预约 APP_任意附注', '.docx')
        result = service.receive_path(source)
        assert result.get('intake_choice') == str(source)
        assert result['routing']['requires_choice']
        assert source.read_bytes() == b'anonymous source contents'
    assert project_rows(service) == before


def test_text_cannot_auto_merge_legacy_distinct_ids_with_app_spacing(service):
    first = capture(service, '匿名预约APP', '匿名第一批')
    second = capture(service, '匿名预约 APP', '匿名第二批')
    assert first != second
    before = project_rows(service)

    result = service.receive_text('1. 匿名全新工具\n2. 匿名预约APP')

    assert result['state'] == 'needs-review'
    assert result['project_ids'] == []
    assert project_rows(service) == before
    assert len(before) == 2
