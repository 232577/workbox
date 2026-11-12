"""Anonymous filename-annotation intake regressions; all storage is isolated."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from file_matcher import match_work_file_projects, new_file_project_name
from zip_manager import match_projects


def project(pid, name, aliases=(), **extra):
    return dict(id=pid, name=name, name_aliases=list(aliases), **extra)


def candidates(stem, projects, extension):
    filename = stem + extension
    if extension == '.zip':
        return match_projects(filename, projects)
    return match_work_file_projects(filename, projects)['candidates']


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
@pytest.mark.parametrize('annotation', [
    '_备注', '_任意附注', '_草稿待核对', '_重新下载', '_申请表重新下载备份',
    '_代码（Python）_备注', '_备注_代码（C#+Python）_v2.0 (2)',
    '_任意英文(note)-附注17', '＿全角下划线备注',
])
def test_existing_project_accepts_open_underscore_annotations(extension, annotation):
    row = project(1, '匿名资料系统')
    assert candidates(row['name'] + annotation, [row], extension) == [row]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
@pytest.mark.parametrize('app', ['APP', 'App', 'app', 'ＡＰＰ'])
def test_app_spelling_normalizes_without_removing_it_from_identity(extension, app):
    rows = [project(1, '匿名预约'), project(2, '匿名预约APP')]
    result = candidates('匿名预约' + app + '_客户备注', rows, extension)
    assert result == [rows[1]]
    assert result[0]['name'] == '匿名预约APP'


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_complete_project_name_wins_before_treating_underscores_as_annotations(extension):
    rows = [project(1, '匿名资料系统'), project(2, '匿名资料系统_客户备注')]
    assert candidates('匿名资料系统_客户备注', rows, extension) == [rows[1]]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_longest_existing_project_name_wins_for_open_annotations(extension):
    rows = [project(1, '匿名资料系统'), project(2, '匿名资料系统_移动端'),
            project(3, '匿名资料系统_移动端_甲组')]
    assert candidates('匿名资料系统_移动端_甲组_客户备注', rows, extension) == [rows[2]]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_same_longest_project_name_keeps_every_batch_candidate(extension):
    rows = [project(1, '匿名资料系统'),
            project(2, '匿名资料系统_移动端', batch_name='匿名甲批'),
            project(3, '匿名资料系统_移动端', batch_name='匿名乙批')]
    assert candidates('匿名资料系统_移动端_客户备注', rows, extension) == rows[1:]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_longest_registered_alias_is_used_before_a_shorter_annotation_prefix(extension):
    rows = [project(1, '匿名资料系统'),
            project(2, '匿名新资料系统', ['匿名资料系统_移动端'])]
    assert candidates('匿名资料系统_移动端_客户备注', rows, extension) == [rows[1]]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_current_name_wins_over_equal_strength_alias_annotation_prefix(extension):
    current = project(1, '匿名资料系统')
    alias = project(2, '匿名其他系统', ['匿名资料系统'])
    assert candidates('匿名资料系统_客户备注', [alias, current], extension) == [current]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_alias_annotation_collision_does_not_choose_a_project(extension):
    rows = [project(1, '匿名甲系统', ['匿名旧系统']),
            project(2, '匿名乙系统', ['匿名旧系统'])]
    assert candidates('匿名旧系统_客户备注', rows, extension) == rows


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_existing_exact_alias_precedes_new_annotation_fallback(extension):
    rows = [project(1, '匿名资料系统'),
            project(2, '匿名其他系统', ['匿名资料系统_客户备注'])]
    assert candidates('匿名资料系统_客户备注', rows, extension) == [rows[1]]


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
@pytest.mark.parametrize('stem', [
    '匿名资料系统2_备注', '匿名资料系统-2_备注', '匿名资料系统(2)_备注',
    '匿名资料_备注', '匿名资料系统备注',
])
def test_annotation_matching_does_not_guess_project_number_or_partial_names(extension, stem):
    assert candidates(stem, [project(1, '匿名资料系统')], extension) == []


def test_unknown_project_name_keeps_annotation_when_provisioning():
    assert new_file_project_name('匿名新系统_客户备注.docx') == '匿名新系统_客户备注'


def test_zip_project_codes_still_override_name_annotation_candidates():
    rows = [project(1, '匿名资料系统'), project(2, '匿名其他系统')]
    assert match_projects('匿名资料系统_WB-000002_备注.zip', rows) == [rows[1]]
    assert match_projects('匿名资料系统_WB-999999_备注.zip', rows) == []


@pytest.mark.parametrize('stem', ['匿名资料系统_备注-2', '匿名资料系统_备注-2-G'])
def test_bundle_count_syntax_does_not_become_an_annotation_fallback(stem):
    assert match_projects(stem + '.zip', [project(1, '匿名资料系统')]) == []


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def capture(service, name):
    return service.capture_message(name, name)['project_ids'][0]


def material(tmp_path, name, extension):
    source = tmp_path / 'external' / (name + extension)
    source.parent.mkdir(parents=True, exist_ok=True)
    if extension == '.zip':
        with ZipFile(source, 'w') as archive:
            archive.writestr('anonymous.txt', b'anonymous material')
    else:
        source.write_bytes(b'anonymous material')
    return source


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_receiving_annotated_material_reuses_project_and_keeps_original(service, tmp_path, extension):
    pid = capture(service, '匿名预约APP')
    source = material(tmp_path, '匿名预约App_客户备注', extension)
    original = source.read_bytes()

    result = service.receive_path(source)

    assert result.get('created_project_ids') == []
    assert service.db.counts()['projects'] == 1
    if extension == '.zip':
        assert result['project_id'] == pid and result['watching']
        target = Path(result['working_zip_path'])
    else:
        assert result['file']['project_id'] == pid
        target = Path(result['file']['path'])
    assert target.name == source.name
    assert target.read_bytes() == original
    assert source.read_bytes() == original
    assert service.get_project(pid)['name'] == '匿名预约APP'


@pytest.mark.parametrize('extension', ['.docx', '.zip'])
@pytest.mark.parametrize('boundary', ['archived', 'deleted'])
def test_protected_longer_name_blocks_active_short_annotation_prefix(service, tmp_path, extension, boundary):
    capture(service, '匿名资料系统')
    protected_id = capture(service, '匿名资料系统_移动端')
    if boundary == 'archived':
        service.bulk_update_projects([protected_id], archived=True)
    else:
        service.delete_project(protected_id, confirmed=True)
    source = material(tmp_path, '匿名资料系统_移动端_客户备注', extension)
    original = source.read_bytes()

    result = service.receive_path(source)

    assert result.get('intake_choice') == str(source)
    assert result['routing']['requires_choice']
    assert len(service.list_projects(archived=None, deleted=None)) == 2
    assert source.read_bytes() == original
    assert service.db.counts()['zips'] == 0


def test_annotated_source_folder_routes_to_existing_project_and_preserves_tree(service, tmp_path):
    pid = capture(service, '匿名资料系统')
    root = tmp_path / 'external' / '匿名资料系统_客户备注'
    source = root / '资料' / 'readme.txt'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'anonymous material')

    result = service.receive_path(root)

    assert result['copied'] == 1
    assert result['failed'] == result['needs_choice'] == 0
    assert result['created_project_ids'] == []
    assert result['project_ids'] == [pid]
    target = Path(service.workspaces.get_workspace(pid)['path']) / '资料' / 'readme.txt'
    assert target.read_bytes() == source.read_bytes() == b'anonymous material'
    assert service.db.counts()['projects'] == 1
